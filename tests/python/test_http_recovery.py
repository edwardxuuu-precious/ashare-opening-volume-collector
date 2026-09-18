import _bootstrap
import multiprocessing as mp
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import refresh_worker
from scripts import daily_collector


class HTTPRecoveryTests(unittest.TestCase):
    def test_stock_level_http_failures_cool_the_source_and_probe_serially(self):
        self.assertEqual(
            [refresh_worker.source_cooldown_seconds(streak)
             for streak in (1, 2, 3, 4, 5, 20)],
            [5, 15, 30, 60, 120, 120],
        )
        self.assertEqual(refresh_worker.source_dispatch_capacity(0), 4)
        self.assertEqual(refresh_worker.source_dispatch_capacity(1), 1)
        self.assertEqual(refresh_worker.source_dispatch_capacity(20), 1)
        self.assertFalse(refresh_worker.source_recovery_confirmed(
            '2026-09-08T10:00:00Z', 104.0, 105.0))
        self.assertTrue(refresh_worker.source_recovery_confirmed(
            '2026-09-08T10:00:00Z', 105.0, 105.0))

    def test_status_uses_nearest_future_retry_instead_of_expired_retry(self):
        items = [dict(code='000001'), dict(code='600519')]
        target = dict(date='2026-09-08', attempts={
            '000001': dict(committed=True, nextRetryAt='2026-09-08T06:55:00+08:00'),
            '600519': dict(committed=True, nextRetryAt='2026-09-08T07:15:00+08:00'),
        })
        checked_at = datetime.fromisoformat('2026-09-08T07:00:00+08:00')
        with patch.object(refresh_worker, 'now', return_value=checked_at):
            status = refresh_worker.metrics(items, {}, target, 'catchup')
        self.assertEqual(status['nextRetryAt'], '2026-09-08T07:15:00+08:00')

    def test_transient_timeout_sets_shared_adaptive_cooldown_before_retry(self):
        import requests

        context = mp.get_context('spawn')
        clock = [100.0]
        cooldown = context.Value('d', 0)
        transient_failures = context.Value('q', 0)
        budget = daily_collector.SharedHTTPBudget(
            context.Lock(), context.Value('d', 0), context.Value('q', 0),
            context.Value('q', 0), .001, 200.0, cooldown, transient_failures)
        budget.TRANSIENT_BACKOFF_SECONDS = (2.0, 5.0, 15.0, 30.0)
        calls = []

        def flaky_request(*args, **kwargs):
            calls.append(clock[0])
            if len(calls) == 1:
                raise requests.exceptions.ReadTimeout('fixture page timeout')
            return SimpleNamespace(content=b'ok', raise_for_status=lambda: None)

        def advance(seconds):
            clock[0] += seconds

        with patch.object(daily_collector.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(daily_collector.time, 'sleep', side_effect=advance), \
             patch.object(requests.sessions.Session, 'request', new=flaky_request):
            budget.install()
            response = requests.get('https://fixture.invalid/page/65')

        self.assertEqual(response.content, b'ok')
        self.assertEqual(calls, [100.0, 102.0])
        self.assertEqual(cooldown.value, 102.0)
        self.assertEqual(transient_failures.value, 0)


if __name__ == '__main__':
    unittest.main()
