from __future__ import annotations

import math
from typing import Any

from config import (
    ENTRY_MIN_AGGRESSIVE_BUY_RATIO,
    ENTRY_MIN_BID_PERCENT,
    ENTRY_PULLBACK_TICKS,
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def idx_tick_size(price: Any) -> float | None:
    """Return the IDX tick size relevant to Bot 4's sub-Rp500 universe."""
    parsed = _number(price)
    if parsed is None or parsed <= 0:
        return None
    if parsed < 200:
        return 1.0
    if parsed < 500:
        return 2.0
    if parsed < 2_000:
        return 5.0
    if parsed < 5_000:
        return 10.0
    return 25.0


def _round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return round(price / tick) * tick


def build_pullback_reversal_entry(result: dict[str, Any]) -> dict[str, Any] | None:
    """Build a post-alert entry plan without changing scanner eligibility.

    The plan intentionally does not auto-enter. It gives a 1-2 tick pullback
    area and asks for a reclaim of the alert price with buyer confirmation.
    """
    alert_price = _number(result.get("price"))
    tick = idx_tick_size(alert_price)
    if alert_price is None or tick is None:
        return None

    pullback_ticks = max(1, int(ENTRY_PULLBACK_TICKS))
    upper = max(tick, alert_price - tick)
    lower = max(tick, alert_price - (float(pullback_ticks) * tick))
    invalidation = max(tick, lower - tick)

    breakout_pct = _number(result.get("breakout_pct"))
    breakout_base = None
    if breakout_pct is not None and breakout_pct > -99.0:
        denominator = 1.0 + breakout_pct / 100.0
        if denominator > 0:
            breakout_base = _round_to_tick(alert_price / denominator, tick)

    aggressive_buy_ratio = _number(result.get("aggressive_buy_ratio"))
    bid_percent = _number(result.get("bid_percent"))
    net_aggression = _number(result.get("net_aggression_value"))
    trend = str(result.get("aggression_trend") or "")
    absorption = str(result.get("selling_absorption") or "NONE")

    anti_knife = bool(
        result.get("anti_falling_knife_pass")
        or result.get("anti_falling_knife_relaxed_pass")
    )
    not_overextended = bool(result.get("not_overextended", True))

    buyer_ratio_ok = (
        aggressive_buy_ratio is not None
        and aggressive_buy_ratio >= ENTRY_MIN_AGGRESSIVE_BUY_RATIO
    )
    bid_ok = bid_percent is not None and bid_percent >= ENTRY_MIN_BID_PERCENT
    net_ok = net_aggression is not None and net_aggression > 0
    trend_ok = trend in {"RISING", "FLAT"}
    trend_wait = trend in {"", "INSUFFICIENT_HISTORY", "MIXED"}

    if trend == "FALLING" or (net_aggression is not None and net_aggression < 0):
        confirmation = "AVOID / WAIT RESET"
    elif (
        buyer_ratio_ok
        and bid_ok
        and net_ok
        and trend_ok
        and anti_knife
        and not_overextended
    ):
        confirmation = "BUYER CONFIRMED"
    elif (
        buyer_ratio_ok
        and bid_ok
        and net_ok
        and trend_wait
        and anti_knife
        and not_overextended
    ):
        confirmation = "PARTIAL — WAIT TREND"
    elif absorption == "CONFIRMED" and anti_knife and not_overextended:
        confirmation = "ABSORPTION CONFIRMED"
    else:
        confirmation = "WAIT CONFIRMATION"

    return {
        "tick_size": tick,
        "alert_price": alert_price,
        "pullback_lower": min(lower, upper),
        "pullback_upper": max(lower, upper),
        "reversal_trigger": alert_price,
        "invalidation_price": invalidation,
        "breakout_base": breakout_base,
        "entry_confirmation": confirmation,
        "buyer_ratio_ok": buyer_ratio_ok,
        "bid_ok": bid_ok,
        "net_aggression_ok": net_ok,
        "trend_ok": trend_ok,
    }
