import _bootstrap
import unittest
from deploy.collector.observe_daily import assess

def run(day,passed=True,boot=14000):
 return dict(runDate=day,endedAt=day+'T21:30:00+08:00',phase='daily',baselineEligible=True,benchmarkPassed=passed,noOp=False,observedBootSeconds=boot)
class ObservationTests(unittest.TestCase):
 def test_backfill_not_benchmark(self):
  self.assertEqual(assess([],[],{'historyTraversalCompleted':False})['phase'],'waiting_backfill')
 def test_three_consecutive_dates_not_three_arbitrary_successes(self):
  days=['2026-09-07','2026-09-08','2026-09-09','2026-09-10','2026-09-11']
  r=assess([run(d) for d in days if d!='2026-09-09'],days,{'historyTraversalCompleted':True})
  self.assertFalse(r['complete']);self.assertFalse(r['fiveHourTargetVerified'])
 def test_failures_and_long_runtime_not_cost_pass(self):
  days=['2026-09-07','2026-09-08','2026-09-09','2026-09-10']
  for bad in [run(days[2],False),run(days[2],True,20000)]:
   r=assess([run(days[0]),run(days[1]),bad,run(days[3])],days,{'historyTraversalCompleted':True})
   self.assertTrue(r['complete']);self.assertFalse(r['fiveHourTargetVerified'])
 def test_complete_after_baseline_plus_three_days(self):
  days=['2026-09-07','2026-09-08','2026-09-09','2026-09-10']
  self.assertTrue(assess([run(d) for d in days],days,{'historyTraversalCompleted':True})['fiveHourTargetVerified'])
if __name__=='__main__':unittest.main()
