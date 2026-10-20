import _bootstrap
import copy,json,unittest
from pathlib import Path
from deploy.collector.daily_infrastructure import make_template,ROOT
class DailyInfrastructureTests(unittest.TestCase):
 def test_stop_schedule_changes_no_existing_resources(self):
  current=json.loads((ROOT/'deploy/collector/template.json').read_text())
  for n in ['DailyStopRole','DailyStopSchedule']:current['Resources'].pop(n,None)
  before=copy.deepcopy(current);candidate=make_template(current,'collector')
  self.assertEqual(before,current)
  for k,v in before['Resources'].items():self.assertEqual(candidate['Resources'][k],v)
  stop=candidate['Resources']['DailyStopSchedule']['Properties']
  self.assertEqual(stop['ScheduleExpressionTimezone'],'Asia/Shanghai')
  self.assertEqual(stop['ScheduleExpression'],'cron(15 22 ? * MON-FRI *)')
  statement=candidate['Resources']['DailyStopRole']['Properties']['Policies'][0]['PolicyDocument']['Statement'][0]
  self.assertEqual(statement['Action'],'ec2:StopInstances')
  self.assertIn('${CollectorInstance}',statement['Resource']['Fn::Sub'])
 def test_retention_preserves_current_data_and_latest_backup(self):
  current={'Resources':{'DataBucket':{'Properties':{'VersioningConfiguration':{'Status':'Enabled'}}},'Function':{'Properties':{'Code':'must not change'}}}}
  candidate=make_template(current,'auth')
  self.assertEqual(candidate['Resources']['Function'],current['Resources']['Function'])
  rules=candidate['Resources']['DataBucket']['Properties']['LifecycleConfiguration']['Rules']
  for r in rules:
   self.assertEqual(r['NoncurrentVersionExpiration']['NoncurrentDays'],7)
   if r['Prefix']!='collector/imports/':self.assertNotIn('ExpirationInDays',r)
  self.assertEqual({r['Prefix'] for r in rules},{'data/','collector/checkpoint.tar.gz','collector/imports/'})
if __name__=='__main__':unittest.main()
