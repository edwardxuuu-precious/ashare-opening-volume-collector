"""Invented automatic slot and checkpoint fixtures, without external calls."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import timedelta
from unittest.mock import patch

from tooling import chip_followup_guard as g
from backend.chips import runner
from test_chip_followup_guard import at, Clock, manifest
from test_chip_followup_late_inputs import MarkerClient, partial, collector, marker
from test_chip_daily_v1 import Memory, FakeSource, STOCK, DAYS

DAY = '2026-10-26'


def paused(**changes):
    value = {'schema': 'stock.chips.market.status.v1', 'state': 'PAUSED_BUDGET',
        'as_of': DAY, 'dataset_id': 'b'*16, 'universe_total': 5,
        'scanned_count': 3, 'model_ready_count': 2,
        'updated_at': DAY+'T20:42:00+08:00', 'workflow_run_id': '100'}
    value.update(changes)
    return value


class AutomaticSlots(unittest.TestCase):
    def test_exact_time_and_official_calendar_windows(self):
        for clock, slot, day, action in (
            ('2026-10-26T15:39:59','primary',DAY,'SKIP'),
            ('2026-10-26T15:40:00','primary',DAY,'READY'),
            ('2026-10-26T21:19:59','primary',DAY,'READY'),
            ('2026-10-26T21:20:00','primary',DAY,'SKIP'),
            ('2026-10-26T21:20:00','evening',DAY,'READY'),
            ('2026-10-27T00:00:00','evening',DAY,'SKIP'),
            ('2026-10-27T02:19:59','overnight',DAY,'SKIP'),
            ('2026-10-27T02:20:00','overnight',DAY,'READY'),
            ('2026-10-27T15:40:00','overnight',DAY,'SKIP'),
            ('2026-10-24T07:00:00','overnight','2026-10-23','READY'),
            ('2026-10-25T07:00:00','overnight','2026-10-23','SKIP')):
            with self.subTest(clock=clock,slot=slot):
                result=g.automatic_window(at(clock),slot,day)
                self.assertEqual(result['action'],action)
        for day in ('2026-10-25','2026-10-05'):
            self.assertEqual(g.automatic_window(at(day+'T21:20:00'),'evening',day)['action'],'SKIP')

    def test_invalid_origin_target_and_future_metadata_fail_closed(self):
        for slot,day in (('other',DAY),('primary','2026-02-30'),('evening','2026-10-27')):
            with self.assertRaises(ValueError):g.automatic_window(at(DAY+'T21:30:00'),slot,day)

    def inputs(self, clock, m=None, status=None, lease=None, existing=None):
        reads=[]
        def fetch(key):
            reads.append(key)
            values={g.MANIFEST:m,g.LEASE:lease,g.CHIP_STATUS:status,
                g.automatic_key(DAY,'evening'):existing}
            return values.get(key)
        return fetch,reads

    def test_paused_checkpoint_is_eligible_without_new_collector_input(self):
        clock=Clock(DAY+'T21:30:00')
        m=partial();m['generated_at']=DAY+'T18:35:00+08:00'
        result=g.decide(clock(),m,None,collector(publishedAt=DAY+'T16:30:00+08:00'),
            marker(),chip_status=paused())
        self.assertEqual(result['action'],'RUN')
        self.assertEqual(result['reason'],'PAUSED_CHECKPOINT_CONTINUATION_DUE')
        self.assertEqual(result['automatic_slot'],'evening')
        self.assertNotIn('repair_key',result)

    def test_old_invalid_complete_and_committed_checkpoints_do_not_resume(self):
        m=manifest(DAY)
        for change in ({'as_of':'2026-10-23'},{'state':'COMMITTED'},
                {'scanned_count':5},{'dataset_id':'a'*16}):
            self.assertFalse(g.checkpoint_pending(paused(**change),DAY,at(DAY+'T21:30:00'),m))
        for change in ({'schema':'OTHER'},{'scanned_count':True},{'scanned_count':6},
                {'model_ready_count':4},{'updated_at':DAY+'T22:00:00+08:00'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                g.checkpoint_pending(paused(**change),DAY,at(DAY+'T21:30:00'),m)

    def test_worker_timeout_continues_only_after_confirmed_cleanup(self):
        clock=Clock(DAY+'T21:30:00')
        for cleaned in (True,False,None):
            status=paused(state='PAUSED_WORKER_TIMEOUT',worker_cleanup_complete=cleaned)
            result=g.decide(clock(),partial(),None,collector(publishedAt=DAY+'T16:30:00+08:00'),
                chip_status=status)
            self.assertEqual(result['action']=='RUN',cleaned is True)
            if cleaned is True:self.assertEqual(result['reason'],'PAUSED_CHECKPOINT_CONTINUATION_DUE')
            else:
                self.assertEqual(result['reason'],'WORKER_CLEANUP_NOT_CONFIRMED')
                fetch,_=self.inputs(clock,m=partial(),status=status)
                self.assertEqual(g.run_automatic_guard(fetch,'evening',DAY,clock=clock,
                    claim=lambda *_:self.fail('No claim before confirmed cleanup'))['reason'],
                    'WORKER_CLEANUP_NOT_CONFIRMED')

    def test_duplicate_automatic_slot_has_one_conditional_claim(self):
        clock=Clock(DAY+'T21:30:00');client=MarkerClient()
        fetch,_=self.inputs(clock,m=partial(),status=paused())
        claim=lambda key,value:g.claim_slot(client,'INVENTED',key,value)
        first=g.run_automatic_guard(fetch,'evening',DAY,clock=clock,claim=claim)
        second=g.run_automatic_guard(fetch,'evening',DAY,clock=clock,claim=claim)
        self.assertEqual([first['should_run'],second['should_run']],['true','false'])
        self.assertEqual(len(client.objects),1)
        self.assertEqual(client.calls[0]['IfNoneMatch'],'*')
        self.assertEqual(first['max_minutes'],'285')

    def test_concurrent_native_and_aws_wakeup_cannot_compute_twice(self):
        client=MarkerClient();barrier=threading.Barrier(2)
        def one(trigger):
            clock=Clock(DAY+'T21:30:00');fetch,_=self.inputs(clock,m=partial(),status=paused())
            def claim(key,value):
                barrier.wait(timeout=3)
                return g.claim_slot(client,'INVENTED',key,value)
            return g.run_automatic_guard(fetch,'evening',DAY,clock=clock,claim=claim,trigger=trigger)
        with ThreadPoolExecutor(max_workers=2) as pool:
            values=list(pool.map(one,('schedule','aws-watchdog')))
        self.assertEqual(sorted(v['should_run'] for v in values),['false','true'])
        self.assertEqual(len(client.objects),1)

    def test_followup_pending_uses_slot_and_never_reads_or_reclaims_input_marker(self):
        clock=Clock(DAY+'T21:30:00');client=MarkerClient()
        fetch,reads=self.inputs(clock,m=partial(),status=paused())
        result=g.run_guard(fetch,clock,clock.sleep,lambda key,value:g.claim_slot(client,'INVENTED',key,value))
        self.assertEqual(result['should_run'],'true')
        self.assertNotIn(g.repair_key(DAY),reads)
        self.assertNotIn(g.COLLECTOR_STATUS,reads)
        stored=client.objects[g.automatic_key(DAY,'evening')]
        self.assertEqual(stored['trigger'],'workflow_run')
        fetch,_=self.inputs(clock,m=partial(),status=paused(),existing=stored)
        self.assertEqual(g.run_guard(fetch,clock,clock.sleep,lambda *_:self.fail('Spent slot'))['should_run'],'false')

    def test_paused_same_generation_cannot_hide_terminal_current_manifest(self):
        clock=Clock(DAY+'T21:30:00')
        m=partial();m['dataset_id']=m['active_slot']='b'*16
        self.assertFalse(g.checkpoint_pending(paused(),DAY,clock(),m))

    def test_active_lease_and_terminal_result_never_consume_slot(self):
        clock=Clock(DAY+'T21:30:00')
        def forbidden(*args):self.fail('No slot should be consumed')
        fetch,_=self.inputs(clock,m=partial(),lease={'expires':clock().timestamp()+1})
        self.assertEqual(g.run_automatic_guard(fetch,'evening',DAY,clock=clock,claim=forbidden)['reason'],
            'CHIP_WRITER_ACTIVE')
        m=manifest(DAY);identity=runner.implementation_identity()
        m.update(operational_complete=True,execution_identity=identity)
        fetch,_=self.inputs(clock,m=m,status={'invalid':'ignored'})
        self.assertEqual(g.run_automatic_guard(fetch,'evening',DAY,clock=clock,claim=forbidden)['reason'],
            'AUTOMATIC_TARGET_ALREADY_COMPLETE')
        # Source changes must re-evaluate the old decision under a new slot.
        m['execution_identity']={}
        fetch,_=self.inputs(clock,m=m)
        self.assertEqual(g.run_automatic_guard(fetch,'evening',DAY,clock=clock,claim=lambda *_:True)['should_run'],'true')

    def test_slot_claim_latency_is_charged_and_clock_rollover_cannot_run(self):
        clock=Clock(DAY+'T21:30:00');fetch,_=self.inputs(clock,m=partial())
        def slow_claim(*args):clock.value+=timedelta(minutes=2);return True
        self.assertEqual(g.run_automatic_guard(fetch,'evening',DAY,clock=clock,claim=slow_claim)['max_minutes'],'283')
        clock=Clock(DAY+'T23:59:59');fetch,_=self.inputs(clock,m=partial())
        def rollover(*args):clock.value+=timedelta(seconds=2);return True
        with self.assertRaises(ValueError):g.run_automatic_guard(fetch,'evening',DAY,clock=clock,claim=rollover)

    def test_aws_dispatch_requires_explicit_target_and_origin(self):
        cases=[{}, {'automatic_origin':'INVENTED','auto_slot':'evening','as_of':DAY},
            {'automatic_origin':'aws-watchdog','auto_slot':'evening'},
            {'automatic_origin':'aws-watchdog','auto_slot':'other','as_of':DAY}]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'event.json'
            for inputs in cases:
                path.write_text(json.dumps({'inputs':inputs}),encoding='utf-8')
                with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_EVENT_PATH':str(path)}),\
                    patch('sys.argv',['chip_followup_guard.py']),self.assertRaises(ValueError):
                    g.main()

    def test_workflow_runs_automatic_guard_for_both_providers_and_preserves_disable(self):
        source=Path('.github/workflows/chips.yml').read_text(encoding='utf-8')
        mirror=Path('deploy/actions/workflows/chips.yml').read_text(encoding='utf-8')
        self.assertEqual(source,mirror)
        self.assertIn("inputs.automatic_origin == '') || vars.DISABLE_CHIPS_UPDATE != 'true'",source)
        self.assertIn("github.event_name != 'workflow_dispatch' || inputs.automatic_origin != ''",source)
        self.assertIn("steps.followup.outputs.should_run == 'true'",source)


class OperationalCompletion(unittest.TestCase):
    def test_terminal_rows_stop_recomputation_without_certifying_catalog(self):
        class CatalogSource(FakeSource):
            def universe(self):
                return [STOCK],[{'provider':'SITE_PUBLISHED_CURRENT_UNIVERSE'}]
        store=Memory()
        with patch.object(runner,'Source',CatalogSource):
            first=runner.run(store,as_of=DAYS[-1],max_minutes=2,workers=1)
            value=store.get(g.MANIFEST)[0]
            self.assertEqual(first['outcome'],'COMMITTED')
            self.assertTrue(value['operational_complete'])
            self.assertFalse(value['calculation_complete'])
            self.assertFalse(value['market_complete'])
            self.assertEqual(value['universe_coverage'],'SITE_CATALOG_UNVERIFIED')
            before=FakeSource.calls
            second=runner.run(store,as_of=DAYS[-1],max_minutes=2,workers=1)
            self.assertEqual(second['outcome'],'ALREADY_COMMITTED')
            self.assertEqual(FakeSource.calls,before)

    def test_conclusive_failure_needs_no_other_unknown_condition(self):
        rows=[{'chip_status':'READY','screen_status':'FAIL','prefilter_status':'UNKNOWN',
            'price_position_status':'FAIL'}]
        self.assertTrue(runner.operational_complete(rows))
        rows[0]['chip_status']='MISSING'
        self.assertFalse(runner.operational_complete(rows))

    def test_terminal_precision_result_is_complete_but_missing_evidence_is_not(self):
        row={'chip_status':'READY','screen_status':'UNKNOWN','prefilter_status':'PASS',
            'price_position_status':'PASS','chip_stability':{'rule_id':g.RULE_ID,'status':'UNKNOWN',
            'reasons':['NATIVE_RESOLUTION_INSUFFICIENT']}}
        self.assertTrue(runner.operational_complete([row]))
        row['chip_stability']['reasons']=['CHIP_HISTORY_INCOMPLETE']
        self.assertFalse(runner.operational_complete([row]))


if __name__=='__main__':unittest.main()
