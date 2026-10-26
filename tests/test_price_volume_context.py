import unittest
from datetime import datetime, timedelta
from scanner.features import build_features, price_volume_context
from scanner.market_hours import WIB


class PriceVolumeTests(unittest.TestCase):
    def snapshots(self):
        start = datetime(2026,10,26,9,15,tzinfo=WIB)
        return [dict(ticker='ABCD', ts=(start+timedelta(minutes=4*i)).isoformat(), last=100+i*.2, prev_close=100, high=102, low=100, volume=1000+i*100 if i<5 else 1600, value=100000+i*10000, frequency=100+i*10) for i in range(6)]

    def test_feature_pipeline_excludes_current_interval_from_baseline(self):
        f = build_features(self.snapshots())
        self.assertEqual(f['price_volume_context'], 'MENGUAT_DENGAN_VOLUME')
        self.assertAlmostEqual(f['volume_spike'], 2)
        self.assertAlmostEqual(f['price_volume_window_return_pct'], 1)
        self.assertIn('interval', f['price_volume_note'])

    def test_price_up_without_volume_is_not_confirmed(self):
        f = dict(volume_spike=.7, interval_return_pct=.4, daily_change_pct=2)
        self.assertEqual(price_volume_context(f, 1)['price_volume_context'], 'NAIK_VOLUME_LEMAH')

    def test_falling_and_flat_are_not_accumulation_claims(self):
        f = dict(volume_spike=2, interval_return_pct=-.5, daily_change_pct=-1)
        self.assertEqual(price_volume_context(f, -1)['price_volume_context'], 'TURUN_DENGAN_VOLUME')
        f['interval_return_pct'] = 0
        self.assertEqual(price_volume_context(f, 0)['price_volume_context'], 'VOLUME_TANPA_KENAIKAN')

    def test_extension_and_missing_values(self):
        f = dict(volume_spike=2, interval_return_pct=.5, daily_change_pct=2)
        self.assertEqual(price_volume_context(f, 6)['price_volume_context'], 'SUDAH_JAUH_NAIK')
        for bad in (None, float('nan'), float('inf')):
            self.assertEqual(price_volume_context(dict(f, volume_spike=bad), 1)['price_volume_context'], 'DATA_BELUM_CUKUP')

    def test_gap_reset_and_zero_baseline_are_unavailable(self):
        for kind in ('gap', 'reset', 'zero'):
            rows = self.snapshots()
            if kind == 'gap':
                rows[-1]['ts'] = (datetime.fromisoformat(rows[-2]['ts'])+timedelta(minutes=20)).isoformat()
            elif kind == 'reset':
                rows[-1]['volume'] = 10
            else:
                for row in rows[:-1]: row['volume'] = 1000
            self.assertEqual(build_features(rows)['price_volume_context'], 'DATA_BELUM_CUKUP')

    def test_alert_and_private_report_expose_context(self):
        from main import format_alert
        from scanner.top_gainer_potential import evaluate_potential, format_potential_report
        from test_project_fabric_top_gainer_potential import PotentialTests
        fixture = PotentialTests()
        fixture.setUp()
        f = dict(fixture.features, **price_volume_context(dict(fixture.features,daily_change_pct=3),1))
        candidate = evaluate_potential(fixture.quote, f, fixture.snapshots, previous_close=100, when=fixture.now)
        self.assertIn('Harga-volume: Menguat + volume', format_potential_report([candidate],when=fixture.now))
        alert = dict(f,ticker='ABCD',phase='EARLY_IGNITION',score=75)
        self.assertIn('Harga-volume: Menguat + volume', format_alert(alert,1))
