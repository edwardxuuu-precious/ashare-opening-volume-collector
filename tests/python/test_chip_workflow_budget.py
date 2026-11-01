from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

import yaml

from tooling import chip_workflow_budget as budget
from tooling import run_chip_bounded as bounded
from tooling import chip_followup_guard as guard


T0 = datetime(2026, 11, 2, 7, 40, tzinfo=timezone.utc)
ENV = {budget.START_ENV: str(int(T0.timestamp()))}


class Deadline(unittest.TestCase):
    def test_setup_and_guard_wait_consume_the_same_source_allowance(self):
        for elapsed, expected in ((12, 265), (22, 255), (100, 177), (275, 2)):
            now = T0 + timedelta(minutes=elapsed)
            result = budget.source_plan(now, 285, ENV)
            self.assertEqual(result['compute_minutes'], expected)
            self.assertLessEqual(elapsed + result['compute_minutes'] + 5, 282)
            self.assertLessEqual(elapsed + result['step_minutes'], 281)
        self.assertEqual(282 + budget.DERIVE_STEP_MINUTES + budget.OUTER_SLACK_MINUTES, 310)

    def test_manual_request_is_capped_and_a_small_request_remains_small(self):
        now = T0 + timedelta(minutes=12)
        self.assertEqual(budget.source_plan(now, 285, ENV)['compute_minutes'], 265)
        self.assertEqual(budget.source_plan(now, 2, ENV)['compute_minutes'], 2)
        with self.assertRaisesRegex(ValueError, 'EXHAUSTED'):
            budget.source_plan(T0 + timedelta(minutes=276), 285, ENV)

    def test_guard_rechecks_after_claim_latency(self):
        started = T0 + timedelta(minutes=12)
        with patch.dict(os.environ, ENV):
            self.assertEqual(guard.runtime_budget(started, started), 265)
            self.assertEqual(guard.runtime_budget(started, started + timedelta(minutes=2)), 263)
            with self.assertRaisesRegex(ValueError, 'EXHAUSTED'):
                guard.runtime_budget(started, T0 + timedelta(minutes=280))

    def test_supervisor_main_cannot_restore_requested_285_minutes(self):
        now = T0 + timedelta(minutes=12)
        with patch.dict(os.environ, ENV), patch.object(bounded, 'datetime') as clock, \
                patch('sys.argv', ['run_chip_bounded.py', '--bucket', 'INVENTED', '--max-minutes', '285']), \
                patch.object(bounded, 'supervise', return_value=(0, False, True)) as supervise:
            clock.now.return_value = now
            self.assertEqual(bounded.main(), 0)
            command, minutes = supervise.call_args.args
            self.assertEqual(minutes, 265)
            self.assertEqual(command[command.index('--max-minutes') + 1], '265')

    def test_invalid_start_or_exhausted_verifier_never_extends_deadline(self):
        for value in ('', 'nan', '-1', '1.5', str(int(T0.timestamp()) + 31)):
            with self.assertRaises(ValueError):
                budget.source_deadline(T0, {budget.START_ENV: value})
        self.assertEqual(budget.verification_seconds(T0, ENV), 90)
        self.assertEqual(budget.verification_seconds(T0 + timedelta(minutes=281, seconds=40), ENV), 10)
        with self.assertRaisesRegex(ValueError, 'EXHAUSTED'):
            budget.verification_seconds(T0 + timedelta(minutes=282), ENV)

    def test_plan_cli_writes_only_bounded_numeric_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'output'
            with patch.dict(os.environ, {**ENV, 'GITHUB_OUTPUT': str(output)}), \
                    patch.object(budget, 'datetime') as clock, \
                    patch('sys.argv', ['chip_workflow_budget.py', 'source-plan', '--requested', '285']):
                clock.now.return_value = T0 + timedelta(minutes=12)
                clock.fromtimestamp.side_effect = datetime.fromtimestamp
                budget.main()
            self.assertEqual(output.read_text(), 'compute_minutes=265\nstep_minutes=269\n')


class Workflow(unittest.TestCase):
    def setUp(self):
        self.raw = Path('.github/workflows/chips.yml').read_text()
        self.workflow = yaml.load(self.raw, Loader=yaml.BaseLoader)
        self.job = self.workflow['jobs']['chips']
        self.steps = {step.get('id'): step for step in self.job['steps'] if step.get('id')}

    def test_both_release_workflows_share_two_group_derivation_and_fixed_schedule(self):
        self.assertEqual(self.raw, Path('deploy/actions/workflows/chips.yml').read_text())
        self.assertEqual(self.job['timeout-minutes'], '310')
        self.assertEqual([x['cron'] for x in self.workflow['on']['schedule']], ['40 7 * * *', '20 13 * * *', '20 18 * * *'])
        command = self.steps['concentration']['run']
        self.assertIn('backend.chips.concentration_two_runner', command)
        self.assertNotIn('concentration_v2_runner', command)
        self.assertIn('1190s', command)
        self.assertIn('--max-minutes 20', command)
        self.assertNotIn('--reuse-verified-v2', command)
        self.assertEqual(self.steps['concentration']['timeout-minutes'], '21')

    def test_setup_has_a_bound_and_source_uses_the_allocated_runtime(self):
        self.assertEqual(self.job['steps'][0]['id'], 'budget')
        setup = ('budget', 'checkout', 'runtime', 'dependencies', 'credentials')
        self.assertEqual(sum(int(self.steps[k]['timeout-minutes']) for k in setup), 12)
        self.assertIn(budget.START_ENV, self.steps['budget']['run'])
        source = self.steps['source']
        self.assertIn('source_budget.outputs.compute_minutes', source['run'])
        self.assertIn('source_budget.outputs.step_minutes', source['timeout-minutes'])
        self.assertIn('verification-seconds', source['run'])

    def test_failed_source_remains_failed_and_recovery_reads_the_actual_publication(self):
        derive = self.steps['concentration']
        condition = derive['if']
        self.assertIn('!cancelled()', condition)
        self.assertIn("inputs.mode != 'probe'", condition)
        for name in ('checkout', 'runtime', 'dependencies', 'credentials'):
            self.assertIn(f"steps.{name}.outcome == 'success'", condition)
        self.assertNotIn('source.outcome', condition)
        self.assertNotIn('followup.outputs.should_run', condition)
        self.assertFalse(any('continue-on-error' in step for step in self.job['steps']))
        self.assertNotIn('--as-of', derive['run'])
        self.assertIn('steps.followup.outputs.should_run', self.steps['source_budget']['if'])
        self.assertIn("inputs.mode != 'derive'", self.steps['source_budget']['if'])
        self.assertIn("inputs.mode != 'derive'", self.steps['followup']['if'])
        self.assertIn('derive', self.workflow['on']['workflow_dispatch']['inputs']['mode']['options'])


if __name__ == '__main__':
    unittest.main()
