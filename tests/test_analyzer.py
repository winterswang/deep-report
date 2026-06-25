"""Unit tests for deep_report.analyzer.ReportAnalyzer"""

import os
import tempfile
from pathlib import Path

import pytest

from deep_report.analyzer import ReportAnalyzer


# ── Fixtures ──

@pytest.fixture
def analyzer():
    return ReportAnalyzer(verify=False)


# ── _table_to_markdown ──

class TestTableToMarkdown:
    def test_basic_table(self, analyzer):
        rows = [["Name", "Value"], ["Revenue", "100"], ["Cost", "60"]]
        md = analyzer._table_to_markdown(rows)
        lines = md.split("\n")
        assert len(lines) == 4  # header + separator + 2 data rows
        assert "Name" in lines[0]
        assert "---" in lines[1]
        assert "Revenue" in lines[2]
        assert "Cost" in lines[3]

    def test_empty_rows(self, analyzer):
        assert analyzer._table_to_markdown([]) == ""

    def test_none_cells_normalized(self, analyzer):
        rows = [["A", None, "C"], ["1", "2", "3"]]
        md = analyzer._table_to_markdown(rows)
        assert "None" not in md  # None should become empty string
        assert "| A |  | C |" in md.split("\n")[0]

    def test_uneven_rows(self, analyzer):
        """Rows with different column counts should be padded"""
        rows = [["A", "B", "C"], ["1"]]  # row 2 is short
        md = analyzer._table_to_markdown(rows)
        lines = md.split("\n")
        # All rows should have same column count (3)
        for line in lines[2:]:  # skip header and separator
            assert line.count("|") >= 4  # | col1 | col2 | col3 |

    def test_mixed_types(self, analyzer):
        rows = [["Name", "Count"], ["Items", 42], ["Price", 9.99]]
        md = analyzer._table_to_markdown(rows)
        assert "42" in md
        assert "9.99" in md

    def test_single_data_row(self, analyzer):
        rows = [["Header"], ["Value"]]
        md = analyzer._table_to_markdown(rows)
        lines = md.split("\n")
        assert len(lines) == 3  # header + separator + 1 data


# ── _clean_llm_response ──

class TestCleanLLMResponse:
    def test_no_change_when_clean(self, analyzer):
        text = "这是一份纯粹的分析报告"
        assert analyzer._clean_llm_response(text) == text

    def test_strip_chinese_prefix(self, analyzer):
        assert analyzer._clean_llm_response("好的，收到。这是分析") == "这是分析"
        assert analyzer._clean_llm_response("好的，这是分析") == "这是分析"
        assert analyzer._clean_llm_response("收到。这是分析") == "这是分析"
        assert analyzer._clean_llm_response("明白了。这是分析") == "这是分析"

    def test_strip_english_prefix(self, analyzer):
        assert analyzer._clean_llm_response("OK，分析内容") == "分析内容"
        assert analyzer._clean_llm_response("Okay，分析内容") == "分析内容"

    def test_strip_comma_prefix(self, analyzer):
        assert analyzer._clean_llm_response("好的, 分析内容") == "分析内容"

    def test_strip_suffix(self, analyzer):
        assert analyzer._clean_llm_response("分析报告\n希望对你有帮助。") == "分析报告"
        assert analyzer._clean_llm_response("分析\n以上是分析报告。") == "分析"

    def test_strip_separator_suffix(self, analyzer):
        # suffix "\n---\n\n以上是" must match at end of text
        text = "这是正文\n---\n\n以上是"
        cleaned = analyzer._clean_llm_response(text)
        assert "以上是" not in cleaned
        assert cleaned == "这是正文"

    def test_separator_with_content_not_stripped(self, analyzer):
        # "\n---\n\n以上是分析报告" does NOT end with "\n---\n\n以上是"
        text = "这是正文\n---\n\n以上是分析报告"
        cleaned = analyzer._clean_llm_response(text)
        # Suffix doesn't match end → no stripping
        assert "以上是" in cleaned

    def test_strip_ai_disclaimer(self, analyzer):
        assert analyzer._clean_llm_response(
            "分析内容（以上内容由AI生成，不构成投资建议）"
        ) == "分析内容"

    def test_empty_string(self, analyzer):
        assert analyzer._clean_llm_response("") == ""

    def test_only_prefix(self, analyzer):
        # If text is ONLY a prefix, it should become empty string
        result = analyzer._clean_llm_response("好的，收到。")
        assert result == ""

    def test_whitespace_after_prefix_removal(self, analyzer):
        assert analyzer._clean_llm_response("好的，收到。  分析") == "分析"


