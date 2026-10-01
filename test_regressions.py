from __future__ import annotations

from datetime import datetime

from scanner.market_hours import WIB
from scanner.microstructure import (
    _gte as micro_gte,
    _trade_age_minutes,
    apply_micro_confirmation,
)
from scanner.scoring import _gte as scoring_gte


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


if __name__ == "__main__":
    test_zero_is_not_missing()
    test_phase_hierarchy_prefers_pre_momentum_over_leakage()
    test_trade_age_parser()
    print("Regression tests passed.")
