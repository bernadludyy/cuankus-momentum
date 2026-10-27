from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile

from scanner.market_hours import WIB
from project_fabric.data_gateway import market_policy
from scanner.market_data import discover_active_tickers, get_stock_summary_row
from scanner.ptsn_structure import build_ptsn_structure, format_ptsn_structure
from bot import notifier
from main import load_universe
from scanner.foreign_score import build_foreign_metrics
from scanner.daily_filters import compute_daily_filter_metrics, daily_filter_is_eligible
from scanner.microstructure import (
    _gte as micro_gte,
    _trade_age_minutes,
    analyze_running_trades,
    apply_micro_confirmation,
    apply_relaxed_micro_confirmation,
    relaxed_anti_falling_knife_pass,
)
from scanner.scoring import _gte as scoring_gte
from main import (
    should_run_filter2_fallback,
    format_alert,
    format_market_cap,
    format_rupiah_compact,
    format_rupiah_signed,
    rank_top_n_candidates,
)
from scanner.tradeflow import classify_aggression_trend
from scanner.quality import build_quality_metrics, classify_selling_absorption
from scanner.liquidity_observer import (
    dynamic_liquidity_threshold,
    build_dynamic_liquidity_observation,
)
from scanner.entry_setup import (
    idx_tick_size,
    build_pullback_reversal_entry,
)
import scanner.reversal_live as reversal_live
from scanner.reversal_live import (
    extract_new_matched_trades,
    score_live_reversal,
)
from scanner.engine import select_next_primary_phase
from scanner.features import build_features
from scanner.solo_filter import assess_solo_candidate
from scanner.mtf_confirmation import (
    apply_mtf_entry_gate,
    evaluate_mtf_confirmation,
)
from config import FILTER1_TELEGRAM_TARGET
import storage.db as db


def test_telegram_supergroup_migration_retries_with_new_chat_id() -> None:
    class FakeResponse:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body

        def json(self):
            return self._body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise notifier.requests.HTTPError(
                    f"{self.status_code} test error",
                    response=self,
                )

    calls = []
    original_post = notifier.requests.post
    try:
        notifier._MIGRATED_CHAT_IDS.clear()

        def fake_post(url, data, timeout):
            chat_id = str(data["chat_id"])
            calls.append(chat_id)
            if chat_id == "-12345":
                return FakeResponse(
                    400,
                    {
                        "ok": False,
                        "error_code": 400,
                        "description": (
                            "Bad Request: group chat was upgraded "
                            "to a supergroup chat"
                        ),
                        "parameters": {
                            "migrate_to_chat_id": -1001234567890,
                        },
                    },
                )
            return FakeResponse(200, {"ok": True, "result": {}})

        notifier.requests.post = fake_post
        result = notifier._send_to_chat("test", "-12345")

        assert result["ok"] is True
        assert calls == ["-12345", "-1001234567890"]
        assert notifier._MIGRATED_CHAT_IDS["-12345"] == "-1001234567890"

        calls.clear()
        result2 = notifier._send_to_chat("test2", "-12345")
        assert result2["ok"] is True
        assert calls == ["-1001234567890"]
    finally:
        notifier.requests.post = original_post
        notifier._MIGRATED_CHAT_IDS.clear()


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


def test_tradeflow_aggressive_values_and_trend() -> None:
    now_text = datetime.now(WIB).strftime("%H:%M:%S")
    micro = analyze_running_trades(
        {
            "items": [
                {"time": now_text, "action": "BUY", "lots": 100, "price": 200},
                {"time": now_text, "action": "BUY", "lots": 50, "price": 210},
                {"time": now_text, "action": "SELL", "lots": 80, "price": 205},
            ]
        }
    )
    assert micro["aggressive_buy_value"] == 3_050_000, micro
    assert micro["aggressive_sell_value"] == 1_640_000, micro
    assert micro["net_aggression_value"] == 1_410_000, micro

    rising = classify_aggression_trend(
        [
            {"net_aggression_value": 100},
            {"net_aggression_value": 130},
            {"net_aggression_value": 170},
            {"net_aggression_value": 220},
        ],
        min_samples=3,
        flat_tolerance_pct=5,
    )
    assert rising["aggression_trend"] == "RISING", rising

    falling = classify_aggression_trend(
        [
            {"net_aggression_value": 220},
            {"net_aggression_value": 170},
            {"net_aggression_value": 130},
            {"net_aggression_value": 90},
        ],
        min_samples=3,
        flat_tolerance_pct=5,
    )
    assert falling["aggression_trend"] == "FALLING", falling


