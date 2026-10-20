import _bootstrap
import importlib.util, json, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]/'deploy/collector'
spec=importlib.util.spec_from_file_location('stop_after_run',ROOT/'stop_after_run.py')
stop=importlib.util.module_from_spec(spec);spec.loader.exec_module(stop)

class LifecycleTests(unittest.TestCase):
    def good(self):return dict(serviceInvocationId='current',shutdownReady=True,status='completed')
    def test_completed_current_run_stops(self):self.assertTrue(stop.should_stop(self.good(),'current',False))
    def test_safely_finalized_pause_stops(self):
        state=self.good();state['status']='paused';self.assertTrue(stop.should_stop(state,'current',False))
    def test_maintenance_prevents_stop(self):self.assertFalse(stop.should_stop(self.good(),'current',True))
    def test_old_result_cannot_stop_new_invocation(self):self.assertFalse(stop.should_stop(self.good(),'new',False))
    def test_manual_python_cannot_trigger_root_stop(self):self.assertFalse(stop.should_stop(self.good(),'',False))
    def test_failed_publication_cannot_stop_early(self):
        state=self.good();state['shutdownReady']=False;self.assertFalse(stop.should_stop(state,'current',False))
    def test_active_state_cannot_stop(self):
        state=self.good();state['status']='running';self.assertFalse(stop.should_stop(state,'current',False))
    def test_schedule_targets_exact_instance_and_local_time(self):
        template=json.loads((ROOT/'template.json').read_text());props=template['Resources']['DailyStartSchedule']['Properties']
        self.assertEqual(props['ScheduleExpression'],'cron(10 17 ? * MON-FRI *)')
        self.assertEqual(props['ScheduleExpressionTimezone'],'Asia/Shanghai')
        self.assertEqual(props['Target']['Arn'],'arn:aws:scheduler:::aws-sdk:ec2:startInstances')
        action=template['Resources']['DailyStartRole']['Properties']['Policies'][0]['PolicyDocument']['Statement'][0]
        self.assertEqual(action['Action'],'ec2:StartInstances')
        self.assertTrue(action['Resource']['Fn::Sub'].endswith('instance/${CollectorInstance}'))
    def test_scheduler_trust_scoped_to_group_not_schedule(self):
        template=json.loads((ROOT/'template.json').read_text())
        trust=template['Resources']['DailyStartRole']['Properties']['AssumeRolePolicyDocument']['Statement'][0]
        self.assertEqual(trust['Condition']['StringEquals']['aws:SourceArn'],{'Fn::GetAtt':['DailyScheduleGroup','Arn']})
    def test_probe_does_not_auto_start_service(self):
        text=(ROOT/'install-service.sh').read_text()
        self.assertNotIn('enable --now stock-collector.service',text)
        self.assertIn('OnBootSec=24h',text)
        self.assertIn('ExecStopPost=+/usr/bin/python3 /usr/local/libexec/stock-collector-stop.py',text)

if __name__=='__main__':unittest.main()
