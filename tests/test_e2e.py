"""E2E & integration tests for deep-report v3.6.

Test categories:
- Credibility table (guidance vs actual)
- Risk evolution (cross-period risk tracking)
- Peer data formatting
- Cross-market validation (CN, HK, US)
- Market detection
"""

from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

# Ensure src is on path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


# ── Test Fixtures ──

def _make_kpi_entry(period: str, **kwargs) -> dict:
    """Build a realistic KPI entry dict (simulates LLM extraction output)."""
    entry: dict = {
        "_period": period,
        "company_name": "Test Corp",
        "kpis": kwargs.pop("kpis", [
            {"field": "revenue", "value": 5690, "unit": "百万元", "yoy": "+28.5%"},
            {"field": "net_profit", "value": 550, "unit": "百万元", "yoy": "+15.0%"},
            {"field": "gross_margin", "value": 43.3, "unit": "%"},
        ]),
        "management_guidance": kwargs.pop("management_guidance", {
            "revenue_outlook": "预计下季度营收同比增长不低于25%",
            "margin_outlook": "毛利率因海外扩张预计小幅承压",
            "key_initiatives": ["海外门店加速扩张"],
            "risk_mentions": kwargs.pop("risk_mentions", ["汇率波动风险"]),
        }),
    }
    entry.update(kwargs)
    return entry


# ══════════════════════════════════════════════
# P2 Feature: Credibility Table
# ══════════════════════════════════════════════

class TestCredibilityTable:
    """_build_credibility_table() — guidance vs actual matching."""

    def test_empty_with_single_period(self):
        from deep_report.analyzer import ReportAnalyzer
        kpis = [_make_kpi_entry("2026Q1")]
        result = ReportAnalyzer._build_credibility_table(kpis)
        assert result == ""

    def test_empty_when_no_guidance(self):
        from deep_report.analyzer import ReportAnalyzer
        kpis = [
            _make_kpi_entry("2025Q4", management_guidance=None),
            _make_kpi_entry("2026Q1"),
        ]
        result = ReportAnalyzer._build_credibility_table(kpis)
        assert result == ""

    def test_builds_table_with_guidance_and_actual(self):
        from deep_report.analyzer import ReportAnalyzer
        kpis = [
            _make_kpi_entry("2025Q4", management_guidance={
                "revenue_outlook": "预计Q1营收增长25%",
                "risk_mentions": ["竞争加剧"],
            }),
            _make_kpi_entry("2026Q1", kpis=[
                {"field": "revenue", "value": 5690, "unit": "百万元", "yoy": "+28%"},
            ]),
        ]
        result = ReportAnalyzer._build_credibility_table(kpis)
        assert "管理层可信度追踪" in result
        assert "2025Q4" in result
        assert "2026Q1" in result
        assert "5690" in result and "百万元" in result
        assert "预计Q1营收增长25%" in result

    def test_multi_quarter_chain(self):
        from deep_report.analyzer import ReportAnalyzer
        kpis = [
            _make_kpi_entry("2025Q2", management_guidance={
                "revenue_outlook": "Q3增长约20%",
                "risk_mentions": ["宏观"],
            }),
            _make_kpi_entry("2025Q3", management_guidance={
                "revenue_outlook": "Q4增长约22%",
                "risk_mentions": ["宏观"],
            }),
            _make_kpi_entry("2025Q4", management_guidance={
                "revenue_outlook": "Q1增长约25%",
                "risk_mentions": ["竞争"],
            }),
            _make_kpi_entry("2026Q1"),
        ]
        result = ReportAnalyzer._build_credibility_table(kpis)
        assert result.count("|") >= 9  # At least 3 data rows
        assert "2025Q2" in result
        assert "2025Q3" in result
        assert "2025Q4" in result


# ══════════════════════════════════════════════
# P2 Feature: Risk Evolution Table
# ══════════════════════════════════════════════

