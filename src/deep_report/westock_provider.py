"""deep-report WeStock Data Provider

统一封装 westock-data CLI，为 analyzer 和 quarterly_trigger 提供
三市场（A股/港股/美股）财务数据获取能力。

参考：financial-report-minesweeper/scripts/westock_fetch.py
"""

from __future__ import annotations
import logging
import math
import re
import subprocess
import time
from typing import Optional

logger = logging.getLogger("deep_report.westock")

# ── CLI 配置 ──
WESTOCK_CLI = ["npx", "-y", "westock-data-skillhub@1.0.3"]
CLI_TIMEOUT = 120  # 秒
RETRY_MAX = 2
RETRY_DELAY = 1.5


def _run_cli(*args: str) -> str:
    """运行 WeStock CLI，自动重试"""
    cmd = WESTOCK_CLI + list(args)
    last_error = None
    for attempt in range(RETRY_MAX + 1):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT,
            )
            if result.returncode == 0:
                return result.stdout
            last_error = f"exit={result.returncode}: {result.stderr[:200]}"
        except subprocess.TimeoutExpired:
            last_error = f"timeout ({attempt+1}/{RETRY_MAX+1})"
        except FileNotFoundError:
            logger.error("npx not found. Install Node.js >= 18")
            raise
        if attempt < RETRY_MAX:
            time.sleep(RETRY_DELAY)
    raise RuntimeError(f"WeStock CLI failed: {last_error}")


def _parse_markdown_table(text: str) -> list[dict]:
    """将 markdown 表格解析为 [{col: val}, ...]"""
    lines = text.strip().split("\n")
    if len(lines) < 3:
        return []
    headers = [h.strip() for h in lines[0].split("|")[1:-1]]
    data = []
    for line in lines[2:]:
        if not line.strip() or re.match(r'^[\s\|\-:]+$', line):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) == len(headers):
            data.append({headers[i]: cells[i] for i in range(len(headers))})
    return data


def _parse_numeric(val: str | None) -> Optional[float]:
    """安全解析数值"""
    if val is None or val in ("", "-", "—", "--"):
        return None
    try:
        v = float(val.replace(",", "").replace("−", "-"))
        if math.isinf(v) or math.isnan(v):
            return None
        return v
    except (ValueError, AttributeError):
        return None


# ── 股票代码转换 ──

def to_westock_code(code: str, market: str) -> str:
    """将 deep-report 代码转换为 WeStock 格式

    Examples:
        600519.SH → sh600519
        00700.HK  → hk00700
        AAPL      → usAAPL
    """
    if market == "A":
        num = code.replace(".SH", "").replace(".SZ", "")
        prefix = "sh" if code.endswith(".SH") else "sz"
        return f"{prefix}{num}"
    elif market == "HK":
        num = code.replace(".HK", "")
        return f"hk{num}"
    elif market == "US":
        return f"us{code}"
    return code


# ── 财务数据获取 ──

# A股利润表字段映射
FINANCE_FIELD_MAP = {
    "OperatingRevenue": "revenue",
    "NPParentCompanyOwners": "net_profit",
    "GrossIncome": "gross_profit",
    "OperatingProfit": "operating_profit",
}

# 港股字段映射（列名不同）
HK_FINANCE_FIELD_MAP = {
    "OperatingIncome": "revenue",
    "ProfitToShareholders": "net_profit",
    "OperatingProfit": "operating_profit",
    "TotalAssets": "total_assets",
    "SeWithoutMinority": "total_equity",
    "TotalLiability": "total_liabilities",
}

# A股资产负债表映射
BS_FIELD_MAP = {
    "TotalAssets": "total_assets",
    "SEWithoutMI": "total_equity",
    "TotalLiability": "total_liabilities",
}

# 港股资产负债表映射（列名不同）
HK_BS_FIELD_MAP = {
    "TotalAssets": "total_assets",
    "TotalEquity": "total_equity",
    "TotalLiability": "total_liabilities",
}

# 美股资产负债表映射
US_BS_FIELD_MAP = {
    "TotalAssets": "total_assets",
    "TotalEquity": "total_equity",
    "TotalLiabilities": "total_liabilities",
}
US_FINANCE_FIELD_MAP = {
    "Sales": "revenue",
    "NetIncome": "net_profit",
    "GrossIncome": "gross_profit",
    "EBIT": "operating_profit",
    "TotalAssets": "total_assets",
    "SEWithoutMI": "total_equity",
    "TotalLiability": "total_liabilities",
}


