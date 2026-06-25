"""deep-report configuration — centralized paths and settings.

Override any path via environment variable.
"""

from __future__ import annotations
import os
from pathlib import Path

# ── Project root ──
PROJECT_ROOT = Path(os.environ.get(
    "DEEP_REPORT_ROOT",
    str(Path(__file__).resolve().parent.parent.parent)
))

# ── External dependencies ──
FINANCIAL_SDK_PATH = Path(os.environ.get(
    "FINANCIAL_SDK_PATH",
    str(Path.home() / "github" / "financial-sdk")
))

MORNING_BRIEF_PATH = Path(os.environ.get(
    "MORNING_BRIEF_PATH",
    str(Path.home() / "github" / "morning-brief")
))

UNIFIED_DOWNLOADER_DOWNLOADS = Path(os.environ.get(
    "UNIFIED_DOWNLOADER_DOWNLOADS",
    str(PROJECT_ROOT.parent / "unified-downloader" / "downloads")
))

# ── Output ──
OUTPUT_DIR = Path(os.environ.get(
    "DEEP_REPORT_OUTPUT_DIR",
    "/tmp/deep_report"
))

# ── LLM ──
LLM_TIMEOUT = int(os.environ.get("DEEP_REPORT_LLM_TIMEOUT", "120"))
LLM_MAX_TOKENS_EXTRACT = int(os.environ.get("DEEP_REPORT_LLM_MAX_TOKENS_EXTRACT", "8000"))
LLM_MAX_TOKENS_ANALYZE = int(os.environ.get("DEEP_REPORT_LLM_MAX_TOKENS_ANALYZE", "8000"))
LLM_TEMPERATURE = float(os.environ.get("DEEP_REPORT_LLM_TEMPERATURE", "0.3"))

# ── Sampling ──
SAMPLING_MAX_CHARS = int(os.environ.get("DEEP_REPORT_SAMPLING_MAX_CHARS", "30000"))

# ── Validation ──
VALIDATION_WARN_THRESHOLD = float(os.environ.get("DEEP_REPORT_VALIDATION_WARN", "20"))
VALIDATION_REJECT_THRESHOLD = float(os.environ.get("DEEP_REPORT_VALIDATION_REJECT", "50"))