class TestRiskEvolution:
    """_build_risk_evolution_table() — cross-period risk tracking."""

    def test_empty_with_single_period(self):
        from deep_report.analyzer import ReportAnalyzer
        kpis = [_make_kpi_entry("2026Q1", risk_mentions=["汇率"])]
        result = ReportAnalyzer._build_risk_evolution_table(kpis)
        assert result == ""

    def test_new_risk_detection(self):
        from deep_report.analyzer import ReportAnalyzer
        kpis = [
            _make_kpi_entry("2025Q4", risk_mentions=["汇率波动风险"]),
            _make_kpi_entry("2026Q1", risk_mentions=["汇率波动风险", "地缘政治风险"]),
        ]
        result = ReportAnalyzer._build_risk_evolution_table(kpis)
        assert "🆕 本期新出现风险" in result
        assert "地缘政治风险" in result
        # "汇率波动风险" should NOT be in the 🆕 section (it's recurring)
        new_section = result.split("🆕")[1].split("###")[0] if result.count("🆕") == 1 else ""
        assert "汇率波动风险" not in new_section

    def test_resolved_risk_detection(self):
        from deep_report.analyzer import ReportAnalyzer
        kpis = [
            _make_kpi_entry("2025Q3", risk_mentions=["汇率波动风险", "供应链风险"]),
            _make_kpi_entry("2025Q4", risk_mentions=["汇率波动风险"]),
        ]
        result = ReportAnalyzer._build_risk_evolution_table(kpis)
        assert "已消退风险" in result
        assert "供应链风险" in result

    def test_persistent_risk_detection(self):
        from deep_report.analyzer import ReportAnalyzer
        kpis = [
            _make_kpi_entry("2025Q1", risk_mentions=["汇率波动风险"]),
            _make_kpi_entry("2025Q2", risk_mentions=["汇率波动风险"]),
            _make_kpi_entry("2025Q3", risk_mentions=["汇率波动风险"]),
            _make_kpi_entry("2025Q4", risk_mentions=["汇率波动风险", "新风险"]),
        ]
        result = ReportAnalyzer._build_risk_evolution_table(kpis)
        assert "🔁 持续风险" in result
        assert "汇率波动风险" in result

    def test_risk_normalization(self):
        """Similar risk descriptions should be matched as same risk."""
        from deep_report.analyzer import ReportAnalyzer
        kpis = [
            _make_kpi_entry("2025Q3", risk_mentions=["地缘政治风险"]),
            _make_kpi_entry("2025Q4", risk_mentions=["地缘 政治 风险"]),
            _make_kpi_entry("2026Q1", risk_mentions=["地缘政治风险"]),
        ]
        result = ReportAnalyzer._build_risk_evolution_table(kpis)
        assert "持续风险" in result or "反复出现" in result
        # Should NOT have "新出现" for this risk
        new_section_idx = result.find("新出现风险")
        if new_section_idx != -1:
            assert "地缘" not in result[new_section_idx:new_section_idx + 500]


# ══════════════════════════════════════════════
# P2 Feature: Peer Data Formatting
# ══════════════════════════════════════════════