def test_pullback_reversal_entry_plan_uses_idx_ticks() -> None:
    assert idx_tick_size(33) == 1.0
    assert idx_tick_size(250) == 2.0

    beks = {
        "ticker": "BEKS",
        "price": 33,
        "breakout_pct": 3.12,
        "aggressive_buy_ratio": 0.8122,
        "bid_percent": 51.0,
        "net_aggression_value": 337_240_000,
        "aggression_trend": "INSUFFICIENT_HISTORY",
        "anti_falling_knife_relaxed_pass": True,
        "not_overextended": True,
        "selling_absorption": "NONE",
    }
    setup = build_pullback_reversal_entry(beks)
    assert setup is not None
    assert setup["pullback_lower"] == 31.0
    assert setup["pullback_upper"] == 32.0
    assert setup["reversal_trigger"] == 33.0
    assert setup["breakout_base"] == 32.0
    # Solo Mode requires bid dominance >=55%; 51% remains a watch only.
    assert setup["entry_confirmation"] == "WAIT CONFIRMATION"

    falling = dict(
        beks,
        aggression_trend="FALLING",
        net_aggression_value=-50_000_000,
    )
    setup2 = build_pullback_reversal_entry(falling)
    assert setup2 is not None
    assert setup2["entry_confirmation"] == "AVOID / WAIT RESET"


def test_telegram_alert_contains_pullback_reversal_entry_section() -> None:
    text = format_alert(
        {
            "phase": "PRE_MOMENTUM",
            "ticker": "BEKS",
            "price": 33,
            "score": 68.3,
            "quality_score": 65.2,
            "breakout_pct": 3.12,
            "aggressive_buy_ratio": 0.8122,
            "bid_percent": 51.0,
            "net_aggression_value": 337_240_000,
            "aggression_trend": "INSUFFICIENT_HISTORY",
            "anti_falling_knife_relaxed_pass": True,
            "not_overextended": True,
            "selling_absorption": "NONE",
            "filter2_relaxed": True,
            "transaction_value_ma50": 2_550_000_000,
            "value_ma": 15_790_000_000,
            "market_cap": 1_690_000_000_000,
        },
        rank=1,
    )
    assert "ENTRY REVERSAL / PULLBACK" in text
    assert "Pullback Area: Rp31–Rp32" in text
    assert "Reversal Trigger: reclaim ≥ Rp33 | Base Rp32" in text
    assert "Buyer Confirm: WAIT CONFIRMATION" in text
    assert "Invalidation: < Rp30" in text



def test_live_reversal_matched_trades_are_deduplicated() -> None:
    seen = set()
    original_save = reversal_live.save_reversal_trade_if_new
    try:
        def fake_save(**kwargs):
            key = (
                kwargs["ticker"],
                kwargs["trade_day"],
                kwargs["trade_key"],
            )
            if key in seen:
                return False
            seen.add(key)
            return True

        reversal_live.save_reversal_trade_if_new = fake_save
        payload = {
            "items": [
                {
                    "id": "t1",
                    "time": "13:31:00",
                    "action": "SELL",
                    "price": 32,
                    "lots": 100,
                },
                {
                    "id": "t2",
                    "time": "13:32:00",
                    "action": "BUY",
                    "price": 32,
                    "lots": 200,
                },
                {
                    "id": "t3",
                    "time": "13:33:00",
                    "action": "BUY",
                    "price": 33,
                    "lots": 300,
                },
            ]
        }
        first = extract_new_matched_trades(
            "BEKS",
            payload,
            touch_ts="2026-10-12T13:30:00+07:00",
        )
        second = extract_new_matched_trades(
            "BEKS",
            payload,
            touch_ts="2026-10-12T13:30:00+07:00",
        )
        assert first["new_trade_count"] == 3, first
        assert first["new_buy_value"] == 32 * 200 * 100 + 33 * 300 * 100
        assert first["new_sell_value"] == 32 * 100 * 100
        assert second["new_trade_count"] == 0, second
        assert second["new_buy_value"] == 0
        assert second["new_sell_value"] == 0
    finally:
        reversal_live.save_reversal_trade_if_new = original_save


def test_live_reversal_requires_reclaim_and_matched_buyer_takeover() -> None:
    watch = {
        "ticker": "BEKS",
        "alert_price": 33,
        "pullback_lower": 31,
        "pullback_upper": 32,
        "reversal_trigger": 33,
        "invalidation_price": 30,
        "low_seen": 31,
        "cumulative_buy_value": 0,
        "cumulative_sell_value": 60_000_000,
        "cumulative_buy_lots": 0,
        "cumulative_sell_lots": 10_000,
        "new_trade_count": 1,
        "last_delta_value": -60_000_000,
    }
    batch = {
        "new_trade_count": 5,
        "new_buy_trade_count": 4,
        "new_sell_trade_count": 1,
        "new_buy_value": 250_000_000,
        "new_sell_value": 40_000_000,
        "new_net_aggression": 210_000_000,
        "new_buy_lots": 50_000,
        "new_sell_lots": 5_000,
        "new_aggressive_buy_ratio": 50_000 / 55_000,
        "new_big_buy_ratio": 0.80,
    }
    book = {"bid_percent": 62.0}

    before_reclaim = score_live_reversal(
        watch,
        current_price=32,
        book=book,
        batch=batch,
    )
    assert before_reclaim["score"] >= 70, before_reclaim
    assert before_reclaim["status"] != "ENTRY_CONFIRMED"
    assert before_reclaim["reclaimed"] is False

    after_reclaim = score_live_reversal(
        watch,
        current_price=33,
        book=book,
        batch=batch,
    )
    assert after_reclaim["status"] == "ENTRY_CONFIRMED", after_reclaim
    assert after_reclaim["cumulative_delta"] > 0
    assert after_reclaim["delta_flip"] is True
    assert after_reclaim["selling_absorption_live"] is True
    assert after_reclaim["cumulative_buy_ratio"] >= 0.60


