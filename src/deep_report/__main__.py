"""deep-report CLI — 财报深度分析引擎"""

from __future__ import annotations
import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("deep_report")


def main():
    parser = argparse.ArgumentParser(prog="deep-report", description="财报深度分析引擎")
    subparsers = parser.add_subparsers(dest="command")

    analyze = subparsers.add_parser("analyze", help="分析指定公司的季度/年度报告")
    analyze.add_argument("code", help="股票代码 (如 MNSO, 600519.SH)")
    analyze.add_argument("--period", required=True, help="报告周期 (如 2026Q1, 2025FY)")
    analyze.add_argument("--history", type=int, default=4, help="分析的历史期数 (默认4)")
    analyze.add_argument("--no-verify", action="store_true", help="跳过 financial-sdk 校验")
    analyze.add_argument("--dry-run", action="store_true", help="仅下载和提取，不生成报告")

    args = parser.parse_args()
    if args.command == "analyze":
        from deep_report.cli import cmd_analyze
        cmd_analyze(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
