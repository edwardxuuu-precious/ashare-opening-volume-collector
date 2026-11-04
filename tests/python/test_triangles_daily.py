import _bootstrap
import json
import unittest
from unittest.mock import patch

from backend.chips.calendar import sessions
from backend.triangles import daily

AS_OF = '2026-11-03'


class MemoryStore:
    def __init__(self):
        self.values, self.versions, self.writes = {}, {}, []
        self.source_bytes = 0
        days = sessions('2026-10-01', AS_OF)
        entries = []
        for day in days:
            generation = day + 'T17:00:00+08:00'
            entries.append({'date': day, 'file': day + '.json', 'generatedAt': generation, 'scope': 'full',
                            'priceDataComplete': True, 'priceMissing': 0, 'status': 'complete'})
            self.seed('data/' + day + '.json', {'date': day, 'generatedAt': generation, 'scope': 'full',
                                              'priceDataComplete': True, 'pendingCount': 0, 'rows': []})
        self.seed('data/manifest.json', {'scope': 'full', 'tradingDates': days, 'dates': entries})
        self.seed('data/stocks.json', {'rows': [{'code': '000001', 'name': '测试股票'}]})

    def seed(self, key, value):
        self.values[key] = daily.encoded(value)
        self.versions[key] = self.versions.get(key, 0) + 1

    def head(self, key):
        if key not in self.values:
            return None
        return {'etag': str(self.versions[key]), 'versionId': str(self.versions[key]), 'bytes': len(self.values[key])}

    def get(self, key, *, limit=daily.MAX_META, expected=None, source=False):
        if key not in self.values:
            return None, None
        head = self.head(key)
        if expected is not None and expected != head:
            raise daily.SourcePending('SOURCE_VERSION_CHANGED')
        raw = self.values[key]
        if len(raw) > limit:
            raise ValueError('OBJECT_ENCODED_BOUND')
        if source:
            self.source_bytes += len(raw)
        return raw, head

    def put(self, key, raw, *, match=None, new=False, compressed=False):
        if new and key in self.values or match is not None and self.head(key)['etag'] != match:
            raise RuntimeError('CAS_CONFLICT')
        self.values[key] = raw
        self.versions[key] = self.versions.get(key, 0) + 1
        self.writes.append(key)
        return self.head(key)['etag']

    def put_immutable(self, key, raw):
        if key in self.values and self.values[key] != raw:
            raise RuntimeError('IMMUTABLE_CONFLICT')
        if key not in self.values:
            self.put(key, raw, new=True)


def builder(input_dir, output_dir, *, dataset_id, as_of, source, timeout):
    output_dir.mkdir()
    screen = daily.encoded({'rows': [{'security': {'code': '000001'}}]})
    output_dir.joinpath('screening.json').write_bytes(screen)
    return {'schema': 'stock.triangles.daily.manifest.v1', 'dataset_id': dataset_id,
            'as_of': as_of, 'generated_at': as_of + 'T17:00:00+08:00', 'universe_total': 1,
            'detail_sessions': json.loads(input_dir.joinpath('manifest.json').read_bytes())['tradingDates'],
            'source': source, 'screening': {'file': 'screening.json', 'sha256': daily.digest(screen), 'bytes': len(screen)},
            'bundles': []}


