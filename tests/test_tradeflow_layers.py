from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import main
import storage.db as db
from scanner.market_hours import WIB
from scanner.microstructure import analyze_running_trades
from scanner.tradeflow import build_tradeflow_layers, classify_aggression_trend


class TradeflowLayerTests(unittest.TestCase):
    def test_running_trades_calculates_aggression_values_in_rupiah(self):
        now_text = datetime.now(WIB).strftime("%H:%M:%S")
        payload = {
            "items": [
                {"time": now_text, "action": "BUY", "lots": 100, "price": 200},
                {"time": now_text, "action": "BUY", "lots": 50, "price": 210},
                {"time": now_text, "action": "SELL", "lots": 80, "price": 205},
            ]
        }

        result = analyze_running_trades(payload)

        self.assertEqual(result["aggressive_buy_value"], 3_050_000)
        self.assertEqual(result["aggressive_sell_value"], 1_640_000)
        self.assertEqual(result["net_aggression_value"], 1_410_000)
        self.assertEqual(result["aggression_priced_trade_count"], 3)

    def test_running_trades_prefers_direct_transaction_value(self):
        now_text = datetime.now(WIB).strftime("%H:%M:%S")
        payload = {
            "items": [
                {
                    "time": now_text,
                    "action": "BUY",
                    "lots": 100,
                    "price": 200,
                    "tradeValue": 9_000_000,
                }
            ]
        }

        result = analyze_running_trades(payload)
        self.assertEqual(result["aggressive_buy_value"], 9_000_000)
        self.assertEqual(result["net_aggression_value"], 9_000_000)

    def test_aggression_trend_rising_flat_falling_and_insufficient(self):
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
        self.assertEqual(rising["aggression_trend"], "RISING")

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
        self.assertEqual(falling["aggression_trend"], "FALLING")

        flat = classify_aggression_trend(
            [
                {"net_aggression_value": 100},
                {"net_aggression_value": 103},
                {"net_aggression_value": 101},
            ],
            min_samples=3,
            flat_tolerance_pct=8,
        )
        self.assertEqual(flat["aggression_trend"], "FLAT")

        insufficient = classify_aggression_trend(
            [{"net_aggression_value": 100}],
            min_samples=3,
        )
        self.assertEqual(
            insufficient["aggression_trend"],
            "INSUFFICIENT_HISTORY",
        )

    def test_build_layers_are_observational(self):
        micro = {
            "aggressive_buy_value": 2_000_000_000,
            "aggressive_sell_value": 500_000_000,
            "net_aggression_value": 1_500_000_000,
        }
        history = [
            {"net_aggression_value": 500_000_000},
            {"net_aggression_value": 900_000_000},
            {"net_aggression_value": 1_500_000_000},
        ]

        layers = build_tradeflow_layers(micro, history)
        self.assertEqual(layers["net_aggression_value"], 1_500_000_000)
        self.assertEqual(layers["aggression_trend"], "RISING")
        self.assertNotIn("score", layers)
        self.assertNotIn("phase", layers)

    def test_tradeflow_snapshots_persist_across_cycles(self):
        with tempfile.TemporaryDirectory() as root:
            test_db = Path(root) / "tradeflow.db"
            with patch.object(db, "DB_PATH", test_db):
                db.init_db()
                today = datetime.now(WIB).date().isoformat()
                db.save_tradeflow_snapshot(
                    "TEST",
                    {
                        "aggressive_buy_value": 1_000,
                        "aggressive_sell_value": 200,
                        "net_aggression_value": 800,
                        "aggressive_buy_ratio": 0.8,
                        "micro_score": 70,
                    },
                    price=100,
                    ts=f"{today}T10:00:00+07:00",
                )
                db.save_tradeflow_snapshot(
                    "TEST",
                    {
                        "aggressive_buy_value": 1_500,
                        "aggressive_sell_value": 300,
                        "net_aggression_value": 1_200,
                        "aggressive_buy_ratio": 0.83,
                        "micro_score": 75,
                    },
                    price=102,
                    ts=f"{today}T10:05:00+07:00",
                )
                rows = db.get_recent_tradeflow_snapshots("TEST", limit=4)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["net_aggression_value"], 1_200)

    def test_new_tradeflow_layers_are_not_in_telegram_yet(self):
        result = {
            "phase": "PRE_MOMENTUM",
            "ticker": "TEST",
            "price": 100,
            "score": 70,
            "leakage_score": 60,
            "momentum_score": 70,
            "aggressive_buy_value": 2_000_000_000,
            "aggressive_sell_value": 500_000_000,
            "net_aggression_value": 1_500_000_000,
            "aggression_trend": "RISING",
        }

        text = main.format_alert(result)

        self.assertNotIn("Net Aggression", text)
        self.assertNotIn("Aggression Trend", text)
        self.assertNotIn("Aggressive Buy Value", text)


if __name__ == "__main__":
    unittest.main()