def fetch_finance_data(code: str, market: str, num_periods: int = 4) -> dict:
    """获取财务报表数据，返回 deep-report 兼容格式

    Returns:
        {
            "income_statement": {field: {period: value}},
            "balance_sheet": {field: {period: value}},
            "cash_flow": {field: {period: value}},
            "periods": ["2025-12-31", ...],
        }
    """
    ws_code = to_westock_code(code, market)

    try:
        stdout = _run_cli("finance", ws_code, "--num", str(num_periods))
    except Exception as e:
        logger.warning("WeStock finance failed for %s: %s", ws_code, e)
        return {}

    # 解析三大报表
    result = {"income_statement": {}, "balance_sheet": {}, "cash_flow": {}, "periods": []}

    # 按 **xxx** 分割
    parts = re.split(r'\*\*(lrb|zcfz|xjll|zhsy|income|balance|cashflow)\*\*', stdout)
    for i, keyword in enumerate(parts):
        if i + 1 >= len(parts):
            continue
        table_text = parts[i + 1]
        rows = _parse_markdown_table(table_text)
        if not rows:
            continue

        # 选择字段映射
        if keyword in ("lrb", "zhsy", "income"):
            if market == "HK":
                field_map = HK_FINANCE_FIELD_MAP
            elif market == "US":
                field_map = US_FINANCE_FIELD_MAP
            else:
                field_map = FINANCE_FIELD_MAP
            target = "income_statement"
        elif keyword in ("zcfz", "balance"):
            if market == "HK":
                field_map = HK_BS_FIELD_MAP
            elif market == "US":
                field_map = US_BS_FIELD_MAP
            else:
                field_map = BS_FIELD_MAP
            target = "balance_sheet"
        elif keyword in ("xjll", "cashflow"):
            field_map = {}
            target = "cash_flow"
        else:
            continue

        # 提取数据
        for row in rows:
            period = row.get("_date", row.get("EndDate", ""))
            if not period:
                continue
            if period not in result["periods"]:
                result["periods"].append(period)

            for ws_field, std_field in field_map.items():
                val = _parse_numeric(row.get(ws_field))
                if val is not None:
                    if std_field not in result[target]:
                        result[target][std_field] = {}
                    result[target][std_field][period] = val

    # 排序期间
    result["periods"].sort()
    return result


def fetch_latest_period(code: str, market: str) -> Optional[str]:
    """获取最新报告期日期"""
    data = fetch_finance_data(code, market, num_periods=1)
    periods = data.get("periods", [])
    return periods[-1] if periods else None


def fetch_company_name(code: str, market: str) -> str:
    """获取公司名称"""
    ws_code = to_westock_code(code, market)
    try:
        stdout = _run_cli("profile", ws_code)
        for line in stdout.split("\n"):
            if ws_code in line:
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if len(cells) >= 2:
                    return cells[1]
    except Exception:
        pass
    return code


# ── 财报披露日 ──

def fetch_disclosure_dates(code: str, market: str) -> list[dict]:
    """获取财报预约披露日期

    Returns:
        [{"report_period": "2026Q1", "disclosure_date": "2026-04-25", "desc": "..."}]
    """
    ws_code = to_westock_code(code, market)
    try:
        stdout = _run_cli("reserve", ws_code)
        rows = _parse_markdown_table(stdout)
        results = []
        for row in rows:
            end_date = row.get("reportEndDate", "")
            disc_date = row.get("disclosureDate", "")
            desc = row.get("disclosureDesc", "")
            if end_date and disc_date:
                results.append({
                    "report_period": end_date,
                    "disclosure_date": disc_date,
                    "desc": desc,
                })
        return results
    except Exception as e:
        logger.debug("reserve failed for %s: %s", ws_code, e)
        return []


# ── 技术指标 ──

def fetch_technical_indicators(code: str, market: str) -> dict:
    """获取技术指标（MACD/KDJ/RSI/均线等）

    Returns:
        {indicator_name: value, ...}
    """
    ws_code = to_westock_code(code, market)
    try:
        stdout = _run_cli("technical", ws_code, "--group", "all")
        rows = _parse_markdown_table(stdout)
        if not rows:
            return {}
        row = rows[0]  # 最新截面
        result = {}
        for key, val in row.items():
            if key in ("code", "name", "date"):
                continue
            num = _parse_numeric(val)
            if num is not None:
                # 展平嵌套字段
                flat_key = key.replace(".", "_")
                result[flat_key] = num
        return result
    except Exception as e:
        logger.debug("technical failed for %s: %s", ws_code, e)
        return {}


# ── 投资日历 ──

def fetch_calendar(date_str: str = "", limit: int = 30, country: int = 0) -> list[dict]:
    """获取投资日历事件

    Args:
        date_str: 起始日期 YYYY-MM-DD，空=有事件的日期列表
        limit: 查询天数
        country: 1=中国 2=美国 3=港股 0=全部
    """
    args = ["calendar"]
    if date_str:
        args.append(date_str)
    args.extend(["--limit", str(limit)])
    if country:
        args.extend(["--country", str(country)])
    try:
        stdout = _run_cli(*args)
        return _parse_markdown_table(stdout)
    except Exception as e:
        logger.debug("calendar failed: %s", e)
        return []
