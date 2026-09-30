from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from config import (
    DAILY_FILTER_ENABLED,
    MIN_VALUE_MA_IDR,
    VALUE_MA_WINDOW,
    VOLUME_MA_LONG,
    VOLUME_MA_RATIO_MIN,
    VOLUME_MA_SHORT,
)

WIB = timezone(timedelta(hours=7))


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


def compute_daily_filter_metrics(payload: Any) -> dict:
    """Compute MA filters from completed IDX daily sessions only.

    Current calendar day is excluded because Bot 4 runs intraday and today's
    daily volume/value are still incomplete.
    """
    today = datetime.now(WIB).date()
    rows: list[tuple] = []

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

        if volume is None or value is None:
            continue

        rows.append((trade_date, volume, value))

    rows.sort(key=lambda x: x[0])

    required = max(VOLUME_MA_LONG, VALUE_MA_WINDOW)
    history_days = len(rows)

    if history_days < required:
        return {
            "history_days": history_days,
            "volume_ma_short": None,
            "volume_ma_long": None,
            "volume_ma_ratio": None,
            "value_ma": None,
            "enough_history": False,
        }

    short_volumes = [x[1] for x in rows[-VOLUME_MA_SHORT:]]
    long_volumes = [x[1] for x in rows[-VOLUME_MA_LONG:]]
    recent_values = [x[2] for x in rows[-VALUE_MA_WINDOW:]]

    volume_ma_short = sum(short_volumes) / len(short_volumes)
    volume_ma_long = sum(long_volumes) / len(long_volumes)
    value_ma = sum(recent_values) / len(recent_values)

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
        "enough_history": True,
    }


def daily_filter_is_eligible(metrics: dict | None) -> bool:
    if not DAILY_FILTER_ENABLED:
        return True
    if not metrics or not metrics.get("enough_history"):
        return False

    ratio = metrics.get("volume_ma_ratio")
    value_ma = metrics.get("value_ma")

    if ratio is None or value_ma is None:
        return False

    return (
        float(ratio) >= VOLUME_MA_RATIO_MIN
        and float(value_ma) > MIN_VALUE_MA_IDR
    )
