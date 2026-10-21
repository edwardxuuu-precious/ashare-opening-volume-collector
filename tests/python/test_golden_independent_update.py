import _bootstrap
import json,gzip,unittest
from datetime import datetime,date,timedelta,timezone
from io import BytesIO
from botocore.exceptions import ClientError
from backend.golden.daily import PREFIX,read,put
from backend.golden.update import run,target_session,next_run
from backend.golden.sources import parse

NOW=datetime(2026,10,21,8,0,tzinfo=timezone.utc)


class Store:
    def __init__(self):self.values={};self.rev=0;self.writes=[]
    def get_object(self,Bucket,Key):
        if Key not in self.values:raise ClientError({'Error':{'Code':'NoSuchKey'}},'GetObject')
        raw,etag=self.values[Key];return dict(Body=BytesIO(raw),ETag=etag)
    def put_object(self,Bucket,Key,Body,**kwargs):
        if (kwargs.get('IfNoneMatch')=='*' and Key in self.values) or ('IfMatch' in kwargs and self.values[Key][1]!=kwargs['IfMatch']):raise ClientError({'Error':{'Code':'PreconditionFailed'}},'PutObject')
        self.rev+=1;self.values[Key]=(Body,str(self.rev));self.writes.append(Key);return dict(ETag=str(self.rev))


class Provider:
    def __init__(self,missing=None,stale=False):self.missing=missing;self.calls=[];self.stale=stale
    def history(self,code,name,as_of,previous=None):
        self.calls.append(code)
        if code==self.missing:raise TimeoutError('fictional')
        end=date.fromisoformat(as_of)-timedelta(days=int(self.stale))
        rows=[dict(date=(end-timedelta(days=119-i)).isoformat(),open='10',high='11.0001',low='9',close='10',volume_shares='100',amount_cny='1000',turnover_percent='1') for i in range(120)]
        return rows,dict(provider='FICTIONAL'),json.dumps(rows).encode(),[]
    def close(self):pass


