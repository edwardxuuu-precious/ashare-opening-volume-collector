import _bootstrap
import unittest,sys,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend/scripts'))
from collect import evaluate,TIMES,market,publish
from reconciliation import quote_metrics
import pandas as pd

class QuoteMetricsTest(unittest.TestCase):
    DAY='2026-09-04';PREV='2026-09-03'
    def frame(self, prev_close=10.0, close=10.5, high=11.0, low=10.2, include_prev=True, day_volume=1000):
        rows=[]
        if include_prev:
            rows.append({'date':self.PREV,'open':9.9,'high':10.1,'low':9.8,'close':prev_close,'volume':100})
        rows.append({'date':self.DAY,'open':10.3,'high':high,'low':low,'close':close,'volume':day_volume})
        return pd.DataFrame(rows)
    def test_sina_columns(self):
        pct,amp=quote_metrics(self.frame(),self.DAY)
        self.assertAlmostEqual(pct,5.0,places=4);self.assertAlmostEqual(amp,8.0,places=4)
    def test_eastmoney_chinese_columns(self):
        d=self.frame().rename(columns={'date':'日期','high':'最高','low':'最低','close':'收盘'})
        pct,amp=quote_metrics(d,self.DAY)
        self.assertAlmostEqual(pct,5.0,places=4);self.assertAlmostEqual(amp,8.0,places=4)
    def test_missing_previous_close_returns_none(self):
        self.assertEqual(quote_metrics(self.frame(include_prev=False),self.DAY),(None,None))
    def test_missing_day_returns_none(self):
        self.assertEqual(quote_metrics(self.frame(),'2026-09-07'),(None,None))
    def test_missing_high_low_columns_returns_none(self):
        d=self.frame().drop(columns=['high','low'])
        self.assertEqual(quote_metrics(d,self.DAY),(None,None))
    def test_zero_previous_close_returns_none(self):
        self.assertEqual(quote_metrics(self.frame(prev_close=0),self.DAY),(None,None))
    def test_evaluate_carries_quote_fields_alongside_ratio(self):
        m=pd.DataFrame({'day':[self.DAY+' '+t for t in TIMES],'volume':[200]+[50]*14+[100]})
        row=evaluate('000001','平安银行',self.DAY,m,self.frame())
        self.assertEqual(row['status'],'ok');self.assertAlmostEqual(row['ratio'],20)
        self.assertAlmostEqual(row['pctChange'],5.0,places=4);self.assertAlmostEqual(row['amplitude'],8.0,places=4)

class MetricsTest(unittest.TestCase):
    def setUp(self):
        self.day='2026-09-04';self.m=pd.DataFrame({'day':[self.day+' '+t for t in TIMES],'volume':[200]+[50]*14+[100]});self.d=pd.DataFrame({'date':[self.day],'volume':[1000]})
    def calc(self,**kw):return evaluate('000001','平安银行',self.day,self.m,self.d,**kw)
    def test_correct_ratio_and_units(self):self.assertEqual(self.calc()['ratio'],20)
    def test_defined_source_method(self):self.assertEqual(self.calc()['status'],'ok');self.assertEqual(self.calc()['quality'],'matched')
    def test_missing_bar_not_zero(self):self.m=self.m.iloc[:-1];self.assertEqual(self.calc()['status'],'missing');self.assertIsNone(self.calc()['ratio'])
    def test_duplicate_bar_rejected(self):self.m.loc[1,'day']=self.m.loc[0,'day'];self.assertEqual(self.calc()['status'],'missing')
    def test_difference_is_disclosed(self):self.d.loc[0,'volume']=2000;self.assertEqual(self.calc()['quality'],'source_difference');self.assertIsNone(self.calc()['ratio']);self.assertEqual(self.calc()['referenceRatio'],10);self.assertEqual(self.calc()['status'],'unverified')
    def test_non_finite_rejected(self):self.m.loc[1,'volume']=float('nan');self.assertEqual(self.calc()['status'],'missing')
    def test_zero_not_assumed_suspended(self):self.d.loc[0,'volume']=0;self.assertEqual(self.calc()['status'],'missing');self.assertIsNone(self.calc()['ratio'])
    def test_lot_conversion(self):self.d=pd.DataFrame({'日期':[self.day],'成交量':[10]});self.assertEqual(self.calc(daily_lot=True,)['ratio'],20)
    def test_failed_refresh_preserves_good_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);good=self.calc()
            publish([{'days':{self.day:good}}],[self.day],path,'full',1)
            bad=dict(good,status='missing',ratio=None)
            publish([{'days':{self.day:bad}}],[self.day],path,'sample',1)
            self.assertEqual(json.loads((path/f'{self.day}.json').read_text())['rows'][0]['status'],'ok')
    def test_new_day_retains_previous_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);good=self.calc()
            publish([{'days':{self.day:good}}],[self.day],path,'full',1)
            publish([{'days':{'2026-09-07':good}}],['2026-09-07'],path,'sample',1)
            self.assertEqual(len(json.loads((path/'manifest.json').read_text())['dates']),2)
    def test_close_mismatch_blocks_calculation(self):
        self.m['close']=10;self.d['close']=20
        self.assertEqual(self.calc()['status'],'unverified');self.assertIsNone(self.calc()['ratio'])
    def test_nullable_daily_volume_is_isolated(self):
        self.d['volume']=pd.Series([pd.NA],dtype='Int64')
        self.assertEqual(self.calc()['status'],'missing')
    def test_invalid_daily_volume_is_isolated(self):
        self.d['volume']=['not-a-number']
        self.assertEqual(self.calc()['status'],'missing')
    def test_nullable_close_is_isolated(self):
        self.m['close']=10;self.d['close']=pd.Series([pd.NA],dtype='Float64')
        self.assertEqual(self.calc()['status'],'unverified')
    def test_refresh_merges_new_success_with_retained_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);a=self.calc();b=dict(a,code='600519',name='贵州茅台',market='SH')
            failed_a=dict(a,status='missing',ratio=None,reason='采集失败：Timeout')
            failed_b=dict(b,status='missing',ratio=None,reason='日线缺失')
            publish([{'days':{self.day:a}},{'days':{self.day:failed_b}}],[self.day],path,'full',2)
            publish([{'days':{self.day:failed_a}},{'days':{self.day:b}}],[self.day],path,'full',2)
            result=json.loads((path/f'{self.day}.json').read_text())
            self.assertEqual([r['status'] for r in result['rows']],['ok','ok'])
            self.assertEqual(result['retainedCount'],1);self.assertTrue(result['rows'][0]['retainedFromPrevious'])
    def test_new_conflict_is_not_hidden_by_old_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);good=self.calc();conflict=dict(good,status='unverified',ratio=None,reason='收盘冲突')
            publish([{'days':{self.day:good}}],[self.day],path,'full',1)
            publish([{'days':{self.day:conflict}}],[self.day],path,'full',1)
            self.assertEqual(json.loads((path/f'{self.day}.json').read_text())['rows'][0]['status'],'unverified')
    def test_smaller_refresh_preserves_other_stocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);a=self.calc();b=dict(a,code='600519',name='贵州茅台',market='SH')
            publish([{'days':{self.day:a}},{'days':{self.day:b}}],[self.day],path,'full',2)
            publish([{'days':{self.day:a}}],[self.day],path,'sample',2)
            self.assertEqual(json.loads((path/f'{self.day}.json').read_text())['total'],2)
    def test_market_codes(self):self.assertEqual([market(x) for x in ['000001','600519','920002']],['SZ','SH','BJ'])

if __name__=='__main__':unittest.main()
