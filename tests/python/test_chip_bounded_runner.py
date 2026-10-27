"""Simulated process/metadata counterexamples; no processes or cloud started."""
import copy
import io
import json
import subprocess
import unittest

from tooling.run_chip_bounded import supervise, mark_timeout, Metadata, STATUS, LEASE


class Child:
    pid = 123456
    returncode = 0

    def __init__(self, results):
        self.results = list(results)
        self.waits = []

    def wait(self, timeout):
        self.waits.append(timeout)
        result = self.results.pop(0) if self.results else self.returncode
        if result == 'timeout':
            raise subprocess.TimeoutExpired('INVENTED', timeout)
        self.returncode = result
        return result

    def poll(self):
        return None


class ProcessBudget(unittest.TestCase):
    def run_case(self, results):
        child = Child(results)
        launches, signals = [], []
        def launch(command, **kwargs):
            launches.append((command, kwargs))
            return child
        result = supervise(['INVENTED'], 2, popen=launch, kill_group=lambda *args: signals.append(args))
        self.assertEqual(len(launches), 1)
        self.assertTrue(launches[0][1]['start_new_session'])
        return result, child, signals

    def test_normal_or_failed_child_preserves_exit_without_signals(self):
        for code in (0, 7):
            result, child, signals = self.run_case([code])
            self.assertEqual(result, (code, False, True))
            self.assertEqual(child.waits, [180])
            self.assertEqual(signals, [])

    def test_timeout_terminates_only_the_owned_group(self):
        result, child, signals = self.run_case(['timeout', -15])
        self.assertEqual(result, (124, True, True))
        self.assertEqual(child.waits, [180, 10, 10])
        self.assertEqual(signals, [(child.pid, 15), (child.pid, 9)])

    def test_unresponsive_group_is_killed_with_another_bounded_wait(self):
        result, child, signals = self.run_case(['timeout', 'timeout', -9])
        self.assertEqual(result, (124, True, True))
        self.assertEqual(child.waits, [180, 10, 10])
        self.assertEqual(signals, [(child.pid, 15), (child.pid, 9)])

    def test_leader_exiting_at_deadline_cannot_leave_a_surviving_worker(self):
        child = Child(['timeout', 0])
        child.poll = lambda: 0
        signals = []
        result = supervise(['INVENTED'], 2, popen=lambda *a, **k: child,
            kill_group=lambda *args: signals.append(args))
        self.assertEqual(result, (124, True, True))
        self.assertEqual(signals, [(child.pid, 15), (child.pid, 9)])

    def test_unreaped_child_reports_timeout_without_waiting_indefinitely(self):
        result, child, signals = self.run_case(['timeout', 'timeout', 'timeout'])
        self.assertEqual(result, (124, True, False))
        self.assertEqual(child.waits, [180, 10, 10])

    def test_cannot_expand_original_runtime_budget(self):
        for value in (1, 286, True, 2.5):
            with self.assertRaises(ValueError):
                supervise([], value, popen=lambda *a, **k: self.fail('Unexpected process'), kill_group=lambda *a: None)


class Store:
    def __init__(self):
        self.rows = {STATUS: {'workflow_run_id': '42', 'lease_owner': 'OWNED', 'state': 'RUNNING',
            'as_of': '2026-10-27', 'dataset_id': 'a'*16, 'scanned_count': 10},
            LEASE: {'owner': 'OWNED', 'expires': 1000}}
        self.writes = []

    def get(self, key):
        return copy.deepcopy(self.rows[key]), key+'-etag'

    def put(self, key, value, *, match):
        self.writes.append((key, match))
        self.rows[key] = copy.deepcopy(value)


class TimeoutState(unittest.TestCase):
    def test_only_stopped_run_is_marked_paused_and_released_with_cas(self):
        store = Store()
        result = mark_timeout(store, '42', 285, timestamp='INVENTED_TIME')
        self.assertTrue(result['timeout_status_updated'])
        self.assertEqual(store.rows[STATUS]['state'], 'PAUSED_WORKER_TIMEOUT')
        self.assertEqual(store.rows[STATUS]['scanned_count'], 10)
        self.assertEqual(store.rows[LEASE]['expires'], 0)
        self.assertEqual(store.writes, [(STATUS, STATUS+'-etag'), (LEASE, LEASE+'-etag')])

    def test_another_run_owner_or_committed_publication_is_untouched(self):
        for field, value in (('workflow_run_id', '43'), ('lease_owner', 'OTHER'), ('state', 'COMMITTED')):
            store = Store()
            store.rows[STATUS][field] = value
            self.assertFalse(mark_timeout(store, '42', 285)['timeout_status_updated'])
            self.assertEqual(store.writes, [])
        store = Store()
        self.assertFalse(mark_timeout(store, None, 285)['timeout_status_updated'])
        self.assertEqual(store.writes, [])

    def test_unconfirmed_cleanup_marks_paused_but_keeps_the_lease_until_expiry(self):
        store = Store()
        result = mark_timeout(store, '42', 285, group_stopped=False)
        self.assertTrue(result['timeout_status_updated'])
        self.assertFalse(result['lease_released'])
        self.assertFalse(store.rows[STATUS]['worker_cleanup_complete'])
        self.assertEqual(store.rows[LEASE]['expires'], 1000)
        self.assertEqual(store.writes, [(STATUS, STATUS+'-etag')])

    def test_conflicting_metadata_write_fails_without_releasing_a_new_owner(self):
        store = Store()
        def conflict(*args, **kwargs):
            raise RuntimeError('INVENTED_CAS_CONFLICT')
        store.put = conflict
        with self.assertRaisesRegex(RuntimeError, 'CAS_CONFLICT'):
            mark_timeout(store, '42', 285)
        self.assertEqual(store.rows[LEASE]['expires'], 1000)

    def test_timeout_metadata_reader_is_bounded(self):
        class Client:
            def __init__(self, raw): self.raw = raw
            def get_object(self, **kwargs): return {'Body': io.BytesIO(self.raw), 'ETag': 'EXACT'}
        value = {'state': 'INVENTED'}
        self.assertEqual(Metadata(Client(json.dumps(value).encode()), 'INVENTED').get(STATUS), (value, 'EXACT'))
        with self.assertRaises(ValueError):
            Metadata(Client(b' '*1000001), 'INVENTED').get(STATUS)


if __name__ == '__main__':
    unittest.main()
