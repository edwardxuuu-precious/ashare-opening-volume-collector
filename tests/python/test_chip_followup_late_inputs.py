"""Invented one-per-day late-input handoffs; no AWS, GitHub or market access."""
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
from test_chip_followup_guard import Clock, at, manifest


DAY='2026-10-26'
NOW=at(DAY+'T17:00:00')


def partial(*, legacy=False):
    value=manifest(DAY)
    value.update(model_ready_count=4,generated_at=DAY+'T15:55:00+08:00')
    if not legacy:value['input_repair']={'schema':'stock.chips.input-repair.v1','model_missing_count':1}
    return value


def collector(**changes):
    value={'targetDate':DAY,'dataComplete':True,'priceDataComplete':True,
        'publishedAt':DAY+'T16:30:00+08:00'}
    value.update(changes)
    return value


def marker():
    return {'schema':'stock.chips.followup-repair.v1','as_of':DAY,'attempts':1,
        'source_dataset_id':'a'*16,'source_generated_at':DAY+'T15:55:00+08:00',
        'collector_progress_at':DAY+'T16:30:00+08:00','model_missing_count':1,
        'claimed_at':DAY+'T16:45:00+08:00'}


class ClaimedElsewhere(Exception):
    response={'Error':{'Code':'PreconditionFailed'}}


class MarkerClient:
    def __init__(self):self.objects={};self.lock=threading.Lock();self.calls=[]
    def put_object(self,**kwargs):
        with self.lock:
            self.calls.append(kwargs)
            if kwargs['Key'] in self.objects:raise ClaimedElsewhere()
            self.objects[kwargs['Key']]=json.loads(kwargs['Body'])
        return {}


class LateDecisions(unittest.TestCase):
    def test_one_missing_model_requires_later_current_day_collector_progress(self):
        for legacy in (False,True):
            with self.subTest(legacy=legacy):
                value=partial(legacy=legacy);before=copy.deepcopy(value)
                result=g.decide(NOW,value,None,collector())
                self.assertEqual(result['action'],'RUN')
                self.assertEqual(result['reason'],'TODAY_MODEL_INPUT_REPAIR_DUE')
                self.assertEqual(result['model_missing_count'],1)
                self.assertEqual(result['repair_key'],g.repair_key(DAY))
                self.assertEqual(value,before)

    def test_terminal_comparison_unknown_or_coverage_false_with_all_models_ready_does_not_repair(self):
        for value in (manifest(DAY),{**partial(),'model_ready_count':5,
                'input_repair':{'schema':'stock.chips.input-repair.v1','model_missing_count':0}}):
            self.assertFalse(value['calculation_complete']);self.assertFalse(value['market_complete'])
            self.assertEqual(g.decide(NOW,value,None,collector())['reason'],'TODAY_ALREADY_PUBLISHED')

    def test_incomplete_core_and_prices_or_wrong_target_cannot_claim_progress(self):
        cases=[(collector(targetDate='2026-10-23'),'COLLECTOR_TARGET_NOT_TODAY'),
            (collector(dataComplete=False,priceDataComplete=False),'COLLECTOR_NOT_COMPLETE'),
            (None,'COLLECTOR_PROGRESS_REQUIRED'),
            (collector(publishedAt=None),'COLLECTOR_PROGRESS_REQUIRED'),
            (collector(publishedAt=DAY+'T15:55:00+08:00'),'COLLECTOR_NO_NEW_PROGRESS'),
            (collector(publishedAt=DAY+'T15:54:59+08:00'),'COLLECTOR_NO_NEW_PROGRESS')]
        for status,reason in cases:
            with self.subTest(reason=reason):
                result=g.decide(NOW,partial(),None,status)
                self.assertEqual(result['action'],'SKIP');self.assertEqual(result['reason'],reason)

    def test_either_completed_domain_and_available_latest_progress_clock_can_qualify(self):
        for field in ('publishedAt','fullyPublishedAt','firstPassCompletedAt'):
            for core,price in ((True,False),(False,True)):
                status=collector(publishedAt=None,dataComplete=core,priceDataComplete=price)
                status[field]=DAY+'T16:30:00+08:00'
                self.assertEqual(g.decide(NOW,partial(),None,status)['action'],'RUN')

    def test_current_lease_and_spent_daily_marker_prevent_an_additional_attempt(self):
        self.assertEqual(g.decide(NOW,partial(),{'expires':NOW.timestamp()+1},collector())['reason'],'CHIP_WRITER_ACTIVE')
        self.assertEqual(g.decide(NOW,partial(),None,collector(),marker())['reason'],
            'DAILY_MODEL_INPUT_REPAIR_ALREADY_ATTEMPTED')

    def test_input_count_schema_and_manifest_generation_errors_fail_closed(self):
        invalid=[]
        for value in (True,-1,6,'1',None):
            m=partial();m['input_repair']['model_missing_count']=value;invalid.append(m)
        for value in (True,-1,6,'4',None):
            m=partial(legacy=True);m['model_ready_count']=value;invalid.append(m)
        for value in ({},[],None,{'schema':'OTHER','model_missing_count':1}):
            m=partial();m['input_repair']=value;invalid.append(m)
        m=partial();m['input_repair']['model_missing_count']=2;invalid.append(m)
        for value in (None,'not-a-time',DAY+'T15:55:00',DAY+'T18:00:00+08:00','2026-10-23T15:55:00+08:00'):
            m=partial();m['generated_at']=value;invalid.append(m)
        for m in invalid:
            with self.subTest(manifest=m),self.assertRaises(ValueError):g.decide(NOW,m,None,collector())

    def test_collector_wrong_types_naive_future_and_wrong_day_progress_fail_closed(self):
        changes=[{'targetDate':'2026-02-30'},{'targetDate':None},{'dataComplete':'true'},
            {'priceDataComplete':1},{'publishedAt':'broken'},{'publishedAt':DAY+'T16:30:00'},
            {'publishedAt':DAY+'T17:01:00+08:00'},{'publishedAt':'2026-10-23T16:30:00+08:00'}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):
                g.decide(NOW,partial(),None,collector(**change))

    def test_invalid_existing_marker_never_opens_a_second_attempt(self):
        for change in ({'schema':'OTHER'},{'as_of':'2026-10-23'},{'attempts':True},{'attempts':2},
            {'source_dataset_id':'wrong'},{'model_missing_count':0},{'claimed_at':DAY+'T18:00:00+08:00'},
            {'collector_progress_at':DAY+'T15:54:00+08:00'}):
            value=marker();value.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                g.decide(NOW,partial(),None,collector(),value)


