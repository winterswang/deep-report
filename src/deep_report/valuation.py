"""deep-report 估值温度计模块

基于 akshare 历史估值数据计算 PE/PB/PS 百分位和温度值，
为 LLM 叙事提供估值上下文。
"""

from __future__ import annotations
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("deep_report.valuation")

# ── 温度分级 ──
TEMP_LEVELS = [
    (0, 20, "极冷", "🧊"),
    (20, 40, "偏冷", "❄️"),
    (40, 60, "适中", "🌡️"),
    (60, 80, "偏热", "🔥"),
    (80, 101, "极热", "🌋"),
]

DEFAULT_WEIGHTS = {"pe": 0.5, "pb": 0.3, "ps": 0.2}


def _get_level(temperature: float) -> tuple[str, str]:
    for low, high, name, emoji in TEMP_LEVELS:
        if low <= temperature < high:
            return name, emoji
    return "极热", "🌋"


def _compute_percentile(historical_values: list[float], current: float) -> Optional[float]:
    """计算当前值在历史分布中的百分位（0-100）。

    百分位 = 低于当前值的历史值占比 × 100
    例如：当前 PE=18，历史上 70% 的时间 PE 低于 18 → 百分位=70（比70%时间贵）
    """
    if not historical_values or len(historical_values) < 10:
        return None
    arr = np.array(historical_values, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 10:
        return None
    rank = np.sum(arr < current)
    pct = rank / len(arr) * 100
    return round(pct, 1)


def _fetch_akshare_series(symbol: str, indicator: str, market: str) -> Optional[pd.DataFrame]:
    """从 akshare 获取估值时间序列"""
    try:
        import akshare as ak

        period_map = {"A": "近五年", "HK": "近三年", "US": "近三年"}
        period = period_map.get(market, "近三年")

        if market == "A":
            code = symbol.replace(".SH", "").replace(".SZ", "")
            df = ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period=period)
        elif market == "HK":
            code = symbol.replace(".HK", "")
            df = ak.stock_hk_valuation_baidu(symbol=code, indicator=indicator, period=period)
        elif market == "US":
            # 美股使用百度估值接口
            df = ak.stock_us_valuation_baidu(symbol=symbol, indicator=indicator, period=period)
        else:
            return None

        if df is None or df.empty:
            return None
        return df
    except Exception as e:
        logger.debug("akshare fetch failed for %s %s: %s", symbol, indicator, e)
        return None


def fetch_valuation_percentiles(
    code: str, market: str
) -> dict[str, Optional[float]]:
    """获取 PE/PB/PS 历史百分位"""
    result: dict[str, Optional[float]] = {
        "pe_pct": None, "pb_pct": None, "ps_pct": None,
        "pe_current": None, "pb_current": None, "ps_current": None,
    }

    indicator_map = {
        "pe": "市盈率(TTM)",
        "pb": "市净率",
        "ps": "市现率",  # akshare 没有 PS，用市现率近似
    }

    for key, indicator in indicator_map.items():
        df = _fetch_akshare_series(code, indicator, market)
        if df is None or df.empty:
            continue

        try:
            values = pd.to_numeric(df["value"], errors="coerce").dropna().tolist()
            if len(values) < 10:
                continue

            current = values[-1]
            historical = values[:-1]
            pct = _compute_percentile(historical, current)

            result[f"{key}_current"] = round(current, 2)
            result[f"{key}_pct"] = pct
        except Exception as e:
            logger.debug("Percentile calc failed for %s %s: %s", code, key, e)

    return result


def calculate_temperature(
    pe_pct: Optional[float],
    pb_pct: Optional[float],
    ps_pct: Optional[float],
    weights: Optional[dict] = None,
) -> dict:
    """计算估值温度（复用 morning-brief 逻辑）

    Returns:
        {temperature: float|None, level: str, emoji: str, status: str}
    """
    w = weights or DEFAULT_WEIGHTS

    indicators = []
    for key, pct in [("pe", pe_pct), ("pb", pb_pct), ("ps", ps_pct)]:
        if pct is not None and pct >= 0:
            clamped = max(0.0, min(100.0, float(pct)))
            indicators.append((key, clamped, w[key]))

    if not indicators:
        return {"temperature": None, "level": "数据不足", "emoji": "❌", "status": "invalid"}

    total_weight = sum(wt for _, _, wt in indicators)
    normalized = [(key, pct, wt / total_weight) for key, pct, wt in indicators]

    temperature = sum(pct * nw for _, pct, nw in normalized)
    temperature = max(0.0, min(100.0, round(temperature, 1)))

    level, emoji = _get_level(temperature)
    status = "valid" if len(indicators) == 3 else "partial"

    return {
        "temperature": temperature,
        "level": level,
        "emoji": emoji,
        "status": status,
    }


def format_valuation_context(code: str, market: str) -> str:
    """生成估值上下文文本，供 LLM 叙事 prompt 使用

    Returns:
        格式化的估值上下文字符串，可直接拼接到 analyze prompt
    """
    percentiles = fetch_valuation_percentiles(code, market)
    temp = calculate_temperature(
        percentiles["pe_pct"], percentiles["pb_pct"], percentiles["ps_pct"]
    )

    if temp["status"] == "invalid":
        return ""

    lines = [
        "## 📊 估值温度计（实时数据，引用时请使用以下数值）",
        "",
        f"| 指标 | 当前值 | 历史百分位 |",
        f"|------|--------|-----------|",
    ]

    labels = {"pe": "市盈率(TTM)", "pb": "市净率", "ps": "市现率"}
    for key, label in labels.items():
        cur = percentiles.get(f"{key}_current")
        pct = percentiles.get(f"{key}_pct")
        if cur is not None and pct is not None:
            lines.append(f"| {label} | {cur} | {pct}%（比{pct}%的时间贵）|")

    lines.append("")
    lines.append(
        f"**综合估值温度**: {temp['emoji']} {temp['temperature']}° — **{temp['level']}**"
    )
    lines.append("")
    lines.append(
        "> 温度分级: 🧊极冷(0-20) ❄️偏冷(20-40) 🌡️适中(40-60) 🔥偏热(60-80) 🌋极热(80-100)"
    )
    lines.append("")

    return "\n".join(lines)