def test_live_reversal_rejects_weak_matched_flow_even_after_reclaim() -> None:
    watch = {
        "ticker": "TEST",
        "alert_price": 100,
        "pullback_lower": 98,
        "pullback_upper": 99,
        "reversal_trigger": 100,
        "invalidation_price": 97,
        "low_seen": 98,
        "cumulative_buy_value": 0,
        "cumulative_sell_value": 0,
        "cumulative_buy_lots": 0,
        "cumulative_sell_lots": 0,
        "new_trade_count": 0,
        "last_delta_value": -10_000_000,
    }
    weak = {
        "new_trade_count": 4,
        "new_buy_trade_count": 1,
        "new_sell_trade_count": 3,
        "new_buy_value": 20_000_000,
        "new_sell_value": 120_000_000,
        "new_net_aggression": -100_000_000,
        "new_buy_lots": 2_000,
        "new_sell_lots": 12_000,
        "new_aggressive_buy_ratio": 2_000 / 14_000,
        "new_big_buy_ratio": 0.10,
    }
    result = score_live_reversal(
        watch,
        current_price=100,
        book={"bid_percent": 40.0},
        batch=weak,
    )
    assert result["status"] != "ENTRY_CONFIRMED", result
    assert result["cumulative_delta"] < 0


def test_telegram_prefers_eod_plan_over_live_fallback() -> None:
    text = format_alert(
        {
            "phase": "PRE_MOMENTUM",
            "ticker": "TEST",
            "price": 100,
            "score": 70,
            "quality_score": 75,
            "transaction_value_ma50": 8_000_000_000,
            "eod_entry_plan": {
                "trade_date": "2026-10-12",
                "setup_type": "PRE_BREAKOUT",
                "eod_score": 78,
                "entry_zone_lower": 94,
                "entry_zone_upper": 96,
                "reversal_trigger": 102,
                "invalidation_price": 91,
            },
        }
    )
    assert "EOD ENTRY PLAN — PRE_BREAKOUT" in text
    assert "Entry Zone: Rp94–Rp96" in text
    assert "Live Trigger: reclaim ≥ Rp102" in text
    assert "LIVE FALLBACK" not in text


def test_rupiah_units_are_indonesian_and_directional_only_for_net() -> None:
    assert format_market_cap(806_680_000_000) == "Rp 806,68 Miliar"
    assert format_market_cap(1_240_000_000_000) == "Rp 1,24 Triliun"
    assert format_rupiah_compact(31_567_262_634) == "Rp 31,57 Miliar"
    assert format_rupiah_compact(50_036_696_220) == "Rp 50,04 Miliar"
    assert format_rupiah_signed(16_377_130_000) == "Rp +16,38 Miliar"
    assert format_rupiah_signed(-490_985_000) == "Rp -490,99 Juta"


def test_historical_alert_explains_missing_quality() -> None:
    text = format_alert(
        {
            "phase": "PRE_MOMENTUM",
            "ticker": "HIST",
            "price": 100,
            "score": 72,
            "quality_score": None,
            "transaction_value_ma50": 8_000_000_000,
            "value_ma": 10_000_000_000,
            "market_cap": 806_680_000_000,
        }
    )
    assert "Quality: N/A (data historis pre-V2)" in text
    assert "Market Cap: Rp 806,68 Miliar" in text
    assert "Tx Value MA50: Rp 8,00 Miliar" in text


def test_tradeflow_replaces_golden_pocket_in_telegram() -> None:
    text = format_alert(
        {
            "phase": "PRE_MOMENTUM",
            "ticker": "TEST",
            "price": 100,
            "score": 70,
            "quality_score": 78,
            "leakage_score": 60,
            "momentum_score": 70,
            "ignition_score": 65,
            "aggressive_buy_value": 2_000_000_000,
            "aggressive_sell_value": 500_000_000,
            "net_aggression_value": 1_500_000_000,
            "aggression_trend": "RISING",
            "selling_absorption": "CONFIRMED",
            "transaction_value_ma50": 8_000_000_000,
            "anti_falling_knife_pass": True,
            "not_overextended": True,
        },
        rank=1,
    )
    assert "Net Aggression" in text
    assert "Aggression Trend" in text
    assert "Selling Absorption" in text
    assert "Tx Value MA50" in text
    assert "TOP #1" in text
    assert "GOLDEN POCKET" not in text


