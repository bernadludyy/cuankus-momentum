from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from config import (
    DAILY_FILTER_ENABLED,
    MIN_VALUE_MA_IDR,
    VALUE_MA_WINDOW,
    FILTER1_VALUE_MA50_WINDOW,
    FILTER1_VALUE_MA50_ENABLED,
    FILTER1_MIN_VALUE_MA50_IDR,
    VOLUME_MA_LONG,
    VOLUME_MA_RATIO_MIN,
    VOLUME_MA_SHORT,
)

WIB = timezone(timedelta(hours=7))
GOLDEN_POCKET_LOOKBACK = 20
GOLDEN_POCKET_TACTICAL_LOOKBACK = 5
GOLDEN_POCKET_FIB_UPPER = 0.618
GOLDEN_POCKET_FIB_LOWER = 0.650


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_items(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]

        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            nested = _extract_items(data)
            if nested:
                return nested

        content = payload.get("content")
        if isinstance(content, (dict, list)):
            nested = _extract_items(content)
            if nested:
                return nested

        for value in payload.values():
            if isinstance(value, (dict, list)):
                nested = _extract_items(value)
                if nested:
                    return nested

    elif isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    return []


def _compute_bullish_golden_pocket(
    rows: list[dict],
    lookback: int = GOLDEN_POCKET_LOOKBACK,
) -> dict:
    """Find the strongest recent bullish low->high impulse and its Fib pocket.

    The pocket is informational only. For a bullish impulse:
    0.618 level = swing_high - range * 0.618
    0.650 level = swing_high - range * 0.650
    """
    usable = [
        row
        for row in rows[-max(2, int(lookback)):]
        if (
            row.get("high") is not None
            and row.get("low") is not None
            and float(row["high"]) > 0
            and float(row["low"]) > 0
        )
    ]

    if len(usable) < 2:
        return {
            "swing_low": None,
            "swing_high": None,
            "swing_low_date": None,
            "swing_high_date": None,
            "golden_pocket_lower": None,
            "golden_pocket_upper": None,
            "golden_pocket_lookback": int(lookback),
        }

    best: tuple[float, dict, dict] | None = None

    for i, low_row in enumerate(usable[:-1]):
        low = float(low_row["low"])
        for high_row in usable[i + 1:]:
            high = float(high_row["high"])
            impulse = high - low
            if impulse <= 0:
                continue
            if best is None or impulse > best[0]:
                best = (impulse, low_row, high_row)

    if best is None:
        return {
            "swing_low": None,
            "swing_high": None,
            "swing_low_date": None,
            "swing_high_date": None,
            "golden_pocket_lower": None,
            "golden_pocket_upper": None,
            "golden_pocket_lookback": int(lookback),
        }

    impulse, low_row, high_row = best
    swing_low = float(low_row["low"])
    swing_high = float(high_row["high"])

    pocket_618 = swing_high - impulse * GOLDEN_POCKET_FIB_UPPER
    pocket_650 = swing_high - impulse * GOLDEN_POCKET_FIB_LOWER

    return {
        "swing_low": swing_low,
        "swing_high": swing_high,
        "swing_low_date": str(low_row["date"]),
        "swing_high_date": str(high_row["date"]),
        "golden_pocket_lower": min(pocket_650, pocket_618),
        "golden_pocket_upper": max(pocket_650, pocket_618),
        "golden_pocket_lookback": int(lookback),
    }


