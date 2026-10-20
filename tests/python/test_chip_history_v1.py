"""Invented history, dates and identities; no production account or network."""
import base64
from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import unittest
from backend.chips.history import is_session,previous_session,replay,load_history,archive_keys,ImmutableHistoryStore,load_history_batch
from backend.chips.eastmoney_model import MODEL,estimate_snapshots,compare_snapshots
from backend.chips.model import Bar

CODE='999999';DATASET='0123456789abcdef'

def fixture():
    d=date(2024,9,20);bars=[]
    while d<=date(2025,12,31):
        if is_session(d.isoformat()):
            p=Decimal('5')+Decimal(len(bars)%30)/100
            bars.append(Bar(d.isoformat(),p,p+Decimal('.2'),p-Decimal('.2'),p+Decimal('.05'),Decimal(10000),p*10000,Decimal('1.5')))
        d+=timedelta(days=1)
    last=bars[-1].source_date;prior=previous_session(last)
    snapshots=estimate_snapshots(bars,[prior,last],security_id=CODE)
    comp=compare_snapshots(snapshots[prior],snapshots[last])
    for day,field in [(prior,'previous_share_percent'),(last,'current_same_price_share_percent')]:
        snapshots[day]['comparison_anchor_shares_percent']={r['anchor_price_cny']:r[field] for r in comp['anchors']}
    rows=[dict(zip(('date','open','high','low','close','volume_shares','amount_cny','turnover_percent'),map(str,(b.source_date,b.open,b.high,b.low,b.close,b.volume_shares,b.amount_cny,b.turnover_pct)))) for b in bars]
    frame={'schema':'stock.chips.market.stock.v1','dataset_id':DATASET,'as_of':last,'security_id':CODE,'name':'INVENTED',
           'chip_status':'READY','model':MODEL,'unchanged_threshold':None,'history':rows,'snapshots':snapshots}
    lines=[','.join(map(str,(b.source_date,b.open,b.close,b.high,b.low,b.volume_shares/100,b.amount_cny,'0','0','0',b.turnover_pct))) for b in bars]
    raw=json.dumps({'rc':0,'data':{'code':CODE,'name':'INVENTED','klines':lines}},separators=(',',':')).encode()
    frame['source']={'sha256':hashlib.sha256(raw).hexdigest()}
    return bars,frame,raw