def test_dynamic_liquidity_observer_is_tiered_and_observational() -> None:
    assert dynamic_liquidity_threshold(300_000_000_000) == (
        "SMALL_100B_500B",
        1_000_000_000.0,
    )
    assert dynamic_liquidity_threshold(1_000_000_000_000) == (
        "MID_500B_2T",
        2_500_000_000.0,
    )
    assert dynamic_liquidity_threshold(5_000_000_000_000) == (
        "LARGE_2T_10T",
        5_000_000_000.0,
    )

    matched = build_dynamic_liquidity_observation(
        {
            "ticker": "TEST",
            "market_cap": 400_000_000_000,
            "transaction_value_ma50": 1_600_000_000,
            "price": 120,
            "score": 76,
            "momentum_score": 74,
            "volume_ma_ratio": 2.4,
            "value_ma": 2_000_000_000,
        },
        lane="FILTER1_TEST",
    )
    assert matched is not None
    assert matched["dynamic_threshold"] == 1_000_000_000.0
    assert round(matched["ma50_to_market_cap_pct"], 3) == 0.4
    assert matched["lane"] == "FILTER1_TEST"

    rejected = build_dynamic_liquidity_observation(
        {
            "market_cap": 400_000_000_000,
            "transaction_value_ma50": 900_000_000,
        },
        lane="FILTER1_TEST",
    )
    assert rejected is None


def test_filter1_value_ma50_gate_and_top_n_ranking() -> None:
    eligible = {
        "enough_history": True,
        "volume_ma_ratio": 2.2,
        "value_ma": 4_000_000_000,
        "transaction_value_ma50": 6_000_000_000,
    }
    assert daily_filter_is_eligible(eligible)

    too_illiquid = dict(eligible)
    too_illiquid["transaction_value_ma50"] = 4_900_000_000
    assert not daily_filter_is_eligible(too_illiquid)

    selected, suppressed = rank_top_n_candidates(
        [
            {"ticker": "A", "quality_score": 81, "score": 90, "phase": "PRE_MOMENTUM"},
            {"ticker": "B", "quality_score": 93, "score": 80, "phase": "EARLY_IGNITION"},
            {"ticker": "C", "quality_score": 88, "score": 85, "phase": "POWERFUL_MOMENTUM"},
            {"ticker": "D", "quality_score": 70, "score": 95, "phase": "POWERFUL_MOMENTUM"},
        ],
        3,
    )
    assert [item["ticker"] for item in selected] == ["B", "C", "A"]
    assert [item["ticker"] for item in suppressed] == ["D"]


def _solo_powerful_candidate() -> dict:
    return {
        "ticker": "SOLO",
        "phase": "POWERFUL_MOMENTUM",
        "price": 200,
        "score": 86,
        "quality_score": 82,
        "micro_score": 72,
        "aggressive_buy_ratio": 0.68,
        "bid_percent": 61,
        "net_aggression_value": 180_000_000,
        "aggression_trend": "RISING",
        "volume_spike": 2.2,
        "value_spike": 1.9,
        "frequency_spike": 1.8,
        "activity_spike": 1.97,
        "position_in_range": 0.84,
        "breakout_pct": 0.65,
        "breakout_hold_snapshots": 2,
        "daily_change_pct": 2.4,
        "price_volume_context": "MENGUAT_DENGAN_VOLUME",
        "anti_falling_knife_pass": True,
        "not_overextended": True,
        "selling_absorption": "CONFIRMED",
    }


def test_filter1_solo_mode_keeps_early_internal_and_pre_as_watch() -> None:
    early = assess_solo_candidate(
        {
            "ticker": "EARLY",
            "phase": "EARLY_IGNITION",
            "price": 50,
            "anti_falling_knife_pass": True,
            "not_overextended": True,
        }
    )
    assert early["solo_status"] == "INTERNAL_EARLY_WATCH", early
    assert not early["solo_delivery_allowed"], early

    # Price 50 remains eligible for a watch: Solo Mode intentionally adds no
    # minimum-price gate.
    watch = assess_solo_candidate(
        {
            "ticker": "WATCH",
            "phase": "PRE_MOMENTUM",
            "price": 50,
            "quality_score": 70,
            "micro_score": 65,
            "aggressive_buy_ratio": 0.64,
            "activity_spike": 1.8,
            "position_in_range": 0.78,
            "anti_falling_knife_pass": True,
            "not_overextended": True,
        }
    )
    assert watch["solo_status"] == "WATCH_ONLY", watch
    assert watch["solo_delivery_allowed"], watch
    assert not watch["solo_actionable"], watch
    assert "PANTAU — BELUM ENTRY" in format_alert(watch)


def test_filter1_solo_powerful_requires_full_confirmation() -> None:
    confirmed = assess_solo_candidate(_solo_powerful_candidate())
    assert confirmed["solo_status"] == "ENTRY_READY", confirmed
    assert confirmed["solo_delivery_allowed"], confirmed
    assert confirmed["solo_actionable"], confirmed
    assert confirmed["solo_structure_risk_pct"] == 3.0, confirmed
    assert "LAYAK EVALUASI ENTRY" in format_alert(confirmed)

    weak_flow = _solo_powerful_candidate()
    weak_flow.update(
        {
            "net_aggression_value": -10_000_000,
            "aggression_trend": "FALLING",
        }
    )
    rejected = assess_solo_candidate(weak_flow)
    assert rejected["solo_status"] == "SUPPRESSED", rejected
    assert not rejected["solo_delivery_allowed"], rejected
    assert any("Net aggression" in x for x in rejected["solo_gate_failures"])
    assert any("Aggression trend" in x for x in rejected["solo_gate_failures"])


