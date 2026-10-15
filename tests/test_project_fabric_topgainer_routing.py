"""Regression: requested private potential-only Telegram routing."""
from pathlib import Path
import unittest

import config


class TopGainerRoutingTests(unittest.TestCase):
    def test_standard_top_gainer_is_disabled(self):
        self.assertIs(config.TOP_GAINER_ENABLED, False)

    def test_private_potential_only(self):
        self.assertEqual(config.TOP_GAINER_TARGET, "private")
        scanner = (Path(__file__).resolve().parents[1] / ".github/workflows/market_scanner.yml").read_text()
        self.assertIn('TOP_GAINER_ENABLED: "false"', scanner)
        self.assertIn('TOP_GAINER_POTENTIAL_ENABLED: "true"', scanner)
        potential = (Path(__file__).resolve().parents[1] / "scanner/top_gainer_potential.py").read_text()
        self.assertIn('target="private"', potential)


if __name__ == "__main__":
    unittest.main()
