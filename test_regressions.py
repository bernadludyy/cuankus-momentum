from __future__ import annotations

from datetime import datetime

from scanner.market_hours import WIB
from scanner.foreign_score import build_foreign_metrics
from scanner.daily_filters import compute_daily_filter_metrics
from scanner.microstructure import (
    _gte as micro_gte,
    _trade_age_minutes,
    apply_micro_confirmation,
    apply_relaxed_micro_confirmation,
    relaxed_anti_falling_knife_pass,
)
from scanner.scoring import _gte as scoring_gte
from main import should_run_filter2_fallback
from scanner.engine import select_next_primary_phase


def test_zero_is_not_missing() -> None:
    assert scoring_gte(0.0, -0.30)
    assert micro_gte(0.0, -0.30)
    assert not scoring_gte(0.0, 0.30)


def test_phase_hierarchy_prefers_pre_momentum_over_leakage() -> None:
    result = {
        "ticker": "TEST",
        "leakage_score": 90.0,
        "momentum_score": 80.0,
        "activity_spike": 3.0,
        "interval_return_pct": 0.50,
        "breakout_pct": 0.0,
        "position_in_range": 0.85,
        "volume_spike": 4.0,
        "daily_change_pct": 1.0,
        "anti_falling_knife_pass": True,
        "not_overextended": True,
        "reasons": [],
    }
    micro = {
        "micro_score": 70.0,
        "aggressive_buy_ratio": 0.70,
        "bid_percent": 60.0,
        "micro_reasons": [],
    }

    output = apply_micro_confirmation(result, micro)
    assert output["phase"] == "PRE_MOMENTUM", output


def test_trade_age_parser() -> None:
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=WIB)
    assert _trade_age_minutes("09:55:00", now=now) == 5.0
    assert _trade_age_minutes("bad-time", now=now) is None


def test_golden_pocket_from_recent_bullish_swing() -> None:
    payload = {"data": []}

    for index in range(120):
        day = index + 1
        year = 2025 + (day - 1) // 365
        day_of_year = ((day - 1) % 365) + 1
        date = datetime(year, 1, 1).date()
        from datetime import timedelta
        date = date + timedelta(days=day_of_year - 1)

        low = 150.0
        high = 160.0
        if index == 110:
            low = 100.0
            high = 155.0
        elif index == 115:
            low = 170.0
            high = 200.0

        payload["data"].append(
            {
                "date": date.isoformat(),
                "volume": 1_000_000,
                "value": 1_000_000_000,
                "low": low,
                "high": high,
            }
        )

    metrics = compute_daily_filter_metrics(payload)

    assert metrics["swing_low"] == 100.0
    assert metrics["swing_high"] == 200.0
    assert round(metrics["golden_pocket_lower"], 2) == 135.00
    assert round(metrics["golden_pocket_upper"], 2) == 138.20
    assert metrics["golden_pocket_lookback"] == 20

    # The tactical pocket uses only the five most recent completed sessions.
    assert metrics["swing_low_5d"] == 150.0
    assert metrics["swing_high_5d"] == 160.0
    assert round(metrics["golden_pocket_lower_5d"], 2) == 153.50
    assert round(metrics["golden_pocket_upper_5d"], 2) == 153.82
    assert metrics["golden_pocket_lookback_5d"] == 5


def _relaxed_candidate() -> dict:
    return {
        "ticker": "RELAX",
        "leakage_score": 50.0,
        "momentum_score": 56.0,
        "activity_spike": 1.50,
        "interval_return_pct": 0.05,
        "breakout_pct": -0.20,
        "position_in_range": 0.65,
        "volume_spike": 2.50,
        "daily_change_pct": 0.50,
        "anti_falling_knife_pass": True,
        "not_overextended": True,
        "reasons": [],
    }


def _relaxed_micro() -> dict:
    return {
        "micro_score": 50.0,
        "aggressive_buy_ratio": 0.58,
        "bid_percent": 55.0,
        "micro_reasons": [],
    }


def test_filter2_relaxed_can_confirm_before_filter1() -> None:
    candidate = _relaxed_candidate()
    micro = _relaxed_micro()

    strict = apply_micro_confirmation(candidate, micro)
    relaxed = apply_relaxed_micro_confirmation(candidate, micro)

    assert strict["phase"] == "NONE", strict
    assert relaxed["phase"] == "PRE_MOMENTUM", relaxed
    assert relaxed["anti_falling_knife_relaxed_pass"] is True


def test_filter2_never_relaxes_falling_knife_gate() -> None:
    candidate = _relaxed_candidate()
    candidate["anti_falling_knife_pass"] = False
    relaxed = apply_relaxed_micro_confirmation(candidate, _relaxed_micro())

    assert not relaxed_anti_falling_knife_pass(candidate)
    assert relaxed["phase"] == "NONE", relaxed


