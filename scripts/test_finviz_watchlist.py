import json
import unittest
from unittest.mock import patch

import update_finviz_watchlist as watchlist


def page(first=0, total=2, ticker="TEST", filters=watchlist.FILTERS):
    props = {
        "url": f"/screener/stocks?f={filters}",
        "data": {"firstRow": first, "totalCount": total, "pageSize": 1, "rows": [{
            "text": dict(ticker=ticker, company="Test & Co", sector="Technology",
                         industry="Software", country="USA"),
            "numbers": dict(marketCap=12500, PE=32.5, price=100.25,
                            change=-1.25, volume=1234567),
        }]},
    }
    return '<script type="application/json" data-ssr-props="test">' + json.dumps(props) + '</script>'


class FinvizTests(unittest.TestCase):
    def test_structured_rows_and_units(self):
        row = watchlist.parse_rows(page())[0]
        self.assertEqual(row["market_cap"], "12.50B")
        self.assertEqual(row["change"], "-1.25%")
        self.assertEqual(row["volume"], "1,234,567")
        self.assertEqual(row["company"], "Test & Co")

    def test_dropped_filter_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "required filters"):
            watchlist.parse_rows(page(filters="fa_pe_o30"))

    @patch.object(watchlist.time, "sleep")
    def test_pagination_uses_reported_total(self, _sleep):
        with patch.object(watchlist, "fetch", side_effect=[page(), page(1, ticker="NEXT")]):
            rows = watchlist.get_rows()
        self.assertEqual([row["rank"] for row in rows], ["1", "2"])
        self.assertEqual([row["ticker"] for row in rows], ["TEST", "NEXT"])

    @patch.object(watchlist.time, "sleep")
    def test_missing_page_is_rejected(self, _sleep):
        with patch.object(watchlist, "fetch", side_effect=[page(), "no results"]):
            with self.assertRaisesRegex(RuntimeError, "Incomplete"):
                watchlist.get_rows()

    @patch.object(watchlist.time, "sleep")
    def test_repeated_page_is_rejected(self, _sleep):
        with patch.object(watchlist, "fetch", side_effect=[page(), page()]):
            with self.assertRaisesRegex(RuntimeError, "wrong page"):
                watchlist.get_rows()

    def test_legacy_short_row_does_not_crash(self):
        self.assertEqual(watchlist.parse_rows('<tr class="styled-row"><td>1</td></tr>'), [])


if __name__ == "__main__":
    unittest.main()
