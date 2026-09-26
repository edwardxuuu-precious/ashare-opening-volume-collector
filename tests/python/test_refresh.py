import _bootstrap
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch
import pandas as pd
from scripts import refresh

DAY = '2026-09-07'
def at(day=DAY, clock='15:30'):
    return datetime.fromisoformat(day+'T'+clock).replace(tzinfo=ZoneInfo('Asia/Shanghai'))


class RefreshTests(unittest.TestCase):
    def test_catchup_rotates_due_history_after_latest_first_pass(self):
        latest, old = '2026-09-24', '2026-09-14'
        cache = {'targets': {
            latest: {'summary': dict(dataComplete=False, firstPassComplete=True,
                                    lastAttemptedAt='2026-09-27T07:00:00+08:00')},
            old: {'summary': dict(dataComplete=False, pendingCount=5385)},
        }}
        self.assertEqual(refresh.select_target('catchup', None, [old, latest],
            cache, {}, at('2026-09-27', '07:35')), old)

    def test_catchup_skips_future_retry_and_retains_current_first_pass_priority(self):
        latest, old = '2026-09-24', '2026-09-14'
        summary = dict(dataComplete=False, unprocessedCount=1,
                       nextRetryAt='2026-09-27T08:00:00+08:00')
        cache = {'targets': {latest: {'summary': summary},
                              old: {'summary': dict(dataComplete=False, pendingCount=2)}}}
        self.assertEqual(refresh.select_target('catchup', None, [old, latest],
            cache, {}, at('2026-09-27', '07:35')), old)
        summary['nextRetryAt'] = None
        self.assertEqual(refresh.select_target('catchup', None, [old, latest],
            cache, {}, at('2026-09-27', '07:35')), latest)

    def test_catchup_selects_historical_price_only_gap(self):
        latest, old = '2026-09-24', '2026-09-21'
        cache = {'targets': {
            latest: {'summary': dict(dataComplete=True, priceDataComplete=True)},
            old: {'summary': dict(dataComplete=True, priceDataComplete=False)},
        }}
        self.assertEqual(refresh.select_target('catchup', None, [old, latest],
            cache, {}, at('2026-09-27', '07:35')), old)

    def test_cross_midnight_dispatch_requeues_immediately(self):
        self.assertTrue(refresh.retry_due(dict(committed=False, nextRetryAt=at('2026-09-08','23:00').isoformat()), at('2026-09-08','07:00')))

    def test_retry_backoff_is_5_15_30(self):
        attempt = {}
        for minutes in [5, 15, 30, 30]:
            attempt = refresh.commit_attempt(attempt, at(), False)
            self.assertEqual(datetime.fromisoformat(attempt['nextRetryAt']), at()+timedelta(minutes=minutes))
            self.assertFalse(refresh.retry_due(attempt, at()))
            self.assertTrue(refresh.retry_due(attempt, at()+timedelta(minutes=minutes)))

    def test_close_boundary_and_holiday(self):
        dates = ['2026-09-04', DAY]
        self.assertEqual(refresh.target_date('close',None,dates,at(clock='15:29')), '2026-09-04')
        self.assertEqual(refresh.target_date('close',None,dates,at()), DAY)
        with self.assertRaises(ValueError): refresh.target_date('close',DAY,dates,at(clock='15:29'))
        self.assertIsNone(refresh.target_date('opening',None,dates,at('2026-09-06','09:50')))

    def test_empty_is_not_suspension_or_complete(self):
        self.assertFalse(refresh.complete(dict(status='suspended',ratio=None)))

    def test_suspended_quotes_are_complete_only_when_explicitly_unavailable(self):
        self.assertFalse(refresh.quotes_present(dict(
            status='suspended', pctChange=-100.0, amplitude=0.0)))
        self.assertTrue(refresh.quotes_present(dict(
            status='suspended', pctChange=None, amplitude=None)))

    def test_company_notice_is_exact_date_and_never_fabricates_zero_volume(self):
        from scripts.no_trade_evidence import evidence
        result = refresh.fetch((dict(code='002743',name='fixture'),DAY,None,{},'catchup'))
        self.assertTrue(refresh.complete(result['row']))
        self.assertIsNone(result['row']['ratio'])
        self.assertIsNone(result['row']['dailyVolume'])
        self.assertEqual(result['row']['priceStatus'], 'not_traded')
        self.assertTrue(all(result['row'][key] is None for key in ('open', 'high', 'low', 'close')))
        self.assertIsNone(evidence('002743','2026-09-08'))
        forged = dict(result['row']['noTradeEvidence'],date='2026-09-08')
        self.assertFalse(refresh.complete(dict(result['row'],noTradeEvidence=forged)))
        self.assertFalse(refresh.complete(result['row'], '2026-09-08'))
        self.assertTrue(refresh.complete(result['row'], DAY))

    def test_special_suspension_exposes_specific_user_facing_explanation(self):
        day = '2026-09-18'
        result = refresh.fetch((dict(code='601059', name='信达证券'), day, None, {}, 'catchup'))

        self.assertTrue(refresh.complete(result['row'], day))
        self.assertEqual(result['row']['status'], 'suspended')
        self.assertEqual(result['row']['specialStatus']['type'], 'merger_pending_delisting')
        self.assertEqual(result['row']['specialStatus']['label'], '吸收合并停牌（拟终止上市）')
        self.assertIn('中金公司换股吸收合并', result['row']['specialStatus']['description'])
        self.assertEqual(result['row']['specialStatus']['startedAt'], '2026-09-15')
        self.assertEqual(result['row']['specialStatus']['source'], '上海证券交易所停复牌信息与公司公告')
        self.assertIsNone(result['row']['dailyVolume'])

        # A reviewed status is valid only for the exact stock/date pair.
        from scripts.no_trade_evidence import evidence
        self.assertIsNone(evidence('601059', '2026-09-19'))

    def test_all_reviewed_20260918_gaps_are_specific_and_complete(self):
        expected = {
            '000016': '主动终止上市事项停牌',
            '002731': '规范类退市程序停牌',
            '301139': '重大违法退市程序停牌',
            '600301': '控制权变更停牌',
            '600825': '重大资产重组停牌',
            '601059': '吸收合并停牌（拟终止上市）',
            '601198': '吸收合并停牌（拟终止上市）',
            '601238': '重大资产重组停牌',
            '601995': '换股吸收合并停牌',
            '603400': '重大资产重组停牌',
            '605303': '重大资产重组停牌',
            '688496': '交易类退市程序停牌',
        }
        for code, label in expected.items():
            with self.subTest(code=code):
                row = refresh.fetch((dict(code=code, name=code), '2026-09-18',
                                     None, {}, 'catchup'))['row']
                self.assertTrue(refresh.complete(row, '2026-09-18'))
                self.assertEqual(row['specialStatus']['label'], label)
                self.assertTrue(row['specialStatus']['description'])
                self.assertTrue(row['specialStatus']['announcementTitle'])

    def test_new_notice_does_not_wait_for_source_retry_timer(self):
        attempt = dict(committed=True, nextRetryAt=at('2026-09-08','09:30').isoformat())
        self.assertTrue(refresh.ready('002743', DAY, 'catchup', attempt, at('2026-09-08','08:00')))
        self.assertFalse(refresh.ready('600519', DAY, 'catchup', attempt, at('2026-09-08','08:00')))

    def test_exchange_status_evidence_bypasses_retry_and_completes_without_quotes(self):
        from scripts.exchange_status import parse_sse_suspensions
        day = '2026-09-18'
        exchange_evidence = parse_sse_suspensions([dict(
            productCode='601238', productName='广汽集团', controlType='TR',
            startStopDate='20260915', endStopDate='', type='LXTP',
            stopReason='拟筹划重大资产重组')], day)['601238']
        attempt = dict(committed=True, nextRetryAt=at('2026-09-18','09:30').isoformat())

        self.assertTrue(refresh.ready('601238', day, 'catchup', attempt,
                                      at('2026-09-18','08:00'), exchange_evidence))
        result = refresh.fetch((dict(code='601238', name='广汽集团'), day, None, {},
                                'catchup', None, exchange_evidence))
        self.assertTrue(refresh.complete(result['row'], day))
        self.assertEqual(result['row']['specialStatus']['label'], '重大资产重组停牌')

    @patch('scripts.sina_daily.daily_unadjusted')
    @patch.object(refresh.collect, 'minute_unadjusted')
    def test_cache_avoids_minute_and_reuses_daily(self, minute, daily):
        result = refresh.fetch((dict(code='600519',name='test'),DAY,10,dict(dailyVolume=100),'catchup'))
        minute.assert_not_called(); daily.assert_not_called()
        self.assertEqual(result['row']['ratio'],10)

    @patch('scripts.sina_daily.daily_unadjusted')
    @patch.object(refresh.collect, 'minute_unadjusted')
    def test_current_day_snapshot_avoids_delayed_history_endpoint(self, minute, daily):
        snapshot = dict(dailyVolume=74051597, pctChange=.6838, amplitude=1.3675,
                        open=11.72, high=11.81, low=11.65, close=11.78,
                        priceStatus='available', priceSourceProvider='sina',
                        dailyAdapter='sina_market_snapshot')

        result = refresh.fetch((dict(code='000001', name='平安银行'), '2026-09-08',
                                12891700, {}, 'close', snapshot))

        minute.assert_not_called(); daily.assert_not_called()
        self.assertEqual(result['row']['dailyVolume'], 74051597)
        self.assertEqual(result['row']['ratio'], 17.409078)
        self.assertEqual(result['row']['pctChange'], .6838)
        self.assertEqual(result['row']['amplitude'], 1.3675)
        self.assertEqual(result['row']['open'], 11.72)
        self.assertEqual(result['row']['close'], 11.78)
        self.assertEqual(result['row']['priceStatus'], 'available')
        self.assertEqual(result['row']['dailyAdapter'], 'sina_market_snapshot')

    @patch('scripts.refresh.collect.minute_unadjusted')
    @patch('scripts.sina_daily.daily_unadjusted')
    def test_quote_only_snapshot_preserves_completed_daily_volume(self, daily, minute):
        snapshot = dict(pctChange=-2.7353, amplitude=3.6718,
                        open=40.10, high=40.84, low=39.35, close=39.47,
                        priceStatus='available', priceSourceProvider='tencent',
                        dailyAdapter='tencent_daily_quote_fallback')

        result = refresh.fetch((dict(code='689009', name='九号公司'), DAY, 1014241,
                                dict(dailyVolume=8979553), 'close', snapshot))

        self.assertEqual(result['row']['dailyVolume'], 8979553)
        self.assertEqual(result['row']['pctChange'], -2.7353)
        self.assertEqual(result['row']['amplitude'], 3.6718)
        self.assertEqual(result['row']['high'], 40.84)
        self.assertEqual(result['row']['priceSourceProvider'], 'tencent')
        daily.assert_not_called()

    @patch('scripts.sina_daily.daily_unadjusted')
    @patch.object(refresh.collect, 'minute_unadjusted')
    def test_missing_cache_falls_back_and_retains_partial_result(self, minute, daily):
        minute.return_value = pd.DataFrame([dict(day=DAY+' 09:45:00',volume=10)])
        daily.side_effect = TimeoutError()
        result = refresh.fetch((dict(code='600519',name='test'),DAY,None,{},'close'))
        self.assertEqual(result['opening'],10)
        self.assertEqual(result['row']['status'],'missing')
        self.assertIsNone(result['row']['ratio'])

    def test_yield_before_priority_windows(self):
        self.assertEqual(refresh.yield_at('catchup',at(clock='07:00')),'09:40')
        self.assertEqual(refresh.yield_at('opening',at(clock='09:50')),'15:20')

    def test_latest_complete_then_independent_historical_queue(self):
        old = '2026-09-04'
        cache = dict(targets={DAY:dict(summary=dict(dataComplete=True))})
        legacy = dict(pending={'600519':[old, DAY]})
        self.assertEqual(refresh.select_target('catchup',None,[old, DAY],cache,legacy,at('2026-09-08','07:00')), old)
        self.assertEqual(refresh.select_target('catchup',DAY,[old, DAY],cache,legacy,at('2026-09-08','07:00')), DAY)
        cache['targets'][old] = dict(summary=dict(dataComplete=True))
        self.assertIsNone(refresh.select_target('catchup',None,[old, DAY],cache,legacy,at('2026-09-08','07:00')))

    def test_new_contract_date_reopens_core_complete_target_until_prices_complete(self):
        day = '2026-09-21'
        cache = dict(targets={day:dict(summary=dict(dataComplete=True,priceDataComplete=False))})
        self.assertEqual(refresh.select_target('catchup',None,[day],cache,dict(pending={}),at(day,'16:00')),day)
        cache['targets'][day]['summary']['priceDataComplete']=True
        self.assertIsNone(refresh.select_target('catchup',None,[day],cache,dict(pending={}),at(day,'16:00')))
