"""ReportFetcher — 确保当前+历史财报报告本地可用"""

from __future__ import annotations
import logging
import os
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger("deep_report.fetcher")

# Downloads base dir (shared with unified-downloader)
DOWNLOADS_BASE = Path("/root/code/unified-downloader/downloads")

# Market detection (simple heuristic, refine later)
A_SH_RE = __import__("re").compile(r"^\d{6}\.(SZ|SH)$")
HK_RE = __import__("re").compile(r"^\d{4,5}\.HK$")


class ReportFetcher:
    """报告下载器，调度 unified-downloader CLI"""

    def __init__(self):
        self.downloads = DOWNLOADS_BASE

    def fetch(self, code: str, period: str, history: int = 4) -> list[dict]:
        """
        下载当前+历史报告，返回报告路径列表

        Args:
            code: 股票代码
            period: 报告周期 (如 2026Q1, 2025FY)
            history: 历史期数

        Returns:
            [{period, file_path, type, market}] 按时间倒序
        """
        # Parse period
        try:
            year = int(period[:4])
            freq = period[4:].upper()  # Q1, Q2, Q3, Q4, FY
        except (ValueError, IndexError):
            logger.error("无效的报告周期: %s", period)
            return []

        market = self._detect_market(code)
        logger.info("Fetching %s (%s) %s + %d historical", code, market, period, history)

        reports = []
        dl_type = self._map_dl_type(freq, market)

        # Download current period
        current_file = self._download(code, year, dl_type, market)
        if current_file:
            reports.append({
                "period": period,
                "file_path": current_file,
                "type": freq,
                "market": market,
            })

        # Download historical periods
        for offset in range(1, history + 1):
            hist_year, hist_freq = self._prev_period(year, freq, offset)
            hist_type = self._map_dl_type(hist_freq, market)
            hist_file = self._download(code, hist_year, hist_type, market)
            if hist_file:
                reports.append({
                    "period": f"{hist_year}{hist_freq}",
                    "file_path": hist_file,
                    "type": hist_freq,
                    "market": market,
                })

        logger.info("Fetched %d reports for %s", len(reports), code)
        return reports

    def _download(self, code: str, year: int, dl_type: str, market: str) -> str | None:
        """下载单份报告，返回文件路径"""

        # Check if already downloaded
        existing = self._find_existing(code, year, dl_type, market)
        if existing:
            logger.info("  %s %d %s → 已存在: %s", code, year, dl_type, existing)
            return existing

        # Build command
        # For US stocks, download both PDF and keep HTML originals when available.
        # HTML preserves table structure better than PDF.
        cmd = [
            "unified-downloader", "download", "single", code,
            "-t", dl_type,
            "-m", self._market_flag(market),
            "-y", str(year),
            "--no-cache",
        ]
        if market == "US":
            cmd.append("--pdf")

        logger.info("  Downloading: %s", " ".join(cmd[3:]))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                # Parse output for file path
                for line in result.stdout.split("\n"):
                    if "文件:" in line or "保存到:" in line or "file_path" in line:
                        pass
                    elif "downloads/" in line and (".pdf" in line or ".html" in line):
                        path = line.strip().split()[-1]
                        full = DOWNLOADS_BASE.parent / path
                        if full.exists():
                            logger.info("  → %s", full)
                            return str(full)
                # Fallback: find the downloaded file
                found = self._find_existing(code, year, dl_type, market)
                if found:
                    return found
                logger.warning("  Download appeared to succeed but can't find file")
            else:
                logger.warning("  Download failed: %s", result.stderr[:200])
                return None
        except subprocess.TimeoutExpired:
            logger.warning("  Download timeout for %s %d %s", code, year, dl_type)
            return None
        except Exception as e:
            logger.warning("  Download error: %s", e)
            return None

    def _find_existing(self, code: str, year: int, dl_type: str, market: str) -> str | None:
        """查找已下载的报告文件"""
        mdir = {"CN": "a", "HK": "h", "US": "m"}.get(market, "m")
        prefix = code.upper().replace(".", "")[:3]
        base = self.downloads / mdir / prefix

        if not base.exists():
            return None

        # For US stocks, the actual SEC form may differ from requested type
        # (e.g., FPI 10q→6K, 10k→20F). Search by year first, then filter.
        candidates = [f for f in base.iterdir()
                       if str(year) in f.name and f.suffix.lower() in (".pdf", ".html", ".htm")]
        if not candidates:
            return None

        # Prefer exact type match, then any match
        dl_type_clean = dl_type.replace("-", "").replace("_", "").upper()
        for f in candidates:
            if dl_type_clean in f.name.upper():
                return str(f)

        # FPI fallback: 10q→6K, 10k→20F
        fpi_map = {"10Q": "6K", "10K": "20F"}
        fpi_type = fpi_map.get(dl_type_clean, "")
        if fpi_type:
            for f in candidates:
                if fpi_type in f.name.upper():
                    return str(f)

        # Last resort: only if no candidates found OR dl_type is already fuzzy.
        # DO NOT return wrong form type — let downloader fetch the correct one.
        return None

    def _detect_market(self, code: str) -> str:
        """检测股票市场"""
        if A_SH_RE.match(code.upper()):
            return "CN"
        if HK_RE.match(code.upper()):
            return "HK"
        return "US"

    def _market_flag(self, market: str) -> str:
        return {"CN": "a", "HK": "h", "US": "m"}.get(market, "m")

    def _map_dl_type(self, freq: str, market: str) -> str:
        """映射报告类型到 unified-downloader 的 document type"""
        if market == "CN":
            return {
                "Q1": "q1_report", "Q2": "interim_report",
                "Q3": "q3_report", "Q4": "annual_report",
                "FY": "annual_report", "HY": "interim_report",
            }.get(freq, "annual_report")
        elif market == "HK":
            return {
                "Q1": "quarterly", "Q2": "interim_report",
                "Q3": "quarterly", "Q4": "annual_report",
                "FY": "annual_report", "HY": "interim_report",
            }.get(freq, "annual_report")
        else:  # US — unified-downloader handles FPI auto-detection
            return {
                "Q1": "10q", "Q2": "10q", "Q3": "10q",
                "Q4": "10k", "FY": "10k",
            }.get(freq, "10k")

    def _prev_period(self, year: int, freq: str, offset: int) -> tuple[int, str]:
        """计算 offset 个周期前的 (year, freq)"""
        period_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 4}
        current_idx = period_map.get(freq, 4)
        total_months = current_idx * 3 - offset * 3
        new_year = year
        while total_months <= 0:
            new_year -= 1
            total_months += 12
        new_q = (total_months - 1) // 3 + 1
        return new_year, f"Q{new_q}"
