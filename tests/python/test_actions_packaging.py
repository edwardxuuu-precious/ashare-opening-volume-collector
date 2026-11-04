import unittest
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy.actions.infrastructure import template
from deploy.actions.package import SOURCES, WORKFLOWS, build


PUBLIC_RUNTIME = set('''
backend/scripts/collect.py
backend/scripts/retention.py
backend/scripts/reconciliation.py
backend/scripts/sina_unadjusted.py
backend/scripts/universe_cache.py
backend/scripts/baostock_source.py
backend/scripts/daily_state.py
backend/scripts/daily_collector.py
backend/scripts/actions_source_probe.py
backend/scripts/download_only.py
backend/scripts/backfill_quotes.py
backend/scripts/sina_daily.py
backend/scripts/sina_spot.py
backend/scripts/refresh.py
backend/scripts/no_trade_evidence.py
backend/scripts/exchange_status.py
backend/server/cloud_worker.py
backend/server/publisher_state.py
backend/server/actions_runner.py
backend/server/import_checkpoint_batch.py
backend/server/refresh_worker.py
backend/server/failure_status.py
backend/chips/__init__.py
backend/chips/calendar.py
backend/chips/model.py
backend/chips/eastmoney_model.py
backend/chips/ohlc4_input.py
backend/chips/current_quote.py
backend/chips/probe.py
backend/chips/runner.py
backend/chips/screen.py
backend/chips/source.py
backend/chips/baostock_history.py
backend/chips/ths_history.py
backend/chips/listing.py
backend/chips/display.py
backend/chips/limit_reference.py
backend/chips/golden.py
backend/chips/history.py
backend/chips/stability.py
backend/chips/suspension_evidence.py
backend/chips/concentration.py
backend/chips/concentration_runner.py
backend/chips/concentration_v2.py
backend/chips/concentration_v2_runner.py
backend/chips/concentration_two.py
backend/chips/concentration_two_runner.py
backend/golden/__init__.py
backend/golden/anchors.py
backend/golden/automatic.py
backend/golden/watchlist.py
backend/golden/daily.py
backend/golden/annotations.py
backend/golden/sources.py
backend/golden/update.py
backend/trend_volume/__init__.py
backend/trend_volume/types.py
backend/trend_volume/frozen_v3.py
backend/trend_volume/model.py
backend/trend_volume/daily.py
backend/golden_projection/__init__.py
backend/golden_projection/policy.json
backend/golden_projection/model.py
backend/golden_projection/daily.py
backend/golden_projection/index_sources.py
backend/triangles/__init__.py
backend/triangles/daily.py
tooling/build-triangle-daily.mjs
src/lib/triangles.ts
src/lib/triangle-data.ts
src/lib/auth-fetch.ts
src/lib/read-request.ts
package.json
tooling/run_chip_bounded.py
tooling/chip_workflow_budget.py
tooling/chip_followup_guard.py
'''.split())
PUBLIC_WORKFLOWS = {'chips.yml', 'collector.yml', 'golden-projection.yml', 'golden.yml',
                    'source-probe.yml', 'trend-volume.yml', 'triangles.yml'}

class ActionsPackagingTests(unittest.TestCase):
    def test_collector_starts_after_close_with_redundant_idempotent_runs(self):
        workflow=(Path(__file__).resolve().parents[2]/'.github/workflows/collector.yml').read_text()
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
        self.assertEqual(set(SOURCES), PUBLIC_RUNTIME)
        self.assertEqual(set(WORKFLOWS), PUBLIC_WORKFLOWS)
        root=Path(__file__).resolve().parents[2]
        self.assertEqual({path.name for path in (root/'.github/workflows').glob('*.yml')}, PUBLIC_WORKFLOWS)
        self.assertNotIn('backend.golden.daily',(root/'.github/workflows/chips.yml').read_text())
        self.assertIn('backend.golden.update',(root/'.github/workflows/golden.yml').read_text())
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'bundle'
            manifest=build(out)
            self.assertEqual(set(manifest), PUBLIC_RUNTIME | {'requirements.txt', '.gitignore', 'README.md'}
                             | {'.github/workflows/'+name for name in PUBLIC_WORKFLOWS})
            self.assertFalse(any(part in {'accounts', 'auth', 'data', 'outputs', 'var', '.git', '.env'}
                                 for name in manifest for part in Path(name).parts))
            self.assertNotIn('backend/server/service.py',manifest)
            self.assertNotIn('deploy/auth/accounts.py',manifest)
            for name in PUBLIC_WORKFLOWS:
                self.assertEqual((out/'.github/workflows'/name).read_bytes(),
                                 (root/'.github/workflows'/name).read_bytes())

    def test_public_readme_documents_explicit_pause_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'bundle'
            build(out)
            readme=(out/'README.md').read_text()
        self.assertIn('DISABLE_DAILY_UPDATE',readme)
        self.assertNotIn('ENABLE_DAILY_UPDATE',readme)

if __name__=='__main__':unittest.main()
