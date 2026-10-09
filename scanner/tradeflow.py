from __future__ import annotations

import math
from typing import Any


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def classify_aggression_trend(
    snapshots: list[dict[str, Any]],
    *,
    min_samples: int = 3,
    flat_tolerance_pct: float = 8.0,
) -> dict[str, Any]:
    """Classify rolling net-aggression direction across scanner cycles.

    The inputs are persisted microstructure snapshots from the current WIB
    trading date. RISING/FALLING require directionally consistent steps rather
    than a single last-point jump.
    """
    values = [
        value
        for item in snapshots
        if (value := _number(item.get("net_aggression_value"))) is not None
    ]

    required = max(2, int(min_samples))
    if len(values) < required:
        return {
            "aggression_trend": "INSUFFICIENT_HISTORY",
            "aggression_trend_samples": len(values),
            "aggression_trend_change_value": None,
            "aggression_trend_change_pct": None,
        }

    scale = max(max(abs(value) for value in values), 1.0)
    tolerance = scale * max(0.0, float(flat_tolerance_pct)) / 100.0
    diffs = [right - left for left, right in zip(values, values[1:])]

    positive_steps = sum(diff > tolerance for diff in diffs)
    negative_steps = sum(diff < -tolerance for diff in diffs)
    non_negative_steps = sum(diff >= -tolerance for diff in diffs)
    non_positive_steps = sum(diff <= tolerance for diff in diffs)

    first = values[0]
    last = values[-1]
    change = last - first

    if (
        last > 0
        and change > tolerance
        and positive_steps >= 2
        and non_negative_steps == len(diffs)
    ):
        trend = "RISING"
    elif (
        change < -tolerance
        and negative_steps >= 2
        and non_positive_steps == len(diffs)
    ):
        trend = "FALLING"
    elif abs(change) <= tolerance:
        trend = "FLAT"
    else:
        trend = "MIXED"

    change_pct = None
    if abs(first) > 1e-9:
        change_pct = change / abs(first) * 100.0

    return {
        "aggression_trend": trend,
        "aggression_trend_samples": len(values),
        "aggression_trend_change_value": change,
        "aggression_trend_change_pct": change_pct,
    }


def build_tradeflow_layers(
    micro: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    min_samples: int = 3,
    flat_tolerance_pct: float = 8.0,
) -> dict[str, Any]:
    """Return the three internal tradeflow layers without changing signal score."""
    trend = classify_aggression_trend(
        history,
        min_samples=min_samples,
        flat_tolerance_pct=flat_tolerance_pct,
    )
    return {
        "aggressive_buy_value": _number(micro.get("aggressive_buy_value")),
        "aggressive_sell_value": _number(micro.get("aggressive_sell_value")),
        "net_aggression_value": _number(micro.get("net_aggression_value")),
        **trend,
    }