class IndependentUpdate(unittest.TestCase):
    def test_only_completed_shanghai_session_is_selected(self):
        self.assertEqual(target_session(NOW),'2026-10-21')
        self.assertEqual(target_session(NOW.replace(hour=6)),'2026-10-20')
        with self.assertRaises(ValueError):target_session(NOW,'2026-10-22')
        with self.assertRaises(ValueError):target_session(NOW,'2026-10-01')
        self.assertEqual(next_run(NOW),'2026-10-21T16:15:00+08:00')
    def test_complete_without_any_chip_data_and_same_day_retry_uses_cache(self):
        s=Store();p=Provider();first=run(s,'test',source_factory=lambda:p,clock=lambda:NOW)
        self.assertEqual(first['state'],'COMPLETE');self.assertEqual(first['ready'],20);self.assertEqual(len(p.calls),20)
        self.assertTrue(all(k.startswith(PREFIX) for k in s.writes));self.assertFalse(any('/annotations/' in k for k in s.writes))
        again=Provider(missing='600519');second=run(s,'test',source_factory=lambda:again,clock=lambda:NOW)
        self.assertEqual(second['outcome'],'ALREADY_CURRENT');self.assertEqual(again.calls,[])
        status,_=read(s,'test',PREFIX+'status.json');self.assertEqual(status['state'],'COMPLETE');self.assertEqual(status['ready'],20)
        self.assertIsNotNone(status['data_generated_at'])
    def test_partial_recovers_only_missing_symbol_and_keeps_archive(self):
        s=Store();first=run(s,'test',source_factory=lambda:Provider(missing='sh000001'),clock=lambda:NOW)
        self.assertEqual(first['state'],'PARTIAL');self.assertEqual(first['ready'],19)
        source=Provider();second=run(s,'test',source_factory=lambda:source,clock=lambda:NOW)
        self.assertEqual(second['state'],'COMPLETE');self.assertEqual(source.calls,['sh000001'])
        m,_=read(s,'test',PREFIX+'manifest.json');self.assertEqual(len(m['archives']),2)
    def test_stale_source_is_not_cached_or_counted_complete(self):
        s=Store();result=run(s,'test',source_factory=lambda:Provider(stale=True),clock=lambda:NOW)
        self.assertEqual(result['outcome'],'FAILED');self.assertEqual(result['ready'],0)
        self.assertNotIn(PREFIX+'manifest.json',s.values)
        state,_=read(s,'test',PREFIX+'status.json');self.assertEqual(state['state'],'FAILED')
    def test_active_lease_and_older_target_cannot_overwrite(self):
        s=Store();put(s,'test',PREFIX+'update-lease.json',dict(owner='other',expires=NOW.timestamp()+60))
        self.assertEqual(run(s,'test',clock=lambda:NOW)['outcome'],'LEASE_HELD')
        put(s,'test',PREFIX+'manifest.json',dict(as_of='2026-10-21',dataset_id='a'*16))
        with self.assertRaises(ValueError):run(s,'test',as_of='2026-10-20',clock=lambda:NOW)
    def test_index_identity_precision_and_target_date(self):
        rows=[f'{(date(2026,10,21)-timedelta(days=59-i)).isoformat()},10.1234,10.1234,11.5678,9.0123,100,100000,0,0,0,1' for i in range(60)]
        value=dict(rc=0,data=dict(code='000001',name='上证指数',klines=rows))
        parsed,_=parse(value,'sh000001','2026-10-21');self.assertEqual(parsed[-1]['high'],'11.5678');self.assertEqual(parsed[-1]['volume_shares'],'10000')
        value['data']['name']='平安银行'
        with self.assertRaises(ValueError):parse(value,'sh000001','2026-10-21')
    def test_stale_owner_cannot_clobber_replacement_status(self):
        class Racing(Store):
            def __init__(self):super().__init__();self.injected=False
            def put_object(self,Bucket,Key,Body,**kw):
                if Key==PREFIX+'status.json' and not self.injected:
                    value=json.loads(gzip.decompress(Body))
                    if value.get('processed')==1:
                        self.injected=True
                        super().put_object(Bucket=Bucket,Key=PREFIX+'update-lease.json',Body=gzip.compress(json.dumps(dict(owner='new-run',expires=NOW.timestamp()+1200)).encode()))
                        super().put_object(Bucket=Bucket,Key=Key,Body=gzip.compress(json.dumps(dict(state='COMPLETE',target_date='2026-10-21',run_id='new-run',started_at=(NOW+timedelta(seconds=10)).isoformat())).encode()))
                return super().put_object(Bucket=Bucket,Key=Key,Body=Body,**kw)
        s=Racing()
        with self.assertRaises(RuntimeError):run(s,'test',source_factory=Provider,clock=lambda:NOW)
        current,_=read(s,'test',PREFIX+'status.json');self.assertEqual(current['run_id'],'new-run');self.assertEqual(current['state'],'COMPLETE')
    def test_final_quote_adds_only_target_bar_and_retains_prior_precision(self):
        from backend.golden.sources import DailySources
        p=Provider();prior=p.history('600519','fictional','2026-10-20')[0]
        f=['']*50
        for i,v in {1:'fictional',2:'600519',3:'10',4:'10',5:'10',6:'100',30:'20261021150000',31:'0',32:'0',33:'11',34:'9',35:'10/100/100000',36:'100',37:'10',38:'1',43:'20',47:'11'}.items():f[i]=v
        source=DailySources();source.quotes={'600519':{'raw_quote_body':'~'.join(f)}};source.quote_date='2026-10-21'
        try:
            rows,receipt,raw,_=source.history('600519','fictional','2026-10-21',previous=dict(symbol='600519',dataset_id='a'*16,history=prior,source={}))
            self.assertEqual(rows[:-1],prior);self.assertEqual(rows[-1]['date'],'2026-10-21');self.assertEqual(rows[-1]['volume_shares'],'10000')
            self.assertEqual(rows[0]['high'],'11.0001');self.assertEqual(receipt['provider'],'PUBLISHED_HISTORY_PLUS_TENCENT_CLOSE')
        finally:source.close()
    def test_ths_zero_placeholder_never_confirms_target_suspension(self):
        from backend.golden.sources import DailySources
        from unittest.mock import patch
        p=Provider();old=p.history('600519','fictional','2026-10-20')[0]
        lines=[','.join([r['date'],r['open'],r['close'],r['high'],r['low'],'1','1000','','0','0','1']) for r in old]
        value=dict(rc=0,data=dict(code='600519',name='fictional',klines=lines),confirmed_no_trade_sessions=['2026-10-21'])
        source=DailySources();source.bao_failures=2
        try:
            with patch.object(source.source,'em_history',side_effect=TimeoutError('fictional')),patch('backend.chips.ths_history.history',return_value=(value,dict(provider='THS_UNADJUSTED'),b'{}')):
                with self.assertRaises(ValueError):source.history('600519','fictional','2026-10-21')
        finally:source.close()


if __name__=='__main__':unittest.main()
