import unittest

from project_fabric.fundamental_archive import (
    normalized_fundamental_fields,
    parse_number,
    statement_period_rows,
    unwrap_zapi,
)


class FundamentalParserTests(unittest.TestCase):
    def test_parse_provider_numbers(self):
        self.assertEqual(parse_number("Rp127.23T"), 127.23e12)
        self.assertEqual(parse_number("13.61x"), 13.61)
        self.assertEqual(parse_number("20.44%"), 20.44)
        self.assertIsNone(parse_number("-"))

    def test_unwrap_and_normalize(self):
        payload = {"project": "finance:pluang", "data": {
            "stockId": 10020,
            "ratios": {
                "valuation": {"pe": "13.61x", "pb": "2.78x"},
                "profitability": {"roe": "20.44%", "npm": "45.22%"},
                "solvency": {"de": "4.6%"},
                "dividend": {"ttm": "5.61%"},
            },
            "overview": {"eps": "Rp466.74", "revenue": "Rp127.23T"},
            "earnings": [{"quarter": "Q2 '26", "actualEps": 484}],
        }}
        self.assertEqual(unwrap_zapi(payload)["stockId"], 10020)
        fields = normalized_fundamental_fields(payload)
        self.assertEqual(fields["pe"], 13.61)
        self.assertEqual(fields["roe_pct"], 20.44)
        self.assertEqual(fields["revenue"], 127.23e12)
        self.assertEqual(len(fields["earnings"]), 1)

    def test_financial_statement_flattening(self):
        payload = {"project": "finance:pluang", "data": {"quarterly": {
            "incomeStatement": {"chart": [{"timeframe": "Q2\n’26", "revenue": 10, "netProfitLoss": 3, "profitMargin": .3}]},
            "balanceSheet": {"chart": [{"timeframe": "Q2\n’26", "assets": 100, "liabilities": 70, "debtToAsset": .7}]},
            "cashFlow": {"chart": [{"timeframe": "Q2\n’26", "operating": 5, "investing": -2, "finance": -1, "netCF": 2}]},
        }}}
        rows = statement_period_rows(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timeframe"], "Q2 ’26")
        self.assertEqual(rows[0]["revenue"], 10)
        self.assertEqual(rows[0]["assets"], 100)
        self.assertEqual(rows[0]["net_cash_flow"], 2)


if __name__ == "__main__":
    unittest.main()

