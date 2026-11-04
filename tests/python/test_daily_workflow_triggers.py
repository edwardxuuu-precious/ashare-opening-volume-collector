import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
REPO = 'edwardxuuu-precious/ashare-opening-volume-collector'
WORKFLOWS = {
    'trend-volume.yml': 'DISABLE_TREND_VOLUME_UPDATE',
    'golden-projection.yml': 'DISABLE_GOLDEN_PROJECTION_UPDATE',
    'golden.yml': 'DISABLE_GOLDEN_UPDATE',
}


def permits(text, *, event='workflow_run', branch='main', repository=REPO, disabled='', conclusion='failure', ref='refs/heads/main'):
    expression = re.search(r'^    if: >-\n(.*?)^    runs-on:', text, re.M | re.S).group(1)
    values = {
        'github.ref': ref, 'github.event_name': event,
        'github.event.workflow_run.head_branch': branch,
        'github.event.workflow_run.head_repository.full_name': repository,
        'github.event.workflow_run.conclusion': conclusion,
        **{'vars.' + variable: disabled for variable in WORKFLOWS.values()},
    }
    expression = re.sub(r'\b(?:github|vars)\.[\w.]+', lambda match: repr(values[match[0]]), expression)
    node = ast.parse(' '.join(expression.replace('&&', ' and ').replace('||', ' or ').split()), mode='eval')

    def evaluate(node):
        if isinstance(node, ast.Expression): return evaluate(node.body)
        if isinstance(node, ast.Constant): return node.value
        if isinstance(node, ast.BoolOp):
            return (all if isinstance(node.op, ast.And) else any)(evaluate(value) for value in node.values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = evaluate(node.left), evaluate(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq): return left == right
            if isinstance(node.ops[0], ast.NotEq): return left != right
        raise AssertionError('Unsupported workflow expression')

    return bool(evaluate(node))


class DailyWorkflowTriggers(unittest.TestCase):
    def test_committed_sources_are_checked_after_any_trusted_chip_completion(self):
        for filename in WORKFLOWS:
            text = (ROOT / '.github/workflows' / filename).read_text(encoding='utf-8')
            for conclusion in ('success', 'failure', 'cancelled', 'timed_out'):
                with self.subTest(workflow=filename, conclusion=conclusion):
                    self.assertTrue(permits(text, conclusion=conclusion))
            self.assertIn('workflows: [Daily whole-market chip estimates]', text)
            self.assertIn('types: [completed]', text)

    def test_automatic_events_keep_stop_branch_and_repository_boundaries(self):
        for filename in WORKFLOWS:
            text = (ROOT / '.github/workflows' / filename).read_text(encoding='utf-8')
            with self.subTest(workflow=filename):
                self.assertTrue(permits(text, event='schedule'))
                self.assertFalse(permits(text, disabled='true'))
                self.assertFalse(permits(text, event='schedule', disabled='true'))
                self.assertFalse(permits(text, branch='feature'))
                self.assertFalse(permits(text, repository='someone/fork'))
                self.assertFalse(permits(text, ref='refs/heads/feature'))
                self.assertTrue(permits(text, event='workflow_dispatch', disabled='true'))
                self.assertFalse(permits(text, event='workflow_dispatch', ref='refs/heads/feature'))

    def test_canonical_workflows_preserve_fallback_schedules(self):
        for filename in WORKFLOWS:
            text = (ROOT / '.github/workflows' / filename).read_text(encoding='utf-8')
            with self.subTest(workflow=filename):
                self.assertIn('  schedule:', text)
                if filename != 'golden.yml': self.assertIn("cron: '40 13,18 * * 1-5'", text)


if __name__ == '__main__':
    unittest.main()
