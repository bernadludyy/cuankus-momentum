"""Private-only Top Gainer Potential: screening, entry, retry and zero extras."""
from datetime import date, datetime, timedelta
import unittest
from scanner.top_gainer import WIB
from scanner.top_gainer_potential import (
    POTENTIAL_NAMESPACE,
    idx_tick, evaluate_potential, rank_potential, format_potential_report,
    send_daily_potential,
)


class FakeStore:
    def __init__(self):
        self.sent = set()
        self.held = set()

    def claim(self, day):
        if day in self.sent or day in self.held:
            return False
        self.held.add(day)
        return True

    def complete(self, day, *, success):
        self.held.discard(day)
        if success:
            self.sent.add(day)


class PotentialTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 14, 10, 45, tzinfo=WIB)
        self.quote = {"ticker": "ABCD", "last": 103, "prev_close": 100,
                      "open": 101, "high": 104, "low": 100}
        self.features = {
            "ticker": "ABCD", "frequency_spike": 2.0, "value_spike": 2.0,
            "volume_spike": 1.6, "interval_return_pct": 0.2,
            "breakout_pct": 0.3, "position_in_range": 0.8,
            "frequency_rate": 20.0, "value_rate": 2000000.0,
        }
        prices = [101, 101, 102, 102, 103]
        self.snapshots = [
            {"ticker": "ABCD", "last": p,
             "ts": (self.now - timedelta(minutes=5 * (4 - i))).isoformat()}
            for i, p in enumerate(prices)
        ]

    def test_entry_breakout_and_score_components(self):
        result = evaluate_potential(
            self.quote, self.features, self.snapshots,
            previous_close=None, when=self.now,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["entry_trigger"], 105)
        self.assertEqual(result["invalidation"], 100)
        self.assertLess(result["risk_pct"], 5.0)
        self.assertGreater(result["reference_2r"], result["entry_trigger"])
        self.assertIn("Trigger ≥ Rp105", format_potential_report([result], when=self.now))
        self.assertIn("Invalidasi < Rp100", format_potential_report([result], when=self.now))

    def test_exclude_overextended_stale_or_unconfirmed(self):
        extreme = dict(self.quote, last=110, high=111)
        self.assertIsNone(evaluate_potential(
            extreme, self.features, self.snapshots, previous_close=100, when=self.now,
        ))
        no_freq = dict(self.features, frequency_spike=None)
        self.assertIsNone(evaluate_potential(
            self.quote, no_freq, self.snapshots, previous_close=100, when=self.now,
        ))
        stale_snapshots = [{**row, "ts": (self.now - timedelta(days=1)).isoformat()}
                           for row in self.snapshots]
        self.assertIsNone(evaluate_potential(
            self.quote, self.features, stale_snapshots, previous_close=100, when=self.now,
        ))
        falling = dict(self.features, interval_return_pct=-0.2)
        self.assertIsNone(evaluate_potential(
            self.quote, falling, self.snapshots, previous_close=100, when=self.now,
        ))

    def test_invalid_risk_becomes_watch_only(self):
        wide = [{**r, "last": 96 if i == 0 else r["last"]}
                for i, r in enumerate(self.snapshots)]
        result = evaluate_potential(
            self.quote, self.features, wide, previous_close=100, when=self.now,
        )
        self.assertIsNotNone(result)
        self.assertIsNone(result["entry_trigger"])
        self.assertIn("WATCH ONLY", result["status"])

    def test_privacy_daily_dedup_and_missing_no_alert(self):
        loader_calls = []
        def get_snapshots(ticker):
            loader_calls.append(ticker)
            return self.snapshots
        def send(message, *, target):
            self.assertEqual(target, "private")
            self.assertIn("TOP GAINER POTENTIAL", message)
            self.assertIn("WATCH, bukan sinyal buy otomatis", message)
            send.count += 1
            return {"ok": True}
        send.count = 0
        store = FakeStore()
        args = ( [self.quote], [self.features] )
        kws = dict(summary_payload=None, when=self.now, sender=send,
                   store=store, snapshot_loader=get_snapshots)
        self.assertEqual(send_daily_potential(*args, **kws), "sent:1:private")
        self.assertEqual(send_daily_potential(*args, **kws), "already_sent_or_claimed")
        self.assertEqual(send.count, 1)
        self.assertEqual(loader_calls, ["ABCD", "ABCD"])
        empty_result = send_daily_potential(
            [dict(self.quote, last=110)], [self.features], **kws
        )
        self.assertEqual(empty_result, "no_confirmed_candidates")
        self.assertEqual(send.count, 1)

    def test_only_bot4_analyzed_tickers_and_stale_summary_no_fake_activity(self):
        stale = {"data": [{"StockCode": "ABCD", "Date": "2026-10-13T00:00:00",
                           "Close": 100, "Value": 999999999, "Frequency": 4000}]}
        quote = dict(self.quote, prev_close=None)
        got = rank_potential([quote, dict(self.quote, ticker="NOTSCAN")],
                             [self.features], summary_payload=stale, when=self.now,
                             snapshot_loader=lambda _: self.snapshots)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["ticker"], "ABCD")
        self.assertNotIn("Value:", format_potential_report(got, when=self.now))

    def test_price_tick_ladder(self):
        self.assertEqual([idx_tick(p) for p in (100, 250, 750, 2500, 6000)], [1, 2, 5, 10, 25])
        self.assertEqual(POTENTIAL_NAMESPACE, "bot4-topgainer-potential-daily-v1")

if __name__ == "__main__":
    unittest.main()
