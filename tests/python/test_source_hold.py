"""Bounded source absence is separate from errors, missing data and daily priority."""
import _bootstrap
import copy
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pandas as pd
from scripts import refresh
from deploy.actions import dispatcher
import refresh_worker as worker

DAY = '2026-09-24'
CODE = '688184'
STAMP = datetime(2026, 9, 29, 8, tzinfo=ZoneInfo('Asia/Shanghai'))


def observation(day=DAY, code=CODE):
    return dict(version=1, provider='sina', kind='target_opening_absent',
                code=code, date=day, dailyVolume=203691, targetTime='09:45:00',
                responseRows=1, responseEmpty=False)


def held_attempt():
    attempt = dict(failures=106)
    for i in range(3):
        attempt = refresh.commit_attempt(attempt, STAMP+timedelta(hours=i), False, observation())
    return attempt


def held_summary():
    return dict(dataComplete=False, pendingCount=1, unprocessedCount=0,
                retryableCount=0, sourceBlockedCount=1, automaticActionableCount=0,
                firstPassComplete=True, priceDataComplete=True)


class SourceHoldTests(unittest.TestCase):
    def test_historical_failure_counter_cannot_seed_new_observations(self):
        first = refresh.commit_attempt(dict(failures=106), STAMP, False, observation())
        self.assertEqual(first['failures'], 107)
        self.assertEqual(first['sourceObservation']['observations'], 1)
        self.assertFalse(refresh.source_held(first))
        self.assertTrue(refresh.source_held(held_attempt(), CODE, DAY))
        self.assertFalse(refresh.retry_due(held_attempt(), STAMP+timedelta(days=30)))

    def test_http_failure_breaks_consecutive_observations_without_claiming_source_absence(self):
        first = refresh.commit_attempt({}, STAMP, False, observation())
        error = refresh.commit_attempt(first, STAMP, False)
        self.assertNotIn('sourceObservation', error)
        self.assertNotIn('sourceHold', error)
        self.assertIsNotNone(error['nextRetryAt'])
        restarted = refresh.commit_attempt(error, STAMP, False, observation())
        self.assertEqual(restarted['sourceObservation']['observations'], 1)

    def test_changed_target_result_resets_and_unrelated_bars_do_not(self):
        first = refresh.commit_attempt({}, STAMP, False, observation())
        more_bars = dict(observation(), responseRows=100)
        second = refresh.commit_attempt(first, STAMP, False, more_bars)
        self.assertEqual(second['sourceObservation']['observations'], 2)
        changed = refresh.commit_attempt(second, STAMP, False, dict(observation(), dailyVolume=203692))
        self.assertEqual(changed['sourceObservation']['observations'], 1)

    def test_current_day_and_opening_errors_never_create_hold(self):
        attempt = {}
        for i in range(5):
            attempt = refresh.commit_attempt(attempt, STAMP, False, observation(STAMP.date().isoformat()))
        self.assertNotIn('sourceHold', attempt)
        self.assertNotIn('sourceObservation', attempt)
        with patch.object(refresh.collect, 'minute_unadjusted', side_effect=TimeoutError):
            previous = dict(dailyVolume=203691, priceStatus='available', open=10.85,
                            high=10.85, low=10.71, close=10.76, pctChange=None, amplitude=None,
                            priceSourceProvider='sina')
            value = refresh.fetch((dict(code=CODE,name='fixture'), DAY, None, previous, 'catchup'))
        self.assertIn('opening:TimeoutError', value['errors'])
        self.assertNotIn('sourceObservation', value)

    def test_success_clears_hold_and_exact_official_proof_can_reopen(self):
        held = held_attempt()
        success = refresh.commit_attempt(held, STAMP, True)
        self.assertNotIn('sourceObservation', success)
        self.assertNotIn('sourceHold', success)
        proof_hold = {}
        for _ in range(3):
            proof_hold = refresh.commit_attempt(proof_hold, STAMP, False,
                observation('2026-09-07', '002743'))
        self.assertTrue(refresh.ready('002743','2026-09-07','catchup',proof_hold,STAMP))
        self.assertFalse(refresh.ready('002743','2026-09-08','catchup',proof_hold,STAMP))

    def test_hold_identity_fingerprint_and_saved_data_are_validated(self):
        held = held_attempt()
        self.assertFalse(refresh.source_held(held, '600519', DAY))
        self.assertFalse(refresh.source_held(held, CODE, '2026-09-25'))
        tampered = copy.deepcopy(held)
        tampered['sourceObservation']['dailyVolume'] = 0
        with self.assertRaisesRegex(ValueError, 'Invalid source hold'):
            worker.validate_cache(dict(version=1, targets={DAY:dict(attempts={CODE:tampered})}))

    def test_dispatcher_holds_only_fully_observed_blocked_targets(self):
        status = dict(calendarDates=[DAY,'2026-09-28','2026-09-29','2026-09-30'],
                      calendarValidThrough='2026-09-30',targets={DAY:held_summary(),
                      '2026-09-28':dict(dataComplete=True,priceDataComplete=True)})
        self.assertEqual(dispatcher.choose_work(status,'catchup',STAMP), (None,'source_evidence_required'))
        status['targets'][DAY]['automaticActionableCount']=1
        status['targets'][DAY]['retryableCount']=1
        work,reason=dispatcher.choose_work(status,'catchup',STAMP)
        self.assertEqual(work['target_date'],DAY)
        self.assertEqual(reason,'due')

    def test_new_day_priority_and_other_history_continue_when_old_target_held(self):
        status = dict(calendarDates=[DAY,'2026-09-25','2026-09-28','2026-09-29','2026-09-30'],
                      calendarValidThrough='2026-09-30',targets={DAY:held_summary(),
                      '2026-09-25':dict(dataComplete=False,pendingCount=2,unprocessedCount=2),
                      '2026-09-28':dict(dataComplete=True,priceDataComplete=True)})
        work,_=dispatcher.choose_work(status,'catchup',STAMP)
        self.assertEqual(work['target_date'],'2026-09-25')
        work,_=dispatcher.choose_work(status,'watchdog',STAMP.replace(hour=10))
        self.assertEqual((work['phase'],work['target_date']),('opening','2026-09-29'))
        work,_=dispatcher.choose_work(status,'close',STAMP.replace(hour=16))
        self.assertEqual((work['phase'],work['target_date']),('close','2026-09-29'))

    def test_collector_selector_and_lambda_share_hold_contract(self):
        held = held_summary()
        self.assertEqual(refresh.all_sources_held(held), dispatcher.source_held(held))
        cache=dict(targets={DAY:dict(summary=held),
                            '2026-09-28':dict(summary=dict(dataComplete=True,priceDataComplete=True))})
        self.assertIsNone(refresh.select_target('catchup',None,[DAY,'2026-09-28'],cache,{},STAMP))


if __name__ == '__main__':
    unittest.main()
