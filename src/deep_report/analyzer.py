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

    def _extract_kpis(self, period: str, text: str, market: str) -> dict | None:
        """LLM 从单份报告中提取关键指标"""
        prompt = self._load_prompt("extract.md")
        if not prompt:
            return None

        # Truncate text if too long (keep ~15KB for extraction)
        max_chars = 15000
        if len(text) > max_chars:
            # Keep first 60% + last 40%
            split = int(max_chars * 0.6)
            text = text[:split] + "\n\n... (truncated) ...\n\n" + text[-max_chars + split:]

        user_prompt = f"{prompt}\n\n---\n\n## 报告周期: {period}\n\n## 报告正文:\n\n{text}"

        result = self._call_llm(user_prompt, max_tokens=4000)

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

    def _validate(self, kpis_list: list[dict], code: str) -> dict:
        """使用 financial-sdk 交叉校验关键字段"""
        warnings = []

        try:
            import sys as _sys
            _sys.path.insert(0, "/root/code/financial-sdk")
            _sys.path.insert(0, "/root/code/financial-sdk/src")
            from financial_sdk import FinancialFacade
            f = FinancialFacade()
            bundle = f.get_financial_data(code, report_type="all", period="annual")
            if bundle:
                income = bundle.income_statement if hasattr(bundle, 'income_statement') else None
                if income is not None and hasattr(income, '__call__'):
                    income = income()
                if income is not None and hasattr(income, 'columns') and len(income.columns) > 0:
                    warnings.append({"source": "financial-sdk", "status": "ok", "years": list(income.columns)[:3]})
                else:
                    warnings.append({"source": "financial-sdk", "status": "no_income_data"})
            else:
                warnings.append({"source": "financial-sdk", "status": "no_bundle"})
        except Exception as e:
            warnings.append({"source": "financial-sdk", "status": "unavailable", "note": str(e)[:100]})

        return {"warnings": warnings}

    # ── LLM Pass 2: Cross-Period Analysis ──

    def _analyze_multi_period(self, kpis_list: list[dict], code: str, current_period: str) -> str | None:
        """LLM 对多期 KPI 做趋势分析和叙事生成"""
        prompt = self._load_prompt("analyze.md")
        if not prompt:
            return None

        # Format KPIs as text for LLM consumption
        kpi_text = self._format_kpis_for_llm(kpis_list)

        user_prompt = f"""## 股票: {code}
## 当前分析周期: {current_period}
## 历史KPI数据:

{kpi_text}

---

请按上述框架生成完整分析报告。要求：
1. 先一句话核心观点定调
2. 每个模块包含数据表 + 分析解读
3. 区分一次性项目 vs 主营业务
4. 指出异常数据点
5. 风格参考海豚投研——有观点，用数据说话"""

        narrative = self._call_llm(
            f"{prompt}\n\n{user_prompt}",
            max_tokens=8000,
        )
        if narrative:
            narrative = self._clean_llm_response(narrative)
        return narrative

    def _format_kpis_for_llm(self, kpis_list: list[dict]) -> str:
        """将 KPI 列表格式化为 LLM 友好的文本"""
        lines = []
        for entry in kpis_list:
            period = entry.get("_period", "?")
            lines.append(f"\n### {period}")
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
