import _bootstrap
import json,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend/scripts'))
from collect import make_snapshot,pending_record,reusable_checkpoint,attempted_checkpoint,publish

class BatchTest(unittest.TestCase):
    def setUp(self):
        self.day='2026-09-04';self.today='2026-09-05'
        self.items=[{'code':'000001','name':'平安银行'},{'code':'600519','name':'贵州茅台'},{'code':'920002','name':'万达轴承'}]
    def completed(self,item,error=False):
        record=pending_record(item,[self.day]);record.update(fetchedOn=self.today,fetchStatus='error' if error else 'ok')
        row=record['days'][self.day]
        if error:row['reason']='采集失败：Timeout'
        else:row.update(status='ok',first15Volume=100,dailyVolume=1000,ratio=10,verificationVersion=3,quality='matched');row.pop('reason')
        return record
    def test_partial_full_universe_has_pending_rows(self):
        records={'000001':self.completed(self.items[0])}
        rows,scope,attempted=make_snapshot(self.items,records,[self.day],True)
        self.assertEqual(scope,'partial');self.assertEqual(attempted,1);self.assertEqual(len(rows),3)
        self.assertEqual(rows[1]['days'][self.day]['status'],'missing');self.assertIsNone(rows[1]['days'][self.day]['ratio'])
        with tempfile.TemporaryDirectory() as tmp:
            publish(rows,[self.day],Path(tmp),scope,3,attempted)
            result=json.loads((Path(tmp)/'manifest.json').read_text())
            self.assertEqual(result['scope'],'partial');self.assertEqual(result['pendingCount'],2)
            self.assertEqual(result['dates'][0]['total'],3);self.assertEqual(result['dates'][0]['valid'],1)
            self.assertEqual(result['dates'][0]['status'],'partial')
    def test_full_only_after_every_stock_attempted(self):
        records={i['code']:self.completed(i) for i in self.items}
        _,scope,attempted=make_snapshot(self.items,records,[self.day],True)
        self.assertEqual(scope,'full');self.assertEqual(attempted,3)
    def test_full_traversal_does_not_hide_failure(self):
        records={i['code']:self.completed(i,error=i['code']=='920002') for i in self.items}
        rows,scope,attempted=make_snapshot(self.items,records,[self.day],True)
        with tempfile.TemporaryDirectory() as tmp:
            publish(rows,[self.day],Path(tmp),scope,3,attempted)
            day=json.loads((Path(tmp)/'manifest.json').read_text())['dates'][0]
            self.assertEqual(scope,'full');self.assertEqual(day['status'],'partial');self.assertEqual(day['missing'],1)
    def test_traversal_resume_keeps_unverified_without_marking_it_valid(self):
        record=self.completed(self.items[0]);record['days'][self.day].update(status='unverified',ratio=None)
        self.assertTrue(attempted_checkpoint(record,[self.day]))
        self.assertFalse(reusable_checkpoint(record,[self.day],self.today))
        record['fetchedOn']='2026-09-01'
        self.assertTrue(attempted_checkpoint(record,[self.day]))
        self.assertFalse(attempted_checkpoint(record,[self.day,'2026-09-07']))
        record['days'][self.day].pop('verificationVersion')
        self.assertFalse(attempted_checkpoint(record,[self.day]))
    def test_sample_never_becomes_full(self):
        item=self.items[0]
        _,scope,_=make_snapshot([item],{item['code']:self.completed(item)},[self.day],False)
        self.assertEqual(scope,'sample')
    def test_same_day_success_can_resume(self):self.assertTrue(reusable_checkpoint(self.completed(self.items[0]),[self.day],self.today))
    def test_same_day_error_is_retried(self):self.assertFalse(reusable_checkpoint(self.completed(self.items[0],True),[self.day],self.today))
    def test_legacy_error_is_retried(self):
        record=self.completed(self.items[0],True);record.pop('fetchStatus')
        self.assertFalse(reusable_checkpoint(record,[self.day],self.today))
    def test_same_day_missing_daily_is_retried(self):
        record=self.completed(self.items[0]);record['days'][self.day].update(status='missing',ratio=None,reason='该日的日线数据缺失或重复')
        self.assertFalse(reusable_checkpoint(record,[self.day],self.today))
    def test_same_day_conflict_is_retried(self):
        record=self.completed(self.items[0]);record['days'][self.day].update(status='unverified',ratio=None,reason='收盘冲突')
        self.assertFalse(reusable_checkpoint(record,[self.day],self.today))
    def test_incomplete_date_checkpoint_is_retried(self):self.assertFalse(reusable_checkpoint(self.completed(self.items[0]),[self.day,'2026-09-03'],self.today))
    def test_full_placeholders_retain_prior_verified_and_reference_rows_without_claiming_attempts(self):
        saved=[self.completed(self.items[0]),self.completed(self.items[1])]
        reference=saved[1]['days'][self.day]
        reference.update(status='unverified',ratio=None,referenceRatio=17.08,quality='source_difference',dayVolumeDifference=-44600,
                         reconciliationAttempts=[{'source':'sina','stage':'refresh','status':'unverified'}])
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);publish(saved,[self.day],out,'sample',3,2)
            pending,scope,attempted=make_snapshot(self.items,{},[self.day],True)
            publish(pending,[self.day],out,scope,3,attempted)
            data=json.loads((out/(self.day+'.json')).read_text());rows={r['code']:r for r in data['rows']}
            self.assertEqual(data['attemptedCount'],0);self.assertEqual(data['pendingCount'],3)
            self.assertEqual(rows['000001']['ratio'],10)
            self.assertEqual(rows['600519']['status'],'unverified');self.assertIsNone(rows['600519']['ratio'])
            self.assertEqual(rows['600519']['referenceRatio'],17.08)
            self.assertEqual(rows['600519']['reconciliationAttempts'],reference['reconciliationAttempts'])
            self.assertTrue(rows['600519']['pendingRefresh']);self.assertNotIn('lastAttemptAt',rows['600519'])
    def test_new_explicit_conflict_replaces_previous_verified_row(self):
        old=self.completed(self.items[0]);new=self.completed(self.items[0])
        new['days'][self.day].update(status='unverified',ratio=None,referenceRatio=12,quality='source_difference',dayVolumeDifference=-10)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);publish([old],[self.day],out,'sample',3,1)
            publish([new],[self.day],out,'sample',3,1)
            row=json.loads((out/(self.day+'.json')).read_text())['rows'][0]
            self.assertEqual(row['status'],'unverified');self.assertIsNone(row['ratio'])
            self.assertEqual(row['referenceRatio'],12);self.assertFalse(row.get('retainedFromPrevious',False))
    def test_failed_download_preserves_prior_unverified_evidence(self):
        old=self.completed(self.items[0]);old['days'][self.day].update(status='unverified',ratio=None,referenceRatio=12,quality='source_difference',dayVolumeDifference=-10)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);publish([old],[self.day],out,'sample',3,1)
            publish([self.completed(self.items[0],error=True)],[self.day],out,'sample',3,1)
            row=json.loads((out/(self.day+'.json')).read_text())['rows'][0]
            self.assertEqual(row['referenceRatio'],12);self.assertIsNone(row['ratio'])
            self.assertEqual(row['lastAttemptReason'],'采集失败：Timeout')
    def test_latest_first_publication_labels_policy_and_preserves_old_verified_evidence(self):
        old=self.completed(self.items[0]);new=self.completed(self.items[0])
        new['days'][self.day].update(status='unverified',ratio=None,referenceRatio=12,quality='source_difference',dayVolumeDifference=-10,
                                    reconciliationDeferred=True,reason='历史差异等待后续核验')
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);publish([old],[self.day],out,'sample',3,1)
            publish([new],[self.day],out,'partial',3,1,reconcile_latest_first=True)
            data=json.loads((out/(self.day+'.json')).read_text());manifest=json.loads((out/'manifest.json').read_text());row=data['rows'][0]
            self.assertEqual(row['status'],'unverified');self.assertIsNone(row['ratio'])
            self.assertEqual(row['previousVerifiedObservation']['ratio'],10)
            for obj in (data,manifest):
                self.assertEqual(obj['reconciliationPolicy'],'latest_first')
                self.assertIn('尚未自动排队重核验',obj['methodology'])

if __name__=='__main__':unittest.main()
