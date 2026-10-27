from __future__ import annotations

import math
from typing import Any

from config import (
    FILTER1_SOLO_BREAKOUT_HOLD_SNAPSHOTS,
    FILTER1_SOLO_ENTRY_ACTIVITY_MIN,
    FILTER1_SOLO_ENTRY_AGGRESSIVE_BUY_MIN,
    FILTER1_SOLO_ENTRY_BID_MIN,
    FILTER1_SOLO_ENTRY_BREAKOUT_MAX_PCT,
    FILTER1_SOLO_ENTRY_DAILY_CHANGE_MAX_PCT,
    FILTER1_SOLO_ENTRY_DAILY_CHANGE_MIN_PCT,
    FILTER1_SOLO_ENTRY_MICRO_MIN,
    FILTER1_SOLO_ENTRY_NET_AGGRESSION_MIN_IDR,
    FILTER1_SOLO_ENTRY_POSITION_MIN,
    FILTER1_SOLO_ENTRY_QUALITY_MIN,
    FILTER1_SOLO_MAX_STRUCTURE_RISK_PCT,
    FILTER1_SOLO_MODE_ENABLED,
    FILTER1_SOLO_WATCH_QUALITY_MIN,
    MIN_ACTIVITY_SPIKE,
    MIN_BREAKOUT_PCT,
    PRE_MOMENTUM_MIN_AGGRESSIVE_BUY,
    PRE_MOMENTUM_MIN_MICRO_SCORE,
    PRE_MOMENTUM_MIN_POSITION_IN_RANGE,
)
from scanner.entry_setup import build_pullback_reversal_entry


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _at_least(result: dict[str, Any], field: str, threshold: float) -> bool:
    value = _number(result.get(field))
    return value is not None and value >= float(threshold)


def _structure_risk_pct(result: dict[str, Any]) -> float | None:
    eod_plan = result.get("eod_entry_plan")
    if isinstance(eod_plan, dict):
        trigger = _number(eod_plan.get("reversal_trigger"))
        invalidation = _number(eod_plan.get("invalidation_price"))
    else:
        setup = build_pullback_reversal_entry(result)
        trigger = _number(setup.get("reversal_trigger")) if setup else None
        invalidation = _number(setup.get("invalidation_price")) if setup else None

    if trigger is None or invalidation is None or trigger <= invalidation or trigger <= 0:
        return None
    return (trigger - invalidation) / trigger * 100.0


