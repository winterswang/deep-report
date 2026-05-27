"""CLI command handlers"""

from __future__ import annotations
import logging

from deep_report.fetcher import ReportFetcher
from deep_report.analyzer import ReportAnalyzer
from deep_report.writer import ReportWriter

logger = logging.getLogger("deep_report")


def cmd_analyze(args):
    """执行 analyze 命令"""
    logger.info("deep-report analyze %s --period %s", args.code, args.period)

    # 1. Fetch reports
    fetcher = ReportFetcher()
    reports = fetcher.fetch(args.code, args.period, history=args.history)
    if not reports:
        logger.error("无法获取任何报告，退出")
        return

    logger.info("已获取 %d 份报告", len(reports))

    if args.dry_run:
        logger.info("Dry run — 报告已下载，跳过分析和输出")
        for r in reports:
            print(f"  {r['period']} → {r['file_path']}")
        return

    # 2. Analyze
    analyzer = ReportAnalyzer(verify=not args.no_verify)
    result = analyzer.analyze(args.code, args.period, reports)

    if not result:
        logger.error("分析失败")
        return

    # 3. Write
    writer = ReportWriter()
    doc_url = writer.write(args.code, args.period, result)

    logger.info("报告已生成: %s", doc_url)
