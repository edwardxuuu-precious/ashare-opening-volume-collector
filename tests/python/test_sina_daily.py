import _bootstrap
from datetime import date
import unittest
from unittest.mock import Mock, patch

import akshare as ak
import akshare.stock.stock_zh_a_sina as provider
import pandas as pd
import requests

from scripts import sina_daily


class FastDailyTest(unittest.TestCase):
    def rows(self):
        return [dict(date='2026-09-07T00:00:00.000Z', open=10.126, high=11.239,
                     low=9.101, close=10.985, volume=123456, amount=1357900,
                     prevclose=10.00, postVol=0, postAmt=0)]

    def test_installed_version_matches_both_pinned_fingerprints(self):
        self.assertTrue(sina_daily.adapter_ready(ak))

    def test_identical_date_and_price_volume_subset_with_one_request(self):
        decoder = Mock()
        decoder.call.return_value = self.rows()
        capital = Mock(text='var x = [{"date":"2026-01-01","amount":"1000"}];')
        raw = Mock(text='var x="encoded";')
        with patch.object(provider.py_mini_racer, 'MiniRacer', return_value=decoder), \
                patch.object(provider.requests, 'get', side_effect=[raw, capital]) as get:
            baseline = ak.stock_zh_a_daily(symbol='sz000001', start_date='20260907', end_date='20260907', adjust='')
            self.assertEqual(get.call_count, 2)
        with patch.object(provider.py_mini_racer, 'MiniRacer', return_value=decoder), \
                patch.object(provider.requests, 'get', return_value=raw) as get:
            optimized = sina_daily.daily_unadjusted(ak, 'sz000001', '20260907', '20260907')
            get.assert_called_once_with(sina_daily.HISTORY_URL.format('sz000001'))
        pd.testing.assert_frame_equal(baseline[sina_daily.COLUMNS], optimized)
        self.assertFalse(optimized.attrs['speedDegraded'])
        self.assertEqual(optimized.loc[0, 'volume'], 123456)

    def test_fingerprint_mismatch_falls_back_and_marks_degradation(self):
        official = Mock(return_value=pd.DataFrame(self.rows()))
        with patch.object(ak, 'stock_zh_a_daily', official):
            frame = sina_daily.daily_unadjusted(ak, 'bj920002', '2026-09-07', '2026-09-07')
        official.assert_called_once_with(symbol='bj920002', start_date='20260907', end_date='20260907', adjust='')
        self.assertTrue(frame.attrs['speedDegraded'])
        self.assertEqual(frame.attrs['degradationReason'], 'pinned_source_mismatch')

    def test_changed_decoder_disables_adapter(self):
        with patch.object(provider, 'hk_js_decode', 'unexpected decoder'):
            self.assertFalse(sina_daily.adapter_ready(ak))

    def test_decode_failure_falls_back(self):
        official = Mock(return_value=pd.DataFrame(self.rows()))
        with patch.object(sina_daily, 'adapter_ready', return_value=True), \
                patch.object(ak, 'stock_zh_a_daily', official), \
                patch.object(provider.requests, 'get', return_value=Mock(text='invalid')):
            frame = sina_daily.daily_unadjusted(ak, 'sh600519', '20260907', '20260907')
        self.assertTrue(frame.attrs['speedDegraded'])
        self.assertIn('decode_or_schema_', frame.attrs['degradationReason'])
        official.assert_called_once()

    def test_network_failure_does_not_bypass_retry_or_issue_extra_requests(self):
        for error in [TimeoutError('request cutoff'), requests.Timeout('network')]:
            with self.subTest(error=type(error).__name__), \
                    patch.object(provider.requests, 'get', side_effect=error) as get:
                with self.assertRaises(type(error)):
                    sina_daily.daily_unadjusted(ak, 'sh600519', '20260907', '20260907')
                self.assertEqual(get.call_count, 1)

    def test_empty_is_not_forward_filled_and_date_filter_is_exact(self):
        decoder = Mock()
        decoder.call.return_value = self.rows()
        with patch.object(provider.py_mini_racer, 'MiniRacer', return_value=decoder), \
                patch.object(provider.requests, 'get', return_value=Mock(text='var x="encoded";')):
            frame = sina_daily.daily_unadjusted(ak, 'sz000001', '20260908', '20260908')
            self.assertTrue(frame.empty)
            self.assertEqual(list(frame.columns), sina_daily.COLUMNS)
            decoder.call.return_value = []
            frame = sina_daily.daily_unadjusted(ak, 'sz000001', '20260907', '20260907')
            self.assertTrue(frame.empty)

    def test_duplicate_and_rounding_match_pinned_branch(self):
        rows = self.rows()
        rows.append(dict(rows[0]))
        rows.append(dict(rows[0], date='2026-09-08T00:00:00.000Z'))
        decoder = Mock()
        decoder.call.return_value = rows
        with patch.object(provider.py_mini_racer, 'MiniRacer', return_value=decoder), \
                patch.object(provider.requests, 'get', return_value=Mock(text='var x="encoded";')):
            frame = sina_daily.daily_unadjusted(ak, 'sz000001', '20260907', '20260908')
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.loc[0, 'date'], date(2026, 9, 7))
        self.assertEqual(frame.loc[0, 'open'], 10.13)

    def test_invalid_window_is_rejected_without_request(self):
        with patch.object(provider.requests, 'get') as get:
            with self.assertRaises(ValueError):
                sina_daily.daily_unadjusted(ak, 'sz000001', '20260908', '20260907')
            get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
