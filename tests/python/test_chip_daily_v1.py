"""NEW software fixtures: ALL_VALUES_INVENTED. No network, AWS or strategy run."""
import copy,hashlib,json,threading,unittest
from unittest.mock import patch
from backend.chips.calendar import last_ten
from backend.chips.screen import closing_limit_state,prefilter
from backend.chips.runner import run

DAYS=last_ten('2026-10-16')
STOCK={'code':'000607','name':'INVENTED','secid':'0.000607','listed':'2000-01-01'}
LINES=[f'{d},4.50,4.50,4.50,4.50,100,45000,0,10.02,0.41,1.00' for d in DAYS]
HIT={'000607':{'c':'000607','p':4500,'zdp':10.024449,'amount':45000}}
class Memory:
    def __init__(self):self.data={};self.version={};self.lock=threading.Lock();self.writes=[]
    def get(self,k):
        with self.lock:return copy.deepcopy(self.data.get(k)),str(self.version[k]) if k in self.data else None
    def put(self,k,v,match=None,new=False,compressed=False):
        with self.lock:
            if new and k in self.data:raise RuntimeError('PRECONDITION_FAILED')
            if match and str(self.version.get(k))!=match:raise RuntimeError('PRECONDITION_FAILED')
            self.data[k]=copy.deepcopy(v);self.version[k]=self.version.get(k,0)+1;self.writes.append(k);return str(self.version[k])
class FakeSource:
    VERSION='ALL_VALUES_INVENTED_SOURCE'
    calls=0
    def close(self):pass
    def universe(self):return [STOCK],[]
    def pools(self,days):return {d:HIT for d in days},[]
    def actual_limits(self,stocks,day):return {},[]
    def history(self,stock,day):
        FakeSource.calls+=1
        raw=json.dumps({'rc':0,'data':{'code':stock['code'],'name':stock['name'],'klines':LINES}}).encode()
        return json.loads(raw),{'sha256':hashlib.sha256(raw).hexdigest()},raw
class DailyV1(unittest.TestCase):
    def test_official_window_excludes_holiday(self):
        self.assertEqual(DAYS,['2026-09-28','2026-09-29','2026-09-30','2026-10-08','2026-10-09','2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16'])
    def test_low_price_can_pass_witnessed_limit_without_absolute_price_filter(self):
        r=prefilter(STOCK,LINES,DAYS,{DAYS[-1]:HIT});self.assertEqual(r['prefilter_status'],'PASS');self.assertEqual(r['recent_limit_up_dates'],[DAYS[-1]])
    def test_pool_price_collision_rejected_if_amount_differs(self):
        wrong=copy.deepcopy(HIT);wrong['000607']['amount']=46000
        self.assertEqual(closing_limit_state(STOCK,LINES[-1].split(','),wrong)[0],'UNKNOWN')
    def test_actual_limit_requires_same_day_close_and_reference(self):
        actual={'date':DAYS[-1],'close':'4.50','reference_close':'4.09','upper_limit':'4.50'}
        self.assertEqual(closing_limit_state(STOCK,LINES[-1].split(','),None,actual)[0],'PASS')
        actual['date']=DAYS[-2]
        self.assertEqual(closing_limit_state(STOCK,LINES[-1].split(','),None,actual)[0],'UNKNOWN')
    def test_historical_st_not_inferred_before_supported_rule(self):
        f=LINES[-1].split(',');f[0]='2026-07-01'
        self.assertEqual(closing_limit_state(STOCK,f,HIT)[0],'UNKNOWN')
    def test_v2_valid_source_manifest_last_idempotent_and_isolated(self):
        store=Memory();FakeSource.calls=0
        with patch('backend.chips.runner.Source',FakeSource):
            result=run(store,as_of=DAYS[-1],max_minutes=2,workers=1)
            manifest,_=store.get('data/chips/daily/manifest.json');slot=manifest['dataset_id']
            frame,_=store.get(f'data/chips/daily/{slot}/000607.json')
            self.assertEqual(result['outcome'],'COMMITTED');self.assertIsNone(frame['unchanged_threshold']);self.assertEqual(manifest['price_filter_rule'],'NO_ABSOLUTE_PRICE_FILTER_V1')
            self.assertEqual(manifest['chip_unchanged_rule'],'TOP_REGION_STABLE_1_90_90_V1')
            self.assertEqual(frame['snapshots'][DAYS[-1]]['peak_prices_cny'],['4.50'])
            self.assertEqual(frame['screening_row']['prefilter_status'],'PASS')
            self.assertEqual(frame['screening_row']['screen_status'],'FAIL')
            self.assertEqual(manifest['final_screen_counts'],{'PASS':0,'FAIL':1,'UNKNOWN':0})
            self.assertEqual(frame['screening_row']['original_peak_changes'][0]['change_percentage_points'],'0')
            self.assertLess(store.writes.index(f'data/chips/daily/{slot}/screening.json'),store.writes.index('data/chips/daily/manifest.json'))
            self.assertTrue(all(k.startswith(('data/chips/daily/','collector/chips/')) for k in store.writes))
            self.assertEqual(run(store,as_of=DAYS[-1],max_minutes=2,workers=1)['outcome'],'ALREADY_COMMITTED')
            self.assertEqual(FakeSource.calls,1)
    def test_crash_after_frames_resumes_without_source_read(self):
        store=Memory();FakeSource.calls=0;original=store.put;crashed=False
        def fail_commit(k,v,**kw):
            nonlocal crashed
            if k=='data/chips/daily/manifest.json' and not crashed:crashed=True;raise RuntimeError('INVENTED_CRASH_BEFORE_MANIFEST')
            return original(k,v,**kw)
        store.put=fail_commit
        with patch('backend.chips.runner.Source',FakeSource):
            with self.assertRaisesRegex(RuntimeError,'INVENTED_CRASH'):run(store,as_of=DAYS[-1],max_minutes=2,workers=1)
            self.assertIsNone(store.get('data/chips/daily/manifest.json')[0])
            self.assertEqual(run(store,as_of=DAYS[-1],max_minutes=2,workers=1)['outcome'],'COMMITTED')
            self.assertEqual(FakeSource.calls,1)

    def test_automatic_stability_result_does_not_repeat_completed_calculation(self):
        class Rising(FakeSource):
            def pools(self,days):
                return {days[-1]:{'000607':{'p':110000,'zdp':10,'amount':110000}}},[]
        lines=[f'{d},100,100,100,100,100,1000000,0,0,0,.01' for d in DAYS[:-1]]
        lines+=[f'{DAYS[-1]},110,110,110,110,10,110000,0,10,10,.001']
        store=Memory();FakeSource.calls=0
        with patch('test_chip_daily_v1.LINES',lines),patch('backend.chips.runner.Source',Rising):
            result=run(store,as_of=DAYS[-1],max_minutes=2,workers=1)
            manifest=store.get('data/chips/daily/manifest.json')[0]
            self.assertEqual(result['outcome'],'COMMITTED')
            self.assertTrue(manifest['calculation_complete'])
            self.assertTrue(manifest['market_complete'])
            self.assertEqual(manifest['final_screen_counts'],{'PASS':1,'FAIL':0,'UNKNOWN':0})
            self.assertEqual(run(store,as_of=DAYS[-1],max_minutes=2,workers=1)['outcome'],'ALREADY_COMMITTED')
            self.assertEqual(FakeSource.calls,1)
