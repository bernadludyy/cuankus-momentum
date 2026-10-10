from __future__ import annotations

from typing import Any


TIER_SMALL_MIN_CAP = 100_000_000_000
TIER_SMALL_MAX_CAP = 500_000_000_000
TIER_MID_MAX_CAP = 2_000_000_000_000
TIER_LARGE_MAX_CAP = 10_000_000_000_000

TIER_SMALL_MIN_VALUE_MA50 = 1_000_000_000
TIER_MID_MIN_VALUE_MA50 = 2_500_000_000
TIER_LARGE_MIN_VALUE_MA50 = 5_000_000_000


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def dynamic_liquidity_threshold(market_cap: Any) -> tuple[str, float] | None:
    """Return observational liquidity tier/threshold for the current idea."""
    cap = _number(market_cap)
    if cap is None:
        return None

    if TIER_SMALL_MIN_CAP <= cap <= TIER_SMALL_MAX_CAP:
        return ("SMALL_100B_500B", float(TIER_SMALL_MIN_VALUE_MA50))
    if TIER_SMALL_MAX_CAP < cap <= TIER_MID_MAX_CAP:
        return ("MID_500B_2T", float(TIER_MID_MIN_VALUE_MA50))
    if TIER_MID_MAX_CAP < cap < TIER_LARGE_MAX_CAP:
        return ("LARGE_2T_10T", float(TIER_LARGE_MIN_VALUE_MA50))
    return None


def build_dynamic_liquidity_observation(
    result: dict[str, Any],
    *,
    lane: str,
) -> dict[str, Any] | None:
    """Build a DB-only observation when a live candidate matches the idea.

    This function is intentionally observational. It must not affect filtering,
    ranking, phase selection or Telegram delivery.
    """
    cap = _number(result.get("market_cap"))
    value_ma50 = _number(result.get("transaction_value_ma50"))
    threshold_info = dynamic_liquidity_threshold(cap)
    if threshold_info is None or value_ma50 is None:
        return None

    tier, threshold = threshold_info
    if value_ma50 < threshold:
        return None

    turnover_ratio = value_ma50 / cap if cap and cap > 0 else None
    return {
        "lane": str(lane),
        "market_cap": cap,
        "transaction_value_ma50": value_ma50,
        "dynamic_threshold": threshold,
        "dynamic_tier": tier,
        "ma50_to_market_cap_ratio": turnover_ratio,
        "ma50_to_market_cap_pct": (
            turnover_ratio * 100.0 if turnover_ratio is not None else None
        ),
        "price": _number(result.get("price")),
        "score": _number(result.get("score")),
        "momentum_score": _number(result.get("momentum_score")),
        "leakage_score": _number(result.get("leakage_score")),
        "ignition_score": _number(result.get("ignition_score")),
        "phase": result.get("phase"),
        "volume_ma_ratio": _number(result.get("volume_ma_ratio")),
        "value_ma5": _number(result.get("value_ma")),
        "daily_change_pct": _number(result.get("daily_change_pct")),
        "interval_return_pct": _number(result.get("interval_return_pct")),
    }
