"""Existing OHLC survives core restore/retry for every retained trading date."""
import _bootstrap
import unittest
from unittest.mock import patch

import pandas as pd

from scripts import refresh
from scripts.reconciliation import missing_prices
import refresh_worker as worker
import test_refresh_worker as harness


DAY = '2026-09-14'
CODE = '000001'
FRAME = dict(open=9.8, high=10.3, low=9.7, close=10.0,
             priceStatus='available', priceSourceProvider='tencent',
             pctChange=1.1, amplitude=6.0, dailyAdapter='tencent_daily_quote_backfill')


def incomplete(**values):
    return harness.row(CODE, status='missing', first15Volume=None, ratio=None,
                       **dict(missing_prices(), **values))


class QuotePreservationTests(unittest.TestCase):
    def test_restore_combines_incomplete_core_with_published_independent_quote(self):
        checkpoint = incomplete()
        published = dict(checkpoint, **FRAME)
        restored = worker.preserve_published_quote_frame(checkpoint, published)
        for key, value in FRAME.items():
            self.assertEqual(restored.get(key), value, key)
        for key in ('status', 'first15Volume', 'dailyVolume', 'ratio'):
            self.assertEqual(restored[key], checkpoint[key], key)

    def test_verified_suspension_does_not_retain_old_available_prices(self):
        checkpoint = incomplete()
        checkpoint.update(status='suspended', dailyVolume=0,
            noTradeEvidence=dict(kind='explicit_zero_daily_volume', provider='sina',
                                 date=DAY, symbol='sz'+CODE, volume=0))
        self.assertTrue(refresh.complete(checkpoint, DAY))
        restored = worker.preserve_published_quote_frame(checkpoint, dict(incomplete(), **FRAME))
        self.assertEqual(restored, checkpoint)

    @patch.object(refresh.collect, 'minute_unadjusted')
    @patch('scripts.sina_daily.daily_unadjusted')
    def test_legacy_saved_daily_core_retry_preserves_quote_without_price_request(self, daily, minute):
        for opening in (None, 10):
            with self.subTest(opening=opening):
                minute.return_value = (pd.DataFrame() if opening is None else
                    pd.DataFrame([dict(day=DAY+' 09:45:00', volume=opening)]))
                previous = dict(incomplete(), **FRAME)
                result = refresh.fetch((dict(code=CODE, name='fixture'), DAY, None,
                                        previous, 'catchup'))
                for key, value in FRAME.items():
                    self.assertEqual(result['row'].get(key), value, key)
                self.assertEqual(result['row']['dailyVolume'], 100)
                self.assertEqual(result['row']['status'], 'missing' if opening is None else 'ok')
        daily.assert_not_called()

    @patch.object(refresh.collect, 'minute_unadjusted', return_value=pd.DataFrame())
    @patch('scripts.sina_daily.daily_unadjusted', return_value=pd.DataFrame())
    def test_quote_survives_when_both_core_volumes_remain_missing(self, daily, minute):
        previous = dict(incomplete(), **FRAME, dailyVolume=None)
        result = refresh.fetch((dict(code=CODE, name='fixture'), DAY, None, previous, 'catchup'))
        self.assertEqual(result['row']['status'], 'missing')
        for key, value in FRAME.items():
            self.assertEqual(result['row'].get(key), value, key)

    @patch.object(refresh.collect, 'minute_unadjusted')
    @patch('scripts.sina_daily.daily_unadjusted')
    def test_core_recovery_keeps_published_price_provenance_and_percent_fields(self, daily, minute):
        previous = dict(incomplete(), **FRAME, dailyVolume=None)
        daily.return_value = pd.DataFrame([dict(date=DAY, volume=100,
            **{key: FRAME[key] for key in ('open', 'high', 'low', 'close')})])
        minute.return_value = pd.DataFrame([dict(day=DAY+' 09:45:00', volume=10)])
        result = refresh.fetch((dict(code=CODE, name='fixture'), DAY, None, previous, 'catchup'))
        self.assertEqual(result['row']['status'], 'ok')
        for key, value in FRAME.items():
            self.assertEqual(result['row'].get(key), value, key)

    @patch('scripts.sina_daily.daily_unadjusted')
    def test_new_confirmed_zero_volume_replaces_prior_available_prices(self, daily):
        previous = dict(incomplete(), **FRAME, dailyVolume=None)
        daily.return_value = pd.DataFrame([dict(date=DAY, volume=0)])
        result = refresh.fetch((dict(code=CODE, name='fixture'), DAY, 10, previous, 'catchup'))
        self.assertEqual(result['row']['status'], 'suspended')
        self.assertTrue(refresh.complete(result['row'], DAY))
        self.assertEqual(result['row']['priceStatus'], 'not_traded')
        self.assertTrue(all(result['row'][key] is None for key in ('open', 'high', 'low', 'close')))


class QuotePreservationWorkerTests(unittest.TestCase):
    reset_fixture = harness.RefreshWorkerTests.reset_fixture
    write = harness.RefreshWorkerTests.write
    read = harness.RefreshWorkerTests.read
    visible = harness.RefreshWorkerTests.visible
    run_worker = harness.RefreshWorkerTests.run_worker

    def setUp(self):
        self.reset_fixture()
        self.clock = harness.Clock('2026-09-27T07:00:00+08:00')
        self.write(DAY+'.json', dict(date=DAY, universeTotal=1,
                                    rows=[dict(incomplete(), **FRAME)]))
        self.write('checkpoint/'+CODE+'.json', dict(code=CODE, days={DAY: incomplete()}))

    def test_restore_and_successful_core_future_preserve_published_quote(self):
        def successful_core(task):
            # Restore must expose the published frame to the worker, and an
            # older core-only result must not erase it at the merge boundary.
            for key, value in FRAME.items():
                self.assertEqual(task[3].get(key), value, key)
            return harness.result(CODE, row=harness.row(CODE, **missing_prices()))
        self.assertEqual(self.run_worker(handler=successful_core, target=DAY,
                                        calendar_dates=[DAY]), 0)
        saved = self.read(DAY+'.json')['rows'][0]
        for key, value in FRAME.items():
            self.assertEqual(saved.get(key), value, key)
        self.assertEqual(saved['status'], 'ok')
        self.assertEqual(saved['first15Volume'], 10)
        self.assertEqual(saved['dailyVolume'], 100)
        self.assertEqual(saved['ratio'], 10)

    def test_missing_core_future_preserves_quote_and_remains_incomplete(self):
        result = harness.result(CODE, row=incomplete(), opening=None)
        self.assertEqual(self.run_worker(handler=lambda task: result, target=DAY,
                                        calendar_dates=[DAY]), 0)
        saved = self.read(DAY+'.json')['rows'][0]
        for key, value in FRAME.items():
            self.assertEqual(saved.get(key), value, key)
        self.assertEqual(saved['status'], 'missing')
        self.assertIsNone(saved['ratio'])
        self.assertFalse(self.visible()['dataComplete'])
        self.assertTrue(self.visible()['priceDataComplete'])


if __name__ == '__main__':
    unittest.main()
