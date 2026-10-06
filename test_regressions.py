from __future__ import annotations

from datetime import datetime

from scanner.market_hours import WIB
from scanner.foreign_score import build_foreign_metrics
from scanner.daily_filters import compute_daily_filter_metrics
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


if __name__ == "__main__":
    test_zero_is_not_missing()
    test_phase_hierarchy_prefers_pre_momentum_over_leakage()
    test_trade_age_parser()
    test_golden_pocket_from_recent_bullish_swing()
    test_foreign_value_basis()
    print("Regression tests passed.")
