"""Invented late-input and immutable-resume cases; no cloud or source calls."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from backend.chips import runner
from backend.chips.current_quote import daily_line
from test_chip_daily_v1 import DAYS,LINES,Memory,STOCK,FakeSource

DAY=DAYS[-1]
SECOND={**STOCK,'code':'000608','secid':'0.000608'}


def quote(code,*,zero=False,invalid=False):
    fields=['0']*55
    for key,value in {2:code,3:'4.50',4:'4.50',5:'4.50',6:'100',30:DAY.replace('-','')+'154001',
        31:'0',32:'0',33:'4.50',34:'4.50',35:'4.50/100/45000',36:'100',37:'4.50',
        38:'1.00',43:'0',47:'4.95'}.items():fields[key]=value
    if zero:fields[6]=fields[36]='0';fields[35]='4.50/0/0'
    if invalid:fields[38]=''
    body='~'.join(fields)
    result={'date':DAY,'close':'4.50','reference_close':'4.50','upper_limit':'4.95',
        'raw_quote_body':body,'raw_quote_sha256':hashlib.sha256(body.encode('gb18030')).hexdigest()}
    if not invalid:result['daily_line']=daily_line(code,DAY,body)
    return result


class QuoteRefresh(unittest.TestCase):
    def test_partial_cache_fetches_only_missing_and_preserves_complete_quote(self):
        store=Memory();original=quote(STOCK['code']);late=quote(SECOND['code'])
        key=f'collector/chips/limits/{DAY}.json'
        store.put(key,{'quotes':{STOCK['code']:original},'receipts':[{'sha256':'a'*64}]})
        before=copy.deepcopy(store.data[key])
        with patch.object(runner,'Storage',Memory),patch.object(runner,'collect_final_quotes',return_value=(
                {SECOND['code']:late},[{'sha256':'b'*64}])) as collect:
            result,receipts,changed=runner.refresh_daily_quotes(store,object(),[STOCK,SECOND],DAY,before['quotes'])
        self.assertEqual(collect.call_args.args[1],[SECOND])
        self.assertTrue(changed)
        self.assertEqual(result[STOCK['code']],original)
        self.assertEqual(store.data[key]['quotes'][STOCK['code']],original)
        self.assertEqual(result[SECOND['code']],late)
        self.assertEqual(receipts,[{'sha256':'a'*64},{'sha256':'b'*64}])
        self.assertEqual(store.version[key],2)

    def test_valid_zero_trade_and_complete_quotes_do_not_refetch(self):
        store=Memory();current={STOCK['code']:quote(STOCK['code']),SECOND['code']:quote(SECOND['code'],zero=True)}
        with patch.object(runner,'Storage',Memory),patch.object(runner,'collect_final_quotes') as collect:
            result,_,changed=runner.refresh_daily_quotes(store,object(),[STOCK,SECOND],DAY,current)
        collect.assert_not_called();self.assertEqual(result,current);self.assertFalse(changed)

    def test_invalid_daily_line_and_missing_original_body_are_refreshed(self):
        current={STOCK['code']:quote(STOCK['code'],invalid=True),SECOND['code']:{'date':DAY,'daily_line':'UNTRUSTED'}}
        fresh={stock['code']:quote(stock['code']) for stock in (STOCK,SECOND)}
        with patch.object(runner,'Storage',Memory),patch.object(runner,'collect_final_quotes',return_value=(fresh,[])) as collect:
            result,_,changed=runner.refresh_daily_quotes(Memory(),object(),[STOCK,SECOND],DAY,current)
        self.assertEqual(collect.call_args.args[1],[STOCK,SECOND]);self.assertTrue(changed)
        self.assertEqual(result,fresh)

    def test_new_shared_cache_fact_fills_frozen_gap_without_network(self):
        store=Memory();current={STOCK['code']:quote(STOCK['code'])};late=quote(SECOND['code'])
        store.put(f'collector/chips/limits/{DAY}.json',{'quotes':{**current,SECOND['code']:late},'receipts':[]})
        with patch.object(runner,'Storage',Memory),patch.object(runner,'collect_final_quotes') as collect:
            result,_,changed=runner.refresh_daily_quotes(store,object(),[STOCK,SECOND],DAY,current)
        collect.assert_not_called();self.assertTrue(changed);self.assertEqual(result[SECOND['code']],late)

    def test_bad_hash_or_unrequested_security_is_never_adopted(self):
        corrupt=quote(STOCK['code']);corrupt['raw_quote_sha256']='0'*64
        with patch.object(runner,'Storage',Memory),patch.object(runner,'collect_final_quotes') as collect:
            with self.assertRaisesRegex(ValueError,'HASH_CONFLICT'):
                runner.refresh_daily_quotes(Memory(),object(),[STOCK],DAY,{STOCK['code']:corrupt})
        collect.assert_not_called()
        with patch.object(runner,'Storage',Memory),patch.object(runner,'collect_final_quotes',return_value=({SECOND['code']:quote(SECOND['code'])},[])):
            with self.assertRaisesRegex(ValueError,'SECURITY_MISMATCH'):
                runner.refresh_daily_quotes(Memory(),object(),[STOCK],DAY,{})

    def test_quote_cache_write_uses_observed_cas(self):
        store=Memory();key=f'collector/chips/limits/{DAY}.json'
        store.put(key,{'quotes':{},'receipts':[]})
        original_put=store.put
        def racing(source,stocks,day):
            original_put(key,{'quotes':{},'receipts':[{'OTHER_INVENTED_WRITER':True}]},match='1')
            return {STOCK['code']:quote(STOCK['code'])},[]
        with patch.object(runner,'Storage',Memory),patch.object(runner,'collect_final_quotes',side_effect=racing):
            with self.assertRaisesRegex(RuntimeError,'PRECONDITION_FAILED'):
                runner.refresh_daily_quotes(store,object(),[STOCK],DAY,{})
        self.assertEqual(store.data[key]['receipts'],[{'OTHER_INVENTED_WRITER':True}])


class ResumeInputs(unittest.TestCase):
    def checkpoint(self,store,current):
        state={'as_of':DAY,'dataset_id':'a'*16,'active_slot':'a'*16,'stocks':[STOCK,SECOND],
            'execution_identity':{'sha256':'INVENTED'},'actual_limits':{DAY:copy.deepcopy(current)},
            'rows':{STOCK['code']:{'chip_status':'READY'}},'limit_receipts':[]}
        etag=store.put('collector/chips/state.json',state)
        store.put(f'data/chips/daily/{"a"*16}/{STOCK["code"]}.json',{'immutable':'INVENTED_OLD_FRAME'})
        return state,etag

    def test_late_quote_rotates_and_archives_entire_original_checkpoint(self):
        store=Memory();state,etag=self.checkpoint(store,{STOCK['code']:quote(STOCK['code'])})
        old=copy.deepcopy(state);old_frame=copy.deepcopy(store.data[f'data/chips/daily/{"a"*16}/{STOCK["code"]}.json'])
        with patch.object(runner,'Storage',Memory),patch.object(runner,'collect_final_quotes',return_value=({SECOND['code']:quote(SECOND['code'])},[])):
            fresh,_=runner.resume_quote_inputs(store,object(),state,etag)
        self.assertEqual(state,old)
        self.assertNotEqual(fresh['dataset_id'],old['dataset_id']);self.assertEqual(fresh['previous_slot'],old['active_slot'])
        self.assertEqual(fresh['rows'],{})
        self.assertEqual(store.data[fresh['quote_refresh_parent_checkpoint']],old)
        self.assertEqual(store.data[f'data/chips/daily/{"a"*16}/{STOCK["code"]}.json'],old_frame)

    def test_no_new_quote_continues_exact_same_generation_and_rows(self):
        store=Memory();state,etag=self.checkpoint(store,{STOCK['code']:quote(STOCK['code'])})
        with patch.object(runner,'Storage',Memory),patch.object(runner,'collect_final_quotes',return_value=({},[{'error':'INVENTED_NOT_READY'}])) as collect:
            fresh,new_etag=runner.resume_quote_inputs(store,object(),state,etag)
        collect.assert_called_once();self.assertEqual(fresh['dataset_id'],'a'*16)
        self.assertEqual(fresh['rows'],state['rows']);self.assertEqual(new_etag,etag)
        self.assertFalse(any(key.startswith('collector/chips/recovery/') for key in store.data))

    def test_still_invalid_changed_body_is_cached_but_never_rotates_frozen_inputs(self):
        store=Memory()
        current=runner.renormalize_final_quotes({STOCK['code']:quote(STOCK['code']),
            SECOND['code']:quote(SECOND['code'],invalid=True)},DAY)
        state,etag=self.checkpoint(store,current)
        original=copy.deepcopy(state)
        fresh_invalid=quote(SECOND['code'],invalid=True)
        fresh_invalid['raw_quote_body']=fresh_invalid['raw_quote_body'].replace('154001','154501')
        fresh_invalid['raw_quote_sha256']=hashlib.sha256(fresh_invalid['raw_quote_body'].encode('gb18030')).hexdigest()
        fresh_invalid['diagnostic_attempt']='INVENTED_LATER_INVALID_OBSERVATION'
        with patch.object(runner,'Storage',Memory),patch.object(runner,'collect_final_quotes',return_value=(
                {SECOND['code']:fresh_invalid},[{'error':'INVENTED_STILL_INVALID'}])) as collect:
            resumed,new_etag=runner.resume_quote_inputs(store,object(),state,etag)
        collect.assert_called_once()
        self.assertEqual(resumed['actual_limits'],original['actual_limits'])
        self.assertEqual(resumed['dataset_id'],original['dataset_id'])
        self.assertEqual(resumed['rows'],original['rows']);self.assertEqual(new_etag,etag)
        self.assertFalse(any(key.startswith('collector/chips/recovery/') for key in store.data))
        cached=store.data[f'collector/chips/limits/{DAY}.json']['quotes'][SECOND['code']]
        self.assertEqual(cached['raw_quote_sha256'],fresh_invalid['raw_quote_sha256'])
        self.assertTrue(cached['daily_line_error'])


class Integration(unittest.TestCase):
    def source(self,*,late=False):
        class LateSource(FakeSource):
            requests=[];ready=late;history_calls=0
            def universe(self):return [STOCK,SECOND],[]
            def pools(self,days):return {day:{} for day in days},[]
            def actual_limits(self,stocks,day):
                type(self).requests.append([stock['code'] for stock in stocks])
                return {stock['code']:quote(stock['code']) for stock in stocks
                        if stock['code']==STOCK['code'] or type(self).ready},[]
            def history(self,stock,day,cached=None):
                type(self).history_calls+=1
                value={'rc':0,'data':{'code':stock['code'],'name':stock['name'],'klines':LINES}}
                raw=json.dumps(value).encode()
                return value,{'provider':'THS_UNADJUSTED','sha256':hashlib.sha256(raw).hexdigest()},raw
        return LateSource

    def test_late_quote_repairs_committed_missing_model_without_overwriting_frames(self):
        source=self.source();store=Memory()
        with patch.object(runner,'Source',source),patch.object(runner,'target_day',return_value=DAY):
            first=runner.run(store,as_of=DAY,max_minutes=2,workers=1)
            old_manifest=store.get('data/chips/daily/manifest.json')[0]
            prefix=f'data/chips/daily/{old_manifest["dataset_id"]}/'
            old_frames={key:copy.deepcopy(value) for key,value in store.data.items() if key.startswith(prefix)}
            self.assertEqual(first['model_ready'],1)
            self.assertEqual(old_manifest['input_repair']['model_missing_count'],1)
            source.ready=True
            second=runner.run(store,as_of=DAY,max_minutes=2,workers=1)
        manifest=store.get('data/chips/daily/manifest.json')[0]
        self.assertEqual(second['model_ready'],2);self.assertNotEqual(manifest['dataset_id'],old_manifest['dataset_id'])
        self.assertEqual(manifest['input_repair'],{'schema':'stock.chips.input-repair.v1','model_missing_count':0})
        self.assertEqual(source.requests,[[STOCK['code'],SECOND['code']],[SECOND['code']]])
        for key,value in old_frames.items():self.assertEqual(store.data[key],value)

    def test_paused_complete_frames_resume_without_rotation_or_history_recalculation(self):
        source=self.source(late=True);store=Memory();put=store.put;failed=False
        def fail_commit(key,value,**kwargs):
            nonlocal failed
            if key=='data/chips/daily/manifest.json' and not failed:
                failed=True;raise RuntimeError('INVENTED_PAUSE_BEFORE_MANIFEST')
            return put(key,value,**kwargs)
        store.put=fail_commit
        with patch.object(runner,'Source',source),patch.object(runner,'target_day',return_value=DAY),patch.dict('os.environ',{'GITHUB_RUN_ID':'INVENTED_RUN_ID'}):
            with self.assertRaisesRegex(RuntimeError,'INVENTED_PAUSE'):
                runner.run(store,as_of=DAY,max_minutes=2,workers=1)
            original=store.get('collector/chips/state.json')[0];history_calls=source.history_calls
            self.assertEqual(runner.run(store,as_of=DAY,max_minutes=2,workers=1)['outcome'],'COMMITTED')
        self.assertEqual(store.get('data/chips/daily/manifest.json')[0]['dataset_id'],original['dataset_id'])
        self.assertEqual(source.history_calls,history_calls)
        self.assertEqual(len(source.requests),1)
        status=store.get('data/chips/daily/status.json')[0]
        self.assertEqual(status['workflow_run_id'],'INVENTED_RUN_ID')
        self.assertTrue(status['lease_owner'])

    def test_paused_missing_frame_rotates_when_quote_arrives_and_retains_old_evidence(self):
        source=self.source();store=Memory();put=store.put;failed=False
        def fail_commit(key,value,**kwargs):
            nonlocal failed
            if key=='data/chips/daily/manifest.json' and not failed:
                failed=True;raise RuntimeError('INVENTED_PAUSE_WITH_MISSING_QUOTE')
            return put(key,value,**kwargs)
        store.put=fail_commit
        with patch.object(runner,'Source',source),patch.object(runner,'target_day',return_value=DAY):
            with self.assertRaisesRegex(RuntimeError,'INVENTED_PAUSE'):
                runner.run(store,as_of=DAY,max_minutes=2,workers=1)
            checkpoint=store.get('collector/chips/state.json')[0]
            self.assertEqual(checkpoint['rows'][SECOND['code']]['chip_status'],'MISSING')
            old_prefix=f'data/chips/daily/{checkpoint["active_slot"]}/'
            frozen={key:copy.deepcopy(value) for key,value in store.data.items() if key.startswith(old_prefix)}
            source.ready=True
            result=runner.run(store,as_of=DAY,max_minutes=2,workers=1)
        self.assertEqual(result['model_ready'],2)
        current=store.get('collector/chips/state.json')[0]
        self.assertNotEqual(current['active_slot'],checkpoint['active_slot'])
        self.assertEqual(current['previous_slot'],checkpoint['active_slot'])
        self.assertEqual(store.data[current['quote_refresh_parent_checkpoint']],checkpoint)
        self.assertEqual(source.requests,[[STOCK['code'],SECOND['code']],[SECOND['code']]])
        for key,value in frozen.items():self.assertEqual(store.data[key],value)

    def test_operations_identity_change_preserves_guarded_ready_frame_reuse_entry(self):
        source=self.source(late=True);store=Memory();put=store.put;failed=False
        def fail_commit(key,value,**kwargs):
            nonlocal failed
            if key=='data/chips/daily/manifest.json' and not failed:
                failed=True;raise RuntimeError('INVENTED_PAUSE_BEFORE_OPERATIONS_RELEASE')
            return put(key,value,**kwargs)
        store.put=fail_commit
        with patch.object(runner,'Source',source),patch.object(runner,'target_day',return_value=DAY):
            with self.assertRaisesRegex(RuntimeError,'INVENTED_PAUSE'):
                runner.run(store,as_of=DAY,max_minutes=2,workers=1)
            original=store.get('collector/chips/state.json')[0];history_calls=source.history_calls
            previous_slot=original['active_slot']
            frames={key:copy.deepcopy(value) for key,value in store.data.items()
                    if key.startswith(f'data/chips/daily/{previous_slot}/')}
            updated_identity=copy.deepcopy(original['execution_identity'])
            updated_identity['sha256']='b'*64;updated_identity['files']['runner.py']='c'*64
            with patch.object(runner,'implementation_identity',return_value=updated_identity):
                self.assertEqual(runner.run(store,as_of=DAY,max_minutes=2,workers=1)['model_ready'],2)
        current=store.get('collector/chips/state.json')[0]
        self.assertNotEqual(current['active_slot'],previous_slot);self.assertEqual(current['previous_slot'],previous_slot)
        self.assertEqual(source.history_calls,history_calls)
        for stock in (STOCK,SECOND):
            frame=store.data[f'data/chips/daily/{current["active_slot"]}/{stock["code"]}.json']
            self.assertEqual(frame['reused_from_generation'],previous_slot)
            self.assertEqual(frame['calculation_execution_identity'],original['execution_identity'])
        for key,value in frames.items():self.assertEqual(store.data[key],value)
        archives=[value for key,value in store.data.items() if key.startswith('collector/chips/recovery/')]
        self.assertIn(original,archives)

    def test_storage_sets_explicit_finite_network_config(self):
        with patch('boto3.client') as client:
            runner.Storage('INVENTED_BUCKET','us-east-1')
        config=client.call_args.kwargs['config']
        self.assertEqual((config.connect_timeout,config.read_timeout),(7,25))
        self.assertEqual(config.retries,{'total_max_attempts':2})


if __name__=='__main__':unittest.main()