def assess_solo_candidate(result: dict[str, Any]) -> dict[str, Any]:
    """Classify Filter 1 output for private solo-trader delivery.

    Early Ignition remains visible to the scanner lifecycle but is not sent.
    Pre-Momentum is a private watch. Powerful Momentum is actionable only when
    buyer flow, breakout persistence, price-volume context and structure risk
    all confirm. The function is pure and performs no I/O.
    """
    output = dict(result)
    phase = str(result.get("phase") or "NONE")
    risk_pct = _structure_risk_pct(result)

    if not FILTER1_SOLO_MODE_ENABLED:
        output.update(
            {
                "solo_delivery_allowed": phase != "NONE",
                "solo_actionable": phase == "POWERFUL_MOMENTUM",
                "solo_status": "LEGACY_ALERT",
                "solo_gate_failures": [],
                "solo_structure_risk_pct": risk_pct,
            }
        )
        return output

    if phase == "EARLY_IGNITION":
        output.update(
            {
                "solo_delivery_allowed": False,
                "solo_actionable": False,
                "solo_status": "INTERNAL_EARLY_WATCH",
                "solo_gate_failures": ["Early Ignition disimpan internal"],
                "solo_structure_risk_pct": risk_pct,
            }
        )
        return output

    safety_ok = bool(result.get("anti_falling_knife_pass")) and bool(
        result.get("not_overextended")
    )

    if phase == "PRE_MOMENTUM":
        checks = (
            (
                _at_least(
                    result,
                    "quality_score",
                    FILTER1_SOLO_WATCH_QUALITY_MIN,
                ),
                "Quality belum 65",
            ),
            (
                _at_least(
                    result,
                    "micro_score",
                    PRE_MOMENTUM_MIN_MICRO_SCORE,
                ),
                "Microstructure belum kuat",
            ),
            (
                _at_least(
                    result,
                    "aggressive_buy_ratio",
                    PRE_MOMENTUM_MIN_AGGRESSIVE_BUY,
                ),
                "Aggressive buy belum 60%",
            ),
            (
                _at_least(result, "activity_spike", MIN_ACTIVITY_SPIKE),
                "Aktivitas belum 1,5x",
            ),
            (
                _at_least(
                    result,
                    "position_in_range",
                    PRE_MOMENTUM_MIN_POSITION_IN_RANGE,
                ),
                "Posisi harga belum 70% range",
            ),
            (safety_ok, "Safety gate gagal"),
        )
        failures = [reason for passed, reason in checks if not passed]
        output.update(
            {
                "solo_delivery_allowed": not failures,
                "solo_actionable": False,
                "solo_status": "WATCH_ONLY" if not failures else "SUPPRESSED",
                "solo_gate_failures": failures,
                "solo_structure_risk_pct": risk_pct,
            }
        )
        return output

    if phase != "POWERFUL_MOMENTUM":
        output.update(
            {
                "solo_delivery_allowed": False,
                "solo_actionable": False,
                "solo_status": "SUPPRESSED",
                "solo_gate_failures": ["Fase belum layak dikirim"],
                "solo_structure_risk_pct": risk_pct,
            }
        )
        return output

    breakout = _number(result.get("breakout_pct"))
    daily_change = _number(result.get("daily_change_pct"))
    trend = str(result.get("aggression_trend") or "")
    checks = (
        (
            _at_least(
                result,
                "quality_score",
                FILTER1_SOLO_ENTRY_QUALITY_MIN,
            ),
            "Quality belum 75",
        ),
        (
            _at_least(result, "micro_score", FILTER1_SOLO_ENTRY_MICRO_MIN),
            "Microstructure belum 60",
        ),
        (
            _at_least(
                result,
                "aggressive_buy_ratio",
                FILTER1_SOLO_ENTRY_AGGRESSIVE_BUY_MIN,
            ),
            "Aggressive buy belum 60%",
        ),
        (
            _at_least(result, "bid_percent", FILTER1_SOLO_ENTRY_BID_MIN),
            "Bid dominance belum 55%",
        ),
        (
            _at_least(
                result,
                "net_aggression_value",
                FILTER1_SOLO_ENTRY_NET_AGGRESSION_MIN_IDR,
            ),
            "Net aggression belum Rp50 juta",
        ),
        (trend in {"RISING", "FLAT"}, "Aggression trend belum naik/stabil"),
        (
            _at_least(
                result,
                "volume_spike",
                FILTER1_SOLO_ENTRY_ACTIVITY_MIN,
            ),
            "Volume spike belum 1,5x",
        ),
        (
            _at_least(
                result,
                "value_spike",
                FILTER1_SOLO_ENTRY_ACTIVITY_MIN,
            ),
            "Value spike belum 1,5x",
        ),
        (
            _at_least(
                result,
                "frequency_spike",
                FILTER1_SOLO_ENTRY_ACTIVITY_MIN,
            ),
            "Frequency spike belum 1,5x",
        ),
        (
            _at_least(
                result,
                "position_in_range",
                FILTER1_SOLO_ENTRY_POSITION_MIN,
            ),
            "Posisi harga belum 75% range",
        ),
        (
            breakout is not None
            and MIN_BREAKOUT_PCT
            <= breakout
            <= FILTER1_SOLO_ENTRY_BREAKOUT_MAX_PCT,
            "Breakout di luar 0,30%-1,50%",
        ),
        (
            daily_change is not None
            and FILTER1_SOLO_ENTRY_DAILY_CHANGE_MIN_PCT
            <= daily_change
            <= FILTER1_SOLO_ENTRY_DAILY_CHANGE_MAX_PCT,
            "Daily change di luar 0%-6%",
        ),
        (
            int(result.get("breakout_hold_snapshots") or 0)
            >= FILTER1_SOLO_BREAKOUT_HOLD_SNAPSHOTS,
            "Breakout belum bertahan 2 snapshot",
        ),
        (
            result.get("price_volume_context") == "MENGUAT_DENGAN_VOLUME",
            "Harga-volume belum menguat",
        ),
        (
            risk_pct is not None
            and 0 < risk_pct <= FILTER1_SOLO_MAX_STRUCTURE_RISK_PCT,
            "Risiko struktur di atas 3%/tidak tersedia",
        ),
        (safety_ok, "Safety gate gagal"),
    )
    failures = [reason for passed, reason in checks if not passed]
    output.update(
        {
            "solo_delivery_allowed": not failures,
            "solo_actionable": not failures,
            "solo_status": "ENTRY_READY" if not failures else "SUPPRESSED",
            "solo_gate_failures": failures,
            "solo_structure_risk_pct": (
                round(risk_pct, 2) if risk_pct is not None else None
            ),
        }
    )
    return output