def test_filter2_rejects_price_near_intraday_low() -> None:
    candidate = _relaxed_candidate()
    candidate["position_in_range"] = 0.20
    relaxed = apply_relaxed_micro_confirmation(candidate, _relaxed_micro())

    assert not relaxed_anti_falling_knife_pass(candidate)
    assert relaxed["phase"] == "NONE", relaxed


def test_filter2_is_fallback_only() -> None:
    assert should_run_filter2_fallback(0)
    assert not should_run_filter2_fallback(1)
    assert not should_run_filter2_fallback(2)


def test_foreign_value_basis() -> None:
    row = {
        "ForeignBuy": 2_000_000,
        "ForeignSell": 500_000,
        "Volume": 10_000_000,
        "Value": 1_000_000_000,
    }
    metrics = build_foreign_metrics(row, price=100.0)

    assert metrics["foreign_buy_value"] == 200_000_000
    assert metrics["foreign_sell_value"] == 50_000_000
    assert metrics["net_foreign_value"] == 150_000_000
    assert round(metrics["net_foreign_ratio_pct"], 2) == 15.00




def _multi_stage_candidate() -> tuple[dict, dict]:
    # Qualified for all three stages simultaneously. The earliest should win.
    return (
        {
            "ticker": "STAGED", "leakage_score": 90.0,
            "momentum_score": 96.0, "activity_spike": 4.0,
            "interval_return_pct": 0.70, "breakout_pct": 1.10,
            "position_in_range": 0.95, "volume_spike": 6.0,
            "daily_change_pct": 2.0, "anti_falling_knife_pass": True,
            "not_overextended": True, "reasons": [],
        },
        {
            "micro_score": 90.0, "aggressive_buy_ratio": 0.80,
            "bid_percent": 62.0, "micro_reasons": [],
        },
    )


def test_filter1_staged_alert_order() -> None:
    candidate, micro = _multi_stage_candidate()
    result = apply_micro_confirmation(candidate, micro)
    assert result["eligible_phases"] == [
        "EARLY_IGNITION", "PRE_MOMENTUM", "POWERFUL_MOMENTUM"
    ]
    assert result["phase"] == "EARLY_IGNITION", result
    first = select_next_primary_phase(result, state={})
    assert first["phase"] == "EARLY_IGNITION", first
    second = select_next_primary_phase(result, state={"anchor_phase": "EARLY_IGNITION"})
    assert second["phase"] == "PRE_MOMENTUM", second
    third = select_next_primary_phase(result, state={"anchor_phase": "PRE_MOMENTUM"})
    assert third["phase"] == "POWERFUL_MOMENTUM", third
    done = select_next_primary_phase(result, state={"anchor_phase": "POWERFUL_MOMENTUM"})
    assert done["phase"] == "POWERFUL_MOMENTUM", done


def test_filter1_never_skips_intermediate_stage_or_fakes_ignition() -> None:
    candidate, micro = _multi_stage_candidate()
    candidate["daily_change_pct"] = 6.0  # Ignition window already missed.
    result = apply_micro_confirmation(candidate, micro)
    assert "EARLY_IGNITION" not in result["eligible_phases"]
    assert select_next_primary_phase(result, state={})["phase"] == "PRE_MOMENTUM"
    # After a genuine ignition, the next stage must be Pre, not Powerful.
    only_powerful = dict(result)
    only_powerful["eligible_phases"] = ["POWERFUL_MOMENTUM"]
    pending = select_next_primary_phase(only_powerful, state={"anchor_phase": "EARLY_IGNITION"})
    assert pending["phase"] == "NONE" and pending["pending_stage"]


def test_filter1_leakage_is_context_not_a_separate_telegram_stage() -> None:
    candidate, micro = _multi_stage_candidate()
    candidate.update({
        "momentum_score": 10.0, "interval_return_pct": -0.1,
        "volume_spike": 1.0, "activity_spike": 1.0,
        "breakout_pct": -0.5,
    })
    result = apply_micro_confirmation(candidate, micro)
    assert result["leakage_context"]
    assert result["phase"] == "NONE", result

if __name__ == "__main__":
    test_zero_is_not_missing()
    test_phase_hierarchy_prefers_pre_momentum_over_leakage()
    test_filter1_staged_alert_order()
    test_filter1_never_skips_intermediate_stage_or_fakes_ignition()
    test_filter1_leakage_is_context_not_a_separate_telegram_stage()
    test_trade_age_parser()
    test_golden_pocket_from_recent_bullish_swing()
    test_filter2_relaxed_can_confirm_before_filter1()
    test_filter2_never_relaxes_falling_knife_gate()
    test_filter2_rejects_price_near_intraday_low()
    test_filter2_is_fallback_only()
    test_foreign_value_basis()
    print("Regression tests passed.")
