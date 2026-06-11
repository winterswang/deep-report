"""ReportWriter — 将分析结果输出为飞书文档"""

from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger("deep_report.writer")


class ReportWriter:
    """报告输出器：生成飞书文档"""

    def write(self, code: str, period: str, result: dict) -> str | None:
        """
        生成报告文档

        Args:
            code: 股票代码
            period: 报告周期
            result: analyzer.analyze() 的输出 {kpis, trends, narrative, validation, _rejected}

        Returns:
            文件路径（飞书上传由调用方 OpenClaw agent 处理）
        """
        narrative = result.get("narrative", "")
        rejected = result.get("_rejected", False)
        validation = result.get("validation", {})

        title = self._build_title(code, period, result)

        if rejected or not narrative:
            # Build diagnostic-only output
            markdown = self._build_diagnostic(title, code, period, validation)
        else:
            markdown = self._build_markdown(title, code, period, narrative, validation)

        # Save to local file
        out_dir = Path("/tmp/deep_report")
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = title.replace(" ", "_").replace("/", "_")
        md_path = out_dir / f"{safe_name}.md"
        md_path.write_text(markdown, encoding="utf-8")
        logger.info("Markdown saved to %s", md_path)

        # Write marker file for feishu upload
        marker = out_dir / f"{safe_name}.ready"
        marker.write_text(str(md_path), encoding="utf-8")
        logger.info("Marker written to %s", marker)

        return str(md_path)

    def _build_title(self, code: str, period: str, result: dict) -> str:
        kpis = result.get("kpis", [])
        company_name = code
        for entry in kpis:
            if isinstance(entry, dict):
                name = entry.get("company_name", "")
                if name and name != code and len(name) > 2:
                    company_name = name
                    break

        freq_label = "年报" if period.endswith("FY") else "季报"
        return f"{company_name} {period} 财报深度分析"

    def _build_markdown(
        self, title: str, code: str, period: str,
        narrative: str, validation: dict,
    ) -> str:
        warnings = validation.get("warnings", [])
        verify_note = ""
        if warnings:
            verify_note = "\n\n---\n## 📎 数据校验\n"
            for w in warnings:
                verify_note += f"- {w.get('source', '?')}: {w.get('status', '?')}\n"

        return f"""# {title}

> 📅 分析日期: {self._today()} | 📊 数据源: SEC EDGAR / HKEX / 交易所公告 | ⚙️ 引擎: deep-report v0.2

{narrative}

{verify_note}

---

📝 分析由 deep-report AI 生成 · 数据经 financial-sdk 交叉校验 · 不构成投资建议
"""

    def _build_diagnostic(
        self, title: str, code: str, period: str,
        validation: dict,
    ) -> str:
        """Build diagnostic output when validation rejects or no narrative."""
        checks = validation.get("checks", [])
        summary = validation.get("summary", {})

        # Build comparison table
        rows = []
        for c in checks:
            llm_v = f"{c['llm_value']:,.0f}" if c['llm_value'] else "N/A"
            sdk_v = f"{c['sdk_value']:,.0f}" if c['sdk_value'] else "N/A"
            dev = f"{c['deviation_pct']:.0f}%" if c.get('deviation_pct') else "N/A"
            flag = "⚠️ 符号反转" if c.get('sign_flip') else ("🔴" if c.get('status') == 'reject' else "🟡" if c.get('status') == 'warn' else "✅")
            rows.append(f"| {c['field']} | {llm_v} | {sdk_v} | {dev} | {flag} |")

        table = "\n".join(rows) if rows else "| — | — | — | — | — |"

        return f"""# ⚠️ {title} — 数据校验未通过

> 📅 分析日期: {self._today()} | ⚙️ 引擎: deep-report v0.1 | 🔴 状态: 校验拒绝

## 校验摘要

| 指标 | 值 |
|------|-----|
| 状态 | **{validation.get('status', '?').upper()}** — {validation.get('reason', '?')} |
| 校验字段数 | {summary.get('compared_fields', 0)} |
| 平均偏差 | {summary.get('avg_deviation_pct', 0):.1f}% |
| 符号错误 | {summary.get('sign_errors', 0)} |
| 拒绝字段 | {summary.get('reject_count', 0)} |

## LLM 提取值 vs financial-sdk 基准值

| 字段 | LLM 提取 | SDK 基准 | 偏差 | 状态 |
|------|----------|----------|------|------|
{table}

## 诊断

LLM 从财报原文中提取的 KPI 与 financial-sdk 的结构化数据偏差过大（>50% 和/或符号反转），**叙事报告未生成**以避免基于错误数据的分析。

### 可能原因
- LLM 提取了错误报表段（母公司 vs 合并，或不同期间）
- 单位换算错误（千元 vs 亿元）
- 截断窗口丢失关键上下文

### 建议
- 尝试用 `--no-verify` 跳过校验直接生成报告（风险自负）
- 检查提取 prompt 是否需要针对该报告格式优化
- 手动 review 被提取的原文片段

---

📝 分析由 deep-report AI 生成 · 数据经 financial-sdk 交叉校验 · 不构成投资建议
"""

    @staticmethod
    def _today() -> str:
        from datetime import date
        return date.today().isoformat()
