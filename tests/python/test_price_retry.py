"""Exercise historical price-only repair through the real fetch and worker loop."""
import _bootstrap
from datetime import datetime
import unittest
from unittest.mock import patch

import pandas as pd

from scripts import refresh
from scripts.reconciliation import missing_prices
import refresh_worker as worker
import test_refresh_worker as harness


DAY = '2026-09-24'
CODE = '000001'


def core():
    return harness.row(CODE, pctChange=1.5, amplitude=2.5, **missing_prices())


def daily_frame(day=DAY):
    # A different downloaded volume must never replace the completed core pair.
    return pd.DataFrame([dict(date=day, volume=999, open=10, high=12, low=9, close=11)])


class PriceRetryFetchTests(unittest.TestCase):
    @patch.object(refresh.collect, 'minute_unadjusted')
    @patch('scripts.sina_daily.daily_unadjusted')
    def test_exact_day_prices_repair_without_changing_successful_core(self, daily, minute):
        previous = core()
        daily.return_value = daily_frame()
        result = refresh.fetch((dict(code=CODE, name='fixture'), DAY, 10, previous, 'catchup'))
        daily.assert_called_once_with(refresh.AK, 'sz'+CODE, '20260924', '20260924')
        minute.assert_not_called()
        self.assertEqual(result['row']['priceStatus'], 'available')
        for key in ('first15Volume', 'dailyVolume', 'ratio', 'status', 'sourceProvider'):
            self.assertEqual(result['row'][key], previous[key], key)
        self.assertEqual(result['row']['close'], 11)
        self.assertEqual(result['row']['priceSourceProvider'], 'sina')

    @patch.object(refresh.collect, 'minute_unadjusted')
    @patch('scripts.sina_daily.daily_unadjusted')
    def test_failed_or_wrong_day_price_fetch_preserves_the_completed_row(self, daily, minute):
        for value in (TimeoutError(), daily_frame('2026-09-23')):
            with self.subTest(value=type(value).__name__):
                daily.reset_mock()
                daily.side_effect = value if isinstance(value, Exception) else None
                daily.return_value = value
                previous = core()
                result = refresh.fetch((dict(code=CODE, name='fixture'), DAY, 10, previous, 'catchup'))
                daily.assert_called_once()
                self.assertEqual(result['row'], previous)
        minute.assert_not_called()

    @patch('scripts.sina_daily.daily_unadjusted')
    def test_legacy_dates_do_not_open_an_automatic_price_backfill(self, daily):
        result = refresh.fetch((dict(code=CODE, name='fixture'), '2026-09-18', 10, core(), 'catchup'))
        daily.assert_not_called()
        self.assertTrue(refresh.complete(result['row']))

    @patch('scripts.sina_daily.daily_unadjusted')
    def test_confirmed_no_trade_keeps_its_evidence_and_does_not_request_prices(self, daily):
        previous = core()
        previous.update(status='suspended', first15Volume=None, dailyVolume=0, ratio=None,
                        noTradeEvidence=dict(kind='explicit_zero_daily_volume', provider='sina',
                                             date=DAY, symbol='sz'+CODE, volume=0))
        result = refresh.fetch((dict(code=CODE, name='fixture'), DAY, None, previous, 'catchup'))
        daily.assert_not_called()
        self.assertEqual(result['row']['noTradeEvidence'], previous['noTradeEvidence'])
        self.assertEqual(result['row']['dailyVolume'], 0)
        self.assertIsNone(result['row']['ratio'])
        self.assertEqual(result['row']['priceStatus'], 'not_traded')

    @patch.object(refresh.collect, 'minute_unadjusted', return_value=pd.DataFrame())
    @patch('scripts.sina_daily.daily_unadjusted')
    def test_saved_daily_volume_does_not_block_prices_when_opening_is_missing(self, daily, minute):
        previous = core()
        previous.update(status='missing', first15Volume=None, ratio=None)
        daily.return_value = daily_frame()
        result = refresh.fetch((dict(code=CODE, name='fixture'), DAY, None, previous, 'catchup'))
        daily.assert_called_once()
        self.assertEqual(result['row']['priceStatus'], 'available')
        self.assertEqual(result['row']['dailyVolume'], 100)
        self.assertEqual(result['row']['status'], 'missing')
        self.assertIsNone(result['row']['ratio'])
        self.assertIsNone(result['row']['first15Volume'])
        # Another missing opening response cannot erase the repaired prices.
        daily.reset_mock()
        retried = refresh.fetch((dict(code=CODE, name='fixture'), DAY, None, result['row'], 'catchup'))
        daily.assert_not_called()
        for key in ('open', 'high', 'low', 'close', 'priceStatus', 'priceSourceProvider'):
            self.assertEqual(retried['row'][key], result['row'][key], key)


