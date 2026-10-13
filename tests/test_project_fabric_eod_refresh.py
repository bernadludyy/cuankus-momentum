from datetime import datetime
from unittest import TestCase
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from project_fabric import eod_finalizer as eod
from project_fabric.market_archive import MarketArchive

WIB = ZoneInfo('Asia/Jakarta')
NOW = datetime(2026, 10, 13, 17, 5, tzinfo=WIB)


def payload(day='2026-10-13'):
    return {'recordsTotal': 1, 'data': [{
        'StockCode': 'TEST', 'Date': day + 'T00:00:00',
        'OpenPrice': 100, 'High': 110, 'Low': 95, 'Close': 105,
        'Volume': 1000, 'Value': 105000, 'Frequency': 20,
    }]}


class EODRefreshTests(TestCase):
    def test_exact_day_response_valid(self):
        self.assertEqual(len(eod.validate_eod_summary(payload(), '2026-10-13')), 1)

    def test_future_history_is_not_merged_into_eod(self):
        history = {'data': [{'Date': '2026-10-14', 'Close': 999}]}
        merged = eod.merge_completed_candle(history, {'Date': '2026-10-13', 'Close': 105})
        self.assertEqual([row['Date'] for row in merged['data']], ['2026-10-13'])

    def test_project_wrapper_valid(self):
        self.assertEqual(len(eod.validate_eod_summary({'project': 'idx', 'data': payload()}, '2026-10-13')), 1)

    def test_stale_response_rejected(self):
        with self.assertRaises(eod.EODSourceNotReady):
            eod.validate_eod_summary(payload('2026-10-12'), '2026-10-13')

    def test_mixed_dates_rejected(self):
        value = payload()
        value['recordsTotal'] = 2
        value['data'].append(dict(value['data'][0], StockCode='OTHER', Date='2026-10-12'))
        with self.assertRaises(eod.EODSourceNotReady):
            eod.validate_eod_summary(value, '2026-10-13')

    def test_missing_date_rejected(self):
        value = payload()
        del value['data'][0]['Date']
        with self.assertRaises(eod.EODSourceNotReady):
            eod.validate_eod_summary(value, '2026-10-13')

    def test_incomplete_pagination_rejected(self):
        value = payload()
        value['recordsTotal'] = 963
        with self.assertRaises(eod.EODSourceNotReady):
            eod.validate_eod_summary(value, '2026-10-13')

    def test_duplicate_ticker_rejected(self):
        value = payload()
        value['recordsTotal'] = 2
        value['data'] *= 2
        with self.assertRaises(eod.EODSourceNotReady):
            eod.validate_eod_summary(value, '2026-10-13')

    def test_no_writes_after_stale_response(self):
        gateway, cache, archive = MagicMock(), MagicMock(), MagicMock()
        gateway.get_json.return_value = payload('2026-10-12')
        with patch.dict('os.environ', {'ZPI_API_KEY': 'test-only'}):
            with self.assertRaises(eod.EODSourceNotReady):
                eod.refresh_eod_summary('2026-10-13', asof=NOW, cache=cache, gateway=gateway, archive=archive)
        archive.save_summary.assert_not_called()
        cache.set.assert_not_called()

    def test_refresh_uses_exact_date_no_stale_policy_and_archives(self):
        gateway, cache, archive = MagicMock(), MagicMock(), MagicMock()
        gateway.get_json.return_value = payload()
        archive.save_summary.return_value = 1
        with patch.dict('os.environ', {'ZPI_API_KEY': 'test-only'}):
            result, count = eod.refresh_eod_summary('2026-10-13', asof=NOW, cache=cache, gateway=gateway, archive=archive)
        kwargs = gateway.get_json.call_args.kwargs
        self.assertEqual(kwargs['params']['date'], '2026-10-13')
        self.assertEqual(kwargs['policy'].ttl_seconds, 0)
        self.assertEqual(kwargs['policy'].stale_if_error_seconds, 0)
        archive.save_summary.assert_called_once_with(result, completed_through='2026-10-13', asof=NOW)
        self.assertEqual(count, 1)
        self.assertEqual(cache.set.call_count, 2)

    def test_manual_force_cannot_finalize_intraday(self):
        result = eod.finalize_eod(asof=NOW.replace(hour=15), force=True)
        self.assertEqual(result['status'], 'SKIPPED_BEFORE_EOD')

    def test_successful_retry_is_noop(self):
        cache = MagicMock()
        cache.get.return_value = {'status': 'SUCCESS', 'trade_date': '2026-10-13', 'archived_summary_rows': 963}
        with patch.object(eod, 'configured_database_url', return_value='unused'), patch.object(eod, 'PostgresTTLCache', return_value=cache), patch.object(eod, 'DataGateway') as gateway:
            result = eod.finalize_eod(asof=NOW)
        self.assertEqual(result['status'], 'ALREADY_FINALIZED')
        self.assertEqual(result['zapi_calls'], 0)
        gateway.assert_not_called()

    def test_waiting_source_does_not_publish_plans(self):
        cache, gateway = MagicMock(), MagicMock()
        cache.get.return_value = None
        gateway.usage.return_value = {'totals': {'upstream_attempts': 1}}
        with patch.object(eod, 'configured_database_url', return_value='unused'), patch.object(eod, 'PostgresTTLCache', return_value=cache), patch.object(eod, 'DataGateway', return_value=gateway), patch.object(eod, 'refresh_eod_summary', side_effect=eod.EODSourceNotReady('source=2026-10-12')):
            result = eod.finalize_eod(asof=NOW)
        self.assertEqual(result['status'], 'WAITING_SOURCE')
        self.assertEqual(cache.set.call_count, 1)
        self.assertIn('eod-status', cache.set.call_args.args[0])

    def test_eod_archive_accepts_today_only_after_cutoff_and_uses_transaction(self):
        archive = MarketArchive(database_url='unused')
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1,)
        with patch.object(archive, '_connect', return_value=connection):
            self.assertEqual(archive.save_summary(payload(), completed_through='2026-10-13', asof=NOW), 1)
        connection.__enter__.return_value.transaction.assert_called_once()
        self.assertIn('DO UPDATE', cursor.executemany.call_args.args[0])

    def test_eod_archive_does_not_accept_intraday(self):
        archive = MarketArchive(database_url='unused')
        with patch.object(archive, '_connect') as connect:
            with self.assertRaises(ValueError):
                archive.save_summary(payload(), completed_through='2026-10-13', asof=NOW.replace(hour=15))
        connect.assert_not_called()

    def test_live_archive_still_excludes_today(self):
        archive = MarketArchive(database_url='unused')
        with patch.object(archive, '_connect') as connect:
            self.assertEqual(archive.save_summary(payload(), asof=NOW), 0)
        connect.assert_not_called()

    def test_eod_archive_rejects_wrong_source_day(self):
        archive = MarketArchive(database_url='unused')
        with patch.object(archive, '_connect') as connect:
            with self.assertRaises(ValueError):
                archive.save_summary(payload('2026-10-12'), completed_through='2026-10-13', asof=NOW)
        connect.assert_not_called()
