import _bootstrap
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import Mock

from scripts import daily_state as state

DAY = '2026-09-04'
OLD = '2026-09-03'
NEXT = '2026-09-07'
CODE = '600519'


def row(code=CODE, status='ok'):
    result = dict(code=code, name='test', market='SH', verificationVersion=3, status=status,
                  first15Volume=10, dailyVolume=160, minuteDayVolume=160, ratio=6.25,
                  quality='matched', dayVolumeDifference=0)
    if status != 'ok':
        result.update(ratio=None, quality='source_difference', minuteDayVolume=159, dayVolumeDifference=-1)
    return result


def now(day=DAY, clock='20:05'):
    return datetime.fromisoformat(day+'T'+clock+':00').replace(tzinfo=ZoneInfo('Asia/Shanghai'))


class DailyStateTests(unittest.TestCase):
    def test_cross_day_dispatch_is_reclaimed_for_target_date(self):
        pending = dict(pending={CODE:[DAY]}, attempts={CODE:{DAY:{'primary':DAY}}})
        state.resume_target_attempts(pending, DAY, now(NEXT, '07:00'))
        self.assertEqual(state.primary_tasks(pending, DAY), [(CODE, [DAY])])

    def test_committed_failure_waits_until_retry_due(self):
        pending = dict(pending={CODE:[DAY]}, attempts={CODE:{DAY:{'primary':DAY,
            'primaryDone':DAY, 'nextRetryAt':now(NEXT, '07:15').isoformat()}}})
        state.resume_target_attempts(pending, DAY, now(NEXT, '07:00'))
        self.assertEqual(state.primary_tasks(pending, DAY), [])
        state.resume_target_attempts(pending, DAY, now(NEXT, '07:15'))
        self.assertEqual(state.primary_tasks(pending, DAY), [(CODE, [DAY])])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.out = Path(self.folder.name)
        (self.out/'checkpoint').mkdir()
        self.items = [dict(code=CODE, name='test')]

    def cp(self, rows):
        state.write_json(self.out/'checkpoint'/f'{CODE}.json', dict(code=CODE, fetchedOn=OLD, fetchStatus='ok', days=rows))


    def test_review_bypasses_daily_attempts_once_and_resumes_remaining_problem_rows(self):
        cycle = 'review:2026-09-07:audit-1'
        self.cp({OLD:row(status='unverified'), DAY:row(status='unverified')})
        previous = state.build_state(self.out,self.items,[OLD,DAY])
        for provider in ('primary','bao'):
            state.mark_attempt(previous,CODE,[OLD,DAY],provider,DAY)
        review = state.build_state(self.out,self.items,[OLD,DAY],previous,review_cycle=cycle)
        self.assertEqual(state.primary_tasks(review,cycle),[(CODE,[DAY,OLD])])
        state.journal_attempt(self.out,review,CODE,[DAY],'primary',cycle)
        resumed = state.build_state(self.out,self.items,[OLD,DAY],previous,review_cycle=cycle)
        state.replay_attempts(self.out,resumed,review_cycle=cycle)
        self.assertEqual(state.primary_tasks(resumed,cycle),[(CODE,[OLD])])
        self.assertEqual(state.bao_tasks(resumed,cycle,now('2026-09-07',clock='10:00'),'20:05'),[(CODE,[DAY])])
        state.journal_attempt(self.out,resumed,CODE,[DAY],'bao',cycle)
        state.replay_attempts(self.out,resumed,review_cycle=cycle)
        self.assertEqual(state.bao_tasks(resumed,cycle,now('2026-09-07'),'20:05'),[])
        next_review = state.build_state(self.out,self.items,[OLD,DAY],resumed,review_cycle='review:2026-09-07:audit-0')
        state.replay_attempts(self.out,next_review,review_cycle='review:2026-09-07:audit-0')
        self.assertEqual(state.primary_tasks(next_review,'review:2026-09-07:audit-0'),[(CODE,[DAY,OLD])])


    def test_review_resume_reclaims_uncommitted_dispatch_but_keeps_committed_sources(self):
        cycle='review:2026-09-07:audit-1'
        self.cp({OLD:dict(row(status='unverified'),dailyAttempts={'primary':cycle}),DAY:row(status='unverified')})
        prior=state.build_state(self.out,self.items,[OLD,DAY],review_cycle=cycle)
        # DAY's future may not finish, or may finish before its CP commit: both have only a dispatch.
        state.journal_attempt(self.out,prior,CODE,[DAY],'primary',cycle)
        # OLD's primary result is durable; only its unfinished backup should be dispatched again.
        state.journal_attempt(self.out,prior,CODE,[OLD],'bao',cycle)
        resumed=state.build_state(self.out,self.items,[OLD,DAY],prior,review_cycle=cycle)
        state.replay_attempts(self.out,resumed,review_cycle=cycle)
        state.resume_review_attempts(resumed,cycle)
        self.assertEqual(state.primary_tasks(resumed,cycle),[(CODE,[DAY])])
        self.assertEqual(state.bao_tasks(resumed,cycle,now('2026-09-07'),'20:05'),[(CODE,[OLD])])
        # A completed backup is retained even if its old dispatch is replayed on another resume.
        state.mark_attempt(resumed,CODE,[OLD],'bao',cycle)
        state.mark_attempt(resumed,CODE,[OLD],'baoDone',cycle)
        state.replay_attempts(self.out,resumed,review_cycle=cycle)
        state.resume_review_attempts(resumed,cycle)
        self.assertEqual(state.bao_tasks(resumed,cycle,now('2026-09-07'),'20:05'),[])

    def test_review_attempts_do_not_block_later_daily_collection(self):
        cycle = 'review:2026-09-07:audit-1'
        self.cp({DAY:dict(row(status='unverified'),dailyAttempts={'primary':cycle,'bao':cycle})})
        previous = state.build_state(self.out,self.items,[DAY],review_cycle=cycle)
        state.journal_attempt(self.out,previous,CODE,[DAY],'primary',cycle)
        routine = state.build_state(self.out,self.items,[DAY],previous)
        state.replay_attempts(self.out,routine)
        self.assertEqual(state.primary_tasks(routine,DAY),[(CODE,[DAY])])
        state.mark_attempt(routine,CODE,[DAY],'primary',DAY)
        self.assertEqual(state.primary_tasks(routine,DAY),[])

    def test_review_checkpoint_keeps_old_scope_during_updates(self):
        ancient='2026-03-06'
        previous=dict(code=CODE,days={ancient:row(),OLD:row()})
        result=state.merge_checkpoint(previous,CODE,{DAY:row()},'2026-09-07',preserve_history=True)
        self.assertEqual(set(result['days']),{ancient,OLD,DAY})

    def test_matched_old_checkpoint_skips_without_same_fetched_day(self):
        self.cp({OLD:row(), DAY:row()})
        current = state.build_state(self.out, self.items, [OLD, DAY])
        self.assertEqual(current['pending'], {})
        self.assertEqual(state.primary_tasks(current, DAY), [])

    def test_legacy_truncated_cp_does_not_discard_verified_aggregate_history(self):
        state.write_json(self.out/(OLD+'.json'), dict(date=OLD, rows=[row()]))
        self.cp({DAY:row()})
        self.assertEqual(state.build_state(self.out, self.items, [OLD, DAY])['pending'], {})

    def test_missing_date_is_queued_and_survives_next_day(self):
        self.cp({OLD:row()})
        current = state.build_state(self.out, self.items, [OLD, DAY])
        state.mark_attempt(current, CODE, [DAY], 'primary', DAY)
        self.assertEqual(state.primary_tasks(current, DAY), [])
        resumed = state.build_state(self.out, self.items, [OLD, DAY, NEXT], current)
        self.assertEqual(state.primary_tasks(resumed, NEXT), [(CODE,[NEXT, DAY])])

    def test_today_missing_stock_precedes_history_only_stock(self):
        current = dict(pending={'000001':[OLD], CODE:[DAY, OLD]}, attempts={})
        self.assertEqual(state.primary_tasks(current, DAY)[0][0], CODE)

    def test_merge_preserves_six_months_and_only_prunes_expired(self):
        previous = dict(days={'2026-03-03':row(), OLD:row()}, code=CODE)
        merged = state.merge_checkpoint(previous, CODE, {DAY:row()}, DAY)
        self.assertEqual(set(merged['days']), {OLD, DAY})
        self.assertEqual(merged['days'][OLD], previous['days'][OLD])

    def test_missing_refresh_preserves_proven_row_but_conflict_is_visible(self):
        previous = dict(days={DAY:row()}, code=CODE)
        missing = dict(code=CODE, status='missing', ratio=None, verificationVersion=3)
        self.assertEqual(state.merge_checkpoint(previous, CODE, {DAY:missing}, DAY)['days'][DAY], row())
        conflict = state.merge_checkpoint(previous, CODE, {DAY:row(status='unverified')}, DAY)['days'][DAY]
        self.assertIsNone(conflict['ratio'])
        self.assertEqual(conflict['previousVerifiedObservation'], row())

    def test_source_conflict_cp_invalidates_old_aggregate_success_even_legacy_ok_status(self):
        state.write_json(self.out/(DAY+'.json'), dict(date=DAY, rows=[row()]))
        conflicting = row(status='unverified')
        conflicting['status']='ok'
        self.cp({DAY:conflicting})
        self.assertEqual(state.build_state(self.out, self.items, [DAY])['pending'], {CODE:[DAY]})

    def test_corrupt_cached_identity_and_ratio_fail_closed(self):
        self.cp({DAY:dict(row(), code='000001')})
        with self.assertRaises(ValueError):state.build_state(self.out, self.items, [DAY])
        self.cp({DAY:dict(row(), ratio=12)})
        with self.assertRaises(ValueError):state.build_state(self.out, self.items, [DAY])

    def test_bao_today_waits_until_2005_history_ready_immediately(self):
        current = dict(pending={CODE:[DAY, OLD], '920001':[DAY]}, attempts={})
        state.mark_attempt(current, CODE, [DAY, OLD], 'primary', DAY)
        state.mark_attempt(current, '920001', [DAY], 'primary', DAY)
        self.assertEqual(state.bao_tasks(current, DAY, now(clock='20:04'), '20:05'), [(CODE,[OLD])])
        self.assertEqual(state.bao_tasks(current, DAY, now(), '20:05'), [(CODE,[DAY, OLD])])
        state.mark_attempt(current, CODE, [DAY, OLD], 'bao', DAY)
        self.assertEqual(state.bao_tasks(current, DAY, now(), '20:05'), [])

    def test_journal_recovers_dispatch_without_claiming_observation(self):
        current = dict(pending={CODE:[DAY]}, attempts={}, observed={})
        state.journal_attempt(self.out, current, CODE, [DAY], 'primary', DAY)
        recovered = dict(pending={CODE:[DAY]}, attempts={}, observed={})
        state.replay_attempts(self.out, recovered)
        self.assertEqual(state.primary_tasks(recovered, DAY), [])
        self.assertEqual(recovered['observed'], {})
        self.assertEqual(state.primary_tasks(recovered, NEXT), [(CODE,[DAY])])
        recovered['dates']=[DAY]
        self.assertFalse(state.counters(recovered,1,DAY)['traversalCompleted'])

    def test_later_same_day_run_retries_only_unfinished_current_rows(self):
        current=dict(pending={CODE:[DAY,OLD]},attempts={},observed={CODE:[DAY,OLD]})
        for day in (DAY,OLD):
            state.mark_attempt(current,CODE,[day],'primary',DAY)
            state.mark_attempt(current,CODE,[day],'primaryDone',DAY)
        state.resume_current_day_attempts(current,DAY)
        self.assertEqual(state.primary_tasks(current,DAY),[(CODE,[DAY])])
        self.assertTrue(state.attempted(current,CODE,OLD,'primary',DAY))

    def test_terminal_cp_attempt_recovers_after_crash_before_full_state_save(self):
        self.cp({DAY:dict(row(status='unverified'), dailyAttempts={'primary':DAY,'bao':DAY})})
        current=state.build_state(self.out,self.items,[DAY])
        self.assertEqual(state.primary_tasks(current,DAY),[])
        self.assertEqual(state.bao_tasks(current,DAY,now(),'20:05'),[])
        self.assertTrue(state.counters(current,1,DAY)['traversalCompleted'])

    def test_journal_compaction_retains_attempts_in_durable_state(self):
        current=dict(dates=[DAY],pending={CODE:[DAY]},attempts={},observed={})
        state.journal_attempt(self.out,current,CODE,[DAY],'primary',DAY)
        state.persist_state(self.out,current,compact=True)
        self.assertEqual((self.out/'daily-attempts.jsonl').read_text(),'')
        self.assertEqual(state.read_json(self.out/'daily-state.json')['attempts'],current['attempts'])

    def test_traversal_is_not_verified_success(self):
        current = dict(dates=[DAY], pending={CODE:[DAY]}, observed={CODE:[DAY]}, attempts={})
        state.mark_attempt(current, CODE, [DAY], 'primary', DAY)
        metrics = state.counters(current, 1, DAY)
        self.assertTrue(metrics['latestTraversalCompleted'])
        self.assertEqual(metrics['completedStockDates'], 0)
        self.assertEqual(metrics['pendingStockDates'], 1)

    def test_calendar_weekday_holiday_and_same_day_refresh_once(self):
        fetch = Mock(return_value=[OLD, DAY, NEXT])
        first = state.load_calendar(self.out, now('2026-09-05'), fetch)
        second = state.load_calendar(self.out, now('2026-09-05'), fetch)
        self.assertEqual(first['tradingDates'], [OLD, DAY])
        self.assertEqual(first, second)
        self.assertEqual(fetch.call_count, 1)

    def test_calendar_1500_closed_boundary_without_second_fetch(self):
        fetch = Mock(return_value=[OLD, DAY])
        self.assertEqual(state.load_calendar(self.out, now(clock='14:59'), fetch)['tradingDates'], [OLD])
        self.assertEqual(state.load_calendar(self.out, now(clock='15:00'), fetch)['tradingDates'], [OLD, DAY])
        self.assertEqual(fetch.call_count, 1)


if __name__ == '__main__':unittest.main()