class PriceRetryWorkerTests(unittest.TestCase):
    # Reuse only the established fixture harness, without duplicating its tests.
    reset_fixture = harness.RefreshWorkerTests.reset_fixture
    write = harness.RefreshWorkerTests.write
    read = harness.RefreshWorkerTests.read
    visible = harness.RefreshWorkerTests.visible
    run_worker = harness.RefreshWorkerTests.run_worker

    def setUp(self):
        self.reset_fixture()
        self.clock = harness.Clock('2026-09-27T07:00:00+08:00')
        self.write(DAY+'.json', dict(date=DAY, universeTotal=1, rows=[core()]))

    @patch.object(refresh.collect, 'minute_unadjusted')
    @patch('scripts.sina_daily.daily_unadjusted')
    def test_worker_reopens_core_complete_history_and_publishes_prices(self, daily, minute):
        daily.return_value = daily_frame()
        self.assertEqual(self.run_worker(handler=refresh.fetch, target=DAY, calendar_dates=[DAY]), 0)
        self.assertEqual(len(self.pool.tasks), 1)
        daily.assert_called_once()
        minute.assert_not_called()
        saved = self.read(DAY+'.json')['rows'][0]
        self.assertEqual(saved['dailyVolume'], 100)
        self.assertEqual(saved['ratio'], 10)
        self.assertEqual(saved['priceStatus'], 'available')
        self.assertTrue(self.visible()['dataComplete'])
        self.assertTrue(self.visible()['priceDataComplete'])
        self.assertEqual(self.visible()['outcome'], 'completed')

    @patch('scripts.sina_daily.daily_unadjusted', side_effect=TimeoutError())
    def test_failed_price_repair_is_backed_off_once_and_returns_the_runner(self, daily):
        self.assertEqual(self.run_worker(handler=refresh.fetch, target=DAY,
                                        calendar_dates=[DAY], max_minutes=20), 0)
        self.assertEqual(len(self.pool.tasks), 1)
        daily.assert_called_once()
        saved = self.read(DAY+'.json')['rows'][0]
        for key in ('first15Volume', 'dailyVolume', 'ratio', 'status'):
            self.assertEqual(saved[key], core()[key], key)
        status = self.visible()
        self.assertTrue(status['dataComplete'])
        self.assertFalse(status['priceDataComplete'])
        self.assertEqual(status['exitReason'], 'source_retry_not_due')
        self.assertLess(self.clock.elapsed, 2)
        self.assertGreater(datetime.fromisoformat(status['nextRetryAt']), self.clock.now())

    @patch('scripts.sina_daily.daily_unadjusted')
    def test_price_only_future_retry_is_visible_and_respected(self, daily):
        attempt = dict(committed=True, nextRetryAt='2026-09-27T08:00:00+08:00')
        self.write('refresh-state.json', dict(version=1, targets={DAY: dict(
            date=DAY, attempts={CODE: attempt})}))
        self.assertEqual(self.run_worker(handler=refresh.fetch, target=DAY,
                                        calendar_dates=[DAY], max_minutes=20), 0)
        daily.assert_not_called()
        self.assertEqual(len(self.pool.tasks), 0)
        self.assertEqual(self.visible()['nextRetryAt'], attempt['nextRetryAt'])
        self.assertEqual(self.visible()['exitReason'], 'source_retry_not_due')
        self.assertLess(self.clock.elapsed, 2)

    @patch('scripts.sina_daily.daily_unadjusted')
    def test_worker_does_not_reopen_pre_contract_price_gap(self, daily):
        legacy = '2026-09-18'
        self.write(legacy+'.json', dict(date=legacy, universeTotal=1, rows=[core()]))
        self.assertEqual(self.run_worker(handler=refresh.fetch, target=legacy,
                                        calendar_dates=[legacy]), 0)
        self.assertEqual(len(self.pool.tasks), 0)
        daily.assert_not_called()


if __name__ == '__main__':
    unittest.main()
