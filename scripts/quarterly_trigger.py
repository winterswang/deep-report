#!/usr/bin/env python3
"""deep-report 季报自动化触发器 v2

基于 WeStock Data 检测自选股新财报，自动触发 deep-report 分析。

数据源: WeStock Data (westock-data-skillhub)
  - A股: reserve (财报披露日) + finance (财务报表)
  - 港股/美股: finance 检测最新报告期变化

状态追踪: ~/.hermes/data/deep_report_state.json
"""

from __future__ import annotations
import json
import logging
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deep_report.trigger")

# ── Config ──
STATE_FILE = Path.home() / ".hermes" / "data" / "deep_report_state.json"
DEEP_REPORT_DIR = Path.home() / "github" / "deep-report"
WATCHLIST_FILE = DEEP_REPORT_DIR / "config" / "watchlist.json"
PYTHON = "/usr/bin/python3"
SCAN_DAYS_AFTER_REPORT = 90  # 报告日之后多少天内触发


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_analyzed": {}, "last_scan": None}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def get_watchlist() -> list[dict]:
    if WATCHLIST_FILE.exists():
        data = json.loads(WATCHLIST_FILE.read_text())
        return data.get("stocks", [])
    logger.warning("Watchlist config not found at %s", WATCHLIST_FILE)
    return []


def period_label_from_date(report_date: str) -> str:
    """报告日期 → 周期标签"""
    try:
        d = date.fromisoformat(report_date[:10])
        return f"{d.year}FY"
    except ValueError:
        return "unknown"


def trigger_analysis(code: str, period: str, market: str) -> bool:
    """触发 deep-report 分析"""
    cmd = [PYTHON, "-m", "deep_report", "analyze", code, "--period", period]
    logger.info("Triggering: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, cwd=str(DEEP_REPORT_DIR), capture_output=True, text=True,
            timeout=600,
            env={**os.environ, "PYTHONPATH": f"{DEEP_REPORT_DIR}/src"},
        )
        if result.returncode == 0:
            logger.info("Analysis OK for %s %s", code, period)
            return True
        else:
            logger.warning("Analysis FAILED for %s %s: %s", code, period, result.stderr[:200])
            return False
    except subprocess.TimeoutExpired:
        logger.warning("Analysis TIMEOUT for %s %s", code, period)
        return False
    except Exception as e:
        logger.error("Analysis ERROR for %s %s: %s", code, period, e)
        return False


def main():
    logger.info("=== deep-report quarterly trigger v2 (WeStock Data) ===")

    state = load_state()
    watchlist = get_watchlist()
    logger.info("Watchlist: %d stocks", len(watchlist))

    # Import provider
    sys.path.insert(0, str(DEEP_REPORT_DIR / "src"))
    from deep_report.westock_provider import (
        fetch_disclosure_dates, fetch_latest_period,
    )

    triggered = 0
    failed = 0
    today = date.today()

    for stock in watchlist:
        code = stock["stock_code"]
        name = stock.get("stock_name", code)
        market = stock.get("market", "A")

        # ── Strategy 1: Check disclosure dates (A-share only) ──
        if market == "A":
            disclosures = fetch_disclosure_dates(code, market)
            for d in disclosures:
                disc_date = d.get("disclosure_date", "")
                report_period = d.get("report_period", "")
                if not disc_date or not report_period:
                    continue
                try:
                    d_date = date.fromisoformat(disc_date[:10])
                except ValueError:
                    continue

                # Trigger if disclosure is within scan window
                days_after = (today - d_date).days
                if 0 <= days_after <= 7:  # Within a week of disclosure
                    period_label = period_label_from_date(report_period)
                    key = f"{code}:{period_label}"
                    if key in state["last_analyzed"]:
                        continue
                    logger.info("New A-share disclosure: %s %s → %s", code, name, disc_date)
                    ok = trigger_analysis(code, period_label, market)
                    if ok:
                        state["last_analyzed"][key] = today.isoformat()
                        save_state(state)
                        triggered += 1
                    else:
                        failed += 1

        # ── Strategy 2: Detect by latest report period change (all markets) ──
        latest_date = fetch_latest_period(code, market)
        if latest_date:
            period_label = period_label_from_date(latest_date)
            key = f"{code}:{period_label}"
            if key in state["last_analyzed"]:
                continue

            # Check if report is recent enough
            try:
                d = date.fromisoformat(latest_date[:10])
                if (today - d).days > SCAN_DAYS_AFTER_REPORT:
                    continue
            except ValueError:
                continue

            logger.info("New %s report: %s %s → %s", market, code, name, period_label)
            ok = trigger_analysis(code, period_label, market)
            if ok:
                state["last_analyzed"][key] = today.isoformat()
                save_state(state)
                triggered += 1
            else:
                failed += 1

    state["last_scan"] = today.isoformat()
    save_state(state)

    logger.info("Done: %d triggered, %d failed", triggered, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
