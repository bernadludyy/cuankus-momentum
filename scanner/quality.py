from __future__ import annotations

import math
from typing import Any

from config import (
    FILTER1_MIN_VALUE_MA50_IDR,
    SELLING_ABSORPTION_MIN_BID_PERCENT,
    SELLING_ABSORPTION_MIN_INTERVAL_RETURN_PCT,
    SELLING_ABSORPTION_MIN_POSITION_IN_RANGE,
    SELLING_ABSORPTION_MIN_SELL_VALUE_IDR,
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def classify_selling_absorption(result: dict[str, Any]) -> dict[str, Any]:
    """Detect sell pressure that is being absorbed without a meaningful breakdown.

    This is deliberately a soft confirmation layer. It must never turn a falling
    knife into a valid signal by itself.
    """
    sell_value = _number(result.get("aggressive_sell_value"))
    bid_percent = _number(result.get("bid_percent"))
    interval = _number(result.get("interval_return_pct"))
    position = _number(result.get("position_in_range"))
    buy_ratio = _number(result.get("aggressive_buy_ratio"))

    if sell_value is None or sell_value < SELLING_ABSORPTION_MIN_SELL_VALUE_IDR:
        return {
            "selling_absorption": "NONE",
            "selling_absorption_score": 50.0,
        }

    confirmed = (
        bid_percent is not None
        and bid_percent >= SELLING_ABSORPTION_MIN_BID_PERCENT
        and interval is not None
        and interval >= SELLING_ABSORPTION_MIN_INTERVAL_RETURN_PCT
        and position is not None
        and position >= SELLING_ABSORPTION_MIN_POSITION_IN_RANGE
        and buy_ratio is not None
        and buy_ratio >= 0.50
        and bool(result.get("anti_falling_knife_pass"))
        and bool(result.get("not_overextended"))
    )
    if confirmed:
        return {
            "selling_absorption": "CONFIRMED",
            "selling_absorption_score": 90.0,
        }

    watch = (
        bid_percent is not None
        and bid_percent >= 50.0
        and interval is not None
        and interval >= -0.50
        and position is not None
        and position >= 0.50
        and bool(result.get("anti_falling_knife_pass"))
    )
    return {
        "selling_absorption": "WATCH" if watch else "NONE",
        "selling_absorption_score": 65.0 if watch else 35.0,
    }


def _tradeflow_score(result: dict[str, Any], absorption_score: float) -> float:
    buy_ratio = _number(result.get("aggressive_buy_ratio"))
    buy_score = _clamp((buy_ratio or 0.0) * 100.0)

    buy_value = _number(result.get("aggressive_buy_value")) or 0.0
    sell_value = _number(result.get("aggressive_sell_value")) or 0.0
    total = buy_value + sell_value
    if total > 0:
        net_ratio_pct = (buy_value - sell_value) / total * 100.0
        net_score = _clamp((net_ratio_pct + 100.0) / 2.0)
    else:
        net_score = 50.0

    trend_score = {
        "RISING": 100.0,
        "FLAT": 65.0,
        "MIXED": 50.0,
        "INSUFFICIENT_HISTORY": 55.0,
        "FALLING": 20.0,
    }.get(str(result.get("aggression_trend") or ""), 50.0)

    return _clamp(
        buy_score * 0.45
        + net_score * 0.25
        + trend_score * 0.20
        + absorption_score * 0.10
    )


def _liquidity_score(result: dict[str, Any]) -> float:
    value_ma50 = _number(result.get("transaction_value_ma50"))
    volume_ratio = _number(result.get("volume_ma_ratio"))

    if value_ma50 is None or FILTER1_MIN_VALUE_MA50_IDR <= 0:
        value_score = 0.0
    else:
        ratio = value_ma50 / FILTER1_MIN_VALUE_MA50_IDR
        # 50 at the hard threshold, 100 around 4x threshold.
        value_score = _clamp(50.0 + (ratio - 1.0) * (50.0 / 3.0))

    if volume_ratio is None:
        volume_score = 0.0
    else:
        # 50 around 2x, 100 around 5x.
        volume_score = _clamp(50.0 + (volume_ratio - 2.0) * (50.0 / 3.0))

    return _clamp(value_score * 0.70 + volume_score * 0.30)


def _structure_score(result: dict[str, Any]) -> float:
    breakout = _number(result.get("breakout_pct"))
    position = _number(result.get("position_in_range"))

    if breakout is None:
        breakout_score = 40.0
    elif 0.30 <= breakout <= 1.50:
        breakout_score = 100.0
    elif 0.0 <= breakout < 0.30:
        breakout_score = 70.0
    elif 1.50 < breakout <= 3.50:
        breakout_score = 80.0
    elif -0.30 <= breakout < 0:
        breakout_score = 55.0
    else:
        breakout_score = 30.0

    position_score = _clamp((position or 0.0) * 100.0)
    safety_score = (
        100.0
        if bool(result.get("anti_falling_knife_pass"))
        and bool(result.get("not_overextended"))
        else 0.0
    )
    return _clamp(
        breakout_score * 0.40
        + position_score * 0.30
        + safety_score * 0.30
    )


def build_quality_metrics(result: dict[str, Any]) -> dict[str, Any]:
    """Build the Bot 4 V2 final quality score used for ranking, not discovery."""
    absorption = classify_selling_absorption(result)

    momentum_score = _clamp(_number(result.get("score")) or 0.0)
    micro_score = _clamp(_number(result.get("micro_score")) or 0.0)
    tradeflow_score = _tradeflow_score(
        result,
        float(absorption["selling_absorption_score"]),
    )
    liquidity_score = _liquidity_score(result)
    foreign_score = _number(result.get("foreign_score"))
    foreign_component = _clamp(foreign_score if foreign_score is not None else 50.0)
    structure_score = _structure_score(result)

    quality_before_penalty = (
        momentum_score * 0.30
        + micro_score * 0.20
        + tradeflow_score * 0.20
        + liquidity_score * 0.10
        + foreign_component * 0.10
        + structure_score * 0.10
    )

    risk_penalty = 0.0
    daily_change = _number(result.get("daily_change_pct"))
    if daily_change is not None and daily_change > 4.0:
        risk_penalty += min(8.0, (daily_change - 4.0) * 2.0)

    if result.get("aggression_trend") == "FALLING":
        risk_penalty += 8.0

    net_aggression = _number(result.get("net_aggression_value"))
    if net_aggression is not None and net_aggression < 0:
        risk_penalty += 5.0

    quality_score = _clamp(quality_before_penalty - risk_penalty)

    return {
        **absorption,
        "quality_score": round(quality_score, 1),
        "quality_momentum_score": round(momentum_score, 1),
        "quality_micro_score": round(micro_score, 1),
        "quality_tradeflow_score": round(tradeflow_score, 1),
        "quality_liquidity_score": round(liquidity_score, 1),
        "quality_foreign_score": round(foreign_component, 1),
        "quality_structure_score": round(structure_score, 1),
        "quality_risk_penalty": round(risk_penalty, 1),
    }