class TestPeerData:
    """_format_peer_data_for_llm() — peer comparison formatting."""

    def test_empty_peers(self):
        from deep_report.analyzer import ReportAnalyzer
        result = ReportAnalyzer._format_peer_data_for_llm([])
        assert result == ""

    def test_formats_single_peer(self):
        from deep_report.analyzer import ReportAnalyzer
        peers = [{
            "code": "9992.HK", "name": "泡泡玛特", "category": "IP零售/潮玩",
            "metrics": {
                "revenue": 8900, "net_profit": 1800,
                "revenue_growth_pct": 35.2, "gross_profit": 5500,
                "roe": None,
            },
        }]
        result = ReportAnalyzer._format_peer_data_for_llm(peers)
        assert "行业可比公司数据" in result
        assert "泡泡玛特" in result
        assert "9992.HK" in result
        assert "8,900" in result

    def test_formats_multiple_peers(self):
        from deep_report.analyzer import ReportAnalyzer
        peers = [
            {"code": "9992.HK", "name": "泡泡玛特", "category": "潮玩",
             "metrics": {"revenue": 8900, "net_profit": 1800,
                         "revenue_growth_pct": 35.2, "gross_profit": 5500, "roe": None}},
            {"code": "2020.HK", "name": "安踏体育", "category": "品牌零售",
             "metrics": {"revenue": 62000, "net_profit": 12000,
                         "revenue_growth_pct": 12.5, "gross_profit": 38000, "roe": None}},
        ]
        result = ReportAnalyzer._format_peer_data_for_llm(peers)
        assert result.count("|") >= 10  # Header + separator + 2 data rows

    def test_none_metrics_handled(self):
        from deep_report.analyzer import ReportAnalyzer
        peers = [{
            "code": "0000.HK", "name": "测试", "category": "测试",
            "metrics": {"revenue": None, "net_profit": None,
                        "revenue_growth_pct": None, "gross_profit": None, "roe": None},
        }]
        result = ReportAnalyzer._format_peer_data_for_llm(peers)
        assert result.count("N/A") >= 3  # Missing metrics show N/A


# ══════════════════════════════════════════════
# Pipeline Integration: Mocked LLM
# ══════════════════════════════════════════════

class TestPipelineIntegration:
    """Fetcher → Analyzer → Writer pipeline with mocked LLM."""

    def test_full_pipeline_with_rejected_validation(self, monkeypatch):
        """When validation rejects, pipeline should return diagnostic."""
        from deep_report.analyzer import ReportAnalyzer
        from deep_report.writer import ReportWriter

        # Mock _call_llm to avoid real API calls
        def mock_call_llm(self, prompt, max_tokens=4000):
            return json.dumps({
                "company_name": "名创优品",
                "kpis": [
                    {"field": "revenue", "value": 5690, "unit": "百万元"},
                    {"field": "net_profit", "value": 550, "unit": "百万元"},
                ],
            })

        monkeypatch.setattr(ReportAnalyzer, "_call_llm", mock_call_llm)

        # Mock _load_sdk_data to return mismatched values (trigger rejection)
        sdk_data = {
            "income_statement": {"revenue": {0: 1000000000}},  # 10亿 — far from LLM's 56.9亿
            "balance_sheet": {},
            "cash_flow": {},
        }

        analyzer = ReportAnalyzer(verify=True)
        monkeypatch.setattr(analyzer, "_load_sdk_data", lambda c: sdk_data)

        # Create minimal reports list (text only, no files needed for this test)
        reports = [{
            "period": "2026Q1",
            "file_path": "/nonexistent/test.txt",
            "market": "US",
        }]

        # Mock text extraction
        monkeypatch.setattr(analyzer, "_extract_text", lambda p, m: "TEST REPORT TEXT")

        result = analyzer.analyze("MNSO", "2026Q1", reports)
        assert result is not None
        assert result["_rejected"] is True
        assert result["narrative"] is None
        # Writer should handle rejection
        writer = ReportWriter()
        output = writer.write("MNSO", "2026Q1", result)
        assert output is not None
        assert Path(output).exists()
        content = Path(output).read_text()
        assert "校验拒绝" in content


# ══════════════════════════════════════════════
# Cross-Market: A-share + HK + US
# ══════════════════════════════════════════════

