import _bootstrap
from copy import deepcopy
import time
import unittest

from backend.chips.calendar import sessions
from backend.golden_projection import daily, index_sources, model
from test_trend_volume_daily import FakeStore, AS_OF


def payload(spec, as_of=AS_OF):
    days = sessions('2026-01-01', as_of)[-60:]
    return {'symbol': spec.source_code, 'fields': list(index_sources.FIELDS),
            'start_date': days[0], 'end_date': as_of,
            'rows': [[day, spec.source_code, '100.10', '100.50', '99.50', '100.00',
                      '100000', '10000000'] for day in days]}


class FictionalIndices:
    def __init__(self, calls, failures=()):
        self.calls, self.failures, self.query_count = calls, failures, 0

    def fetch(self, spec, as_of, deadline):
        self.calls.append(spec.code)
        self.query_count += 1
        if spec.code in self.failures:
            raise ConnectionError('FICTIONAL_INDEX_UNAVAILABLE')
        return payload(spec, as_of)


class IndexInputTests(unittest.TestCase):
    def prepare(self, store, calls, failures=()):
        return index_sources.prepare_indices(store, AS_OF, deadline=time.monotonic() + 20,
                                              source_factory=lambda: FictionalIndices(calls, failures))

    def test_two_exact_indices_have_native_frames_and_verified_raw_cache(self):
        store, calls = FakeStore(), []
        snapshot = self.prepare(store, calls)
        self.assertEqual(calls, ['sh000001', 'sz399006'])
        self.assertEqual(snapshot.provider_queries, 2)
        for item in snapshot.inputs:
            self.assertIsInstance(item, index_sources.ReadyIndex)
            self.assertEqual(item.frame['schema'], index_sources.FRAME_SCHEMA)
            self.assertEqual(item.frame['dataset_id'], snapshot.dataset_id)
            self.assertNotIn('chips', item.key)
            source = item.frame['source']
            self.assertEqual(source['price_unit'], 'INDEX_POINTS')
            self.assertEqual(source['volume_unit'], 'SHARES')
            self.assertEqual(index_sources.digest(store.values[source['raw_key']]), source['raw_sha256'])
            parsed = index_sources.series_from_index_frame(item.frame, code=item.spec.code, as_of=AS_OF,
                                                            source_dataset_id=snapshot.dataset_id, source_sha256=item.sha256)
            self.assertEqual(len(parsed.bars), 60)
            self.assertEqual(str(parsed.bars[-1].volume), '100000')
        calls.clear()
        repeat = self.prepare(store, calls)
        self.assertEqual(calls, [])
        self.assertEqual(repeat.dataset_id, snapshot.dataset_id)
        self.assertEqual(repeat.provider_queries, 0)

    def test_one_missing_index_stays_unknown_and_recovers_without_refetching_success(self):
        store, calls = FakeStore(), []
        first = self.prepare(store, calls, ('sz399006',))
        self.assertIsInstance(first.inputs[1], index_sources.MissingIndex)
        self.assertNotIn(index_sources.PREFIX + 'index-inputs/' + AS_OF + '/sz399006.json', store.values)
        calls.clear()
        recovered = self.prepare(store, calls)
        self.assertEqual(calls, ['sz399006'])
        self.assertNotEqual(first.fingerprint, recovered.fingerprint)
        self.assertTrue(all(isinstance(item, index_sources.ReadyIndex) for item in recovered.inputs))

    def test_timestamp_only_changes_do_not_create_new_semantic_batch(self):
        store, calls = FakeStore(), []
        first = self.prepare(store, calls)
        for spec in index_sources.INDEX_SPECS:
            key = index_sources.PREFIX + 'index-inputs/' + AS_OF + '/' + spec.code + '.json'
            store.values[key]['source']['ingested_at'] = '2026-11-03T12:00:00+00:00'
        repeat = self.prepare(store, calls)
        self.assertEqual(first.fingerprint, repeat.fingerprint)
        self.assertEqual(first.inputs, repeat.inputs)

    def test_raw_or_normalized_cache_corruption_stops_instead_of_relabelling_it(self):
        store, calls = FakeStore(), []
        first = self.prepare(store, calls)
        raw_key = first.inputs[0].frame['source']['raw_key']
        store.values[raw_key]['rows'][-1][5] = '100.01'
        with self.assertRaisesRegex(RuntimeError, 'INDEX_SUCCESS_CACHE_INVALID'):
            self.prepare(store, calls)

    def test_native_boundary_rejects_bank_collision_wrong_units_gaps_and_stale_days(self):
        spec = index_sources.INDEX_SPECS[0]
        frame = index_sources.frame_from_sdk(payload(spec), spec, AS_OF, '2026-10-28T08:00:00+00:00')
        frame['dataset_id'] = 'a' * 16
        for change in ('bank', 'units', 'gap', 'as_of'):
            broken = deepcopy(frame)
            if change == 'bank':
                broken['code'] = '000001'
            elif change == 'units':
                broken['source']['volume_unit'] = 'HAND'
            elif change == 'gap':
                broken['history'].pop(10)
            else:
                broken['as_of'] = '2026-10-27'
            with self.assertRaises(ValueError):
                index_sources.series_from_index_frame(broken, code=spec.code, as_of=AS_OF,
                                                        source_dataset_id='a' * 16, source_sha256='b' * 64)
        stale = payload(spec)
        stale['rows'].pop()
        with self.assertRaises(ValueError):
            index_sources.frame_from_sdk(stale, spec, AS_OF, '2026-10-28T08:00:00+00:00')

    def test_exhausted_acquisition_budget_does_not_open_a_source(self):
        def forbidden():
            self.fail('Expired acquisition opened provider')
        snapshot = index_sources.prepare_indices(FakeStore(), AS_OF, deadline=time.monotonic() - 1,
                                                  source_factory=forbidden)
        self.assertEqual(snapshot.provider_queries, 0)
        self.assertTrue(all(isinstance(item, index_sources.MissingIndex) for item in snapshot.inputs))