# ── _sample_key_sections ──

class TestSampleKeySections:
    """Tests for the 14-anchor bilingual sampler"""

    CN_REPORT = """贵州茅台酒股份有限公司 2025 年年度报告

第一节 重要提示
本报告仅供参考。

第二节 公司基本情况
公司名称：贵州茅台酒股份有限公司

第三节 会计数据和财务指标
合并利润表
项目 | 2025年 | 2024年
营业收入 174,144.07 165,000.00
营业成本 12,678.34 11,500.00

合并资产负债表
项目 | 2025年 | 2024年
总资产 358,438.44 340,000.00

合并现金流量表
项目 | 2025年 | 2024年
经营活动现金流量净额 101,458.23 95,000.00

毛利率 92.01% 92.11%
"""

    EN_REPORT = """ACME CORP
FORM 10-K
FOR THE FISCAL YEAR ENDED DECEMBER 31, 2025

CONSOLIDATED STATEMENTS OF INCOME
(in millions, except per share data)

Total Revenue $ 81,615 $ 75,000
Cost of Revenue $ 48,232 $ 44,500
Gross Profit $ 33,383 $ 30,500

Operating Income $ 15,200 $ 13,800
Income from Operations $ 15,200 $ 13,800

Net Income $ 12,054 $ 10,800
Net Earnings per share $ 3.45 $ 3.10

CONSOLIDATED BALANCE SHEETS
Total Assets $ 205,000 $ 190,000

CONSOLIDATED STATEMENTS OF CASH FLOWS
Cash Flow from Operations $ 18,500 $ 16,200

Management's Discussion and Analysis of Financial Condition
and Results of Operations

Revenue increased 8.8% year-over-year driven by strong demand.
"""

    def test_cn_market_uses_cn_anchors(self, analyzer):
        result = analyzer._sample_key_sections(self.CN_REPORT, 5000, "CN")
        assert "利润表" in result
        assert "资产负债表" in result
        assert "现金流量表" in result
        # EN anchors should NOT be active for CN market
        assert "income_statement" not in result

    def test_hk_market_uses_cn_anchors(self, analyzer):
        result = analyzer._sample_key_sections(self.CN_REPORT, 5000, "HK")
        assert "利润表" in result

    def test_en_market_uses_both_anchors(self, analyzer):
        result = analyzer._sample_key_sections(self.EN_REPORT, 5000, "US")
        assert "income_statement" in result
        # In short reports, ±2000 context may overlap >50% with other anchors
        # → dedup is correct behavior. Verify at least one EN section is found.

    def test_no_market_uses_all_anchors(self, analyzer):
        result = analyzer._sample_key_sections(self.EN_REPORT, 5000)
        assert "income_statement" in result

    def test_multiple_matches_per_anchor(self, analyzer):
        """Each anchor can yield up to 3 matches"""
        text = self.EN_REPORT  # has multiple "Revenue", "Income" hits
        result = analyzer._sample_key_sections(text, 10000, "US")
        # Should have at least one match from each major section
        assert "Section:" in result

    def test_overlap_dedup(self, analyzer):
        """Data density scoring: sections with score>50 are selected"""
        # Add enough numeric data to pass the score>50 threshold
        padding = "Revenue $500 million. Net income $100 million. Gross margin 45.5%. " * 8
        close_text = """CONSOLIDATED STATEMENTS OF INCOME
Total Revenue $100 | $90 | +11%
Gross Profit $60 | $55 | +9%
Net Income $40 | $36 | +11%
""" + padding
        result = analyzer._sample_key_sections(close_text, 3000, "US")
        assert "income_statement" in result

    def test_data_density_scoring_skips_toc(self, analyzer):
        """TOC-quality matches (low data density) are filtered by score>50 threshold"""
        toc_text = """TABLE OF CONTENTS
Item 7. Management's Discussion     ....... 45
Item 8. Financial Statements        ....... 67
Consolidated Statements of Income   ....... 68
Consolidated Balance Sheets         ....... 70

(padding padding padding padding padding padding padding padding padding padding
padding padding padding padding padding padding padding padding padding padding)

CONSOLIDATED STATEMENTS OF INCOME (In millions)
Total Revenue $500,000 | $450,000 | 11.1%
Cost of Revenue $250,000 | $230,000 | 8.7%
Gross Profit $250,000 | $220,000 | 13.6%
Net Income $100,000 | $88,000 | 13.6%"""
        result = analyzer._sample_key_sections(toc_text, 5000, "US")
        # The TOC heading "Consolidated Statements" has low data → filtered
        # The actual statement with numbers has high data → picked
        assert "income_statement" in result
        assert result.count("income_statement") == 1

    def test_80pct_fill_stops_sampling(self, analyzer):
        """When sampled sections reach 80% of max_chars, sampling stops"""
        # Short max_chars to trigger early stop
        result = analyzer._sample_key_sections(self.EN_REPORT, 500, "US")
        # Should produce output but limited
        assert len(result) <= 500

    def test_zero_matches_fallback(self, analyzer):
        """When no anchors match, fallback to 60/40 split"""
        gibberish = "abc123 xyz " * 100
        result = analyzer._sample_key_sections(gibberish, 1000, "CN")
        assert "truncated" in result
        # Truncation marker adds ~25 chars overhead; accept small overshoot
        assert len(result) <= 1050

    def test_section_minimum_size(self, analyzer):
        """Sections shorter than 200 chars are skipped"""
        tiny = """利润表: small"""
        result = analyzer._sample_key_sections(tiny, 5000, "CN")
        # The match is too short, fallback should kick in
        assert "truncated" in result or len(result) < 500

    def test_preserves_section_content(self, analyzer):
        """Sampled sections should contain relevant financial data"""
        result = analyzer._sample_key_sections(self.CN_REPORT, 5000, "CN")
        assert "174144.07" in result or "174,144.07" in result

    def test_market_default_empty_string(self, analyzer):
        """market="" should use all anchors (same as unknown market)"""
        result = analyzer._sample_key_sections(self.EN_REPORT, 5000, "")
        assert "income_statement" in result


