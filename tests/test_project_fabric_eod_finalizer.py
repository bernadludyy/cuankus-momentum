from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from project_fabric.eod_finalizer import (
    merge_completed_candle,
    stock_summary_candle,
)
from project_fabric.universe_intelligence import history_features


WIB = timezone(timedelta(hours=7))


def make_history(start: str = "2026-08-20", count: int = 35) -> dict:
    start_dt = datetime.fromisoformat(start)
    rows = []
    for i in range(count):
        dt = start_dt + timedelta(days=i)
        # Skip weekends so the last row remains a realistic completed session.
        if dt.weekday() >= 5:
            continue
        price = 100 + len(rows) * 0.5
        rows.append(
            {
                "Date": dt.date().isoformat(),
                "Open": price - 0.5,
                "High": price + 1.0,
                "Low": price - 1.0,
                "Close": price,
                "Volume": 100_000 + len(rows) * 1_000,
                "Value": 10_000_000 + len(rows) * 100_000,
            }
        )
    # Guarantee enough rows for feature computation and always advance
    # the calendar cursor across weekends.
    cursor = datetime.fromisoformat(rows[-1]["Date"])
    while len(rows) < 35:
        cursor += timedelta(days=1)
        if cursor.weekday() >= 5:
            continue
        price = 100 + len(rows) * 0.5
        rows.append(
            {
                "Date": cursor.date().isoformat(),
                "Open": price - 0.5,
                "High": price + 1.0,
                "Low": price - 1.0,
                "Close": price,
                "Volume": 100_000 + len(rows) * 1_000,
                "Value": 10_000_000 + len(rows) * 100_000,
            }
        )
    return {"data": rows}


class EODFinalizerTests(unittest.TestCase):
    def test_stock_summary_row_becomes_completed_daily_candle(self):
        row = {
            "StockCode": "ASPR",
            "Date": "2026-10-12T00:00:00",
            "OpenPrice": 157,
            "High": 165,
            "Low": 152,
            "Close": 154,
            "Volume": 43_714_500,
            "Value": 6_890_677_400,
        }
        candle = stock_summary_candle(row, trade_date="2026-10-12")
        self.assertIsNotNone(candle)
        self.assertEqual(candle["Date"], "2026-10-12")
        self.assertEqual(candle["Open"], 157)
        self.assertEqual(candle["High"], 165)
        self.assertEqual(candle["Low"], 152)
        self.assertEqual(candle["Close"], 154)
        self.assertEqual(candle["Volume"], 43_714_500)

    def test_wrong_day_summary_is_not_finalized(self):
        candle = stock_summary_candle(
            {
                "Date": "2026-10-09T00:00:00",
                "OpenPrice": 100,
                "High": 105,
                "Low": 99,
                "Close": 104,
                "Volume": 1000,
            },
            trade_date="2026-10-12",
        )
        self.assertIsNone(candle)

    def test_merge_replaces_same_day_and_is_idempotent(self):
        history = {
            "data": [
                {
                    "Date": "2026-10-09",
                    "Open": 100,
                    "High": 105,
                    "Low": 99,
                    "Close": 104,
                    "Volume": 1000,
                },
                {
                    "Date": "2026-10-12",
                    "Open": 105,
                    "High": 106,
                    "Low": 100,
                    "Close": 101,
                    "Volume": 2000,
                },
            ]
        }
        candle = {
            "Date": "2026-10-12",
            "Open": 105,
            "High": 110,
            "Low": 100,
            "Close": 109,
            "Volume": 5000,
        }
        once = merge_completed_candle(history, candle)
        twice = merge_completed_candle(once, candle)

        dates = [row["Date"] for row in twice["data"]]
        self.assertEqual(dates.count("2026-10-12"), 1)
        final = [row for row in twice["data"] if row["Date"] == "2026-10-12"][0]
        self.assertEqual(final["Close"], 109)
        self.assertEqual(final["Volume"], 5000)

    def test_intraday_still_excludes_today_but_eod_can_include_it(self):
        history = make_history()
        history["data"].append(
            {
                "Date": "2026-10-12",
                "Open": 120,
                "High": 125,
                "Low": 118,
                "Close": 124,
                "Volume": 900_000,
            }
        )
        asof = datetime(2026, 10, 12, 17, 0, tzinfo=WIB)

        intraday_contract = history_features("TEST", history, asof=asof)
        eod_contract = history_features(
            "TEST",
            history,
            asof=asof,
            completed_through="2026-10-12",
            source="idx:stock-history+stock-summary-eod",
        )

        self.assertNotEqual(
            intraday_contract["history_last_date"],
            "2026-10-12",
        )
        self.assertEqual(eod_contract["history_last_date"], "2026-10-12")
        self.assertEqual(
            eod_contract["source"],
            "idx:stock-history+stock-summary-eod",
        )


if __name__ == "__main__":
    unittest.main()
