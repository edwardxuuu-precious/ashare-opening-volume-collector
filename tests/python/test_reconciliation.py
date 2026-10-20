import _bootstrap
import unittest,sys,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend/scripts'))
import pandas as pd
from collect import evaluate,TIMES,safe_saved_row,reusable_checkpoint,publish
from reconciliation import FallbackBudget,eastmoney_pair,reconcile_stock

DAY='2026-09-04'
def pair(first=200,daily=1000):
    return (pd.DataFrame({'day':[DAY+' '+t for t in TIMES], 'volume':[first]+[50]*14+[100], 'close':[10]*16}),
            pd.DataFrame({'date':[DAY], 'volume':[daily], 'close':[10]}))

class ReconciliationTest(unittest.TestCase):
    def run_case(self, primary, fallback, budget=None):
        self.cleared=0
        def clear():self.cleared+=1
        initial={DAY:evaluate('000001','平安银行',DAY,*pair(daily=1001))}
        return reconcile_stock('000001','平安银行',[DAY],initial,primary,fallback,clear,evaluate,budget or FallbackBudget())[DAY]
    def test_old_conflicting_row_is_quarantined(self):
        old={'status':'ok','ratio':17,'quality':'source_difference','dayVolumeDifference':-1}
        row=safe_saved_row(old)
        self.assertEqual(row['status'],'unverified');self.assertIsNone(row['ratio']);self.assertEqual(row['referenceRatio'],17)
        self.assertEqual(old['status'],'ok')
    def test_legacy_checkpoint_not_reusable(self):
        row=evaluate('000001','平安银行',DAY,*pair());row.pop('verificationVersion')
        self.assertFalse(reusable_checkpoint({'fetchedOn':DAY,'fetchStatus':'ok','days':{DAY:row}},[DAY],DAY))
    def test_versioned_but_conflicting_checkpoint_not_reusable(self):
        row=evaluate('000001','平安银行',DAY,*pair());row.update(quality='source_difference',dayVolumeDifference=1)
        self.assertFalse(reusable_checkpoint({'fetchedOn':DAY,'fetchStatus':'ok','days':{DAY:row}},[DAY],DAY))
    def test_method_change_preserves_other_stocks_and_quarantines_legacy(self):
        good=evaluate('000001','平安银行',DAY,*pair())
        old=dict(good,code='600519',ratio=17,quality='source_difference',dayVolumeDifference=-1)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            (out/(DAY+'.json')).write_text(json.dumps({'scope':'sample','universeTotal':2,'generatedAt':DAY,
                'methodology':'old methodology','rows':[old]}))
            publish([{'days':{DAY:good}}],[DAY],out,'sample',2)
            rows=json.loads((out/(DAY+'.json')).read_text())['rows']
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[1]['code'],'600519');self.assertIsNone(rows[1]['ratio'])
            self.assertEqual(rows[1]['referenceRatio'],17)

    def test_publish_records_atomic_price_coverage_without_reopening_core_completion(self):
        row=evaluate('000001','平安银行',DAY,*pair())
        row.update(open=9.8,high=10.3,low=9.7,close=10.0,
                   priceStatus='available',priceSourceProvider='sina')
        stopped=dict(row,code='600519',name='停牌样本',status='suspended',ratio=None,
                     first15Volume=None,dailyVolume=None,open=None,high=None,low=None,close=None,
                     priceStatus='not_traded',
                     noTradeEvidence={'kind':'fixture','date':DAY},
                     specialStatus={'type':'suspension','label':'重大事项停牌',
                         'description':'交易所公告确认当日停牌','startedAt':DAY,
                         'source':'交易所公告'})
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            publish([{'days':{DAY:row}},{'days':{DAY:stopped}}],[DAY],out,'full',2)
            payload=json.loads((out/(DAY+'.json')).read_text())
            entry=json.loads((out/'manifest.json').read_text())['dates'][0]
            for value in (payload,entry):
                self.assertEqual(value['priceSchemaVersion'],1)
                self.assertEqual(value['priceAvailable'],1)
                self.assertEqual(value['priceNoTrade'],1)
                self.assertEqual(value['priceMissing'],0)
                self.assertTrue(value['priceDataComplete'])
                self.assertEqual(value['specialStatusExplained'],1)
                self.assertEqual(value['specialStatusUnexplained'],0)
                self.assertTrue(value['statusExplanationComplete'])
    def test_new_publication_writes_explicit_missing_price_contract_for_legacy_row(self):
        row=evaluate('000001','平安银行',DAY,*pair())
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            publish([{'days':{DAY:row}}],[DAY],out,'full',1)
            payload=json.loads((out/(DAY+'.json')).read_text())
        actual=payload['rows'][0]
        self.assertEqual(actual['priceStatus'],'missing')
        self.assertTrue(all(key in actual and actual[key] is None
                            for key in ('open','high','low','close')))
        self.assertEqual(payload['priceMissing'],1)
        self.assertFalse(payload['priceDataComplete'])
    def test_saved_new_attempt_reason_preserved(self):
        row=evaluate('000001','平安银行',DAY,*pair(daily=1001))
        row['reason']='自动重取及备用核验未通过'
        self.assertEqual(safe_saved_row(row)['reason'],'自动重取及备用核验未通过')
    def test_one_share_mismatch_never_valid(self):
        row=evaluate('000001','平安银行',DAY,*pair(daily=1001))
        self.assertEqual(row['status'],'unverified');self.assertIsNone(row['ratio']);self.assertGreater(row['referenceRatio'],0)
    def test_refresh_restores_valid_without_fallback(self):
        def forbidden(_):raise AssertionError('must not call')
        row=self.run_case(pair,forbidden)
        self.assertEqual(row['ratio'],20);self.assertEqual(row['sourceProvider'],'sina');self.assertEqual(self.cleared,1)
        self.assertEqual(len(row['reconciliationAttempts']),2)
    def test_alternate_replaces_both_volumes_and_retains_original(self):
        row=self.run_case(lambda:pair(daily=1001),lambda _:pair(first=400,daily=1200))
        self.assertEqual(row['sourceProvider'],'eastmoney');self.assertEqual(row['first15Volume'],400)
        self.assertEqual(row['dailyVolume'],1200);self.assertEqual(row['ratio'],33.333333)
        self.assertEqual(row['verificationState'],'fallback_matched')
        self.assertEqual(row['reconciliationAttempts'][0]['dailyVolume'],1001)
    def test_alternate_conflict_cannot_validate_primary(self):
        row=self.run_case(lambda:pair(daily=1001),lambda _:pair(first=400,daily=1001))
        self.assertEqual(row['status'],'unverified');self.assertIsNone(row['ratio']);self.assertEqual(row['dailyVolume'],1001)
    def test_missing_alternate_bar_remains_unverified(self):
        m,d=pair();m=m.iloc[:-1]
        row=self.run_case(lambda:pair(daily=1001),lambda _:(m,d))
        self.assertIsNone(row['ratio']);self.assertEqual(row['status'],'unverified')
    def test_alternate_close_mismatch_rejected(self):
        m,d=pair();d['close']=11
        row=self.run_case(lambda:pair(daily=1001),lambda _:(m,d))
        self.assertIsNone(row['ratio'])
    def test_failed_refresh_preserves_initial_evidence(self):
        def failed():raise TimeoutError('sensitive transport content not retained')
        row=self.run_case(failed,lambda _:pair(daily=1001))
        self.assertEqual(row['first15Volume'],200);self.assertIsNone(row['ratio'])
        self.assertEqual(row['reconciliationAttempts'][1]['reason'],'TimeoutError')
    def test_failure_breaker_stops_bad_endpoint_requests(self):
        budget=FallbackBudget(failure_limit=2)
        def failed(_):raise ConnectionError()
        self.run_case(lambda:pair(daily=1001),failed,budget)
        self.run_case(lambda:pair(daily=1001),failed,budget)
        row=self.run_case(lambda:pair(daily=1001),failed,budget)
        self.assertEqual(budget.attempted,2);self.assertEqual(row['reconciliationAttempts'][-1]['status'],'skipped')
    def test_run_cap_applies_even_when_endpoint_works(self):
        budget=FallbackBudget(max_stocks=1)
        self.run_case(lambda:pair(daily=1001),lambda _:pair(),budget)
        row=self.run_case(lambda:pair(daily=1001),lambda _:pair(),budget)
        self.assertIsNone(row['ratio']);self.assertEqual(budget.attempted,1)
    def test_eastmoney_explicit_both_lots_conversion_and_close_mapping(self):
        class AK:
            def stock_zh_a_hist_min_em(self,**kwargs):
                self.minute_args=kwargs;return pd.DataFrame({'时间':[DAY+' 09:45:00'],'成交量':[12],'收盘':[10]})
            def stock_zh_a_hist(self,**kwargs):
                self.daily_args=kwargs;return pd.DataFrame({'日期':[DAY],'成交量':[100],'收盘':[10]})
        ak=AK();m,d=eastmoney_pair(ak,'600519',[DAY])
        self.assertEqual(m.volume.iloc[0],1200);self.assertEqual(d.volume.iloc[0],10000)
        self.assertEqual(m.close.iloc[0],10);self.assertEqual(d.close.iloc[0],10)
        self.assertEqual(ak.minute_args['adjust'],'');self.assertEqual(ak.daily_args['adjust'],'')
    def test_one_cent_close_mismatch_is_rejected(self):
        m,d=pair();d['close']=10.01
        self.assertEqual(evaluate('000001','平安银行',DAY,m,d)['status'],'unverified')
    def test_float_noise_in_close_is_allowed(self):
        m,d=pair();d['close']=10.0000000001
        self.assertEqual(evaluate('000001','平安银行',DAY,m,d)['status'],'ok')
    def test_fractional_share_not_tolerated(self):
        m,d=pair();m['volume']=m['volume'].astype(float);m.loc[0,'volume']=200.1
        self.assertIsNone(evaluate('000001','平安银行',DAY,m,d)['ratio'])
    def two_days(self, historical_daily=1001, latest_daily=1000):
        old='2026-09-03';m0,d0=pair(daily=historical_daily);m1,d1=pair(daily=latest_daily)
        m0['day']=m0['day'].str.replace(DAY,old);d0['date']=old
        frames=pd.concat([m0,m1],ignore_index=True),pd.concat([d0,d1],ignore_index=True)
        return old,frames,{day:evaluate('000001','平安银行',day,*frames) for day in (old,DAY)}
    def test_latest_first_historical_conflict_does_not_request_retry(self):
        old,frames,initial=self.two_days();calls=[];budget=FallbackBudget()
        def primary():calls.append('primary');return frames
        def fallback(days):calls.append(('fallback',days));return frames
        result=reconcile_stock('000001','平安银行',[old,DAY],initial,primary,fallback,lambda:calls.append('clear'),evaluate,budget,latest_first=True)
        self.assertEqual(calls,[]);self.assertEqual(budget.attempted,0)
        self.assertEqual(result[DAY]['ratio'],initial[DAY]['ratio'])
        self.assertEqual(result[old]['status'],'unverified');self.assertIsNone(result[old]['ratio'])
        self.assertEqual(result[old]['reason'],'历史差异等待后续核验')
        self.assertEqual(result[old]['referenceRatio'],initial[old]['referenceRatio'])
        self.assertEqual(len(result[old]['reconciliationAttempts']),1)
        self.assertEqual(result[old]['reconciliationAttempts'][0]['stage'],'initial')
        self.assertEqual(result[old]['reconciliationAttempts'][0]['dayVolumeDifference'],-1)
    def test_default_still_retries_historical_conflict(self):
        old,frames,initial=self.two_days();calls=[]
        def primary():calls.append('primary');return frames
        def fallback(days):calls.append(('fallback',days));return frames
        result=reconcile_stock('000001','平安银行',[old,DAY],initial,primary,fallback,lambda:None,evaluate,FallbackBudget())
        self.assertEqual(calls,['primary',('fallback',[old])])
        self.assertFalse(result[old].get('reconciliationDeferred',False))
    def test_latest_first_only_latest_uses_retry_and_alternate(self):
        old,frames,initial=self.two_days(latest_daily=1001);calls=[]
        def primary():calls.append('primary');return frames
        def fallback(days):calls.append(('fallback',days));return pair()
        result=reconcile_stock('000001','平安银行',[old,DAY],initial,primary,fallback,lambda:None,evaluate,FallbackBudget(),latest_first=True)
        self.assertEqual(calls,['primary',('fallback',[DAY])])
        self.assertEqual(result[DAY]['status'],'ok');self.assertEqual(result[DAY]['sourceProvider'],'eastmoney')
        self.assertEqual(result[old]['reason'],'历史差异等待后续核验');self.assertIsNone(result[old]['ratio'])
    def test_latest_first_does_not_reduce_initial_matched_history(self):
        old,frames,initial=self.two_days(historical_daily=1000,latest_daily=1001)
        result=reconcile_stock('000001','平安银行',[old,DAY],initial,lambda:frames,lambda _:pair(),lambda:None,evaluate,FallbackBudget(),latest_first=True)
        for field in ('status','ratio','first15Volume','dailyVolume','minuteDayVolume','quality'):
            self.assertEqual(result[old][field],initial[old][field])
        self.assertNotIn('reconciliationDeferred',result[old])

if __name__=='__main__':unittest.main()