class IndexProjectionTests(unittest.TestCase):
    def run_daily(self, store, calls, failures=()):
        return daily.run(store, as_of=AS_OF, max_seconds=120, workers=2, progress=None,
                         index_source_factory=lambda: FictionalIndices(calls, failures))

    def test_full_publication_binds_two_index_sources_and_preserves_stock_input(self):
        store, calls = FakeStore(), []
        result = self.run_daily(store, calls)
        manifest = store.values[daily.PREFIX + 'manifest.json']
        self.assertEqual((manifest['stock_total'], manifest['index_total'], manifest['universe_total']), (4, 2, 6))
        self.assertEqual(manifest['index_symbols'], list(index_sources.INDEX_SYMBOLS))
        self.assertNotEqual(manifest['index_source_dataset_id'], manifest['source_dataset_id'])
        for spec in index_sources.INDEX_SPECS:
            detail = store.values[daily.PREFIX + result['dataset_id'] + '/' + spec.code + '.json']
            self.assertEqual(detail['source_dataset_id'], manifest['index_source_dataset_id'])
            self.assertEqual(detail['row']['source_status'], 'KNOWN')
            self.assertEqual(detail['source']['price_unit'], 'INDEX_POINTS')
            self.assertEqual(detail['source']['volume_unit'], 'SHARES')
            self.assertTrue(detail['source']['key'].endswith('/' + spec.code + '.json'))
            self.assertNotIn('chips', detail['source']['key'])
        stock = store.values[daily.PREFIX + result['dataset_id'] + '/000001.json']
        self.assertEqual(stock['source_dataset_id'], manifest['source_dataset_id'])
        self.assertTrue(stock['source']['key'].startswith(daily.SOURCE_PREFIX))
        calls.clear()
        repeated = self.run_daily(store, calls)
        self.assertEqual(repeated['outcome'], 'ALREADY_COMMITTED')
        self.assertEqual(calls, [])

    def test_missing_then_recovered_input_changes_same_day_publication_identity(self):
        store, calls = FakeStore(), []
        first = self.run_daily(store, calls, ('sz399006',))
        missing = store.values[daily.PREFIX + first['dataset_id'] + '/sz399006.json']
        self.assertEqual(missing['row']['source_status'], 'UNKNOWN')
        self.assertIsNone(missing['source']['key'])
        recovered = self.run_daily(store, calls)
        self.assertNotEqual(first['dataset_id'], recovered['dataset_id'])
        current = store.values[daily.PREFIX + recovered['dataset_id'] + '/sz399006.json']
        self.assertEqual(current['row']['source_status'], 'KNOWN')

    def test_first_index_may_have_no_prior_but_declared_missing_prior_is_not_hidden(self):
        for previously_declared in (False, True):
            with self.subTest(previously_declared=previously_declared):
                store, calls = FakeStore(), []
                old = {'schema': 'stock.golden-projection.daily.manifest.v1',
                       'as_of': '2026-10-27', 'dataset_id': 'f' * 16,
                       'generated_at': '2026-10-27T08:00:00+00:00'}
                if previously_declared:
                    old['index_symbols'] = list(index_sources.INDEX_SYMBOLS)
                store.seed(daily.PREFIX + 'manifest.json', old)
                store.seed(daily.PREFIX + old['dataset_id'] + '/manifest.json', old)
                result = self.run_daily(store, calls)
                for code in index_sources.INDEX_SYMBOLS:
                    detail = store.values[daily.PREFIX + result['dataset_id'] + '/' + code + '.json']
                    self.assertEqual(detail['row']['source_status'], 'UNKNOWN' if previously_declared else 'KNOWN')
                    if previously_declared:
                        self.assertEqual(detail['reason_code'], 'PREVIOUS_PROJECTION_FRAME_MISSING')

    def test_source_storage_failure_keeps_previous_published_manifest(self):
        store, calls = FakeStore(), []
        self.run_daily(store, calls)
        before = deepcopy(store.values[daily.PREFIX + 'manifest.json'])
        original = store.get
        def failing(key, **kwargs):
            if '/index-inputs/' in key:
                raise RuntimeError('FICTIONAL_STORAGE_DOWN')
            return original(key, **kwargs)
        store.get = failing
        with self.assertRaisesRegex(RuntimeError, 'FICTIONAL_STORAGE_DOWN'):
            self.run_daily(store, calls)
        self.assertEqual(store.values[daily.PREFIX + 'manifest.json'], before)

    def test_native_index_uses_same_selection_and_next_day_pre_open_lock(self):
        from test_golden_projection_model import candles, detail as stock_detail
        rows = candles()
        spec = index_sources.INDEX_SPECS[0]
        def index_detail(history, source_id, previous=None):
            sdk = {'symbol': spec.source_code, 'fields': list(index_sources.FIELDS),
                   'start_date': history[0]['date'], 'end_date': history[-1]['date'],
                   'rows': [[row['date'], spec.source_code, row['open'], row['high'], row['low'],
                             row['close'], row['volume_shares'], '10000000'] for row in history]}
            native = index_sources.frame_from_sdk(sdk, spec, history[-1]['date'], history[-1]['date'] + 'T08:00:00+00:00')
            native['dataset_id'] = source_id
            return model.classify_frame(native, code=spec.code, name=spec.name, as_of=history[-1]['date'],
                                        dataset_id='c' * 16, source_dataset_id=source_id,
                                        source_sha256=index_sources.digest(native), previous=previous,
                                        recorded_at=history[-1]['date'] + 'T08:01:00+00:00',
                                        effective_from='2026-11-02' if previous is None else '2026-11-03',
                                        source_key=index_sources.PREFIX + 'index-snapshots/' + source_id + '/' + spec.code + '.json',
                                        source_metadata=native['source'])
        first = index_detail(rows, 'a' * 16)
        equity = stock_detail(rows)
        for key in ('direction', 'H', 'M', 'L', 'category', 'drawing_state'):
            self.assertEqual(first['row'][key], equity['row'][key])
        self.assertEqual(first['row']['drawing_state'], 'NEW_REFERENCE')
        later = rows + [dict(rows[-1], date='2026-11-02')]
        second = index_detail(later, 'b' * 16, first)
        self.assertEqual(second['row']['drawing_state'], 'CURRENT')
        self.assertEqual(second['row']['basis'], 'PREVIOUS_LOCK')
        self.assertEqual(second['structure']['recorded_at'], first['structure']['recorded_at'])
        self.assertEqual(second['structure']['effective_from'], '2026-11-02')


if __name__ == '__main__':
    unittest.main()
