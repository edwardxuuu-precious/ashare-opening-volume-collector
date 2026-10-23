"""Price-rule regression cases use invented data and never request market data."""
import unittest
from decimal import Decimal
from backend.chips.screen import prefilter, PRICE_FILTER_RULE
from test_chip_daily_v1 import DAYS, STOCK


class ChipPriceFilter(unittest.TestCase):
    def screen(self, price, *, witnessed=True, missing=False):
        lines = [] if missing else [f'{day},{price},{price},{price},{price},100,{Decimal(price)*10000},0,10.02,0.41,1.00' for day in DAYS]
        pool = {STOCK['code']: {'p': int(Decimal(price)*1000), 'zdp': 10.02, 'amount': float(Decimal(price)*10000)}}
        return prefilter(STOCK, lines, DAYS, {DAYS[-1]: pool} if witnessed else {})

    def test_strict_boundary_even_with_a_witnessed_limit_up(self):
        self.assertEqual(PRICE_FILTER_RULE, 'CLOSE_GT_10_CNY_V1')
        for price, expected in (('9.99', 'FAIL'), ('10.00', 'FAIL'), ('10.01', 'PASS'), ('15.00', 'PASS')):
            with self.subTest(price=price):
                row = self.screen(price)
                self.assertEqual(row['prefilter_status'], expected)
                self.assertEqual(row['recent_limit_up_dates'], [DAYS[-1]])

    def test_missing_close_stays_unknown(self):
        self.assertEqual(self.screen('15.00', missing=True)['prefilter_status'], 'UNKNOWN')
