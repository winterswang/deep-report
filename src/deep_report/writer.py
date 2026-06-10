"""ReportWriter — 将分析结果输出为飞书文档"""

from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger("deep_report.writer")


class ReportWriter:
    """报告输出器：生成飞书文档"""

    def write(self, code: str, period: str, result: dict) -> str | None:
        """
        生成飞书文档

        Args:
            code: 股票代码
            period: 报告周期
            result: analyzer.analyze() 的输出 {kpis, trends, narrative, validation}

        Returns:
            飞书文档 URL（通过 OpenClaw feishu_create_doc 工具）
        """
        narrative = result.get("narrative", "")
        if not narrative:
            logger.error("No narrative to write")
            return None

        title = self._build_title(code, period, result)
        markdown = self._build_markdown(title, code, period, narrative, result.get("validation", {}))

        # Save to local file first
        out_dir = Path("/tmp/deep_report")
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = title.replace(" ", "_").replace("/", "_")
        md_path = out_dir / f"{safe_name}.md"
        md_path.write_text(markdown, encoding="utf-8")
        logger.info("Markdown saved to %s", md_path)

        # Write a marker file so the calling OpenClaw agent can pick up
        # and push to feishu via the feishu_create_doc tool.
        marker = out_dir / f"{safe_name}.ready"
        marker.write_text(str(md_path), encoding="utf-8")
        logger.info("Marker written to %s — ready for feishu upload", marker)

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

> 📅 分析日期: {self._today()} | 📊 数据源: SEC EDGAR / HKEX / 交易所公告 | ⚙️ 引擎: deep-report v0.1

{narrative}

{verify_note}

---

📝 分析由 deep-report AI 生成 · 数据经 financial-sdk 交叉校验 · 不构成投资建议
"""

    @staticmethod
    def _today() -> str:
        from datetime import date
        return date.today().isoformat()