class TestCrossMarket:
    """Verify analyzer handles A-share, HK, and US stock codes correctly."""

    def test_mns_peer_map_exists(self):
        from deep_report.analyzer import ReportAnalyzer
        a = ReportAnalyzer()
        assert "9896.HK" in a._PEER_MAP
        assert "MNSO" in a._PEER_MAP
        assert len(a._PEER_MAP["9896.HK"]) >= 2

    def test_ashare_peer_map_exists(self):
        from deep_report.analyzer import ReportAnalyzer
        a = ReportAnalyzer()
        assert "600519.SH" in a._PEER_MAP
        peers = a._PEER_MAP["600519.SH"]
        assert len(peers) >= 2
        # All A-share peers should end with .SZ or .SH
        for code, _, _ in peers:
            assert code.endswith(".SZ") or code.endswith(".SH")

    def test_hk_fallback_peers(self):
        from deep_report.analyzer import ReportAnalyzer
        a = ReportAnalyzer()
        market = a._detect_market_for_peers("9988.HK")
        assert market == "HK"
        assert "HK" in a._PEER_FALLBACK

    def test_us_fallback_peers(self):
        from deep_report.analyzer import ReportAnalyzer
        a = ReportAnalyzer()
        market = a._detect_market_for_peers("AAPL")
        assert market == "US"
        assert "US" in a._PEER_FALLBACK

    def test_ashare_market_detect(self):
        from deep_report.analyzer import ReportAnalyzer
        a = ReportAnalyzer()
        assert a._detect_market_for_peers("600519.SH") == "A"
        assert a._detect_market_for_peers("000858.SZ") == "A"

    def test_unit_normalization(self):
        from deep_report.analyzer import ReportAnalyzer
        assert ReportAnalyzer._normalize_unit(100, "百万元") == 100_000_000
        assert ReportAnalyzer._normalize_unit(50, "亿元") == 5_000_000_000
        assert ReportAnalyzer._normalize_unit(1, "万元") == 10_000

    def test_field_standardization(self):
        from deep_report.analyzer import ReportAnalyzer
        kpis_list = [{
            "_period": "2026Q1",
            "kpis": [
                {"field": "营业收入", "value": 100},
                {"field": "营收", "value": 200},
                {"field": "毛利率", "value": 45},
            ],
        }]
        a = ReportAnalyzer(verify=False)
        result = a._standardize_fields(kpis_list)
        fields = {k["field"] for k in result[0]["kpis"]}
        # "营业收入" and "营收" should both map to "revenue"
        assert fields == {"revenue", "gross_margin"}

    def test_table_to_markdown(self):
        from deep_report.analyzer import ReportAnalyzer
        rows = [
            ["Name", "Value"],
            ["Revenue", "1000"],
            ["Profit", "200"],
        ]
        result = ReportAnalyzer._table_to_markdown(rows)
        assert "| Name | Value |" in result
        assert "| Revenue | 1000 |" in result


# ══════════════════════════════════════════════
# Market Detection
# ══════════════════════════════════════════════

class TestMarketDetection:
    """_detect_market and related helpers."""

    def test_detect_a_share_sh(self):
        from deep_report.fetcher import ReportFetcher
        fetcher = ReportFetcher()
        assert fetcher._detect_market("600519.SH") == "CN"

    def test_detect_a_share_sz(self):
        from deep_report.fetcher import ReportFetcher
        fetcher = ReportFetcher()
        assert fetcher._detect_market("000858.SZ") == "CN"

    def test_detect_hk(self):
        from deep_report.fetcher import ReportFetcher
        fetcher = ReportFetcher()
        assert fetcher._detect_market("0700.HK") == "HK"
        assert fetcher._detect_market("9988.HK") == "HK"

    def test_detect_us(self):
        from deep_report.fetcher import ReportFetcher
        fetcher = ReportFetcher()
        assert fetcher._detect_market("AAPL") == "US"
        assert fetcher._detect_market("MNSO") == "US"

    def test_period_rollback(self):
        from deep_report.fetcher import ReportFetcher
        fetcher = ReportFetcher()
        # Q1 minus 1 quarter → previous Q4
        y, f = fetcher._prev_period(2026, "Q1", 1)
        assert y == 2025
        assert f == "Q4"
        # Q1 minus 4 quarters → previous Q1
        y, f = fetcher._prev_period(2026, "Q1", 4)
        assert y == 2025
        assert f == "Q1"
        # FY minus 1 → previous year
        y, f = fetcher._prev_period(2025, "FY", 1)
        assert y == 2024
        assert f == "FY"
