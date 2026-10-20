import _bootstrap
import json,tempfile,unittest,sys
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend/scripts'));sys.path.insert(0,str(ROOT/'backend/server'))
from retention import six_month_start,retain_six_months,validate_dates
from service import JobStore
class RetentionTest(unittest.TestCase):
 def test_month_end_and_window(self):
  self.assertEqual(six_month_start('2026-08-31'),'2026-02-28');self.assertEqual(six_month_start('2024-08-31'),'2024-02-29')
  with self.assertRaises(ValueError):validate_dates(['2026-03-05'],'2026-09-06',['2026-03-05'])
 def test_keeps_historical_dates_and_prunes_only_working_checkpoints(self):
  with tempfile.TemporaryDirectory() as td:
   out=Path(td);(out/'checkpoint').mkdir()
   (out/'manifest.json').write_text(json.dumps({'dates':[{'date':'2026-03-05'},{'date':'2026-03-06'}]}))
   for name in ['2026-03-05.json','2026-03-06.json','universe.json']:(out/name).write_text('{}')
   (out/'checkpoint/000001.json').write_text(json.dumps({'days':{'2026-03-05':{},'2026-03-06':{}}}))
   retain_six_months(out,'2026-09-06')
   self.assertTrue((out/'2026-03-05.json').exists());self.assertTrue((out/'2026-03-06.json').exists());self.assertTrue((out/'universe.json').exists())
   self.assertEqual(list(json.loads((out/'checkpoint/000001.json').read_text())['days']),['2026-03-06'])
   manifest=json.loads((out/'manifest.json').read_text())
   self.assertEqual([d['date'] for d in manifest['dates']],['2026-03-05','2026-03-06'])
   self.assertEqual(manifest['retentionPolicy'],'accumulate')
   self.assertNotIn('retentionMonths',manifest)
   self.assertEqual(manifest['tradingDates'],['2026-03-05','2026-03-06'])
class ServiceTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.out=Path(self.tmp.name)
  self.day=(datetime.now(ZoneInfo('Asia/Shanghai')).date()-timedelta(days=2)).isoformat()
  (self.out/'manifest.json').write_text(json.dumps({'dates':[],'tradingDates':[self.day]}));(self.out/'universe.json').write_text(json.dumps({'rows':[{'code':'000001','name':'平安银行'}]}))
  self.store=JobStore(self.out,runner=self.runner);self.req={'dates':[self.day],'scope':'single','symbols':['000001']}
 def tearDown(self):self.tmp.cleanup()
 def runner(self,job):
  self.store.update(job,completedStocks=1,totalStocks=1)
  (self.out/(self.day+'.json')).write_text(json.dumps({'scope':'sample','rows':[{'code':'000001','status':'ok','ratio':10}]}))
 def test_deduplicated_job_real_completion_and_cached_query(self):
  first=self.store.submit(self.req);self.assertEqual(first['status'],'queued');self.assertEqual(self.store.submit(self.req)['id'],first['id'])
  self.store.work_once();self.assertEqual(self.store.get(first['id'])['status'],'completed');self.assertEqual(self.store.get(first['id'])['completedStocks'],1)
  self.assertEqual(self.store.submit(self.req)['status'],'completed');self.assertTrue(self.store.pending.empty())
 def test_restart_reports_interruption_and_allows_retry(self):
  first=self.store.submit(self.req);restored=JobStore(self.out,runner=self.runner);self.assertEqual(restored.get(first['id'])['status'],'failed')
  self.assertNotEqual(restored.submit(self.req)['id'],first['id'])
 def test_worker_failure_never_marks_completion(self):
  def fail(job):raise RuntimeError('来源不可用')
  self.store.runner=fail;j=self.store.submit(self.req);self.store.work_once();self.assertEqual(self.store.get(j['id'])['status'],'failed');self.assertEqual(self.store.get(j['id'])['completedStocks'],0)
 def test_invalid_scope_date_and_symbol(self):
  for patch in [{'scope':'garbage'},{'symbols':['../']},{'symbols':[]},{'dates':['2000-01-01']}]:
   with self.assertRaises(ValueError):self.store.submit({**self.req,**patch})
if __name__=='__main__':unittest.main()