def _rising_mtf_snapshots() -> list[dict]:
    start = datetime(2026, 10, 27, 9, 0, tzinfo=WIB)
    return [
        {
            "ticker": "SOLO",
            "ts": (start + timedelta(minutes=5 * index)).isoformat(),
            "last": 100.0 + index * 0.25,
            "volume": 1_000_000 + index * 10_000,
        }
        for index in range(36)
    ]


def test_candidate_only_mtf_aligns_from_cached_snapshots() -> None:
    result = {
        "ticker": "SOLO",
        "price": 109.0,
        "daily_sma20": 100.0,
        "daily_sma20_prev": 99.0,
    }
    mtf = evaluate_mtf_confirmation(
        result,
        _rising_mtf_snapshots(),
        enabled=True,
    )
    assert mtf["mtf_status"] == "ALIGNED", mtf
    assert mtf["mtf_daily"] == "UP"
    assert mtf["mtf_1h"] == "UP"
    assert mtf["mtf_15m"] == "UP"
    assert mtf["mtf_5m"] == "TRIGGER"


def test_mtf_intraday_bars_do_not_bridge_trading_days() -> None:
    snapshots = _rising_mtf_snapshots()
    next_day = datetime(2026, 10, 28, 9, 0, tzinfo=WIB)
    snapshots.extend(
        {
            "ticker": "SOLO",
            "ts": (next_day + timedelta(minutes=5 * index)).isoformat(),
            "last": 110.0 + index * 0.25,
            "volume": 2_000_000 + index * 10_000,
        }
        for index in range(3)
    )
    mtf = evaluate_mtf_confirmation(
        {
            "ticker": "SOLO",
            "price": 111.0,
            "daily_sma20": 100.0,
            "daily_sma20_prev": 99.0,
        },
        snapshots,
        enabled=True,
    )
    assert mtf["mtf_status"] == "INSUFFICIENT", mtf
    assert mtf["mtf_5m_bars"] == 3


def test_mtf_fails_closed_and_holds_solo_entry_until_aligned() -> None:
    insufficient = evaluate_mtf_confirmation(
        {
            "ticker": "SOLO",
            "price": 200,
            "daily_sma20": 180,
            "daily_sma20_prev": 179,
        },
        _rising_mtf_snapshots()[:3],
        enabled=True,
    )
    assert insufficient["mtf_status"] == "INSUFFICIENT", insufficient

    candidate = assess_solo_candidate(_solo_powerful_candidate())
    candidate.update(insufficient)
    held = apply_mtf_entry_gate(candidate)
    assert held["solo_status"] == "MTF_WAIT", held
    assert not held["solo_delivery_allowed"], held
    assert not held["solo_actionable"], held
    assert "Data MTF belum cukup" in held["solo_gate_failures"]

    aligned = dict(candidate, mtf_status="ALIGNED")
    released = apply_mtf_entry_gate(aligned)
    assert released["solo_status"] == "ENTRY_READY", released
    assert released["solo_delivery_allowed"], released
    alert = format_alert(released)
    assert "MULTI-TIMEFRAME" in alert
    assert "MTF SELARAS" in alert


def test_filter1_solo_routing_and_ranking_are_private_action_first() -> None:
    assert FILTER1_TELEGRAM_TARGET == "private"
    watch = dict(
        _solo_powerful_candidate(),
        ticker="WATCH",
        phase="PRE_MOMENTUM",
        quality_score=95,
        solo_actionable=False,
    )
    action = dict(
        _solo_powerful_candidate(),
        ticker="ACTION",
        quality_score=80,
        solo_actionable=True,
    )
    selected, _ = rank_top_n_candidates([watch, action], 2)
    assert [item["ticker"] for item in selected] == ["ACTION", "WATCH"]


def test_filter1_breakout_must_hold_two_snapshots() -> None:
    start = datetime(2026, 10, 27, 9, 15, tzinfo=WIB)
    prices = [100, 100, 100, 100, 100, 101, 101]
    rows = []
    for index, price in enumerate(prices):
        rows.append(
            {
                "ticker": "HOLD",
                "ts": (start + timedelta(minutes=4 * index)).isoformat(),
                "last": price,
                "prev_close": 99,
                "high": 101,
                "low": 99,
                "volume": 1_000 + index * 100,
                "value": 100_000 + index * 10_000,
                "frequency": 100 + index * 10,
            }
        )
    features = build_features(rows)
    assert features is not None
    assert features["breakout_hold_snapshots"] == 2, features


