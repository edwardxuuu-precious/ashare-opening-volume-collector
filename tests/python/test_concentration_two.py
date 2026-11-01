"""Two-group numeric and immutable checkpoint contracts."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_concentration_runner import Store
from test_concentration import DATASET, manifest as source_manifest
from backend.chips import concentration_two as engine
from backend.chips import concentration_two_runner as runner
from backend.chips import concentration_v2 as v2
from backend.chips.concentration_v2_runner import source_closure as v2_closure


class TwoGroups(unittest.TestCase):
    def run_local(self, store, **kwargs):
        with patch.object(engine.EndpointSource, 'point', return_value=(20.0, None)):
            return runner.run(store, workers=1, progress=lambda _: None, **kwargs)

    def test_segments_start_empty_and_complete_recent_before_stopping(self):
        store = Store(1); frame = next(v for k,v in store.objects.items() if k.endswith('/999990.json'))
        axis = engine.supported_sessions(frame['as_of'])
        state = None
        with patch.object(engine.EndpointSource, 'point', return_value=(20.0, None)):
            for size in (8, 16, 24):
                previous = state
                state = engine.segment(frame, axis, previous)
                engine.validate_state(state, previous, frame, axis)
                self.assertEqual(len(state['values']), size)
                self.assertEqual(state['row'] is None, size < 21)
        with patch.object(engine.EndpointSource, 'point', side_effect=lambda day: (1.0 if day == axis[-1] else 0.5, None)):
            state = None
            while state is None or not state['complete']:
                previous = state; state = engine.segment(frame, axis, previous)
                engine.validate_state(state, previous, frame, axis)
            self.assertEqual(len(state['values']), 21)
            self.assertEqual(state['row']['groups'], {'20': 'FAIL', 'LONG': 'FAIL'})

    def test_checkpoint_resume_parent_tamper_and_uncertain_final_read_first(self):
        store = Store(1); now = [0]; create = store.create
        def expire(key, value):
            create(key, value)
            if '/stocks/' in key: now[0] = 1200
        store.create = expire
        self.assertEqual(self.run_local(store, clock=lambda: now[0])['outcome'], 'CHECKPOINT_SAVED')
        key = next(k for k in store.objects if '/stocks/' in k)
        self.assertEqual(len(store.objects[key]['state']['values']), 8)
        store.create = create
        saved = deepcopy(store.objects[key]); store.objects[key]['parent_sha256'] = 'bad'
        with self.assertRaisesRegex(ValueError, 'BINDING_CONFLICT'): self.run_local(store)
        store.objects[key] = saved
        final_writes = []
        def uncertain(key, value):
            create(key, value)
            if key.endswith('/concentration-two-v1.json'):
                final_writes.append(key); raise TimeoutError('lost acknowledgment')
        store.create = uncertain
        with self.assertRaises(runner.PublicationUncertain): self.run_local(store, publish=True)
        with patch.object(engine.EndpointSource, 'point', side_effect=AssertionError('no replay')):
            result = runner.run(store, workers=1, publish=True)
        self.assertEqual(result['outcome'], 'ALREADY_PRESENT')
        self.assertEqual(len(final_writes), 1)
        self.assertEqual(set(store.objects[final_writes[0]]['groups']), {'20', 'LONG'})

    def test_missing_source_and_screen_drift_fail_closed(self):
        store = Store(1); key = next(k for k in store.objects if k.endswith('/999990.json')); del store.objects[key]
        with self.assertRaisesRegex(ValueError, 'PAYLOAD_MISSING'): self.run_local(store, publish=True)
        store = Store(1); read = store.get; calls = [0]
        def drift(key):
            value = read(key)
            if key.endswith('/screening.json'):
                calls[0] += 1
                if calls[0] > 1: value['rows'][0]['name'] = 'changed'
            return value
        store.get = drift
        with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED'): self.run_local(store, publish=True)

    def test_reuse_requires_pinned_source_and_hash(self):
        with self.assertRaisesRegex(ValueError, 'ONLY_PINNED'): self.run_local(Store(1), reuse_verified_v2=True)
        for workers in (0, 5):
            with self.assertRaisesRegex(ValueError, 'BOUNDED'): runner.run(Store(1), workers=workers)

    def test_pinned_numeric_projection_and_fingerprint_are_acquisition_independent(self):
        total = 5577
        manifest = source_manifest(total)
        axis = engine.supported_sessions(manifest['as_of'])
        members = [{'security_id': f'{i:06d}', 'name': 'SYNTHETIC', 'chip_status': 'READY'} for i in range(total)]
        rows = [{'security_id': member['security_id'], 'name': member['name'], 'close_cny': '10',
                 'values': [20.0] * 21, 'point_reasons': {}, 'short_history_days': 0,
                 'streak_days': 21, 'streak_start_session': axis[-22], 'streak_values': [20.0] * 22,
                 'streak_boundary': {'kind': 'UNAVAILABLE', 'session': axis[-23], 'reason': 'SOURCE_SESSION_MISSING'},
                 'groups': {key: 'PASS' for key in v2.GROUPS}} for member in members]
        lineage = {'kind': 'SYNTHETIC_TEST_FIXTURE'}
        old_fp = runner.digest({'manifest': manifest, 'members': [m['security_id'] for m in members],
                                'rule': v2.RULE, 'closure': v2_closure(), 'sessions': axis[-21:],
                                'streak_sessions': axis, 'lineage': lineage})
        old = v2.concentration_document(manifest, rows, input_fingerprint=old_fp, lineage=lineage)
        store = Store(1)
        store.objects[runner.MANIFEST] = manifest
        store.objects[f'data/chips/daily/{DATASET}/screening.json']['rows'] = members
        old_key = f'data/chips/daily/{DATASET}/concentration-v2.json'
        store.objects[old_key] = old
        with patch.multiple(runner, PINNED_DATASET=DATASET, PINNED_FP=old_fp, PINNED_SHA=runner.digest(old)), \
                patch.object(engine.EndpointSource, 'point', side_effect=AssertionError('No endpoint replay during verified reuse')):
            migrated = runner.run(store, workers=1, reuse_verified_v2=True, publish=True)
            recovered = runner.run(store, workers=1, publish=True)
            self.assertEqual(recovered['outcome'], 'ALREADY_PRESENT')
            self.assertEqual(migrated['input_fingerprint'], recovered['input_fingerprint'])
            doc = store.objects[migrated['final_key']]
            self.assertEqual(doc['groups'], {'20': {'PASS': total, 'FAIL': 0, 'UNKNOWN': 0},
                                             'LONG': {'PASS': total, 'FAIL': 0, 'UNKNOWN': 0}})
            for original, projected in zip(rows, doc['rows']):
                self.assertEqual({k:v for k,v in original.items() if k!='groups'},
                                 {k:v for k,v in projected.items() if k!='groups'})
            self.assertEqual(store.writes.count(migrated['final_key']), 1)
            with self.assertRaisesRegex(ValueError, 'LINEAGE_CONFLICT'):
                runner.validate_lineage({**doc['lineage'], 'summary_sha256':'bad'}, manifest)
            store.objects[old_key]['rows'][0]['name'] = 'TAMPERED'
            with self.assertRaisesRegex(ValueError, 'HASH_CONFLICT'):
                runner.run(store, workers=1, publish=True)


if __name__ == '__main__': unittest.main()
