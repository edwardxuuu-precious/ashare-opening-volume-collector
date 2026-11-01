"""V2 recovery and lineage with invented local storage."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from test_concentration import DATASET
from test_concentration_runner import Store
from backend.chips import concentration_v2_runner as runner
from backend.chips import concentration_runner as legacy
from backend.chips.concentration import decisions, concentration_document
from backend.chips.concentration_v2 import supported_sessions


def constant_recent(payload):
    frame, _, _ = payload
    values = [20.0] * 21
    return {'security_id': frame['security_id'], 'name': frame['name'], 'close_cny': '10', 'values': values,
            'point_reasons': {}, 'short_history_days': 0, **decisions(values, {})}


class Runner(unittest.TestCase):
    def setUp(self):
        self.constant = patch.object(runner, 'calculate_recent', side_effect=constant_recent)
        self.points = patch('backend.chips.concentration_v2.EndpointSource.point', return_value=(20.0, None))
        self.constant.start(); self.points.start()
        self.addCleanup(self.constant.stop); self.addCleanup(self.points.stop)

    def run_local(self, store, **kwargs):
        return runner.run(store, workers=1, progress=lambda _: None, **kwargs)

    def test_complete_draft_publish_and_read_first_recovery(self):
        store = Store(1)
        result = self.run_local(store)
        self.assertEqual(result['outcome'], 'DRAFT_COMPLETE')
        self.assertEqual(result['groups']['LONG']['PASS'], 1)
        traces = [v for k, v in store.objects.items() if '/traces/' in k]
        self.assertGreater(len(traces), 1)
        final = traces[-1]['state']['row']
        self.assertEqual(final['streak_days'], 197)
        self.assertEqual(len(final['streak_values']), 198)
        create = store.create; puts = []
        def uncertain(key, value):
            create(key, value)
            if key.endswith('/concentration-v2.json'):
                puts.append(key); raise TimeoutError('lost final response')
        store.create = uncertain
        with self.assertRaises(runner.PublicationUncertain) as caught: self.run_local(store, publish=True)
        recovered = self.run_local(store, publish=True)
        self.assertEqual(recovered['outcome'], 'ALREADY_PRESENT')
        self.assertEqual(caught.exception.key, recovered['final_key'])
        self.assertEqual(len(puts), 1)

    def test_segment_resume_and_tamper_stop(self):
        store = Store(1); now = [0]; create = store.create
        def stop_after_segment(key, value):
            create(key, value)
            if '/traces/' in key: now[0] = 1200
        store.create = stop_after_segment
        result = self.run_local(store, clock=lambda: now[0])
        self.assertEqual(result['outcome'], 'CHECKPOINT_SAVED')
        saved = next(k for k in store.objects if '/traces/' in k)
        self.assertFalse(store.objects[saved]['state']['complete'])
        store.create = create
        result = self.run_local(store)
        self.assertEqual(result['outcome'], 'DRAFT_COMPLETE')
        store.objects[saved]['parent_sha256'] = 'corrupt'
        with self.assertRaisesRegex(ValueError, 'BINDING_CONFLICT'): self.run_local(store, publish=True)
        self.assertNotIn(f'data/chips/daily/{DATASET}/concentration-v2.json', store.objects)

    def test_changed_manifest_and_missing_frame_never_publish(self):
        store = Store(1); del store.objects[f'data/chips/daily/{DATASET}/999990.json']
        with self.assertRaisesRegex(ValueError, 'PAYLOAD_MISSING'): self.run_local(store, publish=True)
        store = Store(1); read = store.get; count = [0]
        def drift(key):
            value = read(key)
            if key == runner.MANIFEST:
                count[0] += 1
                if count[0] > 1: value['generated_at'] = 'changed'
            return value
        store.get = drift
        with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED'): self.run_local(store, publish=True)

    def legacy_store(self):
        store = Store(1); manifest = store.objects[runner.MANIFEST]
        frame = store.objects[f'data/chips/daily/{DATASET}/999990.json']
        days = supported_sessions(manifest['as_of'])[-21:]
        row = constant_recent((frame, days, None))
        fp = legacy.digest({'manifest': manifest, 'members': ['999990'], 'rule': legacy.RULE,
                            'closure': legacy.source_closure(), 'sessions': days})
        store.objects[f'data/chips/daily/{DATASET}/concentration.json'] = concentration_document(manifest, [row], input_fingerprint=fp)
        key = f'collector/chips/concentration/{DATASET}/{fp}/0000.json'
        store.objects[key] = {'schema': 'stock.chips.concentration.chunk.v1', 'input_fingerprint': fp, 'index': 0,
                              'results': [{'row': deepcopy(row), 'frame_sha256': legacy.digest(frame)}]}
        return store, key

    def test_legacy_rows_discard_strict_groups_and_require_frame_hash(self):
        store, key = self.legacy_store()
        result = self.run_local(store)
        self.assertEqual(result['groups']['20']['PASS'], 1)
        store.objects[key]['results'][0]['frame_sha256'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'FRAME_HASH_CONFLICT'): self.run_local(store)

    def test_legacy_chunk_rows_and_summary_are_exact(self):
        store, key = self.legacy_store()
        store.objects[key]['results'][0]['row']['name'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'CHUNK_ROW_CONFLICT'): self.run_local(store)
        store, key = self.legacy_store(); del store.objects[key]
        with self.assertRaisesRegex(ValueError, 'CHECKPOINT_IDENTITY'): self.run_local(store)

    def test_budget_does_not_admit_unbounded_work(self):
        store = Store(3); now = [0]; original = store.get
        def read(key):
            result = original(key)
            if key == runner.MANIFEST: now[0] = 1200
            return result
        store.get = read
        self.assertEqual(self.run_local(store, clock=lambda: now[0])['outcome'], 'CHECKPOINT_SAVED')
        self.assertFalse(any(k.endswith('/999990.json') for k in store.reads))
        self.assertEqual(store.writes, [])


if __name__ == '__main__': unittest.main()