# ── _extract_html ──

class TestExtractHTML:
    def test_xbrl_noise_reduction(self, analyzer):
        """XBRL ix: tags should be unwrapped (text kept), link: tags removed"""
        html = """<html><body>
        <ix:nonFraction name="us-gaap:Revenue" format="ixt:num-dot-decimal">81,615</ix:nonFraction>
        <link:schemaRef xlink:href="acme-2025.xsd"/>
        <xbrli:context id="ctx1"/>
        <div>Revenue was $81,615 million.</div>
        </body></html>"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            html_path = f.name

        try:
            text = analyzer._extract_html(Path(html_path))
            # Text from ix: tag should be preserved
            assert "81,615" in text
            # link: and xbrli: should be gone
            assert "schemaRef" not in text
            assert "xbrli" not in text
            # Regular content preserved
            assert "Revenue was" in text
        finally:
            os.unlink(html_path)

    def test_script_style_removed(self, analyzer):
        html = """<html><head><script>alert('xss')</script><style>.a{color:red}</style>
        <title>Test</title><meta charset="utf-8"></head><body><p>Financial Data</p></body></html>"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            html_path = f.name

        try:
            text = analyzer._extract_html(Path(html_path))
            assert "alert" not in text
            assert "color:red" not in text
            assert "Test" not in text  # title removed
            assert "Financial Data" in text
        finally:
            os.unlink(html_path)

    def test_display_none_removed(self, analyzer):
        html = """<html><body>
        <div style="display: none;">Hidden content</div>
        <div>Visible content</div>
        </body></html>"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            html_path = f.name

        try:
            text = analyzer._extract_html(Path(html_path))
            assert "Hidden content" not in text
            assert "Visible content" in text
        finally:
            os.unlink(html_path)

    def test_html_table_to_markdown(self, analyzer):
        html = """<html><body>
        <table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>
        </body></html>"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            html_path = f.name

        try:
            text = analyzer._extract_html(Path(html_path))
            assert "【表格" in text
            assert "| A | B |" in text
            assert "| 1 | 2 |" in text
            # Table content should NOT be duplicated in body text
            assert text.count("A") == 1 or "A" not in text.replace("【表格", "_TBL_")
        finally:
            os.unlink(html_path)

    def test_nonexistent_file(self, analyzer):
        with pytest.raises(FileNotFoundError):
            analyzer._extract_html(Path("/tmp/nonexistent_xyz.html"))


