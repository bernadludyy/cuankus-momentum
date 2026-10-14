"""Regression coverage for cache-only Top Gainer and quiet Telegram delivery."""
from datetime import date, datetime, timedelta
import os
import unittest

from scanner.top_gainer import (
    WIB,
    NeonDailyDelivery,
    format_top_gainer_report,
    rank_top_gainers,
    rank_live_scan_gainers,
    send_daily_top_gainers,
)


def summary(day=date(2026, 10, 14), n=320):
    data = []
    for i in range(n):
        data.append({
            "StockCode": f"S{i:04d}",
            "Date": f"{day.isoformat()}T00:00:00",
            "Close": 100,
            "Previous": 100,
            "Value": 100000000,
            "Volume": 100000,
            "Frequency": 200,
        })
    data[0].update({"StockCode": "SONA", "Close": 3450, "Previous": 2760, "Value": 2000000000})
    data[1].update({"StockCode": "ARTA", "Close": 2680, "Previous": 2150, "Value": 1000000000})
    data[2].update({"StockCode": "BIKE", "Close": 4150, "Previous": 3330, "Value": 500000000})
    return {"project": "finance:idx", "data": {"data": data}}


class FakeStore:
    def __init__(self):
        self.pending = set()
        self.sent = set()

    def claim(self, day):
        if day in self.pending or day in self.sent:
            return False
        self.pending.add(day)
        return True

    def complete(self, day, *, success):
        self.pending.discard(day)
        if success:
            self.sent.add(day)


