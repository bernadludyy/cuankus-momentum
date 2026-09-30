from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from scanner.market_hours import is_idx_market_window


def _filter_current_market_day(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only snapshots from the current WIB trading day and regular session.

    This prevents overnight/manual zero-activity snapshots from becoming the
    baseline for the next market session.
    """
    if not snapshots:
        return []

    try:
        current_dt = datetime.fromisoformat(snapshots[-1]["ts"])
        current_date = current_dt.date()
    except Exception:
        return snapshots

    filtered: list[dict[str, Any]] = []
    for item in snapshots:
        try:
            dt = datetime.fromisoformat(item["ts"])
        except Exception:
            continue

        if dt.date() == current_date and is_idx_market_window(dt):
            filtered.append(item)

    return filtered


def _safe_pct(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return ((new / old) - 1.0) * 100.0


def _minutes_between(a: str, b: str) -> float:
    try:
        ta = datetime.fromisoformat(a)
        tb = datetime.fromisoformat(b)
        minutes = (tb - ta).total_seconds() / 60.0
        return max(minutes, 0.01)
    except Exception:
        return 1.0


def _rate_delta(curr: dict[str, Any], prev: dict[str, Any], field: str) -> float | None:
    a = prev.get(field)
    b = curr.get(field)

    if a is None or b is None:
        return None

    delta = float(b) - float(a)
    if delta < 0:
        return None

    minutes = _minutes_between(prev["ts"], curr["ts"])
    if minutes > 15:
        return None
    return delta / minutes


def _spike(
    current_rate: float | None,
    baseline_rates: list[float],
    min_baseline_intervals: int,
) -> float | None:
    clean = [x for x in baseline_rates if x is not None and x >= 0]
    if current_rate is None or len(clean) < min_baseline_intervals:
        return None

    base = median(clean)
    if base <= 0:
        return 1.0 if current_rate <= 0 else 5.0

    return current_rate / base


def build_features(
    snapshots: list[dict[str, Any]],
    min_baseline_intervals: int = 4,
) -> dict[str, Any] | None:
    snapshots = _filter_current_market_day(snapshots)

    if len(snapshots) < min_baseline_intervals + 2:
        return None

    current = snapshots[-1]
    previous = snapshots[-2]

    interval_return_pct = _safe_pct(current.get("last"), previous.get("last"))
    daily_change_pct = _safe_pct(current.get("last"), current.get("prev_close"))

    daily_range_pct = None
    if (
        current.get("high") is not None
        and current.get("low") is not None
        and current.get("prev_close")
    ):
        daily_range_pct = (
            (float(current["high"]) - float(current["low"]))
            / float(current["prev_close"])
            * 100.0
        )

    position_in_range = None
    if (
        current.get("last") is not None
        and current.get("high") is not None
        and current.get("low") is not None
        and float(current["high"]) > float(current["low"])
    ):
        position_in_range = (
            (float(current["last"]) - float(current["low"]))
            / (float(current["high"]) - float(current["low"]))
        )

    fields = ("volume", "value", "frequency")
    current_rates: dict[str, float | None] = {}
    baseline_rates: dict[str, list[float]] = {f: [] for f in fields}

    for field in fields:
        current_rates[field] = _rate_delta(current, previous, field)

        for i in range(1, len(snapshots) - 1):
            rate = _rate_delta(snapshots[i], snapshots[i - 1], field)
            if rate is not None:
                baseline_rates[field].append(rate)

    volume_spike = _spike(
        current_rates["volume"],
        baseline_rates["volume"],
        min_baseline_intervals,
    )
    value_spike = _spike(
        current_rates["value"],
        baseline_rates["value"],
        min_baseline_intervals,
    )
    frequency_spike = _spike(
        current_rates["frequency"],
        baseline_rates["frequency"],
        min_baseline_intervals,
    )

    prior_prices = [
        float(x["last"])
        for x in snapshots[:-1]
        if x.get("last") is not None
    ]

    breakout_pct = None
    if prior_prices and current.get("last") is not None:
        prior_high = max(prior_prices[-5:])
        breakout_pct = _safe_pct(float(current["last"]), prior_high)

    spikes = [
        x for x in (volume_spike, value_spike, frequency_spike)
        if x is not None
    ]
    activity_spike = sum(spikes) / len(spikes) if spikes else None

    effort_result = None
    if activity_spike is not None and interval_return_pct is not None:
        effort_result = activity_spike / max(abs(interval_return_pct), 0.15)

    return {
        "ticker": current["ticker"],
        "price": current.get("last"),
        "daily_change_pct": daily_change_pct,
        "interval_return_pct": interval_return_pct,
        "daily_range_pct": daily_range_pct,
        "position_in_range": position_in_range,
        "volume_spike": volume_spike,
        "value_spike": value_spike,
        "frequency_spike": frequency_spike,
        "activity_spike": activity_spike,
        "effort_result": effort_result,
        "breakout_pct": breakout_pct,
        "volume_rate": current_rates["volume"],
        "value_rate": current_rates["value"],
        "frequency_rate": current_rates["frequency"],
    }