def compute_daily_filter_metrics(payload: Any) -> dict:
    """Compute liquidity MAs and Golden Pocket from completed daily sessions.

    Current calendar day is excluded because Bot 4 runs intraday and today's
    daily candle is incomplete.
    """
    today = datetime.now(WIB).date()
    rows: list[dict] = []

    for item in _extract_items(payload):
        raw_date = item.get("date") or item.get("Date")
        if raw_date is None:
            continue

        try:
            trade_date = datetime.fromisoformat(str(raw_date)[:10]).date()
        except ValueError:
            continue

        if trade_date >= today:
            continue

        volume = _number(item.get("volume", item.get("Volume")))
        value = _number(item.get("value", item.get("Value")))
        high = _number(item.get("high", item.get("High")))
        low = _number(item.get("low", item.get("Low")))
        close = _number(item.get("close", item.get("Close")))

        if volume is None or value is None:
            continue

        rows.append(
            {
                "date": trade_date.isoformat(),
                "volume": volume,
                "value": value,
                "high": high,
                "low": low,
                "close": close,
            }
        )

    rows.sort(key=lambda x: x["date"])

    required = max(
        VOLUME_MA_LONG,
        VALUE_MA_WINDOW,
        FILTER1_VALUE_MA50_WINDOW,
    )
    history_days = len(rows)
    golden = _compute_bullish_golden_pocket(rows)
    tactical_raw = _compute_bullish_golden_pocket(
        rows,
        lookback=GOLDEN_POCKET_TACTICAL_LOOKBACK,
    )
    tactical_golden = {
        "swing_low_5d": tactical_raw["swing_low"],
        "swing_high_5d": tactical_raw["swing_high"],
        "swing_low_date_5d": tactical_raw["swing_low_date"],
        "swing_high_date_5d": tactical_raw["swing_high_date"],
        "golden_pocket_lower_5d": tactical_raw["golden_pocket_lower"],
        "golden_pocket_upper_5d": tactical_raw["golden_pocket_upper"],
        "golden_pocket_lookback_5d": tactical_raw[
            "golden_pocket_lookback"
        ],
    }

    close_rows = [row for row in rows if row.get("close") not in (None, 0)]
    daily_sma20 = None
    daily_sma20_prev = None
    daily_last_close = None
    daily_trend = "INSUFFICIENT"
    if close_rows:
        daily_last_close = float(close_rows[-1]["close"])
    if len(close_rows) >= 21:
        daily_sma20 = sum(float(x["close"]) for x in close_rows[-20:]) / 20
        daily_sma20_prev = (
            sum(float(x["close"]) for x in close_rows[-21:-1]) / 20
        )
        if daily_last_close > daily_sma20 and daily_sma20 >= daily_sma20_prev:
            daily_trend = "UP"
        elif daily_last_close < daily_sma20 and daily_sma20 <= daily_sma20_prev:
            daily_trend = "DOWN"
        else:
            daily_trend = "SIDEWAYS"

    daily_context = {
        "daily_last_close": daily_last_close,
        "daily_sma20": daily_sma20,
        "daily_sma20_prev": daily_sma20_prev,
        "daily_trend": daily_trend,
    }

    if history_days < required:
        return {
            "history_days": history_days,
            "volume_ma_short": None,
            "volume_ma_long": None,
            "volume_ma_ratio": None,
            "value_ma": None,
            "transaction_value_ma50": None,
            "enough_history": False,
            **daily_context,
            **golden,
            **tactical_golden,
        }

    short_volumes = [x["volume"] for x in rows[-VOLUME_MA_SHORT:]]
    long_volumes = [x["volume"] for x in rows[-VOLUME_MA_LONG:]]
    recent_values = [x["value"] for x in rows[-VALUE_MA_WINDOW:]]
    value_ma50_values = [
        x["value"] for x in rows[-FILTER1_VALUE_MA50_WINDOW:]
    ]

    volume_ma_short = sum(short_volumes) / len(short_volumes)
    volume_ma_long = sum(long_volumes) / len(long_volumes)
    value_ma = sum(recent_values) / len(recent_values)
    transaction_value_ma50 = (
        sum(value_ma50_values) / len(value_ma50_values)
        if len(value_ma50_values) >= FILTER1_VALUE_MA50_WINDOW
        else None
    )

    volume_ma_ratio = (
        volume_ma_short / volume_ma_long
        if volume_ma_long > 0
        else None
    )

    return {
        "history_days": history_days,
        "volume_ma_short": volume_ma_short,
        "volume_ma_long": volume_ma_long,
        "volume_ma_ratio": volume_ma_ratio,
        "value_ma": value_ma,
        "transaction_value_ma50": transaction_value_ma50,
        "enough_history": True,
        **daily_context,
        **golden,
        **tactical_golden,
    }


def daily_filter_is_eligible(metrics: dict | None) -> bool:
    if not DAILY_FILTER_ENABLED:
        return True
    if not metrics or not metrics.get("enough_history"):
        return False

    ratio = metrics.get("volume_ma_ratio")
    value_ma = metrics.get("value_ma")
    value_ma50 = metrics.get("transaction_value_ma50")

    if ratio is None or value_ma is None:
        return False
    if FILTER1_VALUE_MA50_ENABLED and value_ma50 is None:
        return False

    return (
        float(ratio) >= VOLUME_MA_RATIO_MIN
        and float(value_ma) > MIN_VALUE_MA_IDR
        and (
            not FILTER1_VALUE_MA50_ENABLED
            or float(value_ma50) >= FILTER1_MIN_VALUE_MA50_IDR
        )
    )
