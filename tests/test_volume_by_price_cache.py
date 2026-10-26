from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import MagicMock, patch

from project_fabric.market_archive import prepare_volume_by_price_trades
from project_fabric.universe_intelligence import WIB
from scanner import market_data


class VolumeByPriceCacheTests(unittest.TestCase):
    def test_normalizes_price_lots_value_and_stable_trade_keys(self):
        now = datetime(2026, 10, 26, 10, 0, tzinfo=WIB)
        payload = {"data": {"items": [
            {"id": "a", "time": "09:59:00", "action": "BUY", "lots": 100, "price": 67},
            {"id": "b", "time": "09:59:30", "action": "SELL", "lots": 150, "price": 68},
            {"id": "c", "time": "09:59:45", "action": "BUY", "lots": 50, "price": 69},
        ]}}

        first = prepare_volume_by_price_trades(
            "folk", payload, minimum_lot=100, asof=now
        )
        second = prepare_volume_by_price_trades(
            "folk", payload, minimum_lot=100, asof=now
        )

        self.assertEqual(len(first), 2)
        self.assertEqual([row["price"] for row in first], [67.0, 68.0])
        self.assertEqual(first[0]["trade_value"], 670_000.0)
        self.assertEqual(first[0]["ticker"], "FOLK")
        self.assertEqual(first[0]["trade_key"], second[0]["trade_key"])

    def test_identical_fallback_rows_get_distinct_keys(self):
        now = datetime(2026, 10, 26, 10, 0, tzinfo=WIB)
        row = {"time": "09:59:00", "action": "BUY", "lots": 100, "price": 67}
        prepared = prepare_volume_by_price_trades(
            "FOLK", {"items": [row, dict(row)]}, minimum_lot=100, asof=now
        )
        self.assertEqual(len(prepared), 2)
        self.assertNotEqual(prepared[0]["trade_key"], prepared[1]["trade_key"])

    def test_fetch_reuses_existing_running_trade_response(self):
        payload = {"items": [
            {"id": "a", "time": "09:59:00", "action": "BUY", "lots": 100, "price": 67}
        ]}
        archive = MagicMock()
        archive.ready = True
        archive.save_volume_by_price.return_value = {
            "new_trades": 1,
            "updated_price_levels": 1,
        }

        with patch.object(market_data, "_get", return_value=payload) as upstream, \
             patch.object(market_data, "_ARCHIVE", archive), \
             patch.object(market_data, "_VOLUME_BY_PRICE_ENABLED", True):
            result = market_data.fetch_running_trades("FOLK", min_lot=100)

        self.assertIs(result, payload)
        upstream.assert_called_once_with(
            "running-trades", {"code": "FOLK", "minLot": 100}
        )
        archive.save_volume_by_price.assert_called_once_with(
            "FOLK", payload, minimum_lot=100
        )


if __name__ == "__main__":
    unittest.main()
