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

    def seed_segments(self, total=3):
        store = Store(total)
        axis = engine.supported_sessions(store.objects[runner.MANIFEST]['as_of'])
        with patch.object(engine.EndpointSource, 'point', side_effect=lambda day: (21.0 if day == axis[-1] else 20.0, None)):
            self.assertEqual(runner.run(store, workers=1, progress=lambda _: None)['outcome'], 'DRAFT_COMPLETE')
        for key in list(store.objects):
            if '/completed/' in key:
                del store.objects[key]
        return store

    def test_slow_recovery_seals_progress_and_eventually_publishes_without_recomputation(self):
        store = self.seed_segments(); read = store.get; now = [0]
        def slow(key):
            if '/stocks/' in key:
                now[0] += 6
            return read(key)
        store.get = slow
        results = []
        with patch.object(runner, 'CHUNK_SIZE', 1), \
                patch.object(engine.EndpointSource, 'point', side_effect=AssertionError('Completed points must not be recomputed')):
            for _ in range(4):
                now[0] = 0
                result = runner.run(store, workers=1, max_minutes=1, clock=lambda: now[0], publish=True, progress=lambda _: None)
                results.append(result)
                if result['outcome'] == 'COMMITTED':
                    break
        self.assertEqual([result['sealed_processed'] for result in results], [1, 2, 3])
        self.assertEqual(results[-1]['outcome'], 'COMMITTED')
        self.assertEqual(results[-1]['processed'], 3)

    def test_sealed_chunks_restore_without_rereading_sources_or_segment_objects(self):
        store = Store(2); self.run_local(store)
        old_reads = len(store.reads)
        with patch.object(engine.EndpointSource, 'point', side_effect=AssertionError('No endpoint replay')):
            result = runner.run(store, workers=1, publish=True, progress=lambda _: None)
        self.assertEqual(result['outcome'], 'COMMITTED')
        reads = store.reads[old_reads:]
        self.assertFalse(any('/stocks/' in key or key.endswith(('/999990.json', '/999991.json')) for key in reads))
        self.assertTrue(any('/completed/' in key for key in reads))

    def test_completed_chunk_tampering_and_current_member_drift_are_rejected(self):
        store = Store(2); self.run_local(store)
        key = next(key for key in store.objects if '/completed/' in key)
        original = deepcopy(store.objects[key])
        def invalid_parent(chunk):
            entry = chunk['records'][0]['chain'][1]
            entry['node']['parent_sha256'] = '0' * 64
            entry['sha256'] = runner.digest(entry['node'])
        mutations = {
            'chunk_index': lambda chunk: chunk.update(index=1),
            'member_order': lambda chunk: chunk['records'].reverse(),
            'frame_hash': lambda chunk: chunk['records'][0]['binding'].update(frame_sha256='0' * 64),
            'parent_hash': invalid_parent,
            'node_hash': lambda chunk: chunk['records'][0]['chain'][0].update(sha256='0' * 64),
            'row': lambda chunk: chunk['records'][0]['row'].update(name='TAMPERED'),
            'frame_name': lambda chunk: chunk['records'][0]['frame'].update(name='TAMPERED'),
            'missing_frame_context': lambda chunk: chunk['records'][0]['frame'].update(snapshots=None),
            'bridge': lambda chunk: chunk.update(legacy_checkpoint={'input_fingerprint': '0' * 64}),
        }
        for name, mutate in mutations.items():
            with self.subTest(tamper=name):
                store.objects[key] = deepcopy(original); mutate(store.objects[key])
                with self.assertRaises(ValueError):
                    self.run_local(store, publish=True)
        store.objects[key] = original
        screening = store.objects[f'data/chips/daily/{DATASET}/screening.json']
        for field, value in (('name', 'CHANGED'), ('chip_status', 'MISSING')):
            with self.subTest(current_member=field):
                before = screening['rows'][0][field]
                screening['rows'][0][field] = value
                with self.assertRaisesRegex(ValueError, 'COMPLETED_STOCK_BINDING'):
                    self.run_local(store, publish=True)
                screening['rows'][0][field] = before

    def test_explicit_legacy_bridge_reuses_verified_nodes_without_rewriting_them(self):
        store = self.seed_segments(1)
        manifest = store.objects[runner.MANIFEST]
        screening = store.objects[f'data/chips/daily/{DATASET}/screening.json']
        members = runner.validate_source(manifest, screening)
        axis = engine.supported_sessions(manifest['as_of'])
        closure = runner.source_closure()
        legacy_sha = 'a' * 64
        previous_closure = {**closure, 'concentration_two_runner.py': legacy_sha}
        previous_fp = runner.expected_fingerprint(manifest, members, axis, previous_closure)
        source_nodes = sorted((key, value) for key, value in store.objects.items() if '/stocks/' in key)
        parent = None
        for key, node in source_nodes:
            del store.objects[key]
            node = {**node, 'input_fingerprint': previous_fp, 'parent_sha256': parent}
            legacy_key = runner.checkpoint_prefix(manifest, previous_fp) + '/stocks/999990/' + key.rsplit('/', 1)[1]
            store.objects[legacy_key] = node
            parent = runner.digest(node)
        preserved = deepcopy(store.objects)
        with patch.multiple(runner, LEGACY_SEGMENT_DATASET=DATASET, LEGACY_SEGMENT_DATE=manifest['as_of'],
                            LEGACY_SEGMENT_FP=previous_fp, LEGACY_RUNNER_SHA=legacy_sha), \
                patch.object(engine.EndpointSource, 'point', side_effect=AssertionError('No legacy endpoint replay')):
            result = runner.run(store, workers=1, publish=True, progress=lambda _: None)
            self.assertEqual(result['outcome'], 'COMMITTED')
            self.assertNotEqual(result['input_fingerprint'], previous_fp)
            self.assertEqual(result['reused_legacy_checkpoint_rows'], 1)
            self.assertEqual(result['legacy_checkpoint']['input_fingerprint'], previous_fp)
            chunk = next(value for key, value in store.objects.items() if '/completed/' in key)
            self.assertTrue(all(entry['node']['input_fingerprint'] == previous_fp for entry in chunk['records'][0]['chain']))
            changed = {**closure, 'concentration_two.py': 'f' * 64}
            self.assertIsNone(runner.legacy_checkpoint(manifest, members, axis, changed))
            self.assertIsNone(runner.legacy_checkpoint({**manifest, 'as_of': '2026-11-02'}, members, axis, closure))
            self.assertIsNone(runner.legacy_checkpoint(manifest, members + [{'security_id': '999999'}], axis, closure))
        self.assertTrue(all(store.objects[key] == value for key, value in preserved.items()))

    def test_completed_chunk_write_acknowledgment_loss_is_recovered_without_replay(self):
        store = Store(1); create = store.create; completed_writes = []
        def uncertain(key, value):
            create(key, value)
            if '/completed/' in key:
                completed_writes.append(key)
                raise TimeoutError('lost checkpoint acknowledgment')
        store.create = uncertain
        self.assertEqual(self.run_local(store, publish=True)['outcome'], 'CHECKPOINT_SAVED')
        store.create = create
        with patch.object(engine.EndpointSource, 'point', side_effect=AssertionError('No endpoint replay')):
            self.assertEqual(runner.run(store, workers=1, publish=True, progress=lambda _: None)['outcome'], 'COMMITTED')
        self.assertEqual(len(completed_writes), 1)

    def test_partial_legacy_chain_can_extend_with_new_fingerprint_nodes(self):
        store = self.seed_segments(1)
        manifest = store.objects[runner.MANIFEST]
        members = runner.validate_source(manifest, store.objects[f'data/chips/daily/{DATASET}/screening.json'])
        axis = engine.supported_sessions(manifest['as_of'])
        closure = runner.source_closure(); legacy_sha = 'a' * 64
        previous_fp = runner.expected_fingerprint(manifest, members, axis, {**closure, 'concentration_two_runner.py': legacy_sha})
        nodes = sorted((key, value) for key, value in store.objects.items() if '/stocks/' in key)
        parent = None
        for index, (key, node) in enumerate(nodes):
            del store.objects[key]
            if index >= 2:
                continue
            node = {**node, 'input_fingerprint': previous_fp, 'parent_sha256': parent}
            store.objects[runner.checkpoint_prefix(manifest, previous_fp) + f'/stocks/999990/{index:04d}.json'] = node
            parent = runner.digest(node)
        with patch.multiple(runner, LEGACY_SEGMENT_DATASET=DATASET, LEGACY_SEGMENT_DATE=manifest['as_of'],
                            LEGACY_SEGMENT_FP=previous_fp, LEGACY_RUNNER_SHA=legacy_sha), \
                patch.object(engine.EndpointSource, 'point', side_effect=lambda day: (21.0 if day == axis[-1] else 20.0, None)):
            result = runner.run(store, workers=1, publish=True, progress=lambda _: None)
        self.assertEqual(result['outcome'], 'COMMITTED')
        chunk = next(value for key, value in store.objects.items() if '/completed/' in key)
        origins = [entry['node']['input_fingerprint'] for entry in chunk['records'][0]['chain']]
        self.assertEqual(origins, [previous_fp, previous_fp, result['input_fingerprint']])

    def test_real_process_workers_produce_the_same_synthetic_document(self):
        serial = Store(2); concurrent = Store(2)
        first = runner.run(serial, workers=1, publish=True, progress=lambda _: None)
        second = runner.run(concurrent, workers=2, publish=True, progress=lambda _: None)
        self.assertEqual(first['outcome'], 'COMMITTED')
        self.assertEqual(second['outcome'], 'COMMITTED')
        self.assertEqual(serial.objects[first['final_key']], concurrent.objects[second['final_key']])

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
