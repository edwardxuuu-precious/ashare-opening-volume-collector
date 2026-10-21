import unittest
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy.actions.infrastructure import template
from deploy.actions.package import SOURCES, build

class ActionsPackagingTests(unittest.TestCase):
    def test_collector_starts_after_close_with_redundant_idempotent_runs(self):
        workflow=(Path(__file__).resolve().parents[2]/'deploy/actions/workflows/collector.yml').read_text()
        for cron in ("'50 1 * * 1-5'", "'30 7 * * 1-5'", "'0 23 * * 0-4'", "'10 3 * * 1-5'"):
            self.assertIn(cron,workflow)
        self.assertIn('--phase "$phase"',workflow)
        self.assertIn('--target-date "$STOCK_TARGET_DATE"',workflow)
        self.assertNotIn('actions_source_probe.py',workflow)
        self.assertIn('group: stock-private-writer',workflow)
        self.assertIn('cancel-in-progress: false',workflow)
        self.assertIn('DISABLE_DAILY_UPDATE',workflow)
        self.assertNotIn('ENABLE_DAILY_UPDATE',workflow)

    def test_role_is_exact_subject_and_s3_only(self):
        sub='repo:edwardxuuu-precious@248306801/ashare-opening-volume-collector@123:ref:refs/heads/main'
        value=template('example-stock-bucket',sub)
        role=value['Resources']['CollectorRole']['Properties']
        self.assertEqual(role['MaxSessionDuration'],21600)
        self.assertEqual(role['AssumeRolePolicyDocument']['Statement'][0]['Condition']['StringEquals']['token.actions.githubusercontent.com:sub'],sub)
        for stmt in role['Policies'][0]['PolicyDocument']['Statement']:
            self.assertTrue(all(action.startswith('s3:') for action in stmt['Action']))
        self.assertNotIn('AWS::EC2::Instance',str(value))

    def test_collector_cannot_delete_saved_data_or_backups(self):
        value=template('example-stock-bucket','repo:edwardxuuu-precious/ashare-opening-volume-collector:ref:refs/heads/main')
        statements=value['Resources']['CollectorRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
        deletions=[stmt for stmt in statements if 's3:DeleteObject' in stmt['Action']]
        self.assertEqual(len(deletions),1)
        self.assertEqual(deletions[0]['Resource'],['arn:aws:s3:::example-stock-bucket/collector/actions-lease.json'])

    def test_untrusted_subjects_rejected(self):
        for sub in ('repo:*','repo:another-owner/ashare-opening-volume-collector:ref:refs/heads/main',
                    'repo:edwardxuuu-precious/ashare-opening-volume-collector:pull_request',
                    'repo:edwardxuuu-precious/ashare-opening-volume-collector:ref:refs/heads/dev'):
            with self.assertRaises(ValueError): template('example-stock-bucket',sub)

    def test_only_explicit_runtime_sources_are_packaged(self):
        self.assertEqual(len(SOURCES),len(set(SOURCES)))
        self.assertTrue(all(p.startswith(('backend/scripts/','backend/server/','backend/chips/','backend/golden/')) and p.endswith('.py') for p in SOURCES))
        self.assertEqual({p for p in SOURCES if p.startswith('backend/chips/')},
            {'backend/chips/'+name+'.py' for name in ('__init__','calendar','model','eastmoney_model','ohlc4_input','current_quote','probe','runner','screen','source','baostock_history','ths_history','listing','display','limit_reference','golden')})
        self.assertIn('backend/scripts/sina_spot.py',SOURCES)
        self.assertEqual({p for p in SOURCES if p.startswith('backend/golden/')},
            {'backend/golden/'+name+'.py' for name in ('__init__','anchors','automatic','watchlist','daily','annotations')})
        root=Path(__file__).resolve().parents[2]
        self.assertEqual((root/'.github/workflows/chips.yml').read_text(),(root/'deploy/actions/workflows/chips.yml').read_text())
        self.assertIn('python -m backend.golden.daily --bucket "$STOCK_BUCKET"',(root/'.github/workflows/chips.yml').read_text())
        self.assertIn('backend/scripts/exchange_status.py',SOURCES)
        self.assertNotIn('backend/server/service.py',SOURCES)
        self.assertNotIn('deploy/auth/accounts.py',SOURCES)

    def test_public_readme_documents_explicit_pause_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'bundle'
            build(out)
            readme=(out/'README.md').read_text()
        self.assertIn('DISABLE_DAILY_UPDATE',readme)
        self.assertNotIn('ENABLE_DAILY_UPDATE',readme)

if __name__=='__main__':unittest.main()