def test_quality_score_and_selling_absorption() -> None:
    result = {
        "score": 85,
        "micro_score": 82,
        "aggressive_buy_ratio": 0.62,
        "aggressive_buy_value": 1_800_000_000,
        "aggressive_sell_value": 700_000_000,
        "net_aggression_value": 1_100_000_000,
        "aggression_trend": "RISING",
        "bid_percent": 63,
        "interval_return_pct": 0.05,
        "position_in_range": 0.78,
        "breakout_pct": 0.65,
        "daily_change_pct": 1.8,
        "anti_falling_knife_pass": True,
        "not_overextended": True,
        "transaction_value_ma50": 12_000_000_000,
        "volume_ma_ratio": 2.8,
        "foreign_score": 72,
    }
    absorption = classify_selling_absorption(result)
    assert absorption["selling_absorption"] == "CONFIRMED", absorption

    quality = build_quality_metrics(result)
    assert quality["selling_absorption"] == "CONFIRMED"
    assert 70 <= quality["quality_score"] <= 100, quality


def test_active_eod_watch_is_not_overwritten_by_live_fallback() -> None:
    original_path = db.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as root:
            db.DB_PATH = Path(root) / "entry-engine.db"
            db.init_db()
            db.upsert_reversal_watch(
                ticker="TEST",
                source_lane="EOD_PRE_BREAKOUT",
                source_phase="EOD_PLAN",
                target="all",
                alert_price=100,
                pullback_lower=96,
                pullback_upper=98,
                reversal_trigger=102,
                invalidation_price=94,
                alert_ts="2026-10-13T08:59:00+07:00",
            )
            db.upsert_reversal_watch(
                ticker="TEST",
                source_lane="FILTER1_STRICT",
                source_phase="PRE_MOMENTUM",
                target="all",
                alert_price=105,
                pullback_lower=103,
                pullback_upper=104,
                reversal_trigger=105,
                invalidation_price=102,
                alert_ts="2026-10-13T10:00:00+07:00",
            )
            con = db.connect()
            row = con.execute(
                "SELECT source_lane, pullback_lower, reversal_trigger "
                "FROM reversal_watches WHERE ticker='TEST'"
            ).fetchone()
            con.close()
            assert row["source_lane"] == "EOD_PRE_BREAKOUT"
            assert row["pullback_lower"] == 96
            assert row["reversal_trigger"] == 102

            db.update_reversal_watch("TEST", state="CANCELLED")
            db.upsert_reversal_watch(
                ticker="TEST",
                source_lane="FILTER1_STRICT",
                source_phase="PRE_MOMENTUM",
                target="all",
                alert_price=105,
                pullback_lower=103,
                pullback_upper=104,
                reversal_trigger=105,
                invalidation_price=102,
                alert_ts="2026-10-13T10:05:00+07:00",
            )
            con = db.connect()
            row = con.execute(
                "SELECT source_lane, pullback_lower FROM reversal_watches "
                "WHERE ticker='TEST'"
            ).fetchone()
            con.close()
            assert row["source_lane"] == "FILTER1_STRICT"
            assert row["pullback_lower"] == 103
    finally:
        db.DB_PATH = original_path


def test_filter2_legacy_reversal_watch_is_cancelled() -> None:
    original_path = db.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as root:
            db.DB_PATH = Path(root) / "filter2-retired.db"
            db.init_db()
            db.upsert_reversal_watch(
                ticker="F2OLD",
                source_lane="FILTER2_RELAXED",
                source_phase="PRE_MOMENTUM",
                target="all",
                alert_price=100,
                pullback_lower=98,
                pullback_upper=99,
                reversal_trigger=100,
                invalidation_price=97,
                alert_ts=datetime.now(WIB).isoformat(),
            )
            db.upsert_reversal_watch(
                ticker="F1KEEP",
                source_lane="FILTER1_STRICT",
                source_phase="PRE_MOMENTUM",
                target="group",
                alert_price=100,
                pullback_lower=98,
                pullback_upper=99,
                reversal_trigger=100,
                invalidation_price=97,
                alert_ts=datetime.now(WIB).isoformat(),
            )

            changed = db.cancel_filter2_reversal_watches()
            assert changed == 1

            con = db.connect()
            rows = {
                row["ticker"]: row["state"]
                for row in con.execute(
                    "SELECT ticker, state FROM reversal_watches"
                ).fetchall()
            }
            con.close()
            assert rows["F2OLD"] == "CANCELLED"
            assert rows["F1KEEP"] == "WAIT_PULLBACK"
    finally:
        db.DB_PATH = original_path


