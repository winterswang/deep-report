"""ReportAnalyzer — LLM驱动的财报文本提取与跨期分析"""

from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Optional

import pdfplumber
from bs4 import BeautifulSoup

from deep_report.config import MORNING_BRIEF_PATH

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

        # Detect transition / short-period filings and adjust period label
        report_period = self._detect_filing_period(texts[0]["text"], period)
        if report_period != period:
            logger.info("Adjusted period: %s → %s", period, report_period)
            period = report_period
            for t in texts:
                t["period"] = report_period

        logger.info("Text extracted from %d reports", len(texts))

        # Step 2: KPI extraction (Pass 1 — per-report)
        kpis_list = []
        
        # Check if pre-extracted KPIs are available (from deterministic extraction)
        if hasattr(self, '_pre_extracted_kpis') and self._pre_extracted_kpis:
            logger.info("Using pre-extracted KPIs: %d fields", len(self._pre_extracted_kpis))
            for t in texts:
                kpis_list.append({
                    "period": t["period"],
                    "kpis": self._pre_extracted_kpis,
                })
        else:
            # Fall back to LLM-based extraction
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

        # Step 5: Fetch peer comparison data (industry context)
        peers = self._fetch_peer_data(code)

        # Step 5.5: Fetch valuation & technical indicators context
        # Infer market from reports data
        market = texts[0].get("market", "A") if texts else "A"
        valuation_text = ""
        tech_text = ""
        try:
            from deep_report.valuation import format_valuation_context as _val_ctx
            valuation_text = _val_ctx(code, market)
        except Exception as e:
            logger.debug("Valuation context unavailable: %s", e)
        try:
            from deep_report.westock_provider import fetch_technical_indicators as _tech
            tech_data = _tech(code, market)
            if tech_data:
                tech_text = ReportAnalyzer._format_tech_context(tech_data)
        except Exception as e:
            logger.debug("Technical context unavailable: %s", e)

        # Step 6: LLM analysis (Pass 2 — cross-period, with peer + credibility + risk context)
        peer_text = self._format_peer_data_for_llm(peers)
        credibility = self._build_credibility_table(kpis_list)
        risk_evolution = self._build_risk_evolution_table(kpis_list)
        narrative = self._analyze_multi_period(
            kpis_list, code, period, peer_text, credibility, risk_evolution,
            valuation_text=valuation_text, tech_text=tech_text,
        )

        return {
            "kpis": kpis_list,
            "trends": self._build_trend_table(kpis_list),
            "narrative": narrative,
            "validation": validation,
            "peers": peers,
        }

    # ── Industry Peer Comparison ──

    # Industry peer mapping: stock_code → list of comparable stocks
    _PEER_MAP: dict[str, list[tuple[str, str, str]]] = {
        # ── 白酒 ──
        "600519.SH": [("000858.SZ", "五粮液", "高端白酒"), ("000568.SZ", "泸州老窖", "高端白酒"), ("002304.SZ", "洋河股份", "次高端白酒")],
        "000858.SZ": [("600519.SH", "贵州茅台", "高端白酒"), ("000568.SZ", "泸州老窖", "高端白酒")],
        # ── 互联网/平台 ──
        "0700.HK": [("9988.HK", "阿里巴巴", "互联网平台"), ("3690.HK", "美团", "本地生活"), ("BIDU", "百度", "搜索/AI")],
        "9988.HK": [("0700.HK", "腾讯", "互联网平台"), ("3690.HK", "美团", "本地生活"), ("PDD", "拼多多", "电商")],
        "3690.HK": [("0700.HK", "腾讯", "互联网平台"), ("9988.HK", "阿里巴巴", "互联网平台")],
        # ── 电商 ──
        "PDD": [("BABA", "阿里巴巴", "电商平台"), ("JD", "京东", "电商平台"), ("MELI", "MercadoLibre", "拉美电商")],
        "BABA": [("PDD", "拼多多", "电商平台"), ("JD", "京东", "电商平台"), ("0700.HK", "腾讯", "互联网平台")],
        "JD": [("BABA", "阿里巴巴", "电商平台"), ("PDD", "拼多多", "电商平台")],
        # ── 消费/零售 ──
        "MNSO": [("9992.HK", "泡泡玛特", "IP零售/潮玩"), ("2020.HK", "安踏体育", "品牌零售")],
        "9896.HK": [("9992.HK", "泡泡玛特", "IP零售/潮玩"), ("2020.HK", "安踏体育", "品牌零售")],
        "9992.HK": [("9896.HK", "名创优品", "IP零售"), ("MNSO", "名创优品", "IP零售")],
        # ── AI/半导体 ──
        "NVDA": [("AMD", "AMD", "GPU/CPU"), ("AVGO", "博通", "网络/AI芯片"), ("INTC", "英特尔", "CPU/代工")],
        "AMD": [("NVDA", "英伟达", "GPU"), ("INTC", "英特尔", "CPU")],
        "AVGO": [("NVDA", "英伟达", "GPU"), ("MRVL", "Marvell", "数据中心芯片")],
        # ── 消费电子 ──
        "AAPL": [("MSFT", "Microsoft", "软件/云"), ("GOOGL", "Alphabet", "搜索/云"), ("SAMSUNG", "三星", "消费电子")],
        # ── 新能源 ──
        "TSLA": [("BYD", "比亚迪", "新能源车"), ("NIO", "蔚来", "新能源车"), ("XPEV", "小鹏", "新能源车")],
        # ── 医药 ──
        "NVO": [("LLY", "礼来", "GLP-1/减肥药"), ("MRK", "默沙东", "制药")],
        "LLY": [("NVO", "诺和诺德", "GLP-1/减肥药")],
        # ── 音乐/内容 ──
        "TME": [("SPOT", "Spotify", "音乐流媒体"), ("9899.HK", "云音乐", "音乐流媒体")],
        # ── 游戏 ──
        "RBLX": [("U", "Unity", "游戏引擎"), ("EA", "EA", "游戏发行"), ("TTWO", "Take-Two", "游戏发行")],
        "NTES": [("0700.HK", "腾讯", "游戏/社交"), ("EA", "EA", "游戏发行")],
        # ── 网络安全/SaaS ──
        "OKTA": [("CRWD", "CrowdStrike", "网络安全"), ("ZS", "Zscaler", "零信任"), ("PANW", "Palo Alto", "网络安全")],
        # ── 社交 ──
        "RDDT": [("META", "Meta", "社交/广告"), ("SNAP", "Snap", "社交"), ("PINS", "Pinterest", "图片社交")],
        # ── 出行/旅游 ──
        "TCOM": [("BKNG", "Booking", "在线旅游"), ("EXPE", "Expedia", "在线旅游")],
        # ── 航天 ──
        "RKLB": [("SPCE", "Virgin Galactic", "航天"), ("LUNR", "Intuitive Machines", "月球探测")],
        # ── 金融 ──
        "000001.SZ": [("600036.SH", "招商银行", "零售银行"), ("601318.SH", "中国平安", "综合金融")],
        # ── 食品饮料 ──
        "09698.HK": [("09633.HK", "农夫山泉", "饮料"), ("00322.HK", "康师傅", "食品饮料")],
        # ── 医药CRO ──
        "02272.HK": [("02359.HK", "药明康德", "CRO"), ("03759.HK", "康龙化成", "CRO")],
        # ── 医疗设备 ──
        "300760.SZ": [("688271.SH", "联影医疗", "医疗影像"), ("002223.SZ", "鱼跃医疗", "家用医疗")],
        # ── 新能源/电力设备 ──
        "300274.SZ": [("688390.SH", "固德威", "逆变器"), ("300763.SZ", "锦浪科技", "逆变器")],
        # ── 电子/半导体(A股) ──
        "300661.SZ": [("603501.SH", "韦尔股份", "CIS芯片"), ("688008.SH", "澜起科技", "内存接口")],
        # ── 电力自动化 ──
        "600406.SH": [("601126.SH", "四方股份", "电力自动化"), ("002121.SZ", "科陆电子", "智能电网")],
        "601126.SH": [("600406.SH", "国电南瑞", "电力自动化")],
        # ── 芯片/PCB ──
        "688630.SH": [("002916.SZ", "深南电路", "PCB"), ("603228.SH", "景旺电子", "PCB")],
    }

    _PEER_FALLBACK: dict[str, list[tuple[str, str, str]]] = {
        "A": [("600519.SH", "贵州茅台", "A股蓝筹"), ("000858.SZ", "五粮液", "A股消费")],
        "HK": [("0700.HK", "腾讯", "港股科技"), ("9988.HK", "阿里巴巴", "港股互联网")],
        "US": [("AAPL", "Apple", "美股科技"), ("MSFT", "Microsoft", "美股软件")],
    }

    def _fetch_peer_data(self, code: str) -> list[dict]:
        code_upper = code.upper()
        peers = self._PEER_MAP.get(code_upper)
        if not peers:
            market = self._detect_market_for_peers(code_upper)
            peers = self._PEER_FALLBACK.get(market, [])[:2]
        if not peers:
            return []

        from deep_report.westock_provider import fetch_finance_data

        def _latest(data_dict, field):
            if not data_dict or not isinstance(data_dict, dict):
                return None
            vals = data_dict.get(field, {})
            if isinstance(vals, dict) and vals:
                idx = sorted(vals.keys())[-1]
                v = vals[idx]
                return float(v) if v is not None else None
            return None

        results = []
        for p_code, p_name, p_cat in peers:
            try:
                p_market = self._detect_market_for_peers(p_code)
                data = fetch_finance_data(p_code, p_market, num_periods=1)
                if not data:
                    continue
                is_data = data.get("income_statement", {})
                bs_data = data.get("balance_sheet", {})

                # Convert to indexed format for _latest()
                def _to_indexed(source: dict) -> dict:
                    result = {}
                    for field, pvals in source.items():
                        result[field] = {str(i): v for i, (k, v) in enumerate(sorted(pvals.items()))}
                    return result

                idict = _to_indexed(is_data)
                bdict = _to_indexed(bs_data)
                results.append({
                    "code": p_code, "name": p_name, "category": p_cat,
                    "metrics": {
                        "revenue": _latest(idict, "revenue"),
                        "net_profit": _latest(idict, "net_profit"),
                        "gross_profit": _latest(idict, "gross_profit"),
                        "total_assets": _latest(bdict, "total_assets"),
                    },
                })
            except Exception as e:
                logger.debug("Peer %s fetch failed: %s", p_code, e)
        logger.info("Peer data fetched: %d/%d", len(results), len(peers))
        return results

    @staticmethod
    def _detect_market_for_peers(code: str) -> str:
        u = code.upper()
        if u.endswith(".SH") or u.endswith(".SZ"):
            return "A"
        if u.endswith(".HK"):
            return "HK"
        return "US"

    @staticmethod
    def _format_peer_data_for_llm(peers: list[dict]) -> str:
        if not peers:
            return ""
        rows = []
        for p in peers:
            m = p.get("metrics", {})
            rev = f"{m['revenue']:,.0f}" if m.get("revenue") else "N/A"
            np_val = f"{m['net_profit']:,.0f}" if m.get("net_profit") else "N/A"
            gp = f"{m['gross_profit']:,.0f}" if m.get("gross_profit") else "N/A"
            rows.append(f"| {p['name']} ({p['code']}) | {p['category']} | {rev} | {np_val} | {gp} |")
        header = ("## 行业可比公司数据\n\n"
                  "| 公司 | 可比维度 | 营收 | 净利润 | 毛利润 |\n"
                  "|------|---------|------|--------|--------|\n")
        return header + "\n".join(rows) + "\n"

    @staticmethod
    def _format_tech_context(tech: dict) -> str:
        """Format technical indicators as LLM context."""
        if not tech:
            return ""
        lines = ["## 📈 技术指标（最新交易日）", ""]
        # Try kdj variants
        kdj_k = tech.get("kdj_KDJ_K") or tech.get("kdj_K")
        kdj_d = tech.get("kdj_KDJ_D") or tech.get("kdj_D")
        kdj_j = tech.get("kdj_KDJ_J") or tech.get("kdj_J")

        for field, label in [
            ("closePrice", "收盘价"),
            ("ma_MA_5", "MA5"), ("ma_MA_20", "MA20"), ("ma_MA_60", "MA60"),
            ("macd_DIF", "MACD DIF"), ("macd_DEA", "MACD DEA"),
            ("rsi_RSI_6", "RSI(6)"), ("rsi_RSI_12", "RSI(12)"),
        ]:
            val = tech.get(field)
            if val is not None:
                lines.append(f"- {label}: {val:.2f}")

        if kdj_k is not None:
            lines.append(f"- KDJ: K={kdj_k:.2f} D={kdj_d:.2f} J={kdj_j:.2f}")

        lines.append("")
        lines.append("> 请在报告中引用以上实时技术指标，避免依赖 LLM 内置过期数据。")
        return "\n".join(lines)

    @staticmethod
    def _period_sort_key(entry: dict) -> tuple[int, int]:
        """Sort key for period identifiers (e.g. 2025Q1, 2025FY)."""
        p = entry.get("_period", "")
        freq = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5, "HY": 6}
        try:
            return (int(p[:4]), freq.get(p[4:].upper(), 0))
        except (ValueError, IndexError):
            return (0, 0)

    # ── Management Credibility & Risk Evolution ──

    @staticmethod
    def _build_credibility_table(kpis_list: list[dict]) -> str:
        """Guidance-vs-actual tracking table."""
        sorted_kpis = sorted(kpis_list, key=ReportAnalyzer._period_sort_key)
        if len(sorted_kpis) < 2:
            return ""
        def _get_revenue(entry):
            for kpi in entry.get("kpis", []):
                if kpi.get("field") == "revenue" and kpi.get("value") is not None:
                    try:
                        return float(kpi["value"]), kpi.get("unit", "")
                    except (ValueError, TypeError):
                        pass
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
            rows.append(f"| {sorted_kpis[i].get('_period','?')} | {outlook} | "
                        f"{sorted_kpis[i+1].get('_period','?')} | {actual_val}{unit} |")
        if not rows:
            return ""
        return ("## 管理层可信度追踪（指引 vs 实际）\n\n"
                "| 指引来源季 | 管理层展望 | 实际季度 | 实际收入 |\n"
                "|-----------|-----------|---------|--------|\n"
                + "\n".join(rows) + "\n")

    @staticmethod
    def _build_risk_evolution_table(kpis_list: list[dict]) -> str:
        """Risk factor evolution across periods."""
        sorted_kpis = sorted(kpis_list, key=ReportAnalyzer._period_sort_key)
        period_risks: list[tuple[str, list[str]]] = []
        for entry in sorted_kpis:
            guidance = entry.get("management_guidance")
            if guidance:
                risks = guidance.get("risk_mentions", [])
                if risks:
                    period_risks.append((entry.get("_period", "?"), risks))
        if len(period_risks) < 2:
            return ""
        import re as _re
        def _norm(risk: str) -> str:
            s = risk.lower().strip()
            s = _re.sub(r"[，。；：！？、,\;:!?\s]+", " ", s)
            s = _re.sub(r"\s+", "", s)
            return s
        risk_lifecycle: dict[str, dict] = {}
        for period, risks in period_risks:
            for r in risks:
                normed = _norm(r)
                if normed in risk_lifecycle:
                    risk_lifecycle[normed]["last_period"] = period
                else:
                    risk_lifecycle[normed] = {"first_period": period, "last_period": period, "original": r}
        current_period = period_risks[-1][0] if period_risks else "?"
        new_risks, persistent, resolved = [], [], []
        for normed, info in risk_lifecycle.items():
            if info["last_period"] == current_period:
                if info["first_period"] == current_period:
                    new_risks.append(info["original"])
                else:
                    persistent.append(info["original"])
            else:
                resolved.append((info["original"], info["first_period"], info["last_period"]))
        parts = ["## 风险因子跨期变化追踪\n"]
        if new_risks:
            parts.append(f"\n### 🆕 本期新出现风险 ({current_period})")
            for r in new_risks:
                parts.append(f"- {r}")
        if persistent:
            parts.append("\n### 🔁 持续风险")
            for r in persistent:
                parts.append(f"- {r}")
        if resolved:
            parts.append("\n### ✅ 已消退风险")
            for r, first, last in resolved:
                parts.append(f"- {r} （{first} → {last}，本期未再提及）")
        parts.append("")
        return "\n".join(parts)

    @staticmethod
    def _detect_filing_period(text: str, user_period: str) -> str:
        """Detect transition/short-period filings and return correct period label.

        Example: MNSO 20-F for Jul-Dec 2023 transition period →
        'Jul-Dec2023(6mo_Transition)' instead of '2024FY'.
        """
        import re as _re
        m = _re.search(
            r'TRANSITION REPORT.*?period from\s+(.+?)\s+to\s+([A-Z][a-z]+ \d{1,2}, \d{4})',
            text[:5000], _re.IGNORECASE | _re.DOTALL,
        )
        if m:
            from_d = m.group(1).strip()
            to_d = m.group(2).strip()
            # Parse dates and compute duration
            try:
                from_parsed = _re.match(r'([A-Z][a-z]+) (\d{1,2}), (\d{4})', from_d)
                to_parsed = _re.match(r'([A-Z][a-z]+) (\d{1,2}), (\d{4})', to_d)
                if from_parsed and to_parsed:
                    from_mon = from_parsed.group(1)[:3]
                    to_mon = to_parsed.group(1)[:3]
                    to_yr = to_parsed.group(3)
                    label = f"{from_mon}-{to_mon}{to_yr}(Transition)"
                    logger.info("Detected TRANSITION REPORT: %s → label=%s", from_d, label)
                    return label
            except Exception:
                pass
        return user_period

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
                try:
                    text = page.extract_text()
                    if text:
                        parts.append(f"=== 第{i+1}页 ===\n{text}")
                except Exception:
                    pass  # Skip unextractable pages

                # Extract tables as markdown
                try:
                    tables = page.extract_tables()
                    for j, table in enumerate(tables):
                        if table and len(table) > 1:
                            # Normalize None cells to empty strings
                            clean = [[c or "" for c in row] for row in table]
                            md = self._table_to_markdown(clean)
                            parts.append(f"【表格 {j+1}】\n{md}")
                except Exception:
                    pass  # Skip problematic tables

        full_text = "\n\n".join(parts)
        logger.info("  PDF %s: %d chars, %d pages", path.name, len(full_text), len(pdf.pages))
        return full_text

    def _extract_html(self, path: Path) -> str:
        """BeautifulSoup 提取 HTML，保留表格为 markdown"""
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()

        soup = BeautifulSoup(html, "html.parser")

        # Remove script/style/structural
        for tag in soup(["script", "style", "meta", "link", "title"]):
            tag.decompose()

        # Strip XBRL namespace tags (ix:nonFraction, ix:nonNumeric etc.) — keep text, drop tags
        for tag in soup.find_all(re.compile(r'ix:')):
            tag.unwrap()
        # Remove linkbase/reference/schema sections entirely
        for tag in soup.find_all(re.compile(r'link:')):
            tag.decompose()
        for tag in soup.find_all(re.compile(r'xbrl[i]?:')):
            tag.decompose()
        # Remove hidden/non-printing elements
        for tag in soup.find_all(attrs={"style": re.compile(r'display\s*:\s*none', re.IGNORECASE)}):
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
        # Normalize None → "" for safe join
        safe_rows = [[str(c) if c is not None else "" for c in row] for row in rows]
        col_count = max(len(r) for r in safe_rows)
        # Pad rows
        padded = [r + [""] * (col_count - len(r)) for r in safe_rows]
        lines = []
        lines.append("| " + " | ".join(padded[0]) + " |")
        lines.append("|" + "|".join(["---"] * col_count) + "|")
        for row in padded[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    # ── LLM Pass 1: KPI Extraction ──

    @staticmethod
    def _sample_key_sections(text: str, max_chars: int, market: str = "") -> str:
        """Anchor-based sampling with data density scoring.

        Locates key financial sections via bilingual anchors, scores each
        match by data density (digits + table markers), and picks the
        best match(es) per anchor type. Avoids TOC false positives.
        """

        # Chinese anchors (A-share / HK)
        _CN_ANCHORS = [
            ("利润表", r"(?:利润表|合并利润表|综合收益表)", 2000),
            ("资产负债表", r"(?:资产负债表|合并资产负债表)", 2000),
            ("现金流量表", r"(?:现金流量表|合并现金流量表)", 2000),
            ("营业收入", r"(?:营业收入|总收入|营收)", 1500),
            ("毛利率", r"(?:毛利率|毛利)", 1500),
        ]
        # English anchors (US filings)
        _EN_ANCHORS = [
            ("income_statement", r"(?i)(?:consolidated\s+statements?\s+of\s+(?:income|operations)|income\s+statement)", 2000),
            ("balance_sheet", r"(?i)(?:consolidated\s+balance\s+sheets?|balance\s+sheet)", 2000),
            ("cash_flow", r"(?i)(?:consolidated\s+statements?\s+of\s+cash\s+flows?|cash\s+flow)", 2000),
            ("mda", r"(?i)(?:[Mm]anagement'?s?\s+[Dd]iscussion|[Rr]esults?\s+of\s+[Oo]perations|[Ff]inancial\s+[Cc]ondition)", 3000),
            ("total_revenue", r"(?i)(?:^(?:Total\s+)?[Rr]evenue\s*\$)", 1500),
            ("gross_profit", r"(?i)(?:^(?:Gross\s+[Pp]rofit|Cost\s+of\s+[Rr]evenue)\s*\$)", 1500),
            ("segment_info", r"(?i)(?:Note\s+\d+.*?Segment|Segment\s+(?:Information|Reporting|Data))", 3000),
            ("operating_income", r"(?i)(?:[Oo]perating\s+[Ii]ncome|[Ii]ncome\s+from\s+[Oo]perations)\s*\$", 1500),
            ("net_income", r"(?i)(?:[Nn]et\s+[Ii]ncome|[Nn]et\s+[Ee]arnings)\s*\$", 1500),
        ]
        # Select anchors by market to avoid useless cross-language scans
        is_cn = market in ("CN", "HK")
        anchors = _CN_ANCHORS if is_cn else _CN_ANCHORS + _EN_ANCHORS

        sections = []
        used_ranges = []  # (start, end) to avoid overlaps

        for _name, pattern, context_size in anchors:
            if sum(len(s) for s in sections) >= max_chars * 0.85:
                break

            # Collect all matches, score by data density
            scored = []
            for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
                pre = max(0, match.start() - context_size // 4)
                post = min(len(text), match.end() + context_size)
                candidate = text[pre:post]
                # Score: digit count + table lines (weighted) to prefer data-rich sections
                digit_count = sum(1 for c in candidate if c.isdigit())
                table_lines = candidate.count("|")
                score = digit_count + table_lines * 10
                if score > 50:  # Require minimum data density
                    scored.append((score, pre, post))

            # Take up to 3 best matches per anchor (dedup by overlap)
            scored.sort(reverse=True)
            taken = 0
            for score, start, end in scored:
                if taken >= 3:
                    break
                # Check overlap with already-sampled sections (>50% overlap = skip)
                overlaps = any(
                    max(start, s) < min(end, e)
                    and (min(end, e) - max(start, s)) > (end - start) * 0.5
                    for s, e in used_ranges
                )
                if overlaps:
                    continue
                section_text = text[start:end]
                if len(section_text) > 200:
                    used_ranges.append((start, end))
                    sections.append(f"\n--- Section: {_name}[{taken+1}] ---\n{section_text}")
                    taken += 1

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
            text = self._sample_key_sections(text, max_chars, market)

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
        # Margin/ratio fields (percentages, no sign check needed)
        "gross_margin": ("income_statement", "gross_margin"),
        "net_margin": ("income_statement", "net_margin"),
        "roe": ("balance_sheet", "roe"),
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
        """Load financial data via WeStock Data for cross-validation.

        Returns format compatible with _get_sdk_field():
            {source_name: {field_name: {period_index: value}}}
        """
        try:
            from deep_report.westock_provider import fetch_finance_data

            # Infer market from code format
            if code.endswith((".SH", ".SZ")):
                market = "A"
            elif code.endswith(".HK"):
                market = "HK"
            elif code.isalpha() or "." not in code:
                market = "US"
            else:
                market = "A"

            data = fetch_finance_data(code, market, num_periods=8)
            if not data or not data.get("periods"):
                return None

            # Convert period-date keys to integer indices (compatible with _get_sdk_field)
            def _to_indexed(values: dict) -> dict:
                if not values:
                    return {}
                return {str(i): v for i, (k, v) in enumerate(sorted(values.items()))}

            result = {}
            for source_name in ("income_statement", "balance_sheet", "cash_flow"):
                source = data.get(source_name, {})
                if source:
                    indexed = {}
                    for field_name, period_values in source.items():
                        indexed[field_name] = _to_indexed(period_values)
                    result[source_name] = indexed

            return result if result else None
        except Exception as e:
            logger.warning("Failed to load WeStock data: %s", e)
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
        # USD units (SEC filings use millions USD)
        "百万美元": 1_000_000, "million usd": 1_000_000, "usd millions": 1_000_000,
        "亿美元": 100_000_000, "billion usd": 100_000_000, "usd billions": 100_000_000,
        # HK units (HKEX filings may use 千元)
        "千港元": 1_000, "hkd thousands": 1_000,
        "百万港元": 1_000_000, "hkd millions": 1_000_000,
        "亿港元": 100_000_000, "hkd billions": 100_000_000,
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

    # ── Reference Card Builder (anti-hallucination) ──

    @classmethod
    def _build_ref_card(cls, kpis_list: list[dict]) -> str:
        """Pre-compute key metrics in human-readable format.

        Prevents LLM arithmetic hallucination by providing ready-to-use
        formatted values (e.g. converts 7632.5百万元→76.3亿).
        Detects currency from unit field and adds appropriate suffix (亿/亿美元/亿港元).
        """
        entry = kpis_list[-1] if kpis_list else {}
        refs = []
        for kpi in entry.get("kpis", []):
            field = kpi.get("field", "")
            val = kpi.get("value")
            unit = str(kpi.get("unit", "")).lower()
            if val is None:
                continue
            try:
                v = float(val)
            except (ValueError, TypeError):
                continue
            base = cls._UNIT_MULTIPLIER.get(unit, 1)
            yi = v * base / 100_000_000  # Convert to 亿 (100 million)

            # Detect currency for proper suffix
            currency_suffix = "亿"
            if any(kw in unit for kw in ("美元", "usd", "dollar")):
                currency_suffix = "亿美元"
            elif any(kw in unit for kw in ("港元", "hkd")):
                currency_suffix = "亿港元"

            if field in ("revenue", "net_profit", "gross_profit", "operating_profit", "total_assets", "total_equity"):
                refs.append(f"- {field}（{kpi.get('label', field)}）: **{yi:.1f}{currency_suffix}** (原始: {v} {unit})")
            elif field in ("gross_margin", "net_margin", "roe"):
                refs.append(f"- {field}（{kpi.get('label', field)}）: **{v:.1f}%**")
            elif field in ("eps",):
                refs.append(f"- {field}（{kpi.get('label', field)}）: **{v:.2f}** {unit}")
        if not refs:
            return ""
        header = "📋 参考数据卡（标题/正文引用财务数据请直接复制以下格式化数值，禁止自行换算）:\n"
        return header + "\n".join(refs) + "\n"

    # ── LLM Pass 2: Cross-Period Analysis ──

    def _analyze_multi_period(self, kpis_list: list[dict], code: str, current_period: str,
                               peer_text: str = "", credibility: str = "", risk_evolution: str = "",
                               valuation_text: str = "", tech_text: str = "") -> str | None:
        """LLM 对多期 KPI 做趋势分析和叙事生成"""
        prompt = self._load_prompt("analyze.md")
        if not prompt:
            return None

        # Format KPIs as text for LLM consumption
        kpi_text = self._format_kpis_for_llm(kpis_list)

        # Build context sections
        extra_sections = []
        if credibility:
            extra_sections.append(credibility)
        if risk_evolution:
            extra_sections.append(risk_evolution)
        if peer_text:
            extra_sections.append(peer_text)
        if valuation_text:
            extra_sections.append(valuation_text)
        if tech_text:
            extra_sections.append(tech_text)

        # ── Anti-hallucination: pre-compute formatted reference values ──
        ref_card = self._build_ref_card(kpis_list)
        if ref_card:
            extra_sections.append(ref_card)

        extra_block = "\n".join(extra_sections) if extra_sections else ""

        user_prompt = f"""## 股票: {code}
## 当前分析周期: {current_period}

{extra_block}
## 历史KPI数据:

{kpi_text}

---

请按上述框架生成完整分析报告。要求：
1. 先一句话核心观点定调
2. 每个模块包含数据表 + 分析解读
3. 区分一次性项目 vs 主营业务
4. 指出异常数据点
5. 风格参考海豚投研——有观点，用数据说话
6. ⚠️ 标题和正文中引用财务数据，**必须原样使用上方「📋 参考数据卡」的格式化数值**，禁止自行换算（例如 "76.3亿" 正确，"763亿" 错误）"""

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
        """调用 LLM（优先本地 client，fallback morning-brief）"""
        system_prompt = "你是一位资深财务分析师，擅长从财报中提取关键数据并撰写深度分析。用中文回复。"

        # ── Primary: local LLM client (self-contained) ──
        try:
            from deep_report._llm_client import (
                LLMProvider, call_with_fallback,
                DEEPSEEK_ENDPOINT, DEEPSEEK_MODEL,
                get_deepseek_api_key,
                get_ark_api_key, ARKCODE_ENDPOINT,
            )
            providers = []
            try:
                providers.append(LLMProvider(
                    endpoint=ARKCODE_ENDPOINT, model="deepseek-v4-pro",
                    api_key=get_deepseek_api_key(), label="DeepSeek",
                ))
            except Exception as e:
                logger.warning("DeepSeek init failed: %s", e)
            try:
                ark_key = get_ark_api_key()
                if ark_key:
                    providers.append(LLMProvider(
                        endpoint=ARKCODE_ENDPOINT, model="doubao-seed-2.0-pro",
                        api_key=ark_key, label="ARK",
                        extra={"thinking": {"type": "disabled"}},
                    ))
            except Exception:
                pass
            if providers:
                result = call_with_fallback(
                    providers=providers, system_prompt=system_prompt,
                    user_prompt=user_prompt, max_tokens=max_tokens,
                    temperature=0.3, timeout=120,
                )
                if result:
                    return result
        except ImportError:
            logger.debug("Local LLM client not available, trying morning-brief...")

        # ── Fallback: morning-brief ──
        try:
            import os as _os
            import sys as _sys
            _mb_path = _os.environ.get("MORNING_BRIEF_PATH", str(MORNING_BRIEF_PATH))
            _sys.path.insert(0, _mb_path)

            from src.utils.config import (
                get_ark_api_key as _mb_ark, get_deepseek_api_key as _mb_ds,
                LLM_ENDPOINT, LLM_MODEL, DEEPSEEK_ENDPOINT, DEEPSEEK_MODEL,
            )
            from src.utils.llm_client import LLMProvider as _MBProvider
            from src.utils.llm_client import call_with_fallback as _mb_call

            providers = []
            try:
                providers.append(_MBProvider(
                    endpoint=DEEPSEEK_ENDPOINT, model="deepseek-chat",
                    api_key=_mb_ds(), label="DeepSeek",
                ))
            except Exception:
                pass
            try:
                providers.append(_MBProvider(
                    endpoint=LLM_ENDPOINT, model="doubao-seed-2.0-pro",
                    api_key=_mb_ark(), label="ARK",
                    extra={"thinking": {"type": "disabled"}},
                ))
            except Exception:
                pass

            if not providers:
                logger.error("No LLM providers available")
                return None

            return _mb_call(
                providers=providers, system_prompt=system_prompt,
                user_prompt=user_prompt, max_tokens=max_tokens,
                temperature=0.3, timeout=120,
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
