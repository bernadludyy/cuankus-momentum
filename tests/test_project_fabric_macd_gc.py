"""Offline daily MACD watch: indicators, data dates, privacy and retries."""
from datetime import date, datetime, timedelta
import unittest

from scanner.macd_gc import compute_indicators, evaluate_macd_gc, run_macd_gc
from scanner.market_hours import WIB


NOW = datetime(2026, 11, 17, 14, 30, tzinfo=WIB)


def history(closes):
    days = []
    day = NOW.date() - timedelta(days=1)
    while len(days) < len(closes):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [{"date": d.isoformat(), "open": p, "high": p + 2,
             "low": p - 2, "close": p, "volume": 10_000_000}
            for d, p in zip(reversed(days), closes)]


def fixture():
    # Alternating prices retain two-sided RSI; a modest rebound crosses MACD.
    bars = history([100 + (i % 2) * 2 for i in range(60)] + [101, 100])
    row = {"StockCode": "TEST", "Date": NOW.date().isoformat(),
           "Close": 102, "Previous": 100, "OpenPrice": 100,
           "High": 103, "Low": 99, "Volume": 15_000_000,
           "Value": 4_000_000_000}
    return row, bars


class Store:
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


class MACDTests(unittest.TestCase):
    def test_indicator_reference_and_wilder_rsi(self):
        metrics = compute_indicators([100.0] * 60 + [101.0],
                                     [10.0] * 60 + [15.0])
        self.assertAlmostEqual(metrics["previous_macd"], 0)
        self.assertAlmostEqual(metrics["previous_signal"], 0)
        self.assertAlmostEqual(metrics["macd"], 2 / 13 - 2 / 27)
        self.assertAlmostEqual(metrics["signal"], (2 / 13 - 2 / 27) / 5)
        self.assertAlmostEqual(metrics["ema20"], 100 + 2 / 21)
        self.assertEqual(metrics["rsi"], 100)
        self.assertEqual(metrics["previous_rsi"], 50)
        self.assertEqual(metrics["volume_ema20_previous"], 10)
        falling = compute_indicators([100.0] * 60 + [101.0, 100.0], [10.0] * 62)
        self.assertAlmostEqual(falling["rsi"], 100 * 13 / 27)

    def test_passes_fresh_crossover_with_daily_context(self):
        row, bars = fixture()
        result = evaluate_macd_gc(row, bars, when=NOW,
                                  last_session=date(2026, 11, 16))
        self.assertIsNotNone(result)
        self.assertLessEqual(result["previous_macd"], result["previous_signal"])
        self.assertGreater(result["macd"], result["signal"])
        self.assertGreaterEqual(result["rsi"], 40)
        self.assertLess(result["rsi"], 60)
        self.assertEqual(result["history_last_date"], "2026-11-16")

    def test_incomplete_future_duplicate_and_stale_history_fail_closed(self):
        row, bars = fixture()
        same_day = dict(bars[-1], date="2026-11-17", close=999)
        future = dict(same_day, date="2026-11-18")
        baseline = evaluate_macd_gc(row, bars, when=NOW, last_session=date(2026, 11, 16))
        self.assertEqual(baseline, evaluate_macd_gc(
            row, list(reversed(bars + [same_day, future])), when=NOW,
            last_session=date(2026, 11, 16)))
        for invalid in (bars[:-1], bars[-20:], bars + [dict(bars[-1], close=96)],
                        bars[:-1] + [dict(bars[-1], close=float("nan"))]):
            self.assertIsNone(evaluate_macd_gc(
                row, invalid, when=NOW, last_session=date(2026, 11, 16)))
        self.assertIsNone(evaluate_macd_gc(
            dict(row, Previous=96), bars, when=NOW,
            last_session=date(2026, 11, 16)))

    def test_original_boundaries_and_extra_guards(self):
        row, bars = fixture()
        changes = [dict(Value=3_000_000_000), dict(Volume=12_000_000),
                   dict(Close=100), dict(Close=106, High=106),
                   dict(Date="2026-11-16"), dict(High=110),
                   dict(OpenPrice=103), dict(Volume=float("inf"))]
        for change in changes:
            with self.subTest(change=change):
                self.assertIsNone(evaluate_macd_gc(
                    dict(row, **change), bars, when=NOW,
                    last_session=date(2026, 11, 16)))

    def test_private_daily_dedup_failure_retry_and_no_empty_alert(self):
        row, bars = fixture()
        stores = {}
        sent = []
        loaded = []
        def load(codes, before):
            loaded.append((codes, before))
            return {"last_session": date(2026, 11, 16), "histories": {"TEST": bars}}
        def factory(code):
            return stores.setdefault(code, Store())
        def send(text, *, target):
            sent.append((text, target))
            return {"ok": len(sent) > 1}
        args = dict(when=NOW, history_loader=load, sender=send, store_factory=factory)
        for _ in range(3):
            run_macd_gc({"data": [row]}, **args)
        self.assertEqual(len(sent), 2)
        self.assertTrue(all(target == "private" for _, target in sent))
        self.assertIn("SEMENTARA", sent[-1][0])
        self.assertIn("2026-11-16", sent[-1][0])
        self.assertIn("miliar", sent[-1][0])
        self.assertIn("Belum dikonfirmasi", sent[-1][0])
        loaded.clear()
        run_macd_gc({"data": [dict(row, Date="2026-11-16")]}, **args)
        self.assertEqual(loaded, [])
        self.assertEqual(len(sent), 2)

    def test_market_hours_and_existing_price_cap(self):
        row, _ = fixture()
        def forbidden(*args, **kwargs):
            self.fail("must not read history or send")
        for now, payload in [(NOW.replace(hour=12, minute=30), [row]),
                             (NOW.replace(day=21), [row]),
                             (NOW, [dict(row, Close=500, Previous=490,
                                         OpenPrice=490, High=501, Low=489)])]:
            run_macd_gc(payload, when=now, history_loader=forbidden,
                        sender=forbidden, store_factory=forbidden)

    def test_existing_bullish_macd_is_not_a_new_cross(self):
        row, bars = fixture()
        bars = history([100 + (i % 2) * 2 for i in range(60)] + [101, 102])
        self.assertIsNone(evaluate_macd_gc(
            dict(row, Previous=102, Close=103), bars,
            when=NOW, last_session=date(2026, 11, 16)))

    def test_actual_notifier_routes_only_to_private_chat(self):
        from unittest.mock import patch
        from bot import notifier
        row, bars = fixture()
        with patch.object(notifier, "TELEGRAM_CHAT_ID", "12345"), \
             patch.object(notifier, "TELEGRAM_GROUP_CHAT_ID", "-1006789"), \
             patch.object(notifier, "validate_telegram_config"), \
             patch.object(notifier, "_send_to_chat", return_value={"ok": True}) as send:
            stats = run_macd_gc([row], when=NOW,
                history_loader=lambda *a: {"last_session": date(2026, 11, 16),
                                           "histories": {"TEST": bars}},
                sender=notifier.send_telegram, store_factory=lambda code: Store())
        self.assertEqual(stats["sent"], 1)
        self.assertEqual([call.args[1] for call in send.call_args_list], ["12345"])

    def test_each_ticker_has_its_own_daily_guard_and_cycle_is_capped(self):
        row, bars = fixture()
        codes = ["TSTA", "TSTB", "TSTC", "TSTD"]
        stores = {code: Store() for code in codes}
        messages = []
        def send(text, **kwargs):
            messages.append(text)
            return {"ok": True}
        args = dict(when=NOW, sender=send, store_factory=stores.__getitem__,
                    history_loader=lambda *a: {"last_session": date(2026, 11, 16),
                        "histories": {code: bars for code in codes}})
        payload = [dict(row, StockCode=code) for code in codes]
        self.assertEqual(run_macd_gc(payload, **args)["sent"], 3)
        self.assertEqual(run_macd_gc(payload, **args)["sent"], 1)
        self.assertEqual(run_macd_gc(payload, **args)["sent"], 0)
        self.assertEqual(len(messages), 4)

    def test_sender_exception_releases_claim_for_retry(self):
        row, bars = fixture()
        store = Store()
        def error(*a, **kw):
            raise RuntimeError("offline simulated failure")
        with self.assertRaisesRegex(RuntimeError, "offline simulated"):
            run_macd_gc([row], when=NOW,
                history_loader=lambda *a: {"last_session": date(2026, 11, 16),
                                           "histories": {"TEST": bars}},
                sender=error, store_factory=lambda code: store)
        self.assertTrue(store.claim(NOW.date()))

    def test_archive_merges_latest_eod_summary_with_history(self):
        import os
        if os.getenv("PROJECT_FABRIC_INTEGRATION_TESTS") != "1":
            self.skipTest("requires isolated PostgreSQL")
        from project_fabric.market_archive import MarketArchive
        archive = MarketArchive(service="macd-offline-integration")
        archive.migrate()
        row, bars = fixture()
        archive.save_history("MGCX", bars[:-1])
        archive.save_summary([dict(row, StockCode="MGCX", Date="2026-11-16",
                                   Close=100, Volume=10_000_000)])
        snapshot = archive.completed_histories(["MGCX"], NOW.date())
        self.assertEqual(snapshot["last_session"], date(2026, 11, 16))
        self.assertEqual(len(snapshot["histories"]["MGCX"]), len(bars))
        self.assertEqual(snapshot["histories"]["MGCX"][-1]["close"], 100)
        self.assertIsNotNone(evaluate_macd_gc(
            dict(row, StockCode="MGCX"), snapshot["histories"]["MGCX"],
            when=NOW, last_session=snapshot["last_session"]))


if __name__ == "__main__":
    unittest.main()
