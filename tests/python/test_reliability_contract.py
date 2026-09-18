import _bootstrap
from datetime import datetime
from pathlib import Path
import unittest

import actions_runner
import refresh_worker
from deploy.actions.dispatcher import pending
from deploy.actions.observe_refresh import audit, missing_payload_report

DAY = '2026-09-08'


def complete_row(code='600519'):
    return dict(
        code=code,
        status='ok',
        first15Volume=10,
        dailyVolume=100,
        ratio=10,
    )


def status(**overrides):
    value = dict(
        targetDate=DAY,
        dataComplete=True,
        outcome='completed',
        calculableCount=1,
        noTradeCount=0,
        calendarDates=[DAY],
        calendarValidThrough=DAY,
        phase='close',
    )
    value.update(overrides)
    return value


def payload(**overrides):
    value = dict(date=DAY, rows=[complete_row()], universeTotal=1, generatedAt='2026-09-08T16:00:00+08:00')
    value.update(overrides)
    return value


def manifest(**overrides):
    value = dict(dates=[dict(date=DAY, valid=1, suspended=0, generatedAt='2026-09-08T16:00:00+08:00')])
    value.update(overrides)
    return value


class CompletionContractTests(unittest.TestCase):
    def test_legacy_schedule_switch_does_not_block_dispatcher_or_recovery(self):
        workflow = (Path(__file__).resolve().parents[2] / '.github/workflows/collector.yml').read_text()
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertNotIn('Production writer is explicitly disabled.', workflow)

    def test_checkpointed_pause_is_visible_failure(self):
        result = dict(
            status='paused',
            outcome='incomplete_checkpointed',
            checkpointSaved=True,
            workerExitCode=0,
            dataComplete=False,
        )
        self.assertFalse(actions_runner.successful_exit(result))

    def test_only_completed_validated_and_no_work_succeed(self):
        for outcome in ('completed', 'validated', 'no_work'):
            self.assertTrue(actions_runner.successful_exit(dict(outcome=outcome)))
        for outcome in ('incomplete_checkpointed', 'failed', 'running'):
            self.assertFalse(actions_runner.successful_exit(dict(outcome=outcome)))

    def test_no_work_cannot_mask_an_incomplete_opening_or_close(self):
        self.assertFalse(actions_runner.successful_exit(dict(
            outcome='no_work', phase='opening', openingComplete=False,
            dataComplete=False, unprocessedCount=5565,
            exitReason='source_throttled', checkpointSaved=True,
        )))

    def test_explicit_incomplete_target_is_watchdog_work(self):
        self.assertTrue(pending(dict(dataComplete=False)))
        self.assertFalse(pending(dict(dataComplete=True, pendingCount=99)))

    def test_source_throttle_finishes_after_active_requests_drain(self):
        self.assertFalse(refresh_worker.should_finish_after_stop(True, {'600519': object()}))
        self.assertTrue(refresh_worker.should_finish_after_stop(True, {}))
        self.assertFalse(refresh_worker.should_finish_after_stop(False, {}))


class HealthAuditTests(unittest.TestCase):
    def setUp(self):
        self.checked_at = datetime.fromisoformat('2026-09-08T16:00:00+08:00')

    def test_consistent_latest_complete_publication_is_healthy(self):
        result = audit(status(), payload(), manifest(), self.checked_at)
        self.assertEqual(result['health'], 'healthy')
        self.assertTrue(result['dataComplete'])
        self.assertEqual(result['expectedClosedDate'], DAY)

    def test_incomplete_checkpoint_never_becomes_healthy(self):
        result = audit(
            status(dataComplete=False, outcome='incomplete_checkpointed', retryableCount=5),
            payload(),
            manifest(),
            self.checked_at,
        )
        self.assertEqual(result['health'], 'needs_attention')
        self.assertIn('status_incomplete', result['publicationErrors'])
        self.assertIn('worker_outcome_incomplete_checkpointed', result['publicationErrors'])

    def test_stale_target_and_manifest_mismatch_are_rejected(self):
        stale = audit(
            status(calendarDates=['2026-09-08', '2026-09-09'], calendarValidThrough='2026-09-09'),
            payload(),
            manifest(dates=[dict(date=DAY, valid=1, suspended=0, generatedAt='different')]),
            datetime.fromisoformat('2026-09-09T16:00:00+08:00'),
        )
        self.assertEqual(stale['health'], 'needs_attention')
        self.assertIn('target_not_latest_closed_day', stale['publicationErrors'])
        self.assertIn('manifest_generation', stale['publicationErrors'])

    def test_missing_calendar_is_not_a_green_health_check(self):
        result = audit(status(calendarDates=[]), payload(), manifest(), self.checked_at)
        self.assertEqual(result['health'], 'needs_attention')
        self.assertIn('calendar_unavailable', result['publicationErrors'])

    def test_missing_payload_is_an_actionable_health_failure(self):
        result = missing_payload_report(status(), DAY, self.checked_at)
        self.assertEqual(result['health'], 'needs_attention')
        self.assertFalse(result['dataComplete'])
        self.assertIn('payload_missing', result['publicationErrors'])
        self.assertIn('false_completion', result['publicationErrors'])
        self.assertIsNone(result['total'])


if __name__ == '__main__':
    unittest.main()