def test_daily_filter_cache_roundtrip_includes_value_ma50() -> None:
    original_path = db.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as root:
            db.DB_PATH = Path(root) / "bot4-v2.db"
            db.init_db()
            metrics = {
                "volume_ma_short": 2_000_000,
                "volume_ma_long": 800_000,
                "volume_ma_ratio": 2.5,
                "value_ma": 2_000_000_000,
                "transaction_value_ma50": 7_500_000_000,
                "history_days": 120,
                "enough_history": True,
                "swing_low": 90,
                "swing_high": 120,
                "swing_low_date": "2026-09-01",
                "swing_high_date": "2026-09-20",
                "golden_pocket_lower": 100,
                "golden_pocket_upper": 102,
                "golden_pocket_lookback": 20,
                "swing_low_5d": 100,
                "swing_high_5d": 120,
                "swing_low_date_5d": "2026-10-01",
                "swing_high_date_5d": "2026-10-05",
                "golden_pocket_lower_5d": 108,
                "golden_pocket_upper_5d": 110,
                "golden_pocket_lookback_5d": 5,
                "daily_last_close": 120,
                "daily_sma20": 110,
                "daily_sma20_prev": 109,
                "daily_trend": "UP",
            }
            db.save_daily_filter("TEST", metrics)
            cached = db.get_cached_daily_filter("TEST", max_age_hours=1)
            assert cached is not None
            assert cached["transaction_value_ma50"] == 7_500_000_000
            assert cached["daily_sma20"] == 110
            assert cached["daily_trend"] == "UP"

            con = db.connect()
            columns = {
                row["name"]
                for row in con.execute(
                    "PRAGMA table_info(daily_filter_cache)"
                ).fetchall()
            }
            con.close()
            assert "transaction_value_ma50" in columns
            assert "daily_sma20" in columns
    finally:
        db.DB_PATH = original_path


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


def test_daily_filter_exposes_completed_eod_sma20_trend() -> None:
    start = datetime(2026, 9, 1).date()
    payload = {
        "data": [
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "volume": 1_000_000,
                "value": 1_000_000_000,
                "low": 90 + index,
                "high": 92 + index,
                "close": 91 + index,
            }
            for index in range(25)
        ]
    }
    metrics = compute_daily_filter_metrics(payload)
    assert metrics["daily_last_close"] == 115
    assert metrics["daily_sma20"] is not None
    assert metrics["daily_sma20_prev"] is not None
    assert metrics["daily_trend"] == "UP", metrics


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
    # The relaxed lane is switched off, but its isolated implementation is intact.
    import main
    assert not should_run_filter2_fallback(0)
    original = main.FILTER2_ENABLED
    try:
        main.FILTER2_ENABLED = True
        assert should_run_filter2_fallback(0)
        assert not should_run_filter2_fallback(1)
        assert not should_run_filter2_fallback(2)
    finally:
        main.FILTER2_ENABLED = original



def test_shared_market_collector_snapshot() -> None:
    policy = market_policy("idx", "stock-summary")
    assert policy.ttl_seconds == 300
    assert policy.stale_if_error_seconds == 900

    payload = {
        "data": [
            {"StockCode": "AAAA", "Frequency": 100, "Value": 10, "Volume": 10},
            {"StockCode": "BBBB", "Frequency": 10, "Value": 100, "Volume": 20},
            {"StockCode": "CCCC", "Frequency": 20, "Value": 20, "Volume": 100},
        ]
    }
    tickers = discover_active_tickers(1, payload=payload)

    assert set(tickers) == {"AAAA", "BBBB", "CCCC"}
    assert get_stock_summary_row("AAAA")["Frequency"] == 100


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


def test_filter1_lifecycle_resets_on_new_trading_day() -> None:
    candidate, micro = _multi_stage_candidate()
    result = apply_micro_confirmation(candidate, micro)
    yesterday = (datetime.now(WIB) - timedelta(days=1)).isoformat()
    selected = select_next_primary_phase(
        result,
        state={
            "anchor_phase": "POWERFUL_MOMENTUM",
            "none_since": None,
            "last_seen_ts": yesterday,
        },
    )
    assert selected["phase"] == "EARLY_IGNITION", selected


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



def _ptsn_test_snapshots() -> list[dict]:
    start = datetime(2026, 10, 8, 9, 0, tzinfo=WIB)
    prices = [379, 379, 380, 378, 380, 381, 382, 382, 382, 384]
    return [
        {"ticker": "PTSN", "ts": (start + timedelta(minutes=5 * i)).isoformat(),
         "last": float(price)}
        for i, price in enumerate(prices)
    ]


def _ptsn_test_features() -> dict:
    return {
        "ticker": "PTSN", "price": 384.0,
        "daily_change_pct": 0.52, "interval_return_pct": 0.52,
        "position_in_range": 0.78,
        "volume_spike": 2.2, "value_spike": 1.7,
        "frequency_spike": 1.8,
        "anti_falling_knife_pass": True,
        "not_overextended": True,
    }


def test_ptsn_structural_watch_uses_cached_snapshots() -> None:
    watch = build_ptsn_structure(_ptsn_test_snapshots(), _ptsn_test_features())
    assert watch is not None, "Expected a base breakout watch"
    assert watch["stage"] == "WATCH", watch
    assert watch["base_high"] == 382.0, watch
    assert watch["base_low"] == 378.0, watch
    assert watch["structure_score"] >= 60, watch
    assert "PTSN STRUCTURAL MOMENTUM | PRIVATE" in format_ptsn_structure(watch)


def test_ptsn_structural_confirmed_uses_existing_micro_only() -> None:
    micro = {"micro_score": 74.0, "aggressive_buy_ratio": 0.66, "spread_pct": 0.5}
    confirmed = build_ptsn_structure(_ptsn_test_snapshots(), _ptsn_test_features(), micro)
    assert confirmed is not None
    assert confirmed["stage"] == "CONFIRMED", confirmed
    no_spike = _ptsn_test_features()
    no_spike.update({"volume_spike": 1.0, "value_spike": 1.0, "frequency_spike": 1.0})
    assert build_ptsn_structure(_ptsn_test_snapshots(), no_spike, micro) is None