class ChipHistory(unittest.TestCase):
    def test_batch_matches_every_single_day_and_reads_the_input_once(self):
        _,f,_=fixture();calls=[]
        class Store:
            def get_object(self,**kw):
                calls.append(kw);return {'Body':BytesIO(json.dumps(f).encode())}
        store=ImmutableHistoryStore(Store());selected='2025-11-20'
        result=load_history_batch(store,'INVENTED',DATASET,CODE,selected)
        self.assertEqual(len(result['dates']),8);self.assertEqual(result['errors'],{})
        self.assertEqual(len(calls),1)
        for day,frame in result['frames'].items():self.assertEqual(frame,replay(f,day))
        load_history_batch(store,'INVENTED',DATASET,CODE,result['dates'][-1])
        self.assertEqual(len(calls),1)

    def test_warm_cache_is_bounded_and_cannot_mix_bucket_or_dataset(self):
        calls=[]
        class Store:
            def get_object(self,**kw):calls.append(kw);return {'Body':BytesIO(kw['Key'].encode())}
        store=ImmutableHistoryStore(Store(),max_bytes=6,max_entries=2)
        for key in ['aaa','bbb','aaa','ccc']:
            self.assertEqual(store.get_object(Bucket='A',Key=key)['Body'].read(),key.encode())
        self.assertEqual(len(calls),3);self.assertEqual(store.size,6)
        store.get_object(Bucket='B',Key='aaa');self.assertEqual(len(calls),4)
        store.get_object(Bucket='A',Key='bbb');self.assertEqual(len(calls),5)
        self.assertLessEqual(store.size,6);self.assertLessEqual(len(store.values),2)

    def test_daily_view_is_exactly_the_sealed_pair(self):
        _,f,_=fixture();result=replay(f,f['as_of'])
        self.assertEqual(result['snapshots'],f['snapshots']);self.assertIsNone(result['unchanged_threshold'])

    def test_selected_date_has_its_own_210_window_and_no_future_input(self):
        bars,f,_=fixture();day='2025-11-20';prior=previous_session(day)
        result=replay(f,day)
        expected=estimate_snapshots(bars,[prior,day],security_id=CODE)
        for d in [prior,day]:self.assertEqual(result['snapshots'][d]['raw_node_masses'],expected[d]['raw_node_masses'])
        self.assertEqual(result['snapshots'][day]['loaded_window_rows'],210)
        self.assertEqual(result['snapshots'][prior]['history_rows_processed'],209)
        changed=deepcopy(f)
        for row in changed['history']:
            if row['date']>day:row['high']='99999'
        self.assertEqual(replay(changed,day),result)

    def test_oldest_display_candle_gets_full_archived_warmup(self):
        bars,f,raw=fixture();f['history']=f['history'][-10:];selected=f['history'][0]['date']
        with self.assertRaisesRegex(ValueError,'ARCHIVED_WARMUP'):replay(f,selected)
        result=replay(f,selected,archived_raw=raw)
        self.assertEqual(result['snapshots'][selected]['loaded_window_rows'],210)
        expected=estimate_snapshots(bars,[previous_session(selected),selected],security_id=CODE)
        self.assertEqual(result['snapshots'][selected]['raw_node_masses'],expected[selected]['raw_node_masses'])

    def test_missing_previous_session_is_not_assumed_suspended(self):
        _,f,_=fixture();day='2025-11-20';prior=previous_session(day)
        f['history']=[r for r in f['history'] if r['date']!=prior]
        result=replay(f,day)
        self.assertEqual(result['comparison_status'],'PREVIOUS_SESSION_UNAVAILABLE')
        self.assertNotIn(prior,result['snapshots'])

    def test_closed_days_and_weekends_are_skipped(self):
        for d,p in [('2026-10-08','2026-09-30'),('2025-10-09','2025-09-30'),('2024-10-08','2024-09-30'),('2026-10-19','2026-10-16')]:
            self.assertEqual(previous_session(d),p)
        with self.assertRaises(ValueError):previous_session('2026-10-01')

    def test_archive_only_accepts_same_stock_keys_and_source_hash(self):
        _,f,raw=fixture();f['history']=f['history'][-10:];selected=f['history'][0]['date']
        f['source']['same_session_cached_raw_key']='collector/chips/raw/../../credentials.json'
        self.assertNotIn(f['source']['same_session_cached_raw_key'],archive_keys(f))
        calls=[]
        saved={'receipt':{'sha256':f['source']['sha256']},'raw_base64':base64.b64encode(raw).decode()}
        class Store:
            def get_object(self,**kw):
                calls.append(kw['Key'])
                value=f if kw['Key'].startswith('data/') else saved
                return {'Body':BytesIO(json.dumps(value).encode())}
        r=load_history(Store(),'INVENTED',DATASET,CODE,selected)
        self.assertEqual(r['input_kind'],'HASH_BOUND_ARCHIVE')
        self.assertTrue(all(k.startswith(('data/chips/daily/','collector/chips/raw/')) for k in calls))
        saved['raw_base64']=base64.b64encode(raw+b' ').decode()
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):load_history(Store(),'INVENTED',DATASET,CODE,selected)

    def test_unpublished_candle_and_wrong_stock_are_rejected(self):
        _,f,_=fixture()
        with self.assertRaisesRegex(ValueError,'PUBLISHED_CANDLE'):replay(f,'2025-10-01')
        class Store:
            def get_object(self,**kw):return {'Body':BytesIO(json.dumps(f).encode())}
        with self.assertRaisesRegex(ValueError,'IDENTITY_MISMATCH'):load_history(Store(),'INVENTED',DATASET,'888888','2025-11-20')