class TrianglePublishingTests(unittest.TestCase):
    def run_daily(self, store, **kwargs):
        with patch.object(daily, 'latest_completed_session', return_value=AS_OF):
            return daily.run(store, as_of=AS_OF, builder=builder, identity={'rule_sha256': 'a' * 64}, progress=None, **kwargs)

    def test_complete_screen_is_read_back_before_manifest_and_repeated_input_skips(self):
        store = MemoryStore()
        first = self.run_daily(store)
        self.assertEqual(first['outcome'], 'COMMITTED')
        manifest_key = daily.PREFIX + 'manifest.json'
        result = json.loads(store.values[manifest_key])
        screen_key = daily.PREFIX + result['dataset_id'] + '/screening.json'
        self.assertLess(store.writes.index(screen_key), store.writes.index(manifest_key))
        count = store.writes.count(manifest_key)
        second = self.run_daily(store)
        self.assertEqual(second['outcome'], 'ALREADY_COMMITTED')
        self.assertEqual(store.writes.count(manifest_key), count)

    def test_uncommitted_raw_generation_preserves_previous_publication(self):
        store = MemoryStore()
        self.run_daily(store)
        before = store.values[daily.PREFIX + 'manifest.json']
        value = json.loads(store.values['data/' + AS_OF + '.json'])
        value['generatedAt'] = AS_OF + 'T18:00:00+08:00'
        store.seed('data/' + AS_OF + '.json', value)
        result = self.run_daily(store)
        self.assertEqual(result['outcome'], 'SOURCE_PENDING')
        self.assertEqual(store.values[daily.PREFIX + 'manifest.json'], before)

    def test_engine_failure_does_not_publish_partial_stock_list(self):
        store = MemoryStore()
        with patch.object(daily, 'latest_completed_session', return_value=AS_OF):
            with self.assertRaisesRegex(RuntimeError, 'FICTIONAL_ENGINE_FAILURE'):
                daily.run(store, as_of=AS_OF, identity={}, progress=None,
                          builder=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError('FICTIONAL_ENGINE_FAILURE')))
        self.assertNotIn(daily.PREFIX + 'manifest.json', store.values)
        self.assertEqual(json.loads(store.values[daily.PREFIX + 'status.json'])['state'], 'FAILED')

    def test_source_change_during_calculation_refuses_commit(self):
        store = MemoryStore()
        def changing(*args, **kwargs):
            publication = builder(*args, **kwargs)
            source = json.loads(store.values['data/manifest.json'])
            source['generatedAt'] = 'changed'
            store.seed('data/manifest.json', source)
            return publication
        with patch.object(daily, 'latest_completed_session', return_value=AS_OF):
            result = daily.run(store, as_of=AS_OF, builder=changing, identity={}, progress=None)
        self.assertEqual(result['outcome'], 'SOURCE_PENDING')
        self.assertNotIn(daily.PREFIX + 'manifest.json', store.values)

    def test_missing_calendar_or_incomplete_latest_is_not_empty_success(self):
        for field, value, error in [('tradingDates', [], ValueError), ('dates', [], None)]:
            store = MemoryStore()
            manifest = json.loads(store.values['data/manifest.json'])
            manifest[field] = value
            store.seed('data/manifest.json', manifest)
            if error:
                with self.assertRaises(error):
                    self.run_daily(store)
            else:
                self.assertEqual(self.run_daily(store)['outcome'], 'SOURCE_PENDING')
            self.assertNotIn(daily.PREFIX + 'manifest.json', store.values)

    def test_completed_date_guard_and_lease_conflict(self):
        store = MemoryStore()
        store.seed(daily.PREFIX + 'lease.json', {'owner': 'other', 'expires': 10 ** 15})
        with self.assertRaisesRegex(RuntimeError, 'WRITER_BUSY'):
            self.run_daily(store)
        with patch.object(daily, 'latest_completed_session', return_value='2026-11-02'):
            with self.assertRaisesRegex(ValueError, 'COMPLETED_OFFICIAL_SESSION'):
                daily.run(MemoryStore(), as_of=AS_OF)

    def test_later_committed_date_cannot_be_rolled_back(self):
        store = MemoryStore()
        store.seed(daily.PREFIX + 'manifest.json', {'as_of': '2026-11-04'})
        before = store.values[daily.PREFIX + 'manifest.json']
        with self.assertRaisesRegex(ValueError, 'REFUSE_TO_ROLL_BACK'):
            self.run_daily(store)
        self.assertEqual(store.values[daily.PREFIX + 'manifest.json'], before)
