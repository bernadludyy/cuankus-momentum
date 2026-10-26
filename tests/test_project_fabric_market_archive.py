"""PostgreSQL integration coverage for durable history and five Universe scores."""
from __future__ import annotations
from datetime import datetime, timedelta
import os
import unittest
from project_fabric.market_archive import MarketArchive, WIB

@unittest.skipUnless(
    os.environ.get("PROJECT_FABRIC_INTEGRATION_TESTS") == "1"
    and os.environ.get("PROJECT_FABRIC_DATABASE_URL"),
    "Integration test requires isolated PostgreSQL test connection",
)
class MarketWarehouseIntegrationTests(unittest.TestCase):
    def test_completed_sessions_and_score_persist(self):
        warehouse = MarketArchive(service="warehouse-contract-test")
        warehouse.migrate()
        ticker = "ZZTST"
        base = datetime(2026, 8, 1)
        history = []
        for i in range(37):
            price = 120 + (i % 6) + i * 0.2
            history.append({
                "date": (base + timedelta(days=i)).date().isoformat(),
                "open": round(price - 0.5, 2),
                "high": round(price + 2, 2),
                "low": round(price - 2, 2),
                "close": round(price, 2),
                "volume": 50000 + i*200,
                "value": 5_000_000 + i*10_000,
                "foreignBuyValue": 1_000_000 + i*4000,
                "foreignSellValue": 400_000 + i*2000,
            })
        n = warehouse.save_history(ticker, {"items": history})
        self.assertEqual(n, 37)
        self.assertEqual(len(warehouse.history_rows(ticker, limit=120)), 37)
        same = warehouse.save_history(ticker, {"items": history})
        self.assertEqual(same, 37)
        self.assertEqual(len(warehouse.history_rows(ticker, limit=120)), 37)
        report = warehouse.compute_score(ticker)
        self.assertIsNotNone(report)
        stored = warehouse.latest_score(ticker)
        self.assertTrue(stored["archived"])
        self.assertEqual(stored["source"], "neon-market-warehouse")
        self.assertGreaterEqual(sum(
            value.get("score") is not None for value in stored["scores"].values()
        ), 4)
        self.assertEqual(
            stored["scores"]["institutional_flow"]["source"],
            "reported_foreign_value",
        )
        self.assertEqual(warehouse.save_summary({"data": [{
            "StockCode": ticker, "Date": "2026-09-20T00:00:00",
            "OpenPrice": 120, "High": 123, "Low": 118,
            "Close": 121, "Volume": 500_000, "Value": 6_000_000,
            "Frequency": 1000
        }]}), 1)
        row, session = warehouse.summary_row(ticker)
        self.assertEqual(row["StockCode"], ticker)
        self.assertEqual(session.isoformat(), "2026-09-20")

    def test_volume_by_price_is_idempotent(self):
        warehouse = MarketArchive(service="warehouse-contract-test")
        warehouse.migrate()
        now = datetime.now(WIB)
        clock = now.strftime("%H:%M:%S")
        payload = {"items": [
            {"id": "vbp-a", "time": clock, "action": "BUY", "lots": 100, "price": 67},
            {"id": "vbp-b", "time": clock, "action": "SELL", "lots": 150, "price": 68},
        ]}
        first = warehouse.save_volume_by_price(
            "ZZVBP", payload, minimum_lot=100, asof=now
        )
        second = warehouse.save_volume_by_price(
            "ZZVBP", payload, minimum_lot=100, asof=now
        )
        self.assertEqual(first["new_trades"], 2)
        self.assertEqual(second["new_trades"], 0)
        rows = warehouse.volume_by_price_rows(
            "ZZVBP", trade_date=now.date(), minimum_lot=100,
            low_price=67, high_price=69,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(float(rows[0]["buy_lots"]), 100.0)
        self.assertEqual(float(rows[1]["sell_lots"]), 150.0)

if __name__ == "__main__":
    unittest.main()
