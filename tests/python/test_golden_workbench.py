import _bootstrap
import copy,json,unittest
from datetime import date,timedelta
from backend.golden.annotations import empty,apply,Conflict,Forbidden
from backend.golden.automatic import evaluate
from backend.golden.daily import make_case
from backend.golden.watchlist import WATCHLIST,SYMBOLS


def rows():
    values=[10,11,12,11,10,9,8,9,10,11,10,9,8,7,8,9,8,7,6,7]
    return [dict(date=(date(2020,1,1)+timedelta(days=i)).isoformat(),open=str(v),high=str(v+1),low=str(v-1),close=str(v),volume_shares='100') for i,v in enumerate(values)]


class DrawingSecurity(unittest.TestCase):
    def payload(self):
        return dict(kind='drawing',revision=0,drawing=dict(id='00000000-0000-0000-0000-000000000001',
            title='fictional',mode='HL',points=[dict(date='2020-01-01',field='high',price='20'),dict(date='2020-01-04',field='low',price='10')],
            note='why',as_of='2020-01-05',dataset_id='a'*16))
    def test_owner_inferred_and_other_owner_cannot_overwrite(self):
        p=self.payload();doc=apply(empty('600519'),p,'alice','600519');p['revision']=1
        with self.assertRaises(Forbidden): apply(doc,p,'bob','600519')
        p['drawing']['owner']='alice'
        with self.assertRaises(ValueError): apply(doc,p,'bob','600519')
    def test_edit_history_and_revision_conflicts_preserve_other_entries(self):
        p=self.payload();doc=apply(empty('600519'),p,'alice','600519')
        with self.assertRaises(Conflict): apply(doc,p,'alice','600519')
        p['revision']=1;p['drawing']['points'][1]['price']='11'
        updated=apply(doc,p,'alice','600519');self.assertEqual(updated['drawings'][0]['history'][0]['points'][1]['price'],'10')
        self.assertEqual(doc['drawings'][0]['points'][1]['price'],'10')
    def test_no_delete_nonfinite_unknown_symbol_or_wrong_order(self):
        p=self.payload()
        for value in ['NaN','Infinity','-1','20','21']:
            bad=copy.deepcopy(p);bad['drawing']['points'][1]['price']=value
            with self.assertRaises(ValueError): apply(empty('600519'),bad,'alice','600519')
        with self.assertRaises(ValueError): empty('999999')
        with self.assertRaises(ValueError): apply(empty('600519'),dict(revision=0,kind='delete'),'alice','600519')
    def test_review_stores_specific_daily_batch_and_candidate(self):
        payload=dict(revision=0,kind='review',as_of='2020-01-05',dataset_id='a'*16,status='M_ISSUE',note='wrong',axis_id='M:2020-01-04:15.00',auto_mode='HM')
        doc=apply(empty('600519'),payload,'alice','600519')
        self.assertEqual(doc['reviews'][0]['owner'],'alice');self.assertEqual(doc['reviews'][0]['axis_id'],payload['axis_id'])


