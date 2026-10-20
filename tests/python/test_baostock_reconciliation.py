import _bootstrap
import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend/scripts'))
import pandas as pd
from collect import evaluate,TIMES,attempted_checkpoint
from reconciliation import FallbackBudget,reconcile_baostock,needs_baostock_checkpoint
DAY='2026-09-04';OLD='2026-09-03'

def pair(days=(OLD,DAY),first=200,total=1000):
    minute=pd.DataFrame([{'day':day+' '+t,'volume':first if n==0 else (100 if n==15 else 50),'close':10} for day in days for n,t in enumerate(TIMES)])
    daily=pd.DataFrame([{'date':day,'volume':total,'close':10} for day in days])
    return minute,daily

def initial():return {day:evaluate('600519','贵州茅台',day,*pair(total=1001)) for day in (OLD,DAY)}

class BaoReconcileTests(unittest.TestCase):
    def call(self,rows=None,fetch=None,budget=None,code='600519'):
        return reconcile_baostock(code,'test',[OLD,DAY],rows or initial(),fetch or (lambda _:pair()),evaluate,budget or FallbackBudget(max_stocks=5556))
    def test_single_pair_restores_all_dates_and_both_values(self):
        calls=[]
        def fetch(days):calls.append(days);return pair(first=400,total=1200)
        rows=self.call(fetch=fetch)
        self.assertEqual(calls,[[OLD,DAY]])
        for row in rows.values():
            self.assertEqual(row['sourceProvider'],'baostock');self.assertEqual(row['first15Volume'],400)
            self.assertEqual(row['dailyVolume'],1200);self.assertEqual(row['ratio'],33.333333)
            self.assertEqual(row['verificationState'],'fallback_matched')
            self.assertEqual(row['reconciliationAttempts'][0]['dailyVolume'],1001)
            self.assertTrue(row['baostockAttempted'])
    def test_history_deferred_repaired_preserving_prior_attempts(self):
        rows=initial();rows[OLD].update(reconciliationDeferred=True,reconciliationAttempts=[{'source':'sina','stage':'initial','status':'unverified'},{'source':'eastmoney','stage':'fallback','status':'skipped'}])
        result=self.call(rows)
        self.assertNotIn('reconciliationDeferred',result[OLD]);self.assertEqual(len(result[OLD]['reconciliationAttempts']),3)
    def test_already_matched_history_not_modified(self):
        rows=initial();rows[OLD]=dict(evaluate('600519','test',OLD,*pair()),verifiedAt='original')
        result=self.call(rows,fetch=lambda _:pair(first=400,total=1200))
        self.assertEqual(result[OLD],rows[OLD])
    def test_missing_is_recoverable_but_suspended_not_requested(self):
        rows=initial();rows[OLD].update(status='missing',ratio=None,first15Volume=None,dailyVolume=None)
        rows[DAY].update(status='suspended',ratio=None)
        result=self.call(rows)
        self.assertEqual(result[OLD]['status'],'ok');self.assertEqual(result[DAY],rows[DAY])
    def test_bj_unsupported_never_connects_or_counts_failure(self):
        budget=FallbackBudget()
        def forbidden(_):raise AssertionError('must not fetch')
        result=self.call(fetch=forbidden,budget=budget,code='920002')
        self.assertEqual(budget.attempted,0);self.assertEqual(budget.consecutive_failures,0)
        self.assertEqual(result[DAY]['baostockOutcome'],'unsupported')
    def test_three_transport_failures_break_only_bao(self):
        budget=FallbackBudget(max_stocks=5556);calls=[]
        def fail(_):calls.append(1);raise ConnectionError()
        for _ in range(4):result=self.call(fetch=fail,budget=budget)
        self.assertEqual(len(calls),3);self.assertEqual(result[DAY]['baostockOutcome'],'skipped')
        self.assertFalse(result[DAY]['baostockAttempted'])
        self.assertIsNone(result[DAY]['ratio'])
    def test_data_error_does_not_trip_connection_breaker(self):
        budget=FallbackBudget(max_stocks=5556)
        def fail(_):raise ValueError()
        for _ in range(4):self.call(fetch=fail,budget=budget)
        self.assertEqual(budget.attempted,4);self.assertEqual(budget.consecutive_failures,0)
    def test_independent_budget_works_after_em_breaker(self):
        em=FallbackBudget();em.consecutive_failures=3
        result=self.call(budget=FallbackBudget(max_stocks=5556))
        self.assertEqual(result[DAY]['status'],'ok');self.assertEqual(em.consecutive_failures,3)
    def test_still_conflicting_bao_does_not_create_ratio(self):
        result=self.call(fetch=lambda _:pair(first=400,total=1201))
        self.assertIsNone(result[DAY]['ratio']);self.assertEqual(result[DAY]['first15Volume'],200)
        self.assertEqual(result[DAY]['baostockOutcome'],'unverified')
    def test_old_checkpoint_gets_repaired_only_if_bao_not_attempted(self):
        record=dict(code='600519',fetchStatus='ok',fetchedOn='2026-09-05',days=initial())
        self.assertTrue(attempted_checkpoint(record,[OLD,DAY]));self.assertTrue(needs_baostock_checkpoint(record,[OLD,DAY]))
        for row in record['days'].values():row['baostockAttempted']=True
        self.assertFalse(needs_baostock_checkpoint(record,[OLD,DAY]))
        record['code']='920002'
        for row in record['days'].values():row['baostockAttempted']=False
        self.assertFalse(needs_baostock_checkpoint(record,[OLD,DAY]))

if __name__=='__main__':unittest.main()
