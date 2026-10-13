from datetime import datetime
from unittest import TestCase
from unittest.mock import MagicMock, patch

from project_fabric import eod_plan_bootstrap as bootstrap


class BootstrapTests(TestCase):
    def test_bootstrap_no_longer_calls_refresh_or_needs_api_key(self):
        from pathlib import Path
        source = Path(bootstrap.__file__).read_text()
        self.assertNotIn('finalize_eod(', source)
        self.assertNotIn('ZPI_API_KEY', source)
        self.assertNotIn('refresh_eod_summary', source)

    def test_newer_archived_session_blocks_older_bootstrap(self):
        cache = MagicMock()
        connection = MagicMock()
        cur = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = ('2026-10-13',)
        with patch.object(bootstrap.psycopg, 'connect', return_value=connection):
            result = bootstrap.rebuild_cached_plans(cache, 'unused', {'trade_date': '2026-10-12'})
        self.assertEqual(result['status'], 'WAITING_FINALIZER')
        cache.set.assert_not_called()

    def test_missing_history_does_not_overwrite_plans(self):
        cache, connection = MagicMock(), MagicMock()
        cur = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = ('2026-10-12',)
        cur.fetchall.return_value = []
        with patch.object(bootstrap.psycopg, 'connect', return_value=connection):
            with self.assertRaises(RuntimeError):
                bootstrap.rebuild_cached_plans(cache, 'unused', {'trade_date': '2026-10-12'})
        cache.set.assert_not_called()
