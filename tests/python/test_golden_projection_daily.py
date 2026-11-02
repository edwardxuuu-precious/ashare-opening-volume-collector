import _bootstrap
from copy import deepcopy
import unittest
from unittest.mock import patch

from backend.golden_projection import daily
from test_trend_volume_daily import FakeStore, AS_OF


class ProjectionDailyTests(unittest.TestCase):
    def run_daily(self, store, **kwargs):
        return daily.run(store, as_of=AS_OF, max_seconds=120, workers=2, progress=None, **kwargs)

    def test_complete_universe_published_and_same_input_is_idempotent(self):
        store = FakeStore()
        result = self.run_daily(store)
        self.assertEqual(result['outcome'], 'COMMITTED')
        self.assertEqual(result['universe_total'], 4)
        manifest = store.values[daily.PREFIX + 'manifest.json']
        screen = store.values[daily.PREFIX + result['dataset_id'] + '/screening.json']
        self.assertEqual(len(screen['rows']), 4)
        self.assertEqual(sum(manifest['counts'].values()), 4)
        self.assertEqual(sum(manifest['state_counts'].values()), 4)
        self.assertEqual(screen['rows'][-1]['category'], 'UNKNOWN')
        writes, reads = len(store.writes), store.source_reads
        repeat = self.run_daily(store)
        self.assertEqual(repeat['outcome'], 'ALREADY_COMMITTED')
        self.assertEqual(repeat['source_reads'], 0)
        self.assertEqual(store.source_reads, reads)
        self.assertEqual(store.values[daily.PREFIX + 'status.json']['dataset_id'], result['dataset_id'])
        latest_index = store.writes.index(daily.PREFIX + 'manifest.json')
        self.assertTrue(all(store.writes.index(daily.PREFIX + result['dataset_id'] + '/' + row['code'] + '.json') < latest_index for row in screen['rows']))
        self.assertGreater(len(store.writes), writes)

    def test_manifest_commit_failure_resumes_exact_immutable_results(self):
        store = FakeStore()
        original = store.put
        fail = [True]

        def write(key, value, **kwargs):
            if key == daily.PREFIX + 'manifest.json' and fail[0]:
                fail[0] = False
                raise RuntimeError('FICTIONAL_COMMIT_INTERRUPTED')
            return original(key, value, **kwargs)

        store.put = write
        with self.assertRaisesRegex(RuntimeError, 'FICTIONAL_COMMIT_INTERRUPTED'):
            self.run_daily(store)
        self.assertNotIn(daily.PREFIX + 'manifest.json', store.values)
        partial = {key: deepcopy(value) for key, value in store.values.items()
                   if key.startswith(daily.PREFIX) and '/'+key.split('/')[-1] not in ('/lease.json', '/status.json')}
        resumed = self.run_daily(store)
        self.assertEqual(resumed['outcome'], 'COMMITTED')
        for key, value in partial.items():
            self.assertEqual(store.values[key], value)

    def test_pending_source_keeps_the_published_manifest(self):
        store = FakeStore()
        self.run_daily(store)
        before = deepcopy(store.values[daily.PREFIX + 'manifest.json'])
        store.values[daily.SOURCE_MANIFEST]['as_of'] = '2026-10-27'
        result = self.run_daily(store)
        self.assertEqual(result['outcome'], 'SOURCE_PENDING')
        self.assertEqual(store.values[daily.PREFIX + 'manifest.json'], before)

    def test_newer_publication_and_live_lease_are_not_overwritten(self):
        store = FakeStore()
        store.seed(daily.PREFIX + 'manifest.json', {'as_of': '2026-10-29'})
        with self.assertRaisesRegex(ValueError, 'ROLL_BACK'):
            self.run_daily(store)
        store = FakeStore()
        store.seed(daily.PREFIX + 'lease.json', {'owner': 'other', 'expires': 10**15})
        before = deepcopy(store.values)
        with self.assertRaisesRegex(RuntimeError, 'WRITER_BUSY'):
            self.run_daily(store)
        self.assertEqual(store.values, before)

    def test_recorded_day_controls_first_effective_session(self):
        self.assertEqual(daily.next_effective_session('2026-10-30', '2026-11-02T06:00:00+00:00'), '2026-11-03')
        self.assertEqual(daily.next_effective_session('2026-10-30', '2026-10-30T10:00:00+00:00'), '2026-11-02')

    def test_changed_input_reuses_prior_day_not_same_day_result(self):
        store = FakeStore()
        self.run_daily(store)
        before = deepcopy(store.values[daily.PREFIX + 'manifest.json'])
        changed = deepcopy(store.values[daily.SOURCE_MANIFEST])
        changed['generated_at'] = 'fictional_correction'
        store.seed(daily.SOURCE_MANIFEST, changed)
        result = self.run_daily(store)
        self.assertNotEqual(result['dataset_id'], before['dataset_id'])
        self.assertIsNone(store.values[daily.PREFIX + 'manifest.json']['previous_dataset_id'])

    def test_transport_failure_is_not_frozen_as_an_immutable_unknown(self):
        from botocore.exceptions import EndpointConnectionError
        store = FakeStore()
        original = store.get
        fail = [True]

        def get(key, **kwargs):
            if key.endswith('/000002.json') and key.startswith(daily.SOURCE_PREFIX) and fail[0]:
                fail[0] = False
                raise EndpointConnectionError(endpoint_url='https://fictional.invalid')
            return original(key, **kwargs)

        store.get = get
        with self.assertRaisesRegex(RuntimeError, 'SOURCE_STORAGE_UNAVAILABLE'):
            self.run_daily(store)
        self.assertFalse(any(key.startswith(daily.PREFIX) and key.endswith('/000002.json') for key in store.values))
        self.assertEqual(self.run_daily(store)['outcome'], 'COMMITTED')

    def test_incomplete_batch_restarted_on_another_day_gets_new_availability(self):
        store = FakeStore()
        original = store.put

        def interrupted(key, value, **kwargs):
            if key == daily.PREFIX + 'manifest.json':
                raise RuntimeError('FICTIONAL_INTERRUPTED')
            return original(key, value, **kwargs)

        store.put = interrupted
        with patch.object(daily, 'now', return_value='2026-10-28T08:00:00+00:00'):
            with self.assertRaisesRegex(RuntimeError, 'FICTIONAL_INTERRUPTED'):
                self.run_daily(store)
        old_batches = {key for key in store.values if key.startswith(daily.PREFIX) and key.endswith('/batch.json')}
        store.put = original
        with patch.object(daily, 'now', return_value='2026-11-02T06:00:00+00:00'):
            result = self.run_daily(store)
        key = daily.PREFIX + result['dataset_id'] + '/batch.json'
        self.assertNotIn(key, old_batches)
        self.assertEqual(store.values[key]['recorded_at'], '2026-11-02T06:00:00+00:00')
        self.assertEqual(store.values[key]['effective_from'], '2026-11-03')


if __name__ == '__main__':
    unittest.main()