class LateHandoffs(unittest.TestCase):
    def inputs(self,clock=None,stored=None):
        reads=[];clock=clock or Clock(DAY+'T17:00:00')
        def fetch(key):
            reads.append(key)
            if key==g.MANIFEST:return partial()
            if key==g.LEASE:return None
            if key==g.CHIP_STATUS:return None
            if key==g.COLLECTOR_STATUS:return collector()
            if key==g.repair_key(DAY):return stored
            raise AssertionError(key)
        return fetch,reads,clock

    def test_single_daily_claim_uses_conditional_put_and_is_never_reset(self):
        fetch,reads,clock=self.inputs();client=MarkerClient()
        result=g.run_guard(fetch,clock,clock.sleep,lambda key,value:g.claim_repair(client,'INVENTED',key,value))
        self.assertEqual(result['should_run'],'true');self.assertEqual(result['max_minutes'],'285')
        self.assertEqual(len(client.calls),1);call=client.calls[0]
        self.assertEqual(call['IfNoneMatch'],'*');self.assertEqual(call['ServerSideEncryption'],'AES256')
        self.assertEqual(reads,[g.MANIFEST,g.LEASE,g.CHIP_STATUS,g.COLLECTOR_STATUS,g.repair_key(DAY),g.LEASE])
        fetch,_,clock=self.inputs(stored=client.objects[g.repair_key(DAY)])
        def forbidden(*args):self.fail('Spent marker was claimed again')
        self.assertEqual(g.run_guard(fetch,clock,clock.sleep,forbidden)['should_run'],'false')

    def test_two_simultaneous_guards_have_exactly_one_cas_winner(self):
        client=MarkerClient();barrier=threading.Barrier(2)
        def one(_):
            fetch,_,clock=self.inputs()
            def claim(key,value):
                barrier.wait(timeout=3)
                return g.claim_repair(client,'INVENTED',key,value)
            return g.run_guard(fetch,clock,clock.sleep,claim)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,range(2)))
        self.assertEqual(sorted(r['should_run'] for r in results),['false','true'])
        self.assertEqual(len(client.objects),1)
        self.assertIn('DAILY_MODEL_INPUT_REPAIR_CLAIM_CONFLICT',[r['reason'] for r in results])

    def test_lease_appearing_after_collector_read_prevents_claim(self):
        fetch,_,clock=self.inputs();reads=0
        def changing(key):
            nonlocal reads
            if key==g.LEASE:
                reads+=1
                if reads==2:return {'expires':clock().timestamp()+60}
            return fetch(key)
        def forbidden(*args):self.fail('Active chip writer must prevent claiming')
        result=g.run_guard(changing,clock,clock.sleep,forbidden)
        self.assertEqual(result['reason'],'CHIP_WRITER_ACTIVE');self.assertEqual(result['should_run'],'false')

    def test_exhausted_budget_cannot_consume_daily_claim_and_claim_time_is_deducted(self):
        fetch,_,clock=self.inputs()
        def slow(key):
            value=fetch(key)
            if key==g.COLLECTOR_STATUS:clock.value+=timedelta(minutes=284)
            return value
        def forbidden(*args):self.fail('Budget exhausted before claim')
        with self.assertRaisesRegex(ValueError,'BUDGET_EXHAUSTED'):g.run_guard(slow,clock,clock.sleep,forbidden)
        fetch,_,clock=self.inputs()
        def delayed_claim(key,value):clock.value+=timedelta(minutes=2);return True
        self.assertEqual(g.run_guard(fetch,clock,clock.sleep,delayed_claim)['max_minutes'],'283')

    def test_missing_claim_function_and_storage_access_error_never_start_a_repair(self):
        fetch,_,clock=self.inputs()
        with self.assertRaisesRegex(ValueError,'CLAIM_REQUIRED'):g.run_guard(fetch,clock,clock.sleep)
        class Denied(Exception):response={'Error':{'Code':'AccessDenied'}}
        class Client:
            def put_object(self,**kwargs):raise Denied()
        with self.assertRaises(Denied):g.claim_repair(Client(),'INVENTED',g.repair_key(DAY),marker())

    def test_complete_or_unpublished_paths_never_claim_input_repair(self):
        for m in (None,manifest(DAY),manifest('2026-10-23')):
            reads=[];clock=Clock(DAY+'T17:00:00')
            def fetch(key):reads.append(key);return m if key==g.MANIFEST else None
            claims=[]
            def claim(key,value):
                claims.append(key)
                self.assertEqual(value['schema'],'stock.chips.automatic-slot.v1')
                return True
            result=g.run_guard(fetch,clock,clock.sleep,claim)
            if m and m['as_of']==DAY:
                self.assertEqual(reads,[g.MANIFEST,g.LEASE,g.CHIP_STATUS]);self.assertEqual(claims,[])
            else:
                self.assertEqual(claims,[g.automatic_key(DAY,'primary')])
            self.assertEqual(result['should_run'],'false' if m and m['as_of']==DAY else 'true')

    def test_verify_main_reads_only_manifest_and_never_claims_a_marker(self):
        event={'action':'completed','workflow_run':{'status':'completed','name':g.WORKFLOW,
            'head_branch':'main','head_repository':{'full_name':g.REPOSITORY}}}
        reads=[]
        def read(_client,_bucket,key):reads.append(key);return partial()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'invented-event.json';path.write_text(json.dumps(event),encoding='utf-8')
            with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_run','GITHUB_EVENT_PATH':str(path),'STOCK_BUCKET':'INVENTED'}),\
                patch('sys.argv',['chip_followup_guard.py','--verify-as-of',DAY]),\
                patch('boto3.client',return_value=object()),patch.object(g,'read_metadata',side_effect=read),\
                patch.object(g,'claim_repair',side_effect=AssertionError('Verification cannot claim')),redirect_stdout(io.StringIO()):
                g.main()
        self.assertEqual(reads,[g.MANIFEST])


if __name__=='__main__':unittest.main()
