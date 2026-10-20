"""New process-worker contracts using ALL_VALUES_INVENTED in-memory data."""
import base64,copy,hashlib,json,time,unittest
from backend.chips.runner import _calculate,implementation_identity,compatible_state
from backend.chips.eastmoney_model import prepare_eastmoney,estimate_snapshots,compare_snapshots
from backend.chips.calendar import last_ten
from backend.chips.source import merge_cached
from test_chip_daily_v1 import Memory

class WorkerV3(unittest.TestCase):
    def test_v4_warm_changed_session_matches_full_recurrence_and_keeps_state_private(self):
        store=Memory();owner='INVENTED_OWNER';store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+1000})
        stock={'code':'000001','name':'ALL_VALUES_INVENTED'}
        class Source:
            cached_input=None
            def history(self,stock,as_of,cached=None):
                self.cached_input=cached
                lines=[f'{d},5.00,5.00,5.02,4.98,10,{4990 if d=="2026-10-19" else 5000},,0,0,{3.7 if d=="2026-10-19" else 2.5}' for d in sorted(set(last_ten('2026-10-16')+last_ten(as_of)))]
                raw=json.dumps({'rc':0,'data':{'code':stock['code'],'name':stock['name'],'klines':lines}}).encode()
                return json.loads(raw),{'sha256':hashlib.sha256(raw).hexdigest(),'provider':'EASTMONEY'},raw
        source=Source()
        def ctx(day,batch):return {'slot':batch,'dataset':batch,'days':last_ten(day),'as_of':day,'owner':owner,'pools':{},'actual_limits':{},'execution_identity':implementation_identity()}
        a=_calculate(store,source,stock,ctx('2026-10-16','1111111111111111'))
        self.assertEqual(a['chip_status'],'READY');self.assertIsNone(source.cached_input)
        b=_calculate(store,source,stock,ctx('2026-10-19','2222222222222222'))
        self.assertEqual(b['chip_status'],'READY');self.assertIsNotNone(source.cached_input)
        published=store.get('data/chips/daily/2222222222222222/000001.json')[0]
        saved=store.get('collector/chips/raw/2222222222222222/000001.json')[0]
        bars,_=prepare_eastmoney(base64.b64decode(saved['raw_base64']),expected_code=stock['code'],cutoff='2026-10-19')
        cold=estimate_snapshots(bars,last_ten('2026-10-19')[-2:],security_id=stock['code'])
        for day,snapshot in published['snapshots'].items():
            calculation={k:v for k,v in snapshot.items() if k not in ('comparison_anchor_shares_percent','comparison_anchor_definition')}
            self.assertEqual(calculation,cold[day])
        expected=compare_snapshots(cold['2026-10-16'],cold['2026-10-19'])
        self.assertEqual(b['original_peak_changes'][0]['change_percentage_points'],expected['anchors'][0]['change_percentage_points'])
        self.assertNotIn('_model_state',published);self.assertNotIn('_source_history',published)
    def test_v4_incompatible_batch_rejected_even_when_empty_or_partially_successful(self):
        identity=implementation_identity()
        from backend.chips.source import Source
        state={'execution_identity':identity,'source_version':Source.VERSION,'dataset_id':'123456789abcdef0','active_slot':'123456789abcdef0','rows':{}}
        self.assertTrue(compatible_state(state,identity))
        for rows in ({},{'000001':{'close_cny':'5.0'}}):
            old={**state,'rows':rows,'active_slot':'a'}
            self.assertFalse(compatible_state(old,identity))
        changed=copy.deepcopy(identity);changed['sha256']='CHANGED_INVENTED_IMPLEMENTATION'
        self.assertFalse(compatible_state(state,changed))
    def test_lost_owner_cannot_publish_even_after_a_source_read(self):
        store=Memory();store.put('collector/chips/lease.json',{'owner':'OTHER_INVENTED_OWNER','expires':time.time()+1000})
        class Source:
            def history(self,*a,**k):return {},{'sha256':hashlib.sha256(b'{}').hexdigest()},b'{}'
        ctx={'slot':'3333333333333333','dataset':'3333333333333333','days':last_ten('2026-10-16'),'as_of':'2026-10-16','owner':'INVENTED_OWNER','pools':{},'actual_limits':{}}
        with self.assertRaisesRegex(RuntimeError,'LEASE_LOST'):_calculate(store,Source(),{'code':'000001','name':'INVENTED'},ctx)
        self.assertFalse(any(k.startswith('data/') for k in store.data))
    def test_incremental_merge_refuses_a_missing_overlap_day(self):
        cached={'_source_history':['2026-10-14,OLD','2026-10-15,OLD'],'_source_bundle_sha256':'INVENTED','_source_raw_key':'INVENTED'}
        value={'data':{'klines':['2026-10-15,NEW','2026-10-16,NEW']}}
        with self.assertRaisesRegex(ValueError,'OVERLAP_MISSING'):merge_cached(copy.deepcopy(value),{},cached,'2026-10-14')
        value['confirmed_no_trade_sessions']=['2026-10-14']
        v,receipt,raw=merge_cached(value,{},cached,'2026-10-14');self.assertEqual(len(v['data']['klines']),2)
        self.assertFalse(v['cached_full_prefix']['prefix_reauthenticated_today'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),receipt['sha256'])
