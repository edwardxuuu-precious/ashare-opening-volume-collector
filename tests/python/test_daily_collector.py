import _bootstrap
import io
import json
import multiprocessing as mp
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from scripts import daily_collector as daily
from scripts import daily_state
from test_daily_state import CODE, DAY, OLD, now, row


def reserve_in_spawn(budget, queue):
    # Exercise the installed HTTP wrapper too, not merely a duplicated local timer.
    import requests
    def fake_request(*args, **kwargs):
        queue.put(time.monotonic())
        return SimpleNamespace(content=b'ok', raise_for_status=lambda:None)
    requests.sessions.Session.request = fake_request
    budget.install()
    for _ in range(2):
        requests.get('https://fixture.invalid')


class DailyRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.out=Path(self.folder.name)
        self.items=[dict(code=CODE, name='test')]
        self.args=SimpleNamespace(out=str(self.out), workers=4, interval=.75, cutoff='21:55', bao_after='20:05', publish_every=50,
                                  source_policy='legacy')
        daily.STOP=False

    def fixture(self, moment, failed=False):
        daily_state.write_json(self.out/'calendar.json', dict(calendarAsOf=moment.date().isoformat(), calendarDates=[OLD,DAY], tradingDates=[OLD,DAY]))
        daily_state.write_json(self.out/'universe.json', dict(asOf=DAY, rows=self.items, total=1))
        daily_state.write_json(self.out/'checkpoint'/f'{CODE}.json', dict(code=CODE, fetchStatus='ok', fetchedOn=OLD, days={OLD:row(),DAY:row(status='unverified') if failed else row()}))
        for day in [OLD,DAY]:
            daily_state.write_json(self.out/(day+'.json'), dict(date=day, generatedAt='preserved', rows=[row(status='unverified') if failed and day==DAY else row()]))
        daily_state.write_json(self.out/'manifest.json', dict(marker='do not rewrite'))

    def run_no_network(self, moment):
        forbidden=Mock(side_effect=AssertionError('No stock API or process should run'))
        ak=SimpleNamespace(stock_info_a_code_name=forbidden, tool_trade_date_hist_sina=forbidden)
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(io.StringIO()))
            original_ak = sys.modules.get('akshare')
            sys.modules['akshare'] = ak
            def restore_ak():
                if original_ak is None:sys.modules.pop('akshare',None)
                else:sys.modules['akshare']=original_ak
            stack.callback(restore_ak)
            stack.enter_context(patch.object(daily, 'local_now', return_value=moment))
            stack.enter_context(patch.object(daily.SharedHTTPBudget, 'install'))
            stack.enter_context(patch.object(daily, 'validate_universe'))
            stack.enter_context(patch.object(daily, 'load_universe', return_value={'rows':self.items}))
            stack.enter_context(patch.object(daily, 'fetch_primary', forbidden))
            stack.enter_context(patch.object(daily, 'fetch_bao', forbidden))
            result=daily.run(self.args)
        forbidden.assert_not_called()
        return result, daily_state.read_json(self.out/'run-status.json')


    def review_fixture(self, dates=(OLD, DAY), review_id='audit-1'):
        moment = now('2026-09-07', clock='10:00')
        self.fixture(moment)
        for day in dates:
            if not (self.out/(day+'.json')).exists():
                daily_state.write_json(self.out/(day+'.json'), dict(date=day, rows=[row()]))
        daily_state.write_json(self.out/'manifest.json', dict(dates=[dict(date=day,file=day+'.json') for day in dates]))
        self.args.review_id = review_id
        self.args.review_as_of = '2026-09-07'
        self.args.dates = ','.join(dates)
        return moment

    def test_review_accepts_saved_history_outside_window_without_calendar_refresh(self):
        moment = self.review_fixture(('2026-03-06', DAY))
        before = (self.out/'calendar.json').read_bytes()
        _, report = self.run_no_network(moment)
        self.assertEqual(report['dates'], ['2026-03-06', DAY])
        self.assertEqual(report['reviewId'], 'audit-1')
        self.assertEqual(report['todayTargetCount'], 0)
        self.assertEqual(report['httpRequests'], 0)
        self.assertEqual(report['latestDate'], DAY)
        self.assertEqual((self.out/'calendar.json').read_bytes(), before)

    def test_review_rejects_today_future_unknown_or_missing_saved_file(self):
        moment = self.review_fixture()
        for selected in ('2026-09-07', '2026-09-08', '2026-03-05'):
            self.args.dates = selected
            with self.assertRaises(ValueError): daily.review_dates(self.out,self.args,moment.date().isoformat())
        self.args.dates = DAY
        (self.out/(DAY+'.json')).unlink()
        with self.assertRaises(ValueError): daily.review_dates(self.out,self.args,moment.date().isoformat())

    def test_review_rejects_unsafe_id_or_unfrozen_arguments(self):
        self.review_fixture()
        self.args.review_id = '../escape'
        with self.assertRaises(ValueError): daily.review_dates(self.out,self.args,'2026-09-07')
        self.args.review_id = 'audit-1'
        self.args.dates = None
        with self.assertRaises(ValueError): daily.review_dates(self.out,self.args,'2026-09-07')

    def test_review_preserves_published_verified_row_despite_conflicting_checkpoint(self):
        moment = self.review_fixture()
        cp = daily_state.read_json(self.out/'checkpoint'/f'{CODE}.json')
        cp['days'][DAY] = row(status='unverified')
        daily_state.write_json(self.out/'checkpoint'/f'{CODE}.json',cp)
        paths = [self.out/(DAY+'.json'), self.out/(OLD+'.json'), self.out/'checkpoint'/f'{CODE}.json']
        before = {str(path):path.read_bytes() for path in paths}
        _,report = self.run_no_network(moment)
        self.assertEqual(report['pendingStockDates'],0)
        self.assertEqual(report['recoveredRows'],0)
        self.assertEqual(before,{str(path):path.read_bytes() for path in paths})

    def test_review_id_cannot_resume_with_different_scope(self):
        moment = self.review_fixture()
        self.run_no_network(moment)
        self.args.dates = DAY
        with self.assertRaises(ValueError): self.run_no_network(moment)

    def test_holiday_noop_preserves_snapshots_and_cp(self):
        moment=now('2026-09-06')
        self.fixture(moment)
        watched=[self.out/'manifest.json', self.out/(DAY+'.json'), self.out/'checkpoint'/f'{CODE}.json']
        before={path:(path.read_bytes(), path.stat().st_mtime_ns) for path in watched}
        result, report=self.run_no_network(moment)
        self.assertEqual(result, 0)
        self.assertTrue(report['noOp'])
        self.assertEqual(report['exitReason'], 'no_work')
        self.assertEqual(report['httpRequests'], 0)
        self.assertEqual(report['publishCount'], 0)
        self.assertEqual(before, {path:(path.read_bytes(),path.stat().st_mtime_ns) for path in watched})

    def test_same_day_failed_already_attempted_no_retry_and_not_success(self):
        moment=now()
        self.fixture(moment, failed=True)
        current=daily_state.build_state(self.out, self.items, [OLD,DAY])
        daily_state.mark_attempt(current,CODE,[DAY],'primary',DAY)
        daily_state.mark_attempt(current,CODE,[DAY],'bao',DAY)
        daily_state.write_json(self.out/'daily-state.json',current)
        _,report=self.run_no_network(moment)
        self.assertTrue(report['noOp'])
        self.assertEqual(report['pendingStockDates'],1)
        self.assertEqual(report['completedStockDates'],1)

    def test_cutoff_exactly_2155_starts_no_source(self):
        self.fixture(now())
        result,report=self.run_no_network(now(clock='21:55'))
        self.assertEqual(result,0)
        self.assertEqual(report['exitReason'],'cutoff')
        self.assertEqual(report['httpRequests'],0)
        self.assertFalse(report['noOp'])
        self.assertTrue(daily.before_cutoff(now(clock='21:54'),'21:55'))
        self.assertFalse(daily.before_cutoff(now(clock='21:55'),'21:55'))

    def test_empty_closed_calendar_is_noop(self):
        self.fixture(now())
        daily_state.write_json(self.out/'calendar.json',dict(calendarAsOf=DAY,calendarDates=[],tradingDates=[]))
        _,report=self.run_no_network(now())
        self.assertTrue(report['noOp'])
        self.assertEqual(report['dates'],[])

    def test_four_spawn_workers_share_one_http_rate_and_counter(self):
        context=mp.get_context('spawn')
        budget=daily.SharedHTTPBudget(context.Lock(),context.Value('d',0),context.Value('q',0),
            context.Value('q',0),.04,time.monotonic()+30,
            context.Value('d',0),context.Value('q',0))
        queue=context.Queue()
        processes=[context.Process(target=reserve_in_spawn,args=(budget,queue)) for _ in range(4)]
        for process in processes:process.start()
        for process in processes:
            process.join(20)
            if process.is_alive():process.terminate();process.join()
            self.assertEqual(process.exitcode,0)
        stamps=sorted(queue.get(timeout=1) for _ in range(8))
        self.assertGreaterEqual(min(b-a for a,b in zip(stamps,stamps[1:])),.025)
        self.assertEqual(budget.count.value,8)
        self.assertEqual(budget.byte_count.value,16)

    def test_global_http_budget_refuses_new_request_after_cutoff(self):
        context=mp.get_context('spawn')
        budget=daily.SharedHTTPBudget(context.Lock(),context.Value('d',0),context.Value('q',0),
            context.Value('q',0),.75,time.monotonic()-1,
            context.Value('d',0),context.Value('q',0))
        with self.assertRaises(daily.RequestWindowClosed):budget.reserve()
        self.assertEqual(budget.count.value,0)

    def test_global_http_budget_retries_one_transient_page_timeout(self):
        import requests
        context=mp.get_context('spawn')
        budget=daily.SharedHTTPBudget(context.Lock(),context.Value('d',0),context.Value('q',0),
            context.Value('q',0),.001,time.monotonic()+30,
            context.Value('d',0),context.Value('q',0))
        calls=[]
        def flaky_request(*args, **kwargs):
            calls.append(kwargs['timeout'])
            if len(calls) == 1:
                raise requests.exceptions.ReadTimeout('fixture page timeout')
            return SimpleNamespace(content=b'ok',raise_for_status=lambda:None)
        with patch.object(requests.sessions.Session,'request',new=flaky_request):
            budget.install()
            response=requests.get('https://fixture.invalid/page/65')
        self.assertEqual(response.content,b'ok')
        self.assertEqual(len(calls),2)
        self.assertEqual(budget.count.value,2)
        self.assertEqual(budget.byte_count.value,2)

    def test_global_http_budget_shares_adaptive_cooldown_after_transient_timeout(self):
        import requests
        context=mp.get_context('spawn')
        clock=[100.0]
        cooldown=context.Value('d',0)
        transient_failures=context.Value('q',0)
        budget=daily.SharedHTTPBudget(context.Lock(),context.Value('d',0),context.Value('q',0),
            context.Value('q',0),.001,200.0,cooldown,transient_failures)
        budget.TRANSIENT_BACKOFF_SECONDS=(2.0,5.0,15.0,30.0)
        calls=[]
        def flaky_request(*args, **kwargs):
            calls.append(clock[0])
            if len(calls) == 1:
                raise requests.exceptions.ReadTimeout('fixture page timeout')
            return SimpleNamespace(content=b'ok',raise_for_status=lambda:None)
        def advance(seconds):
            clock[0] += seconds
        with patch.object(daily.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(daily.time,'sleep',side_effect=advance), \
             patch.object(requests.sessions.Session,'request',new=flaky_request):
            budget.install()
            response=requests.get('https://fixture.invalid/page/65')
        self.assertEqual(response.content,b'ok')
        self.assertEqual(calls,[100.0,102.0])
        self.assertEqual(cooldown.value,102.0)
        self.assertEqual(transient_failures.value,0)

    def test_global_http_budget_stops_after_three_transient_get_failures(self):
        import requests
        context=mp.get_context('spawn')
        budget=daily.SharedHTTPBudget(context.Lock(),context.Value('d',0),context.Value('q',0),
            context.Value('q',0),.001,time.monotonic()+30,
            context.Value('d',0),context.Value('q',0))
        calls=[]
        def unavailable(*args, **kwargs):
            calls.append(kwargs['timeout'])
            raise requests.exceptions.ReadTimeout('fixture page timeout')
        with patch.object(requests.sessions.Session,'request',new=unavailable):
            budget.install()
            with self.assertRaises(requests.exceptions.ReadTimeout):
                requests.get('https://fixture.invalid/page/65')
        self.assertEqual(len(calls),3)
        self.assertEqual(budget.count.value,3)
        self.assertEqual(budget.byte_count.value,0)

    def test_shared_eastmoney_breaker_is_independent_of_baostock(self):
        context=mp.get_context('spawn')
        budget=daily.SharedFallbackBudget(context.Lock(),context.Value('i',0),context.Value('i',0))
        for _ in range(3):
            self.assertIsNone(budget.reserve());budget.result(False)
        self.assertIsNotNone(budget.reserve())
        self.assertIsNone(daily.FallbackBudget().reserve())

    def exercise_scheduler(self, start, primary_status='ok', stuck=False, interrupt_wait=False, prepare=None, bao_status='ok', reuse=False):
        if not reuse:
            self.fixture(start)
            daily_state.write_json(self.out/'checkpoint'/f'{CODE}.json',dict(code=CODE,fetchStatus='ok',fetchedOn=OLD,days={OLD:row()}))
            daily_state.write_json(self.out/(DAY+'.json'),dict(date=DAY,generatedAt='old',rows=[]))
            # A real manifest shape is required for publishing to merge its previous dates.
            daily_state.write_json(self.out/'manifest.json',dict(dates=[]))
        if prepare:
            prepare()
        clock=[start]
        calls=[]
        pools=[]
        real_context=mp.get_context('spawn')
        class Pool:
            def __init__(self,size,initializer,initargs):
                self.size=size;self.kind=initializer.__name__;self.terminated=False
                pools.append(self)
            def apply_async(self,function,args):
                task=args[0];calls.append((self.kind,clock[0],task))
                if self.kind=='init_primary':
                    code, name, dates=task
                    value=(code,{day:row(code,status=primary_status) for day in dates})
                else:
                    code,name,rows=task
                    value=(code,{day:dict(row(code,status=bao_status),sourceProvider='baostock',verificationState='fallback_matched') for day in rows})
                return SimpleNamespace(ready=lambda:not stuck,get=lambda:value)
            def terminate(self):self.terminated=True
            def close(self):pass
            def join(self):pass
        def sleep(seconds):
            clock[0]+=timedelta(seconds=seconds)
            if interrupt_wait and seconds>=10:daily.STOP=True
        fake_context=SimpleNamespace(Lock=real_context.Lock,Value=real_context.Value,Pool=Pool)
        ak=SimpleNamespace(stock_info_a_code_name=Mock(),tool_trade_date_hist_sina=Mock())
        old_ak=sys.modules.get('akshare');sys.modules['akshare']=ak
        try:
            with redirect_stdout(io.StringIO()), patch.object(daily,'local_now',side_effect=lambda:clock[0]), \
                 patch.object(daily.mp,'get_context',return_value=fake_context), \
                 patch.object(daily.SharedHTTPBudget,'install'), patch.object(daily,'validate_universe'), patch.object(daily,'load_universe',return_value={'rows':self.items}), \
                 patch.object(daily.time,'sleep',side_effect=sleep):
                result=daily.run(self.args)
        finally:
            if old_ak is None:sys.modules.pop('akshare',None)
            else:sys.modules['akshare']=old_ak
        return result,daily_state.read_json(self.out/'run-status.json'),calls,pools


    def test_review_attempts_each_source_once_and_marks_terminal_unresolved_complete(self):
        moment=now('2026-09-07',clock='10:00')
        kept={}
        def prepare():
            self.args.review_id='audit-1'
            self.args.review_as_of='2026-09-07'
            self.args.dates=OLD+','+DAY
            daily_state.write_json(self.out/'manifest.json',dict(dates=[dict(date=day,file=day+'.json') for day in (OLD,DAY)]))
            current=daily_state.build_state(self.out,self.items,[OLD,DAY])
            for provider in ('primary','bao'):
                daily_state.mark_attempt(current,CODE,[DAY],provider,DAY)
            daily_state.write_json(self.out/'daily-state.json',current)
            kept['old']=(self.out/(OLD+'.json')).read_bytes()
        _,report,calls,_=self.exercise_scheduler(moment,primary_status='unverified',bao_status='unverified',prepare=prepare)
        self.assertEqual([call[0] for call in calls],['init_primary','init_bao'])
        self.assertEqual(calls[0][2][2],[DAY])
        self.assertEqual(list(calls[1][2][2]),[DAY])
        self.assertTrue(report['reviewCompleted'])
        self.assertEqual(daily_state.read_json(self.out/'manifest.json')['retentionPolicy'],'accumulate')
        self.assertNotIn('retentionMonths',daily_state.read_json(self.out/'manifest.json'))
        self.assertEqual(report['pendingStockDates'],1)
        self.assertEqual(report['todayTargetCount'],0)
        self.assertEqual((self.out/(OLD+'.json')).read_bytes(),kept['old'])
        _,resumed=self.run_no_network(moment)
        self.assertTrue(resumed['reviewCompleted'])
        self.assertEqual(resumed['attemptedThisRun'],0)
        self.assertFalse((self.out/'2026-09-07.json').exists())

    def test_single_source_review_finishes_without_bao_and_resumes_without_repeat(self):
        moment=now('2026-09-07',clock='10:00')
        self.args.source_policy='sina'
        def prepare():
            self.args.review_id='single-source-audit'
            self.args.review_as_of='2026-09-07'
            self.args.dates=OLD+','+DAY
            daily_state.write_json(self.out/'manifest.json',dict(dates=[dict(date=d,file=d+'.json') for d in (OLD,DAY)]))
        _,report,calls,_=self.exercise_scheduler(moment,primary_status='unverified',prepare=prepare)
        self.assertEqual([c[0] for c in calls],['init_primary'])
        self.assertTrue(report['reviewCompleted'])
        self.assertEqual(report['pendingStockDates'],1)
        self.assertEqual(report['collectionSourcePolicy'],['sina'])
        self.assertFalse((self.out/'2026-09-07.json').exists())
        _,resumed=self.run_no_network(moment)
        self.assertTrue(resumed['reviewCompleted'])
        self.assertEqual(resumed['attemptedThisRun'],0)

    def test_single_source_daily_never_waits_for_bao_release(self):
        self.args.source_policy='sina'
        _,report,calls,_=self.exercise_scheduler(now(clock='17:10'),primary_status='unverified')
        self.assertEqual([c[0] for c in calls],['init_primary'])
        self.assertEqual(report['pendingStockDates'],1)
        self.assertLess(report['updatedAt'][11:16],'20:05')


    def test_review_cutoff_inflight_task_is_reclaimed_on_same_id_resume(self):
        def prepare():
            self.args.review_id='resume-1'
            self.args.review_as_of='2026-09-07'
            self.args.dates=OLD+','+DAY
            daily_state.write_json(self.out/'manifest.json',dict(dates=[dict(date=day,file=day+'.json') for day in (OLD,DAY)]))
        _,paused,calls,_=self.exercise_scheduler(now('2026-09-07',clock='21:54'),stuck=True,prepare=prepare)
        self.assertEqual(paused['exitReason'],'cutoff')
        self.assertFalse(paused['reviewCompleted'])
        self.assertEqual(len(calls),1)
        _,finished,resumed_calls,_=self.exercise_scheduler(now('2026-09-07',clock='10:00'),reuse=True)
        self.assertEqual(len(resumed_calls),1)
        self.assertEqual(resumed_calls[0][2][2],[DAY])
        self.assertTrue(finished['reviewCompleted'])
        _,again=self.run_no_network(now('2026-09-07',clock='10:00'))
        self.assertTrue(again['reviewCompleted'])
        self.assertEqual(again['attemptedThisRun'],0)

    def test_parent_scheduler_collects_only_missing_day_and_preserves_cp_history(self):
        result,report,calls,pools=self.exercise_scheduler(now())
        self.assertEqual(result,0)
        self.assertEqual(calls[0][2][2],[DAY])
        self.assertEqual(len(calls),1)
        self.assertEqual(pools[0].size,4)
        self.assertEqual(set(daily_state.read_json(self.out/'checkpoint'/f'{CODE}.json')['days']),{OLD,DAY})
        self.assertTrue(report['latestTraversalCompleted'])
        self.assertEqual(report['pendingStockDates'],0)

    def test_serial_bao_queue_waits_for_2005_then_repairs_same_provider(self):
        result,report,calls,pools=self.exercise_scheduler(now(clock='20:04'),primary_status='unverified')
        bao_calls=[call for call in calls if call[0]=='init_bao']
        self.assertEqual(len(bao_calls),1)
        self.assertGreaterEqual(bao_calls[0][1].strftime('%H:%M'),'20:05')
        self.assertEqual(pools[-1].size,1)
        self.assertEqual(report['pendingStockDates'],0)
        self.assertEqual(daily_state.read_json(self.out/'checkpoint'/f'{CODE}.json')['days'][DAY]['sourceProvider'],'baostock')

    def test_wait_for_bao_can_be_interrupted_without_losing_pending_date(self):
        result,report,calls,pools=self.exercise_scheduler(now(clock='20:04'),primary_status='unverified',interrupt_wait=True)
        self.assertEqual(result,130)
        self.assertEqual(report['exitReason'],'interrupted')
        self.assertEqual(report['pendingStockDates'],1)
        self.assertFalse(any(call[0]=='init_bao' for call in calls))
        self.assertTrue(pools[0].terminated)

    def test_cutoff_terminates_inflight_and_never_counts_unobserved_as_complete(self):
        result,report,calls,pools=self.exercise_scheduler(now(clock='21:54'),stuck=True)
        self.assertEqual(result,0)
        self.assertEqual(report['exitReason'],'cutoff')
        self.assertFalse(report['latestTraversalCompleted'])
        self.assertFalse(report['traversalCompleted'])
        self.assertEqual(report['todayAttemptedCount'],0)
        self.assertTrue(pools[0].terminated)
        self.assertEqual(len(calls),1)

    def test_restart_publishes_verified_cp_missing_from_aggregate_without_network(self):
        moment=now('2026-09-06')
        self.fixture(moment)
        daily_state.write_json(self.out/(DAY+'.json'),dict(date=DAY,generatedAt='old',rows=[]))
        daily_state.write_json(self.out/'manifest.json',dict(dates=[]))
        _,report=self.run_no_network(moment)
        self.assertEqual(report['recoveredRows'],1)
        self.assertEqual(report['httpRequests'],0)
        self.assertFalse(report['noOp'])
        published=daily_state.read_json(self.out/(DAY+'.json'))['rows'][0]
        self.assertEqual(published['ratio'],6.25)

    def test_restart_publishes_unverified_cp_over_old_verified_aggregate_without_network(self):
        moment=now()
        self.fixture(moment)
        conflict=dict(row(status='unverified'),dailyAttempts={'primary':DAY,'bao':DAY})
        daily_state.write_json(self.out/'checkpoint'/f'{CODE}.json',dict(code=CODE,fetchStatus='ok',days={OLD:row(),DAY:conflict}))
        daily_state.write_json(self.out/'manifest.json',dict(dates=[]))
        _,report=self.run_no_network(moment)
        self.assertEqual(report['recoveredRows'],1)
        self.assertEqual(report['httpRequests'],0)
        published=daily_state.read_json(self.out/(DAY+'.json'))['rows'][0]
        self.assertEqual(published['status'],'unverified')
        self.assertIsNone(published['ratio'])

    def test_restart_publishes_terminal_failure_cp_when_date_was_never_flushed(self):
        moment=now()
        self.fixture(moment)
        missing=dict(code=CODE,name='test',market='SH',status='missing',ratio=None,first15Volume=None,dailyVolume=None,
                     verificationVersion=3,reason='采集失败：TimeoutError',dailyAttempts={'primary':DAY,'bao':DAY})
        daily_state.write_json(self.out/'checkpoint'/f'{CODE}.json',dict(code=CODE,fetchStatus='ok',days={OLD:row(),DAY:missing}))
        (self.out/(DAY+'.json')).unlink()
        daily_state.write_json(self.out/'manifest.json',dict(dates=[]))
        _,report=self.run_no_network(moment)
        self.assertEqual(report['recoveredRows'],1)
        self.assertEqual(report['httpRequests'],0)
        self.assertTrue(report['latestTraversalCompleted'])
        published=daily_state.read_json(self.out/(DAY+'.json'))['rows'][0]
        self.assertEqual(published['status'],'missing')
        self.assertEqual(published['reason'],'采集失败：TimeoutError')
        self.assertIsNone(published['ratio'])

    def test_restart_missing_cp_never_erases_already_verified_aggregate(self):
        moment=now()
        self.fixture(moment)
        missing=dict(code=CODE,status='missing',ratio=None,verificationVersion=3,reason='采集失败：TimeoutError',dailyAttempts={'primary':DAY,'bao':DAY})
        daily_state.write_json(self.out/'checkpoint'/f'{CODE}.json',dict(code=CODE,fetchStatus='ok',days={OLD:row(),DAY:missing}))
        before=(self.out/(DAY+'.json')).read_bytes()
        _,report=self.run_no_network(moment)
        self.assertEqual(report['recoveredRows'],0)
        self.assertTrue(report['noOp'])
        self.assertEqual((self.out/(DAY+'.json')).read_bytes(),before)


if __name__=='__main__':unittest.main()
