"""ReportAnalyzer — LLM驱动的财报文本提取与跨期分析"""

from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Optional

import pdfplumber
from bs4 import BeautifulSoup

logger = logging.getLogger("deep_report.analyzer")

# Prompt templates loaded on first use
_PROMPT_DIR = Path(__file__).parent / "prompts"


class ReportAnalyzer:
    """财报分析器：文本提取 → LLM KPI提取 → 跨期对比 → 叙事生成"""

    def __init__(self, verify: bool = True):
        self.verify = verify

    # ── Public API ──

    def analyze(self, code: str, period: str, reports: list[dict]) -> dict | None:
        """
        主入口：分析多期报告，返回结构化结果

        Returns:
            {kpis: [...], trends: {...}, narrative: "...", validation: {...}}
        """
        # Step 1: Extract text from each report
        texts = []
        for r in reports:
            text = self._extract_text(r["file_path"], r["market"])
            if text:
                texts.append({"period": r["period"], "text": text, "market": r["market"]})
            else:
                logger.warning("  Failed to extract text from %s", r["file_path"])

        if not texts:
            logger.error("No text extracted from any report")
            return None

        logger.info("Text extracted from %d reports", len(texts))

        # Step 2: LLM KPI extraction (Pass 1 — per-report)
        kpis_list = []
        for t in texts:
            kpi = self._extract_kpis(t["period"], t["text"], t["market"])
            if kpi:
                kpis_list.append(kpi)

        if not kpis_list:
            logger.error("No KPIs extracted")
            return None

        logger.info("KPIs extracted from %d reports", len(kpis_list))

        # Step 3: Standardize field names across periods
        kpis_list = self._standardize_fields(kpis_list)

        # Step 4: Cross-validate with financial-sdk
        validation = {}
        if self.verify:
            validation = self._validate(kpis_list, code)
            v_status = validation.get("status", "ok")
            if v_status == "reject":
                logger.warning(
                    "Validation REJECTED: %s — %d checks, %d sign errors, avg deviation %.1f%%",
                    validation.get("reason"),
                    validation.get("summary", {}).get("compared_fields", 0),
                    validation.get("summary", {}).get("sign_errors", 0),
                    validation.get("summary", {}).get("avg_deviation_pct", 0),
                )
                # Return validation-only result (no narrative)
                return {
                    "kpis": kpis_list,
                    "trends": self._build_trend_table(kpis_list),
                    "narrative": None,
                    "validation": validation,
                    "_rejected": True,
                }
            elif v_status == "warn":
                logger.warning(
                    "Validation WARNING: %s — avg deviation %.1f%%",
                    validation.get("reason"),
                    validation.get("summary", {}).get("avg_deviation_pct", 0),
                )

        # Step 5: LLM analysis (Pass 2 — cross-period)
        narrative = self._analyze_multi_period(kpis_list, code, period)

        return {
            "kpis": kpis_list,
            "trends": self._build_trend_table(kpis_list),
            "narrative": narrative,
            "validation": validation,
        }

    # ── Text Extraction ──

    def _extract_text(self, file_path: str, market: str) -> str | None:
        """从报告文件提取文本"""
        path = Path(file_path)
        if not path.exists():
            logger.warning("File not found: %s", path)
            return None

        suffix = path.suffix.lower()

        try:
            if suffix == ".pdf":
                return self._extract_pdf(path)
            elif suffix in (".html", ".htm"):
                return self._extract_html(path)
            else:
                # Try as plain text
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
        except Exception as e:
            logger.warning("Extraction failed for %s: %s", path, e)
            return None

    def _extract_pdf(self, path: Path) -> str:
        """pdfplumber 提取 PDF 文本+表格"""
        parts = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    parts.append(f"=== 第{i+1}页 ===\n{text}")

                # Extract tables as markdown
                tables = page.extract_tables()
                for j, table in enumerate(tables):
                    if table and len(table) > 1:
                        md = self._table_to_markdown(table)
                        parts.append(f"【表格 {j+1}】\n{md}")

        full_text = "\n\n".join(parts)
        logger.info("  PDF %s: %d chars, %d pages", path.name, len(full_text), len(pdf.pages))
        return full_text

    def _extract_html(self, path: Path) -> str:
        """BeautifulSoup 提取 HTML，保留表格为 markdown"""
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()

        soup = BeautifulSoup(html, "html.parser")

        # Remove script/style
        for tag in soup(["script", "style", "meta", "link"]):
            tag.decompose()

        parts = []

        # Extract tables as markdown
        for i, table in enumerate(soup.find_all("table")):
            rows = []
            for tr in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(cells)
            if len(rows) > 1:
                md = self._table_to_markdown(rows)
                parts.append(f"【表格 {i+1}】\n{md}")
            table.decompose()  # Remove from body to avoid duplicated text

        # Get body text
        body = soup.find("body")
        if body:
            text = body.get_text(separator="\n", strip=True)
            # Clean up excessive whitespace
            text = re.sub(r"\n{3,}", "\n\n", text)
            parts.insert(0, text)

        full_text = "\n\n".join(parts)
        logger.info("  HTML %s: %d chars", path.name, len(full_text))
        return full_text

    @staticmethod
    def _table_to_markdown(rows: list[list[str]]) -> str:
        """将表格行列表转为 Markdown 表格"""
        if not rows:
            return ""
        col_count = max(len(r) for r in rows)
        # Pad rows
        padded = [r + [""] * (col_count - len(r)) for r in rows]
        lines = []
        lines.append("| " + " | ".join(padded[0]) + " |")
        lines.append("|" + "|".join(["---"] * col_count) + "|")
        for row in padded[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    # ── LLM Pass 1: KPI Extraction ──

    @staticmethod
    def _sample_key_sections(text: str, max_chars: int) -> str:
        """Anchor-based sampling: locate key financial sections instead of simple head/tail."""

        # Key anchor patterns (ordered by priority)
        anchors = [
            ("consolidated statement", "CONSOLIDATED STATEMENTS OF", 3000),
            ("income statement", "INCOME STATEMENT", 2000),
            ("balance sheet", "BALANCE SHEET", 2000),
            ("cash flow", "CASH FLOW", 2000),
            ("revenue", r"(?i)\b(revenue|total revenues?)\b", 1500),
            ("profit", r"(?i)\b(gross profit|operating profit|net profit)\b", 1500),
        ]

        sections = []

        for _name, pattern, context_size in anchors:
            if sum(len(s) for s in sections) >= max_chars * 0.8:
                break
            for match in re.finditer(pattern, text, re.IGNORECASE):
                start = max(0, match.start() - context_size)
                end = min(len(text), match.end() + context_size)
                # Avoid overlap with already-sampled sections
                section_text = text[start:end]
                if len(section_text) > 200:
                    sections.append(f"\n--- Section: {_name} ---\n{section_text}")
                break  # One sample per anchor type

        if not sections:
            # Fallback: first 60% + last 40%
            split = int(max_chars * 0.6)
            return text[:split] + "\n\n... (truncated) ...\n\n" + text[-max_chars + split:]

        result = "\n".join(sections)
        if len(result) > max_chars:
            result = result[:max_chars]
        logger.info("  Anchor sampling: %d chars from %d sections (original %d chars)",
                     len(result), len(sections), len(text))
        return result

    def _extract_kpis(self, period: str, text: str, market: str) -> dict | None:
        """LLM 从单份报告中提取关键指标"""
        prompt = self._load_prompt("extract.md")
        if not prompt:
            return None

        # Use anchor-based sampling: locate key sections then truncate
        max_chars = 30000
        if len(text) > max_chars:
            text = self._sample_key_sections(text, max_chars)

        user_prompt = f"{prompt}\n\n---\n\n## 报告周期: {period}\n\n## 报告正文:\n\n{text}"

        result = self._call_llm(user_prompt, max_tokens=8000)

        if not result:
            return None

        # Clean LLM conversational prefixes
        result = self._clean_llm_response(result)

        # Parse JSON from LLM response
        try:
            # Extract JSON block
            json_match = re.search(r"```json\s*(.*?)\s*```", result, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
            else:
                # Try raw parse
                data = json.loads(result)
            data["_period"] = period
            return data
        except json.JSONDecodeError:
            logger.warning("Failed to parse KPI JSON for %s, raw: %s...", period, result[:200])
            return None

    # ── Field Standardization ──

    def _standardize_fields(self, kpis_list: list[dict]) -> list[dict]:
        """标准化跨期字段名（不同期的"营业收入"/"营收"/"收入" → revenue）"""
        # Field name aliases
        aliases = {
            "revenue": ["revenue", "营收", "营业收入", "总收入", "收入", "总营收"],
            "revenue_yoy": ["revenue_yoy", "营收同比", "收入同比", "同比增长"],
            "gross_margin": ["gross_margin", "毛利率"],
            "operating_profit": ["operating_profit", "经营利润", "营业利润"],
            "net_profit": ["net_profit", "净利润", "归母净利润", "经调整净利润", "adjusted_net_profit"],
            "selling_expense": ["selling_expense", "销售费用", "销售及分销费用"],
            "admin_expense": ["admin_expense", "管理费用", "一般及行政费用"],
            "store_count": ["store_count", "门店数", "门店总数", "全球门店"],
            "net_new_stores": ["net_new_stores", "净增门店", "净增", "净开店"],
            "domestic_sssg": ["domestic_sssg", "国内同店", "国内同店增速"],
            "overseas_sssg": ["overseas_sssg", "海外同店", "海外同店增速"],
            "ebitda": ["ebitda", "EBITDA", "息税折旧摊销前利润"],
            "roe": ["roe", "ROE", "净资产收益率"],
            "free_cash_flow": ["free_cash_flow", "自由现金流", "FCF", "fcf"],
            "debt_ratio": ["debt_ratio", "资产负债率", "负债率"],
            "r_and_d_expense": ["r_and_d_expense", "研发费用", "研发支出"],
            "eps": ["eps", "EPS", "每股收益", "基本每股收益", "稀释每股收益"],
        }

        for entry in kpis_list:
            kpis = entry.get("kpis", [])
            if isinstance(kpis, dict):
                kpis = list(kpis.values()) if any(v for v in kpis.values()) else []
                entry["kpis"] = kpis

            for kpi in kpis:
                if not isinstance(kpi, dict):
                    continue
                field = kpi.get("field", kpi.get("name", ""))
                for std_name, alias_list in aliases.items():
                    if field in alias_list:
                        kpi["field"] = std_name
                        break

        return kpis_list

    # ── Validation ──

    # Field mapping: LLM field → financial-sdk attribute path
    _SDK_FIELD_MAP = {
        "revenue": ("income_statement", "revenue"),
        "net_profit": ("income_statement", "net_profit"),
        "gross_profit": ("income_statement", "gross_profit"),
        "operating_profit": ("income_statement", "operating_profit"),
        "total_assets": ("balance_sheet", "total_assets"),
        "total_equity": ("balance_sheet", "total_equity"),
        "total_liabilities": ("balance_sheet", "total_liabilities"),
    }

    # Fields where sign matters (positive = healthy, negative = warning)
    _SIGN_SENSITIVE = {"net_profit", "operating_profit"}

    def _validate(self, kpis_list: list[dict], code: str) -> dict:
        """Cross-validate LLM-extracted KPIs against financial-sdk.

        Returns:
            {status: ok|warn|reject, checks: [...], summary: {...}}
        """
        # Load financial-sdk data
        sdk_data = self._load_sdk_data(code)
        if not sdk_data:
            return {"status": "warn", "reason": "financial-sdk_unavailable", "checks": []}

        # Compare each LLM KPI against SDK
        checks = []
        total_deviation = 0.0
        sign_errors = 0
        compared_fields = 0

        for entry in kpis_list:
            period = entry.get("_period", "?")
            kpis = entry.get("kpis", [])
            for kpi in kpis:
                if not isinstance(kpi, dict):
                    continue
                field = kpi.get("field", "")
                if field not in self._SDK_FIELD_MAP:
                    continue

                llm_value = self._parse_numeric(kpi.get("value"))
                if llm_value is None:
                    continue

                # Normalize LLM unit to base (元) for comparison
                llm_unit = kpi.get("unit", "")
                llm_value = self._normalize_unit(llm_value, llm_unit)

                sdk_value = self._get_sdk_field(sdk_data, field, period)
                if sdk_value is None:
                    checks.append({
                        "field": field, "period": period,
                        "llm_value": llm_value, "sdk_value": None,
                        "status": "no_sdk_data",
                    })
                    continue

                # Compute deviation
                abs_sdk = abs(sdk_value)
                if abs_sdk < 1:
                    deviation_pct = 100.0 if abs(llm_value - sdk_value) > 10 else 0.0
                else:
                    deviation_pct = abs(llm_value - sdk_value) / abs_sdk * 100

                # Sign check
                sign_flip = (llm_value > 0 and sdk_value < 0) or (llm_value < 0 and sdk_value > 0)
                if sign_flip and field in self._SIGN_SENSITIVE:
                    sign_errors += 1

                check = {
                    "field": field,
                    "period": period,
                    "llm_value": llm_value,
                    "sdk_value": sdk_value,
                    "deviation_pct": round(deviation_pct, 1),
                    "sign_flip": sign_flip,
                    "status": "ok" if deviation_pct < 20 and not sign_flip else "warn" if deviation_pct < 50 else "reject",
                }
                checks.append(check)
                total_deviation += deviation_pct
                compared_fields += 1

        # Summary
        if compared_fields == 0:
            return {"status": "warn", "reason": "no_comparable_fields", "checks": checks}

        avg_deviation = total_deviation / compared_fields
        reject_count = sum(1 for c in checks if c["status"] == "reject")

        if sign_errors > 0:
            status = "reject"
            reason = f"{sign_errors} sign errors detected"
        elif avg_deviation > 50 or reject_count >= 2:
            status = "reject"
            reason = f"avg deviation {avg_deviation:.0f}%, {reject_count} fields rejected"
        elif avg_deviation > 20:
            status = "warn"
            reason = f"avg deviation {avg_deviation:.0f}%"
        else:
            status = "ok"
            reason = f"avg deviation {avg_deviation:.0f}%"

        return {
            "status": status,
            "reason": reason,
            "checks": checks,
            "summary": {
                "compared_fields": compared_fields,
                "avg_deviation_pct": round(avg_deviation, 1),
                "sign_errors": sign_errors,
                "reject_count": reject_count,
            },
        }

    def _load_sdk_data(self, code: str) -> dict | None:
        """Load financial-sdk data for a stock."""
        try:
            import os as _os
            import sys as _sys
            _sdk_path = _os.environ.get("FINANCIAL_SDK_PATH", "/root/code/financial-sdk")
            _sys.path.insert(0, _sdk_path)
            _sys.path.insert(0, f"{_sdk_path}/src")
            from financial_sdk import FinancialFacade
            f = FinancialFacade()
            bundle = f.get_financial_data(code, report_type="all", period="annual")
            if not bundle:
                return None

            income = getattr(bundle, 'income_statement', None)
            bs = getattr(bundle, 'balance_sheet', None)
            cf = getattr(bundle, 'cash_flow', None)

            def _to_dict(obj):
                if obj is None:
                    return {}
                if hasattr(obj, 'to_dict'):
                    return obj.to_dict()
                if hasattr(obj, '__call__'):
                    obj = obj()
                if hasattr(obj, 'to_dict'):
                    return obj.to_dict()
                return {}

            return {
                "income_statement": _to_dict(income),
                "balance_sheet": _to_dict(bs),
                "cash_flow": _to_dict(cf),
            }
        except Exception as e:
            logger.warning("Failed to load financial-sdk: %s", e)
            return None

    @staticmethod
    def _parse_numeric(value) -> float | None:
        """Parse a numeric value from LLM output (handles strings, ints, floats)."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).replace(",", "").replace(" ", ""))
        except (ValueError, TypeError):
            return None

    # Unit multiplier to base unit (元)
    _UNIT_MULTIPLIER = {
        "元": 1, "rmb": 1, "cny": 1,
        "千元": 1_000, "thousands": 1_000,
        "万元": 10_000,
        "百万元": 1_000_000, "millions": 1_000_000,
        "亿元": 100_000_000, "亿": 100_000_000,
    }

    @classmethod
    def _normalize_unit(cls, value: float, unit: str | None) -> float:
        """Convert a value in given unit to base unit (元)."""
        if not unit:
            return value
        unit_lower = unit.lower().replace(" ", "").replace("人民币", "")
        multiplier = cls._UNIT_MULTIPLIER.get(unit_lower, 1)
        return value * multiplier

    def _get_sdk_field(self, sdk_data: dict, field: str, period: str) -> float | None:
        """Get the latest annual value for a field from SDK data."""
        source_name, attr_name = self._SDK_FIELD_MAP.get(field, (None, None))
        if not source_name:
            return None
        source = sdk_data.get(source_name, {})
        values = source.get(attr_name, {})
        if not values:
            return None
        if isinstance(values, dict):
            indices = sorted(values.keys())
            if indices:
                val = values[indices[-1]]
                return float(val) if val is not None else None
        return None

    # ── LLM Pass 2: Cross-Period Analysis ──

    def _analyze_multi_period(self, kpis_list: list[dict], code: str, current_period: str) -> str | None:
        """LLM 对多期 KPI 做趋势分析和叙事生成"""
        prompt = self._load_prompt("analyze.md")
        if not prompt:
            return None

        # Format KPIs as text for LLM consumption
        kpi_text = self._format_kpis_for_llm(kpis_list)

        # Build credibility table from guidance vs actuals
        credibility = self._build_credibility_table(kpis_list)

        user_prompt = f"""## 股票: {code}
## 当前分析周期: {current_period}

{credibility}

## 历史KPI数据:

{kpi_text}

---

请按上述框架生成完整分析报告。要求：
1. 先一句话核心观点定调
2. 每个模块包含数据表 + 分析解读
3. 区分一次性项目 vs 主营业务
4. 指出异常数据点
5. 风格参考海豚投研——有观点，用数据说话

        narrative = self._call_llm(
            f"{prompt}\n\n{user_prompt}",
            max_tokens=8000,
        )
        if narrative:
            narrative = self._clean_llm_response(narrative)
        return narrative

    def _format_kpis_for_llm(self, kpis_list: list[dict]) -> str:
        """将 KPI 列表 + 业务模型数据格式化为 LLM 友好的文本"""
        lines = []
        for entry in kpis_list:
            period = entry.get("_period", "?")
            lines.append(f"\n### {period}")

            # Business model data (new)
            bm = entry.get("business_model")
            if bm:
                lines.append("\n**业务模型数据**:")
                segments = bm.get("segments", [])
                if segments:
                    lines.append("  分部收入:")
                    for seg in segments:
                        lines.append(f"    - {seg.get('name','?')}: 收入{seg.get('revenue','?')}{seg.get('revenue_unit','')} "
                                     f"({seg.get('revenue_yoy','')}), 占比{seg.get('revenue_share_pct','?')}%, "
                                     f"毛利率{seg.get('gross_margin','?')}%")
                unit_econ = bm.get("unit_economics", [])
                if unit_econ:
                    lines.append("  单位经济:")
                    for ue in unit_econ:
                        lines.append(f"    - {ue.get('name','?')}: {ue.get('value','?')}{ue.get('unit','')} "
                                     f"(同比{ue.get('yoy','?')})")
                rev_mix = bm.get("revenue_mix")
                if rev_mix:
                    mix_parts = [f"{k}: {v}%" for k, v in rev_mix.items() if v]
                    if mix_parts:
                        lines.append(f"  收入结构: {', '.join(mix_parts)}")
                moat = bm.get("moat_signals", [])
                if moat:
                    lines.append("  护城河信号:")
                    for m in moat:
                        lines.append(f"    - {m.get('name','?')}: {m.get('value','?')}{m.get('unit','')}")

            # One-time items (new)
            one_time = entry.get("one_time_items")
            if one_time:
                lines.append("\n**一次性/非经常性项目**:")
                total_one_time = 0
                for item in one_time:
                    val = item.get("value", 0) or 0
                    lines.append(f"    - {item.get('name','?')}: {val}{item.get('unit','')} "
                                 f"[{item.get('nature','?')}] — {item.get('description','?')}")
                    total_one_time += val
                lines.append(f"  一次性项目合计: {total_one_time}{one_time[0].get('unit','') if one_time else ''}")

            # Cash flow quality (new)
            cfq = entry.get("cash_flow_quality")
            if cfq:
                lines.append(f"\n**现金流质量**:")
                lines.append(f"    经营现金流: {cfq.get('operating_cash_flow','?')}{cfq.get('ocf_unit','')}")
                ocf_ratio = cfq.get("net_profit_match", {}).get("ocf_to_net_profit_ratio")
                if ocf_ratio:
                    lines.append(f"    OCF/净利润比值: {ocf_ratio} ({cfq.get('net_profit_match',{}).get('assessment','?')})")
                lines.append(f"    自由现金流: {cfq.get('free_cash_flow','?')}{cfq.get('fcf_unit','')}")
                lines.append(f"    资本开支: {cfq.get('capex','?')}{cfq.get('capex_unit','')}")

            # Management guidance (new)
            guidance = entry.get("management_guidance")
            if guidance:
                lines.append(f"\n**管理层指引**:")
                lines.append(f"    收入展望: {guidance.get('revenue_outlook','?')}")
                margin = guidance.get('margin_outlook')
                if margin:
                    lines.append(f"    利润展望: {margin}")
                initiatives = guidance.get('key_initiatives', [])
                if initiatives:
                    lines.append(f"    战略重点: {', '.join(initiatives)}")
                risks = guidance.get('risk_mentions', [])
                if risks:
                    lines.append(f"    提及风险: {', '.join(risks)}")

            # KPIs
            kpis = entry.get("kpis", [])
            for kpi in kpis:
                field = kpi.get("field", kpi.get("name", "?"))
                value = kpi.get("value", "?")
                unit = kpi.get("unit", "")
                yoy = kpi.get("yoy", "")
                source = kpi.get("source", "")
                parts = [f"  {field}: {value}{unit}"]
                if yoy:
                    parts.append(f"（{yoy}）")
                if source:
                    parts.append(f" [来源: {source[:80]}]")
                lines.append("".join(parts))
        return "\n".join(lines)

    def _build_trend_table(self, kpis_list: list[dict]) -> list[dict]:
        """构建数值化趋势表（备用）"""
        # Simple trend extraction for key fields
        fields = ["revenue", "net_profit", "gross_margin", "store_count"]
        trends = []
        for field in fields:
            values = []
            for entry in kpis_list:
                for kpi in entry.get("kpis", []):
                    if kpi.get("field") == field:
                        try:
                            values.append({
                                "period": entry.get("_period"),
                                "value": float(kpi.get("value", 0)),
                            })
                        except (ValueError, TypeError):
                            pass
            if values:
                trends.append({"field": field, "values": values})
        return trends

    @staticmethod
    def _build_credibility_table(kpis_list: list[dict]) -> str:
        """Build a guidance-vs-actual credibility tracking table.

        Matches each period's management_guidance.revenue_outlook against
        the next period's actual revenue, producing a structured markdown
        table for the LLM to reference in the V展望 analysis.
        """
        # Sort by period: Q1<Q2<Q3<Q4<FY within same year, ascending
        def _period_key(entry):
            p = entry.get("_period", "")
            freq_order = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5, "HY": 6}
            try:
                y = int(p[:4])
                f = p[4:].upper()
                return (y, freq_order.get(f, 0))
            except (ValueError, IndexError):
                return (0, 0)

        sorted_kpis = sorted(kpis_list, key=_period_key)

        if len(sorted_kpis) < 2:
            return ""

        # Extract actual revenue from each period
        def _get_revenue(entry):
            for kpi in entry.get("kpis", []):
                if kpi.get("field") == "revenue":
                    val = kpi.get("value")
                    unit = kpi.get("unit", "")
                    if val is not None:
                        try:
                            return float(val), unit
                        except (ValueError, TypeError):
                            return None, ""
            return None, ""

        rows = []
        for i in range(len(sorted_kpis) - 1):
            guidance = sorted_kpis[i].get("management_guidance")
            if not guidance:
                continue
            outlook = guidance.get("revenue_outlook", "")
            if not outlook:
                continue

            actual_val, unit = _get_revenue(sorted_kpis[i + 1])
            if actual_val is None:
                continue

            period_from = sorted_kpis[i].get("_period", "?")
            period_to = sorted_kpis[i + 1].get("_period", "?")
            rows.append(f"| {period_from} | {outlook} | {period_to} | {actual_val}{unit} |")

        if not rows:
            return ""

        header = (
            "## 管理层可信度追踪（指引 vs 实际）\n\n"
            "| 指引来源季 | 管理层展望 | 实际季度 | 实际收入 |\n"
            "|-----------|-----------|---------|--------|\n"
        )
        return header + "\n".join(rows) + "\n"

    # ── LLM Client ──

    def _call_llm(self, user_prompt: str, max_tokens: int = 4000) -> str | None:
        """调用 LLM（复用 morning-brief 的 LLM client）"""
        try:
            import os as _os
            import sys as _sys
            _mb_path = _os.environ.get("MORNING_BRIEF_PATH", "/root/code/morning-brief")
            _sys.path.insert(0, _mb_path)

            from src.utils.config import (
                get_ark_api_key, get_deepseek_api_key,
                LLM_ENDPOINT, LLM_MODEL,
                DEEPSEEK_ENDPOINT, DEEPSEEK_MODEL,
            )
            from src.utils.llm_client import LLMProvider, call_with_fallback

            providers = []
            # Primary: DeepSeek (more reliable for large prompts than doubao)
            try:
                providers.append(LLMProvider(
                    endpoint=DEEPSEEK_ENDPOINT,
                    model="deepseek-chat",
                    api_key=get_deepseek_api_key(),
                    label="DeepSeek",
                ))
            except Exception:
                pass
            # Fallback: ARK (doubao), with 429 handled by retry + backoff
            try:
                providers.append(LLMProvider(
                    endpoint=LLM_ENDPOINT,
                    model="doubao-seed-2.0-pro",
                    api_key=get_ark_api_key(),
                    label="ARK",
                    extra={"thinking": {"type": "disabled"}},
                ))
            except Exception:
                pass

            if not providers:
                logger.error("No LLM providers available")
                return None

            return call_with_fallback(
                providers=providers,
                system_prompt="你是一位资深财务分析师，擅长从财报中提取关键数据并撰写深度分析。用中文回复。",
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=0.3,
                timeout=120,
            )
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return None

    @staticmethod
    def _load_prompt(name: str) -> str | None:
        """加载 prompt 模板"""
        prompt_file = _PROMPT_DIR / name
        if not prompt_file.exists():
            logger.warning("Prompt template not found: %s", prompt_file)
            return None
        return prompt_file.read_text(encoding="utf-8")

    @staticmethod
    def _clean_llm_response(text: str) -> str:
        """Strip conversational prefixes/suffixes from LLM output"""
        # Prefixes
        prefixes = [
            "好的，收到。", "好的，", "收到。", "明白了。", "没问题。",
            "OK，", "Okay，", "好的, ", "收到, ",
        ]
        for p in prefixes:
            if text.startswith(p):
                text = text[len(p):].lstrip()
                break
        # Suffixes
        suffixes = [
            "希望对你有帮助。", "希望对您有帮助。", "希望以上分析对你有帮助。",
            "以上是分析报告。", "以上是完整的分析报告。",
            "如果有需要调整的地方请告诉我。", "如果有问题请随时指出。",
            "\n---\n\n以上是", "\n---\n以上是",
            "（以上内容由AI生成，不构成投资建议）",
        ]
        for s in suffixes:
            if text.rstrip().endswith(s):
                text = text.rstrip()[: -len(s)].rstrip()
                break
        return text
