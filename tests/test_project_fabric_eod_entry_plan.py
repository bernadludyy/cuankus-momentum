from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from project_fabric.eod_entry_plan import (
    build_eod_entry_plan,
    build_plan_snapshot,
)
from scanner.eod_plan_live import prioritize_reversal_watches


WIB = timezone(timedelta(hours=7))


def history_payload(*, breakout: bool) -> dict:
    start = datetime(2026, 8, 24)
    rows = []
    cursor = start
    while len(rows) < 35:
        if cursor.weekday() < 5:
            i = len(rows)
            base = 100 + i * 0.5
            rows.append(
                {
                    "Date": cursor.date().isoformat(),
                    "Open": base - 0.5,
                    "High": base + 1.0,
                    "Low": base - 1.0,
                    "Close": base,
                    "Volume": 100_000 + i * 2_000,
                }
            )
        cursor += timedelta(days=1)

    # Force final session to the EOD date used by the plan.
    prior_high = max(row["High"] for row in rows[-21:-1])
    rows[-1]["Date"] = "2026-10-12"
    if breakout:
        rows[-1].update(
            {
                "Open": prior_high,
                "High": prior_high + 4,
                "Low": prior_high - 1,
                "Close": prior_high + 2,
                "Volume": 180_000,
            }
        )
    else:
        rows[-1].update(
            {
                "Open": prior_high - 4,
                "High": prior_high - 1,
                "Low": prior_high - 5,
                "Close": prior_high - 3,
                "Volume": 140_000,
            }
        )
    return {"data": rows}


def intelligence(*, breakout: bool) -> dict:
    return {
        "breakout_readiness": {
            "score": 80.0 if not breakout else 72.0,
            "range_contraction_ratio": 0.70,
            "distance_to_prior_20d_high_pct": -1.5 if breakout else 2.5,
            "breakout_confirmed": breakout,
        },
        "entry_quality": {"score": 82.0},
        "distribution_risk": {"score": 18.0},
        "accumulation_persistence": {"score": 70.0},
    }


class EODEntryPlanTests(unittest.TestCase):
    def test_pre_breakout_plan_uses_eod_structure(self):
        plan = build_eod_entry_plan(
            "TEST",
            history_payload(breakout=False),
            intelligence(breakout=False),
            trade_date="2026-10-12",
            generated_at=datetime(2026, 10, 12, 17, 0, tzinfo=WIB),
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["setup_type"], "PRE_BREAKOUT")
        self.assertGreaterEqual(plan["eod_score"], 50)
        self.assertLess(plan["invalidation_price"], plan["entry_zone_lower"])
        self.assertLessEqual(plan["entry_zone_lower"], plan["entry_zone_upper"])
        self.assertGreater(plan["reversal_trigger"], plan["entry_zone_upper"])

    def test_breakout_plan_becomes_pullback_setup(self):
        plan = build_eod_entry_plan(
            "TEST",
            history_payload(breakout=True),
            intelligence(breakout=True),
            trade_date="2026-10-12",
            generated_at=datetime(2026, 10, 12, 17, 0, tzinfo=WIB),
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["setup_type"], "BREAKOUT_PULLBACK")
        self.assertGreater(plan["reversal_trigger"], plan["prior_20d_high"])

    def test_plan_snapshot_ranks_and_caps(self):
        plans = [
            {"ticker": "A", "eod_score": 70},
            {"ticker": "B", "eod_score": 90},
            {"ticker": "C", "eod_score": 80},
        ]
        snapshot = build_plan_snapshot(
            plans,
            trade_date="2026-10-12",
            generated_at=datetime(2026, 10, 12, 17, 0, tzinfo=WIB),
        )
        self.assertEqual(
            [item["ticker"] for item in snapshot["plans"]],
            ["B", "C", "A"],
        )
        self.assertEqual(snapshot["trade_date"], "2026-10-12")

    def test_live_engine_prioritizes_eod_zone_touch(self):
        watches = [
            {
                "ticker": "EOD1",
                "source_lane": "EOD_PRE_BREAKOUT",
                "state": "WAIT_PULLBACK",
                "pullback_lower": 98,
                "pullback_upper": 100,
                "last_score": None,
            },
            {
                "ticker": "LIVE1",
                "source_lane": "FILTER1_STRICT",
                "state": "WAIT_PULLBACK",
                "pullback_lower": 50,
                "pullback_upper": 51,
                "last_score": None,
            },
            {
                "ticker": "LIVE2",
                "source_lane": "FILTER2_RELAXED",
                "state": "PULLBACK_ACTIVE",
                "pullback_lower": 70,
                "pullback_upper": 72,
                "last_score": 75,
            },
        ]
        selected = prioritize_reversal_watches(
            watches,
            {"EOD1": 99, "LIVE1": 60, "LIVE2": 72},
            limit=2,
        )
        self.assertEqual(selected[0]["ticker"], "LIVE2")
        self.assertEqual(selected[1]["ticker"], "EOD1")


if __name__ == "__main__":
    unittest.main()