class DailyExamples(unittest.TestCase):
    def test_daily_missing_recovery_idempotence_and_history(self):
        from backend.golden.daily import publish,encoded
        from botocore.exceptions import ClientError
        from io import BytesIO
        class Store:
            def __init__(self): self.values={};self.revision=0
            def get_object(self,Bucket,Key):
                if Key not in self.values: raise ClientError({'Error':{'Code':'NoSuchKey'}},'GetObject')
                raw,etag=self.values[Key];return dict(Body=BytesIO(raw),ETag=etag)
            def put_object(self,Bucket,Key,Body,**kwargs):
                if kwargs.get('IfNoneMatch')=='*' and Key in self.values: raise AssertionError('overwrite')
                if 'IfMatch' in kwargs: self.assert_etag(Key,kwargs['IfMatch'])
                self.revision+=1;self.values[Key]=(Body,str(self.revision));return dict(ETag=str(self.revision))
            def assert_etag(self,key,etag):
                if self.values[key][1]!=etag: raise AssertionError('CAS')
        store=Store();r=rows()*3
        for i,row in enumerate(r): r[i]=dict(row,date=(date(2020,1,1)+timedelta(days=i)).isoformat())
        day=r[-1]['date'];source=dict(dataset_id='a'*16,as_of=day)
        store.put_object(Bucket='test',Key='data/chips/daily/manifest.json',Body=encoded(source))
        for symbol,_ in WATCHLIST:
            if symbol!='sh000001':store.put_object(Bucket='test',Key=f'data/chips/daily/{source["dataset_id"]}/{symbol}.json',Body=encoded(dict(security_id=symbol,as_of=day,history=r)))
        def failed(_):raise TimeoutError('fictional source outage')
        first=publish(store,'test',failed);self.assertEqual(first['available'],19)
        second=publish(store,'test',lambda _:(r,{'provider':'fictional'}));self.assertEqual(second['available'],20)
        self.assertNotEqual(first['dataset_id'],second['dataset_id'])
        self.assertEqual(publish(store,'test',failed)['outcome'],'ALREADY_CURRENT')
        self.assertIn('data/golden-workbench/'+first['dataset_id']+'/manifest.json',store.values)
        # Index source can succeed yet still be one session behind. It must
        # remain eligible for a same-source-batch refresh rather than freeze.
        fresh=r+[dict(r[-1],date=(date.fromisoformat(day)+timedelta(days=1)).isoformat())]
        newer=dict(dataset_id='b'*16,as_of=fresh[-1]['date'])
        store.put_object(Bucket='test',Key='data/chips/daily/manifest.json',Body=encoded(newer))
        for symbol,_ in WATCHLIST:
            if symbol!='sh000001':store.put_object(Bucket='test',Key=f'data/chips/daily/{newer["dataset_id"]}/{symbol}.json',Body=encoded(dict(security_id=symbol,as_of=newer['as_of'],history=fresh)))
        stale=publish(store,'test',lambda _:(r,{'provider':'fictional'}));self.assertEqual(stale['available'],20)
        recovered=publish(store,'test',lambda _:(fresh,{'provider':'fictional'}))
        self.assertEqual(recovered['outcome'],'PUBLISHED');self.assertNotEqual(stale['dataset_id'],recovered['dataset_id'])
    def test_index_precision_not_rounded_in_calculation(self):
        from backend.golden.anchors import price
        self.assertEqual(price('3995.1751'),'3995.1751')
        self.assertEqual(price('12.0800'),'12.08')
        self.assertNotEqual(price('3743.3601'),price('3743.3641'))

    def test_index_and_bank_have_distinct_identity_and_twenty_symbols(self):
        self.assertEqual(len(WATCHLIST),20);self.assertEqual(len(SYMBOLS),20)
        self.assertIn('sh000001',SYMBOLS);self.assertIn('000001',SYMBOLS);self.assertIn('600519',SYMBOLS)
    def test_only_last_sixty_are_calculated_and_background_does_not_set_points(self):
        r=rows()*3
        for i,row in enumerate(r): r[i]=dict(row,date=(date(2020,1,1)+timedelta(days=i)).isoformat())
        c=make_case('600519','fictional',r,r[-1]['date'],{})
        expected=evaluate(r)
        for k in ('production_changes','full_market_runs','performance_runs'):expected.pop(k)
        self.assertEqual(c['result'],expected)
        older=[dict(r[0],date='2019-12-31',high='9999')]+r
        self.assertEqual(make_case('600519','fictional',older,r[-1]['date'],{})['result'],expected)
    def test_missing_or_stale_bars_not_labelled_current(self):
        r=rows();c=make_case('600519','fictional',r,'2020-03-01',{})
        self.assertEqual(c['freshness'],'STALE_OR_NO_TRADE');self.assertIsNone(c['result'])


if __name__=='__main__':unittest.main()