def test_ptsn_structural_rejects_overnight_or_midday_gap() -> None:
    s = _ptsn_test_snapshots()
    s[5]["ts"] = (datetime(2026, 10, 8, 13, 30, tzinfo=WIB)).isoformat()
    assert build_ptsn_structure(s, _ptsn_test_features()) is None


def test_ptsn_private_routing_never_reaches_group() -> None:
    original_send = notifier._send_to_chat
    original_chat = notifier.TELEGRAM_CHAT_ID
    original_group = notifier.TELEGRAM_GROUP_CHAT_ID
    original_token = notifier.TELEGRAM_BOT_TOKEN
    original_validate = notifier.validate_telegram_config
    try:
        recipients = []
        notifier._send_to_chat = lambda message, chat_id: (recipients.append(chat_id) or {"ok": True})
        notifier.TELEGRAM_CHAT_ID = "user-private-test"
        notifier.TELEGRAM_GROUP_CHAT_ID = "group-test"
        notifier.TELEGRAM_BOT_TOKEN = "fake-test-token"
        notifier.validate_telegram_config = lambda: None  # Isolate routing from secrets
        result = notifier.send_telegram("PTSN private", target="private")
        assert result["ok"] and recipients == ["user-private-test"], recipients
        recipients.clear()
        result = notifier.send_telegram("Filter 1 strict", target="group")
        assert result["ok"] and recipients == ["group-test"], recipients
        recipients.clear()
        notifier.send_telegram("regular signal")
        assert recipients == ["user-private-test", "group-test"], recipients
        try:
            notifier.get_telegram_targets("invalid")
        except ValueError:
            pass
        else:
            assert False, "Unknown target must be rejected"
    finally:
        notifier._send_to_chat = original_send
        notifier.TELEGRAM_CHAT_ID = original_chat
        notifier.TELEGRAM_GROUP_CHAT_ID = original_group
        notifier.TELEGRAM_BOT_TOKEN = original_token
        notifier.validate_telegram_config = original_validate


def test_ptsn_added_to_static_watchlist() -> None:
    assert "PTSN" in load_universe()


if __name__ == "__main__":
    test_telegram_supergroup_migration_retries_with_new_chat_id()
    test_zero_is_not_missing()
    test_ptsn_structural_watch_uses_cached_snapshots()
    test_ptsn_structural_confirmed_uses_existing_micro_only()
    test_ptsn_structural_rejects_overnight_or_midday_gap()
    test_ptsn_private_routing_never_reaches_group()
    test_ptsn_added_to_static_watchlist()
    test_phase_hierarchy_prefers_pre_momentum_over_leakage()
    test_filter1_staged_alert_order()
    test_filter1_lifecycle_resets_on_new_trading_day()
    test_filter1_never_skips_intermediate_stage_or_fakes_ignition()
    test_filter1_leakage_is_context_not_a_separate_telegram_stage()
    test_trade_age_parser()
    test_tradeflow_aggressive_values_and_trend()
    test_pullback_reversal_entry_plan_uses_idx_ticks()
    test_telegram_alert_contains_pullback_reversal_entry_section()
    test_live_reversal_matched_trades_are_deduplicated()
    test_live_reversal_requires_reclaim_and_matched_buyer_takeover()
    test_live_reversal_rejects_weak_matched_flow_even_after_reclaim()
    test_telegram_prefers_eod_plan_over_live_fallback()
    test_rupiah_units_are_indonesian_and_directional_only_for_net()
    test_historical_alert_explains_missing_quality()
    test_tradeflow_replaces_golden_pocket_in_telegram()
    test_dynamic_liquidity_observer_is_tiered_and_observational()
    test_filter1_value_ma50_gate_and_top_n_ranking()
    test_filter1_solo_mode_keeps_early_internal_and_pre_as_watch()
    test_filter1_solo_powerful_requires_full_confirmation()
    test_candidate_only_mtf_aligns_from_cached_snapshots()
    test_mtf_intraday_bars_do_not_bridge_trading_days()
    test_mtf_fails_closed_and_holds_solo_entry_until_aligned()
    test_filter1_solo_routing_and_ranking_are_private_action_first()
    test_filter1_breakout_must_hold_two_snapshots()
    test_quality_score_and_selling_absorption()
    test_active_eod_watch_is_not_overwritten_by_live_fallback()
    test_filter2_legacy_reversal_watch_is_cancelled()
    test_daily_filter_cache_roundtrip_includes_value_ma50()
    test_golden_pocket_from_recent_bullish_swing()
    test_daily_filter_exposes_completed_eod_sma20_trend()
    test_filter2_relaxed_can_confirm_before_filter1()
    test_filter2_never_relaxes_falling_knife_gate()
    test_filter2_rejects_price_near_intraday_low()
    test_filter2_is_fallback_only()
    test_shared_market_collector_snapshot()
    test_foreign_value_basis()
    print("Regression tests passed.")
