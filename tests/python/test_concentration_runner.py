"""Saved-input orchestration with an in-memory store; no external calls."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from test_concentration import DATASET, fixture, manifest
from backend.chips import concentration_runner as runner


class Store:
    def __init__(self, total=2):
        self.objects = {runner.MANIFEST: manifest(total)}
        self.reads = []; self.writes = []
        rows = []
        for i in range(total):
            code = str(999990 + i); frame, raw = fixture(code)
            self.objects[f'data/chips/daily/{DATASET}/{code}.json'] = frame
            rows.append({'security_id': code, 'name': 'INVENTED', 'chip_status': 'READY'})
        self.objects[f'data/chips/daily/{DATASET}/screening.json'] = {
            'schema': 'stock.chips.market.screen.v1', 'dataset_id': DATASET, 'as_of': '2026-10-30', 'rows': rows}
    def get(self, key):
        self.reads.append(key); return deepcopy(self.objects.get(key))
    def create(self, key, value):
        if key in self.objects and self.objects[key] != value: raise ValueError('CONCENTRATION_IMMUTABLE_OBJECT_CONFLICT')
        if key not in self.objects: self.writes.append(key); self.objects[key] = deepcopy(value)
    def archive(self, frame): raise AssertionError('Unexpected source/archive call')


class ConcentrationRunner(unittest.TestCase):
    def test_draft_is_complete_but_final_publish_requires_explicit_flag(self):
        store = Store(1); before = deepcopy(store.objects)
        result = runner.run(store, workers=1, progress=lambda _: None)
        self.assertEqual(result['outcome'], 'DRAFT_COMPLETE')
        self.assertTrue(all(k.startswith('collector/chips/concentration/') for k in store.writes))
        self.assertTrue(all(store.objects[k] == value for k, value in before.items()))
        final = runner.run(store, workers=1, publish=True, progress=lambda _: None)
        self.assertEqual(final['outcome'], 'COMMITTED')
        self.assertEqual(runner.run(store, workers=1, publish=True)['outcome'], 'ALREADY_PRESENT')
        self.assertEqual(len([k for k in store.writes if k.startswith('data/')]), 1)

    def test_budget_resume_reuses_completed_chunks(self):
        store = Store(2); now = [0]; create = store.create
        def save(key, value):
            create(key, value)
            if key.startswith('collector/'):
                now[0] = 1200
        store.create = save
        with patch.object(runner, 'CHUNK_SIZE', 1):
            first = runner.run(store, workers=1, clock=lambda: now[0], progress=lambda _: None)
            self.assertEqual(first['outcome'], 'CHECKPOINT_SAVED'); self.assertEqual(first['processed'], 1)
            store.create = create
            second = runner.run(store, workers=1, publish=True, progress=lambda _: None)
        self.assertEqual(second['processed'], 2)
        self.assertEqual(store.reads.count(f'data/chips/daily/{DATASET}/999990.json'), 1)

    def test_missing_declared_payload_stops_and_never_publishes_empty_success(self):
        store = Store(1); del store.objects[f'data/chips/daily/{DATASET}/999990.json']
        with self.assertRaisesRegex(ValueError, 'REFERENCED_PAYLOAD_MISSING'):
            runner.run(store, workers=1, publish=True, progress=lambda _: None)
        self.assertEqual(store.writes, [])

    def test_corrupt_checkpoint_and_conflicting_final_are_refused(self):
        store = Store(1); runner.run(store, workers=1, progress=lambda _: None)
        checkpoint = store.writes[0]; store.objects[checkpoint]['results'][0]['row']['groups']['3'] = 'BOGUS'
        with self.assertRaisesRegex(ValueError, 'CHECKPOINT_DECISION'): runner.run(store, workers=1, publish=True)
        store = Store(1); store.objects[f'data/chips/daily/{DATASET}/concentration.json'] = {'input_fingerprint': 'WRONG'}
        with self.assertRaisesRegex(ValueError, 'CONFLICT'): runner.run(store, workers=1, publish=True)

    def test_source_change_before_final_keeps_checkpoints_without_committing(self):
        store = Store(1); reads = 0; original = store.get
        def read(key):
            nonlocal reads
            value = original(key)
            if key == runner.MANIFEST:
                reads += 1
                if reads > 1: value['generated_at'] = 'CHANGED'
            return value
        store.get = read
        with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED'):
            runner.run(store, workers=1, publish=True, progress=lambda _: None)
        self.assertTrue(store.writes)
        self.assertFalse(any(k.startswith('data/') for k in store.writes))

    def test_deadline_does_not_queue_the_rest_of_a_64_stock_block(self):
        store = Store(3); now = [0]; original = store.get
        def read(key):
            result = original(key)
            if key.endswith('/999990.json'):
                now[0] = 1200
            return result
        store.get = read
        result = runner.run(store, workers=1, publish=True, clock=lambda: now[0], progress=lambda _: None)
        self.assertEqual(result['outcome'], 'CHECKPOINT_SAVED')
        self.assertEqual(result['publication_state'], 'not_requested')
        self.assertFalse(any(k.endswith('/999991.json') or k.endswith('/999992.json') for k in store.reads))
        self.assertEqual(store.writes, [])

    def test_lost_final_put_response_is_unknown_and_resume_reads_before_writing(self):
        store = Store(1); original = store.create; puts = []
        def create(key, value):
            original(key, value)
            if key.endswith('/concentration.json'):
                puts.append(key)
                raise TimeoutError('Simulated lost response after commit')
        store.create = create
        with self.assertRaises(runner.PublicationUncertain) as caught:
            runner.run(store, workers=1, publish=True, progress=lambda _: None)
        self.assertEqual(caught.exception.key, f'data/chips/daily/{DATASET}/concentration.json')
        self.assertEqual(len(caught.exception.fingerprint), 64)
        recovered = runner.run(store, workers=1, publish=True)
        self.assertEqual(recovered['outcome'], 'ALREADY_PRESENT')
        self.assertEqual(recovered['publication_state'], 'verified')
        self.assertEqual(len(puts), 1)


if __name__ == '__main__': unittest.main()