class TopGainerTests(unittest.TestCase):
    def setUp(self):
        self.when = datetime(2026, 10, 14, 10, 15, tzinfo=WIB)
        self.data = summary()

    def test_formula_ranking_and_full_price_universe(self):
        ranked, coverage = rank_top_gainers(self.data, trade_date=self.when.date())
        self.assertEqual(coverage, 320)
        self.assertEqual([x["ticker"] for x in ranked], ["SONA", "ARTA", "BIKE"])
        self.assertEqual(ranked[0]["gain_pct"], 25.0)
        self.assertAlmostEqual(ranked[1]["gain_pct"], 24.6512, places=4)
        self.assertEqual(ranked[2]["last"], 4150)  # NOT limited to Rp500
        self.assertEqual(ranked[0]["previous"], 2760)
        message = format_top_gainer_report(ranked, trade_date=self.when.date(), when=self.when, coverage=coverage)
        self.assertIn("SONA | Rp3.450 | +25.00%", message)
        self.assertIn("Nilai transaksi:", message)
        self.assertIn("Snapshot scanner: 10:15 WIB", message)

    def test_stale_feed_is_rejected_even_when_nonempty(self):
        ranked, coverage = rank_top_gainers(summary(date(2026, 10, 13)), trade_date=self.when.date())
        self.assertEqual((ranked, coverage), ([], 0))
        self.assertEqual(
            send_daily_top_gainers(summary(date(2026, 10, 13)), when=self.when, store=FakeStore(),
                                   sender=lambda *_args, **_kwargs: self.fail("must not send")),
            "invalid_or_stale_summary_and_scan:coverage=0",
        )

    def test_stale_summary_uses_only_preexisting_live_quotes(self):
        stale = summary(date(2026, 10, 13))
        quotes = [{"ticker": f"S{i:04d}", "last": 102, "prev_close": 100} for i in range(40)]
        quotes[0] = {"ticker": "SONA", "last": 3520, "prev_close": None}
        ranked, coverage = rank_live_scan_gainers(
            quotes, summary_payload=stale, trade_date=self.when.date(),
        )
        self.assertEqual(coverage, 40)
        self.assertEqual(ranked[0]["ticker"], "SONA")
        self.assertIsNone(ranked[0]["value"])
        store = FakeStore()
        messages = []
        status = send_daily_top_gainers(stale, when=self.when, fallback_quotes=quotes,
                                        sender=lambda message, **kwargs: messages.append(message) or {"ok": True},
                                        store=store)
        self.assertEqual(status, "sent:10:scope=scanner")
        self.assertIn("TOP GAINER UNIVERSE SCANNER", messages[0])
        self.assertIn("BUKAN ranking seluruh BEI", messages[0])
        self.assertNotIn("Nilai transaksi:", messages[0])
        self.assertEqual(send_daily_top_gainers(stale, when=self.when, fallback_quotes=quotes,
                                                sender=lambda *_a, **_k: self.fail("repeat"),
                                                store=store), "already_sent_or_claimed")

    def test_stale_summary_with_inadequate_quotes_fails_closed(self):
        stale = summary(date(2026, 10, 13))
        status = send_daily_top_gainers(stale, when=self.when,
            fallback_quotes=[{"ticker": "SONA", "last": 3520, "prev_close": 3450}],
            sender=lambda *_a, **_k: self.fail("must not send"), store=FakeStore())
        self.assertEqual(status, "invalid_or_stale_summary_and_scan:coverage=1")

    def test_bad_prices_and_no_trade_ignored(self):
        data = summary()
        rows = data["data"]["data"]
        rows[3]["Previous"] = 0
        rows[3]["Close"] = 200
        rows[4]["Previous"] = 100
        rows[4]["Close"] = 150
        rows[4]["Volume"] = 0
        ranked, coverage = rank_top_gainers(data, trade_date=self.when.date())
        self.assertEqual(coverage, 318)
        self.assertEqual(len(ranked), 3)

    def test_once_per_day_and_market_delivery_window(self):
        store = FakeStore()
        messages = []
        def send(message, *, target):
            messages.append((message, target))
            return {"ok": True}
        self.assertEqual(
            send_daily_top_gainers(self.data, when=self.when.replace(hour=9), store=store, sender=send),
            "outside_delivery_window",
        )
        self.assertEqual(send_daily_top_gainers(self.data, when=self.when, store=store, sender=send), "sent:3:scope=idx")
        self.assertEqual(
            send_daily_top_gainers(self.data, when=self.when + timedelta(minutes=5), store=store, sender=send),
            "already_sent_or_claimed",
        )
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0][1], "private")

    def test_top_gainer_never_sends_to_group_even_if_target_all(self):
        targets = []
        result = send_daily_top_gainers(
            self.data, when=self.when, store=FakeStore(), target="all",
            sender=lambda message, *, target: targets.append(target) or {"ok": True},
        )
        self.assertEqual(result, "sent:3:scope=idx")
        self.assertEqual(targets, ["private"])

    def test_failed_delivery_is_retryable(self):
        store = FakeStore()
        status = send_daily_top_gainers(self.data, when=self.when, store=store,
                                        sender=lambda *_args, **_kwargs: {"ok": False})
        self.assertEqual(status, "telegram_failed_retryable")
        self.assertEqual(send_daily_top_gainers(self.data, when=self.when, store=store,
                                                sender=lambda *_args, **_kwargs: {"ok": True}), "sent:3:scope=idx")

    @unittest.skipUnless(os.getenv("PROJECT_FABRIC_INTEGRATION_TESTS") == "1",
                         "requires Postgres integration environment")
    def test_neon_atomic_claim_and_sent_guard(self):
        # CI migrates project_fabric.cache_entries first. Isolated future key.
        from scanner.top_gainer import NAMESPACE
        day = date(2040, 5, 21)
        first = NeonDailyDelivery(owner="topgainer-test-1")
        second = NeonDailyDelivery(owner="topgainer-test-2")
        with first._connect() as cn, cn.cursor() as cur:
            cur.execute("DELETE FROM project_fabric.cache_entries WHERE namespace=%s AND cache_key=%s",
                        (NAMESPACE, day.isoformat()))
        try:
            self.assertTrue(first.claim(day))
            self.assertFalse(second.claim(day))
            first.complete(day, success=True)
            self.assertFalse(second.claim(day))
        finally:
            with first._connect() as cn, cn.cursor() as cur:
                cur.execute("DELETE FROM project_fabric.cache_entries WHERE namespace=%s AND cache_key=%s",
                            (NAMESPACE, day.isoformat()))


if __name__ == "__main__":
    unittest.main()
