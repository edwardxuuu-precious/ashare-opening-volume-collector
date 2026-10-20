import _bootstrap
import unittest
from unittest.mock import Mock, patch
import pandas as pd
import akshare as ak
import akshare.stock.stock_zh_a_sina as provider
from scripts.sina_unadjusted import minute_unadjusted

class FastMinuteTest(unittest.TestCase):
    def response(self):
        return Mock(text='=([{ "day":"2026-09-04 09:45:00", "open":"10", "high":"11", "low":"9", "close":"10", "volume":"200", "amount":"2000" }]);')
    def test_identical_frame_and_dependency_restored(self):
        with patch.object(provider,'stock_zh_a_daily') as daily, patch.object(provider.requests,'get',return_value=self.response()):
            baseline=ak.stock_zh_a_minute(symbol='sz000001',period='15',adjust='')
            daily.assert_called_once();daily.reset_mock()
            optimized=minute_unadjusted(ak,'sz000001')
            pd.testing.assert_frame_equal(baseline,optimized)
            daily.assert_not_called();self.assertIs(provider.stock_zh_a_daily,daily)
    def test_unknown_fingerprint_uses_normal_path(self):
        with patch('scripts.sina_unadjusted.SOURCE_SHA256','different'), patch.object(provider,'stock_zh_a_daily') as daily, patch.object(provider.requests,'get',return_value=self.response()):
            minute_unadjusted(ak,'sz000001');daily.assert_called_once()
    def test_error_cannot_leave_stub_installed(self):
        with patch.object(provider,'stock_zh_a_daily') as daily, patch.object(provider.requests,'get',side_effect=RuntimeError('network')):
            with self.assertRaises(RuntimeError):minute_unadjusted(ak,'sz000001')
            self.assertIs(provider.stock_zh_a_daily,daily)