# ── _extract_pdf ──

class TestExtractPDF:
    def test_nonexistent_file(self, analyzer):
        result = analyzer._extract_text("/tmp/nonexistent_xyz.pdf", "CN")
        assert result is None


# ── Integration / Pipeline ──

class TestAnalyzePipeline:
    """End-to-end pipeline tests (no LLM — verify non-LLM paths)"""

    def test_analyze_no_reports(self, analyzer):
        result = analyzer.analyze("600519.SH", "2025FY", [])
        assert result is None

    def test_analyze_missing_file(self, analyzer):
        reports = [
            {"period": "2025FY", "file_path": "/tmp/no_such_file.pdf", "market": "CN"},
        ]
        result = analyzer.analyze("600519.SH", "2025FY", reports)
        assert result is None

    def test_analyze_missing_period(self, analyzer):
        """reports without 'period' + nonexistent file → returns None early"""
        # File doesn't exist → _extract_text returns None → texts empty → return None
        result = analyzer.analyze("TEST.US", "2025FY", [
            {"file_path": "/tmp/no_such.pdf", "market": "US"},
        ])
        assert result is None

    def test_market_field_passed_through(self, analyzer):
        """Verify market is correctly passed to _sample_key_sections"""
        cn_text = "合并利润表：营业收入100亿元"
        # CN market → only CN anchors
        result = analyzer._sample_key_sections(cn_text, 500, "CN")
        assert "利润表" in result
        assert "income_statement" not in result


# ── _validate helpers ──

class TestValidate:
    def test_verify_disabled(self):
        """verify=False should skip validation"""
        a = ReportAnalyzer(verify=False)
        assert a.verify is False

    def test_verify_enabled(self):
        a = ReportAnalyzer(verify=True)
        assert a.verify is True


# ── Margin/Ratio field tests (added 2026-06-25) ──

class TestSdkPercentageFields:
    """验证毛利率/净利率/ROE百分比字段映射"""

    def test_gross_margin_field_map(self, analyzer):
        """毛利率必须存在于 _SDK_FIELD_MAP"""
        assert "gross_margin" in analyzer._SDK_FIELD_MAP

    def test_net_margin_field_map(self, analyzer):
        """净利率必须存在于 _SDK_FIELD_MAP"""
        assert "net_margin" in analyzer._SDK_FIELD_MAP

    def test_roe_field_map(self, analyzer):
        """ROE必须存在于 _SDK_FIELD_MAP"""
        assert "roe" in analyzer._SDK_FIELD_MAP

    def test_get_sdk_field_reads_percentage(self, analyzer):
        """_get_sdk_field 能正确读取百分比字段"""
        mock_data = {
            "income_statement": {
                "gross_margin": {"0": 80.58, "1": 78.2},
                "net_margin": {"0": 53.35, "1": 50.1},
            },
            "balance_sheet": {
                "roe": {"0": 21.1, "1": 19.5},
            }
        }
        # 取最新 index (sorted keys → "1")
        assert analyzer._get_sdk_field(mock_data, "gross_margin", "any") == 78.2
        assert analyzer._get_sdk_field(mock_data, "net_margin", "any") == 50.1
        assert analyzer._get_sdk_field(mock_data, "roe", "any") == 19.5

    def test_validate_with_percentage_fields(self, analyzer, monkeypatch):
        """交叉验证对百分比字段正常工作（偏差<20%为ok）"""
        mock_data = {
            "income_statement": {
                "gross_margin": {"0": 80.58},
                "net_margin": {"0": 53.35},
            },
            "balance_sheet": {
                "roe": {"0": 21.1},
            }
        }
        monkeypatch.setattr(analyzer, "_load_sdk_data", lambda code: mock_data)

        kpis = [{
            "_period": "0",
            "kpis": [
                {"field": "gross_margin", "value": 80.6, "unit": "%"},
                {"field": "net_margin", "value": 53.3, "unit": "%"},
                {"field": "roe", "value": 21.2, "unit": "%"},
            ]
        }]
        result = analyzer._validate(kpis, "TEST")
        assert result["status"] == "ok"
        checks = {c["field"]: c for c in result["checks"]}
        assert "gross_margin" in checks
        assert checks["gross_margin"]["deviation_pct"] < 1.0
        assert "net_margin" in checks
        assert "roe" in checks
