from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import os
from typing import Any

from .universe_intelligence import _history


WIB = timezone(timedelta(hours=7))
EOD_PLAN_ENDPOINT = "universe:eod-entry-plans:v1"
EOD_PLAN_PARAMS = {"scope": "IDX"}
EOD_PLAN_TTL_SECONDS = 4 * 24 * 3600

EOD_ENTRY_MIN_SCORE = float(os.getenv("EOD_ENTRY_MIN_SCORE", "50"))
EOD_ENTRY_MAX_PLANS = int(os.getenv("EOD_ENTRY_MAX_PLANS", "20"))
EOD_ENTRY_MAX_PRICE = float(os.getenv("EOD_ENTRY_MAX_PRICE", "500"))
EOD_ENTRY_MIN_MARKET_CAP_IDR = float(
    os.getenv("EOD_ENTRY_MIN_MARKET_CAP_IDR", "100000000000")
)
EOD_ENTRY_MAX_MARKET_CAP_IDR = float(
    os.getenv("EOD_ENTRY_MAX_MARKET_CAP_IDR", "10000000000000")
)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _num(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _tick_size(price: float) -> float:
    if price < 200:
        return 1.0
    if price < 500:
        return 2.0
    if price < 2_000:
        return 5.0
    if price < 5_000:
        return 10.0
    return 25.0


def _round_down(price: float, tick: float) -> float:
    return math.floor(price / tick) * tick


def _round_up(price: float, tick: float) -> float:
    return math.ceil(price / tick) * tick


def _contraction_quality(ratio: float | None) -> float:
    if ratio is None:
        return 0.0
    # 0.70x or tighter = excellent; 1.50x or wider = no squeeze credit.
    return _clamp((1.50 - ratio) / 0.80 * 100.0)


def _status(score: float) -> str:
    if score >= 80:
        return "HIGH_PRIORITY"
    if score >= 65:
        return "GOOD_SETUP"
    if score >= 50:
        return "WATCH"
    return "SKIP"


def build_eod_entry_plan(
    ticker: str,
    history_payload: Any,
    intelligence: dict[str, Any],
    *,
    trade_date: str,
    generated_at: datetime | None = None,
    summary_row: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    rows = _history(
        history_payload,
        generated_at or datetime.now(WIB),
        completed_through=trade_date,
    )
    if len(rows) < 30 or rows[-1]["date"] != trade_date:
        return None

    last = rows[-1]
    prev20 = rows[-21:-1]
    recent5 = rows[-5:]
    if len(prev20) < 20 or len(recent5) < 5:
        return None

    prior_high = max(row["high"] for row in prev20)
    support_5d = min(row["low"] for row in recent5)
    mean20 = sum(row["close"] for row in prev20) / len(prev20)

    tr_values: list[float] = []
    for current, previous in zip(rows[-14:], rows[-15:-1]):
        tr_values.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    atr14 = sum(tr_values) / len(tr_values) if tr_values else None
    if not atr14 or atr14 <= 0 or prior_high <= 0 or last["close"] <= 0:
        return None

    breakout = intelligence.get("breakout_readiness") or {}
    entry = intelligence.get("entry_quality") or {}
    distribution = intelligence.get("distribution_risk") or {}
    accumulation = intelligence.get("accumulation_persistence") or {}

    breakout_score = _num(breakout.get("score"))
    entry_score = _num(entry.get("score"))
    distribution_score = _num(distribution.get("score"))
    accumulation_score = _num(accumulation.get("score"))
    contraction = _num(breakout.get("range_contraction_ratio"))
    distance_pct = _num(breakout.get("distance_to_prior_20d_high_pct"))
    breakout_confirmed = bool(breakout.get("breakout_confirmed"))

    if None in (
        breakout_score,
        entry_score,
        distribution_score,
        contraction,
        distance_pct,
    ):
        return None

    accumulation_component = accumulation_score if accumulation_score is not None else 50.0
    contraction_score = _contraction_quality(contraction)
    eod_score = _clamp(
        0.30 * entry_score
        + 0.25 * breakout_score
        + 0.20 * (100.0 - distribution_score)
        + 0.15 * contraction_score
        + 0.10 * accumulation_component
    )
    if eod_score < EOD_ENTRY_MIN_SCORE:
        return None

    # Setup classification is deliberately separate from live confirmation.
    if breakout_confirmed:
        setup_type = "BREAKOUT_PULLBACK"
    elif 0.0 <= distance_pct <= 5.0 and contraction <= 1.20:
        setup_type = "PRE_BREAKOUT"
    elif (
        entry_score >= 60.0
        and distribution_score < 40.0
        and accumulation_component >= 50.0
    ):
        setup_type = "REVERSAL_CANDIDATE"
    else:
        return None

    close = float(last["close"])

    # EOD setup engine follows the same broad Bot 4 universe guardrails.
    if close >= EOD_ENTRY_MAX_PRICE:
        return None

    market_cap = None
    if isinstance(summary_row, dict):
        listed = None
        for key in ("ListedShares", "listedShares", "SharesOutstanding"):
            listed = _num(summary_row.get(key))
            if listed is not None:
                break
        if listed is not None and listed > 0:
            market_cap = close * listed
            if not (
                EOD_ENTRY_MIN_MARKET_CAP_IDR
                <= market_cap
                <= EOD_ENTRY_MAX_MARKET_CAP_IDR
            ):
                return None

    tick = _tick_size(close)

    if setup_type == "BREAKOUT_PULLBACK":
        anchor = prior_high
        raw_lower = max(support_5d, anchor - 0.30 * atr14)
        raw_upper = min(close, anchor + 0.15 * atr14)
        trigger = _round_up(prior_high + tick, tick)
        invalidation_raw = min(
            support_5d - tick,
            raw_lower - 0.50 * atr14,
        )
    elif setup_type == "PRE_BREAKOUT":
        anchor = max(support_5d, mean20)
        raw_lower = anchor - 0.25 * atr14
        raw_upper = min(close, anchor + 0.25 * atr14)
        trigger = _round_up(prior_high + tick, tick)
        invalidation_raw = min(
            support_5d - tick,
            raw_lower - 0.50 * atr14,
        )
    else:
        anchor = support_5d
        raw_lower = support_5d
        raw_upper = min(close, support_5d + 0.35 * atr14)
        trigger = _round_up(max(raw_upper + tick, min(close, mean20)), tick)
        invalidation_raw = support_5d - max(tick, 0.50 * atr14)

    zone_lower = max(tick, _round_down(raw_lower, tick))
    zone_upper = max(zone_lower, _round_up(raw_upper, tick))
    invalidation = max(tick, _round_down(invalidation_raw, tick))

    # Reject nonsensical plans where invalidation is not below the zone.
    if invalidation >= zone_lower:
        invalidation = max(tick, zone_lower - tick)
    if trigger <= zone_lower:
        trigger = _round_up(zone_upper + tick, tick)

    now = generated_at or datetime.now(WIB)
    return {
        "schema_version": 1,
        "ticker": ticker.strip().upper(),
        "trade_date": trade_date,
        "generated_at": now.isoformat(timespec="seconds"),
        "setup_type": setup_type,
        "eod_score": round(eod_score, 1),
        "status": _status(eod_score),
        "eod_close": close,
        "market_cap": round(market_cap, 2) if market_cap is not None else None,
        "entry_zone_lower": float(zone_lower),
        "entry_zone_upper": float(zone_upper),
        "reversal_trigger": float(trigger),
        "invalidation_price": float(invalidation),
        "prior_20d_high": round(prior_high, 4),
        "recent_support_5d": round(support_5d, 4),
        "mean20": round(mean20, 4),
        "atr14": round(atr14, 4),
        "breakout_readiness": round(breakout_score, 1),
        "entry_quality": round(entry_score, 1),
        "distribution_risk": round(distribution_score, 1),
        "accumulation_persistence": (
            round(accumulation_score, 1)
            if accumulation_score is not None
            else None
        ),
        "range_contraction_ratio": round(contraction, 3),
        "distance_to_prior_20d_high_pct": round(distance_pct, 3),
    }


def build_plan_snapshot(
    plans: list[dict[str, Any]],
    *,
    trade_date: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now = generated_at or datetime.now(WIB)
    ordered = sorted(
        plans,
        key=lambda item: (
            -float(item.get("eod_score") or 0.0),
            str(item.get("ticker") or ""),
        ),
    )[: max(1, EOD_ENTRY_MAX_PLANS)]
    return {
        "schema_version": 1,
        "trade_date": trade_date,
        "generated_at": now.isoformat(timespec="seconds"),
        "source": "completed-eod-history+stock-summary-eod",
        "count": len(ordered),
        "plans": ordered,
    }
