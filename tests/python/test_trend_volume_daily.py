import _bootstrap
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import io
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from backend.chips.calendar import sessions
from backend.trend_volume import daily
from backend.trend_volume.model import (ORIGINAL_V3_SHA256, RULE_ID, PARAMETER_SHA256,
                                        classify_frame, encoded, digest)

AS_OF = '2026-10-28'
SOURCE = '0123456789abcdef'
DATASET = 'abcdef0123456789'


def frame(code='000001', mode='selected'):
    dates = sessions('2026-01-01', AS_OF)[-30:]
    history = []
    for index, day in enumerate(dates):
        close = 20 - index * .2
        history.append({'date': day, 'open': f'{close + .05:.2f}', 'high': f'{close + .2:.2f}',
                        'low': f'{close - .3:.2f}', 'close': f'{close:.2f}',
                        'volume_shares': '100' if index in (26, 27, 28) else '1000'})
    history[-1].update(open='14.50', high='15.20', low='14.30', close='15.12', volume_shares='300')
    if mode == 'watching':
        history[-1].update(open='14.40', high='14.70', low='14.30', close='14.50', volume_shares='100')
    elif mode == 'not_matched':
        for row in history:
            row.update(open='10', high='10.5', low='9.5', close='10', volume_shares='1000')
    elif mode == 'short':
        history = [dict(history[-2], open='10', high='10.2', low='9.8', close='10'),
                   dict(history[-1], open='10.1', high='10.6', low='10', close='10.5')]
    return {'schema': 'stock.chips.market.stock.v1', 'dataset_id': SOURCE, 'security_id': code,
            'name': 'Fictional ' + code, 'as_of': AS_OF, 'history': history,
            'source': {'provider': 'FICTIONAL_SAVED_INPUT'}, 'chip_status': 'MISSING'}


def classify(value, code='000001'):
    return classify_frame(value, code=code, name='Fictional ' + code, as_of=AS_OF, dataset_id=DATASET,
                          source_dataset_id=SOURCE, source_sha256=digest(encoded(value)))


class FakeStore:
    def __init__(self, modes=('selected', 'watching', 'not_matched', 'missing')):
        self.values, self.etags, self.writes = {}, {}, []
        self.lock = threading.RLock()
        self.source_reads = 0
        self.source_bytes = 0
        manifest = {'schema': 'stock.chips.market.manifest.v1', 'dataset_id': SOURCE, 'active_slot': SOURCE,
                    'as_of': AS_OF, 'universe_total': len(modes), 'scanned_count': len(modes),
                    'universe_coverage': 'SITE_CATALOG_UNVERIFIED'}
        rows = []
        for index, mode in enumerate(modes):
            code = f'{index + 1:06d}'
            rows.append({'security_id': code, 'name': 'Fictional ' + code, 'screen_status': 'FAIL'})
            if mode != 'missing':
                self.seed(daily.SOURCE_PREFIX + SOURCE + '/' + code + '.json', frame(code, mode))
        self.seed(daily.SOURCE_MANIFEST, manifest)
        self.seed(daily.SOURCE_PREFIX + SOURCE + '/screening.json',
                  {'schema': 'stock.chips.market.screen.v1', 'dataset_id': SOURCE, 'as_of': AS_OF, 'rows': rows})

    def seed(self, key, value):
        self.values[key] = deepcopy(value)
        self.etags[key] = 'version-1'

    def get(self, key, *, source=False, **_bounds):
        with self.lock:
            value = deepcopy(self.values.get(key))
            if source:
                self.source_reads += 1
                self.source_bytes += len(encoded(value)) if value is not None else 0
            return value, self.etags.get(key), digest(encoded(value)) if value is not None else None

    def put(self, key, value, *, match=None, new=False, compressed=False):
        with self.lock:
            if new and key in self.values or match is not None and self.etags.get(key) != match:
                raise RuntimeError('CAS_CONFLICT')
            self.values[key] = deepcopy(value)
            self.etags[key] = 'version-' + str(len(self.writes) + 2)
            self.writes.append(key)
            return self.etags[key]

    def put_immutable(self, key, value):
        with self.lock:
            if key in self.values:
                if self.values[key] != value:
                    raise RuntimeError('IMMUTABLE_CONFLICT')
                return self.etags[key]
            return self.put(key, value, new=True)


class DailyTrendTests(unittest.TestCase):
    def storage_with(self, response):
        value = object.__new__(daily.Storage)
        value.bucket = 'fictional-private-bucket'
        value.max_source_bytes = 1024
        value.source_reads = 0
        value.source_bytes = 0
        value.lock = threading.Lock()
        value.s3 = type('FakeS3', (), {'get_object': staticmethod(response)})()
        return value

    def test_storage_counts_missing_object_read_attempts(self):
        from botocore.exceptions import ClientError
        def missing(**_args):
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        store = self.storage_with(missing)
        self.assertEqual(store.get('fictional/source', source=True), (None, None, None))
        self.assertEqual(store.source_reads, 1)
        self.assertEqual(store.source_bytes, 0)

    def test_storage_bounds_encoded_decoded_and_total_source_bytes(self):
        raw = encoded({'fictional': 'x' * 100})
        compressed = gzip.compress(raw, mtime=0)
        store = self.storage_with(lambda **_args: {'Body': io.BytesIO(compressed), 'ETag': 'fictional-etag'})
        with self.assertRaisesRegex(ValueError, 'ENCODED_BOUND'):
            store.get('fictional/source', max_bytes=5, source=True)
        with self.assertRaisesRegex(ValueError, 'DECODED_BOUND'):
            store.get('fictional/source', max_decoded_bytes=5, source=True)
        store.max_source_bytes = 1
        with self.assertRaisesRegex(RuntimeError, 'TRANSFER_BUDGET'):
            store.get('fictional/source', source=True)
        self.assertEqual(store.source_reads, 3)

    def test_v3_source_is_unchanged_except_its_type_import(self):
        source = Path(daily.__file__).with_name('frozen_v3.py').read_text(encoding='utf-8')
        restored = source.replace('from .types import DailySeries, FeatureResult, Status',
                                  'from tools.trend_volume_screen import DailySeries, FeatureResult, Status')
        self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(), ORIGINAL_V3_SHA256)

    def test_selected_uses_all_saved_stocks_independent_of_chip_result(self):
        detail = classify(frame())
        self.assertEqual(detail['row']['status'], 'PASS')
        self.assertEqual(detail['row']['signal_state'], 'SELECTED')
        self.assertEqual(detail['row']['source_status'], 'KNOWN')
        self.assertAlmostEqual(detail['screen']['volume_multiple'], 3)
        self.assertEqual(detail['parameter_sha256'], PARAMETER_SHA256)
        self.assertEqual(detail['screen']['rule_id'], RULE_ID)
        self.assertFalse(detail['can_generate_order'])

    def test_watch_is_preparation_with_unmet_surge_not_confirmed_reversal(self):
        detail = classify(frame(mode='watching'))
        self.assertEqual(detail['row']['signal_state'], 'WATCHING')
        self.assertEqual(detail['row']['status'], 'FAIL')
        self.assertIsNotNone(detail['row']['setup_date'])
        self.assertLess(detail['row']['close_return_pct'], 4)

    def test_missing_stale_incomplete_invalid_or_cross_generation_is_unknown(self):
        values = []
        stale = frame(); stale['history'].pop(); values.append(stale)
        incomplete = frame(); incomplete['history'][-1]['complete'] = False; values.append(incomplete)
        bad = frame(); bad['history'][-1]['volume_shares'] = 'NaN'; values.append(bad)
        cross = frame(); cross['dataset_id'] = DATASET; values.append(cross)
        missing = frame(); missing['history'] = []; values.append(missing)
        for value in values:
            with self.subTest(reason=value['history'][-1] if value['history'] else 'empty'):
                detail = classify(value)
                self.assertEqual(detail['row']['signal_state'], 'UNKNOWN')
                self.assertEqual(detail['screen']['status'], 'UNKNOWN')
                self.assertEqual(detail['screen']['rule_id'], RULE_ID)
                self.assertEqual(detail['history'], [])
                self.assertIsNone(detail['screen']['volume_multiple'])

    def test_valid_short_history_is_known_source_with_unknown_classification(self):
        detail = classify(frame(mode='short'))
        self.assertEqual(detail['source_status'], 'KNOWN')
        self.assertEqual(detail['row']['status'], 'UNKNOWN')
        self.assertTrue(all(candidate['date'] < AS_OF for candidate in detail['screen']['candidates']))

    def test_future_bars_do_not_affect_signal_or_chart_prefix(self):
        value = frame()
        expected = classify(value)
        value['history'].append({'date': '2026-10-29', 'open': 'not-available', 'volume_shares': 'unavailable'})
        actual = classify(value)
        self.assertEqual(actual['row'], expected['row'])
        self.assertEqual(actual['screen'], expected['screen'])
        self.assertEqual(actual['history'], expected['history'])
        self.assertEqual(actual['used_prefix_sha256'], expected['used_prefix_sha256'])

    def test_completed_session_uses_shanghai_close_boundary_and_official_holidays(self):
        self.assertEqual(daily.latest_completed_session(datetime(2026, 10, 28, 7, 39, tzinfo=timezone.utc)), '2026-10-27')
        self.assertEqual(daily.latest_completed_session(datetime(2026, 10, 28, 7, 40, tzinfo=timezone.utc)), AS_OF)
        self.assertEqual(daily.latest_completed_session(datetime(2026, 10, 7, 12, tzinfo=timezone.utc)), '2026-09-30')

    @patch.object(daily, 'latest_completed_session', return_value=AS_OF)
    def test_full_universe_manifest_last_unknown_is_not_negative_and_repeat_reuses(self, _latest):
        store = FakeStore()
        result = daily.run(store, workers=2, progress=None)
        self.assertEqual(result['outcome'], 'COMMITTED')
        self.assertEqual(result['counts'], {'SELECTED': 1, 'WATCHING': 1, 'NOT_MATCHED': 1, 'UNKNOWN': 1})
        self.assertEqual(result['source_known_count'], 3)
        self.assertEqual(store.source_reads, 4)
        publication = store.values[daily.PREFIX + 'manifest.json']
        self.assertEqual(publication['as_of'], AS_OF)
        self.assertIn('未独立核定', publication['scope'])
        self.assertTrue(any('全A股' in item for item in publication['limitations']))
        manifest_index = store.writes.index(daily.PREFIX + 'manifest.json')
        result_prefix = daily.PREFIX + result['dataset_id'] + '/'
        self.assertTrue(all(index < manifest_index for index, key in enumerate(store.writes) if key.startswith(result_prefix)))
        self.assertTrue(all(key.startswith(daily.PREFIX) for key in store.writes))
        self.assertEqual(store.values[daily.PREFIX + 'status.json']['state'], 'COMMITTED')
        again = daily.run(store, workers=2, progress=None)
        self.assertEqual(again['outcome'], 'ALREADY_COMMITTED')
        self.assertEqual(store.source_reads, 4)

    @patch.object(daily, 'latest_completed_session', return_value=AS_OF)
    def test_source_pending_never_substitutes_a_previous_session(self, _latest):
        store = FakeStore()
        store.values[daily.SOURCE_MANIFEST]['as_of'] = '2026-10-27'
        result = daily.run(store, progress=None)
        self.assertEqual(result['outcome'], 'SOURCE_PENDING')
        self.assertEqual(store.source_reads, 0)
        self.assertNotIn(daily.PREFIX + 'manifest.json', store.values)
        self.assertEqual(store.values[daily.PREFIX + 'status.json']['as_of'], AS_OF)

    @patch.object(daily, 'latest_completed_session', return_value=AS_OF)
    def test_source_identity_or_duplicate_universe_refuses_publication(self, _latest):
        for mode in ('mismatch', 'duplicate'):
            store = FakeStore()
            screen = store.values[daily.SOURCE_PREFIX + SOURCE + '/screening.json']
            if mode == 'mismatch': screen['as_of'] = '2026-10-27'
            else: screen['rows'][1] = deepcopy(screen['rows'][0])
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                daily.run(store, progress=None)
            self.assertNotIn(daily.PREFIX + 'manifest.json', store.values)
            self.assertEqual(store.values[daily.PREFIX + 'status.json']['state'], 'FAILED')

    @patch.object(daily, 'latest_completed_session', return_value=AS_OF)
    def test_budget_cutoff_preserves_previous_complete_manifest(self, _latest):
        store = FakeStore()
        old = {'as_of': '2026-10-27', 'dataset_id': '1111111111111111'}
        store.seed(daily.PREFIX + 'manifest.json', old)
        with patch.object(daily.time, 'monotonic', side_effect=[0] + [1000] * 100):
            result = daily.run(store, max_seconds=120, workers=1, progress=None)
        self.assertEqual(result['outcome'], 'INCOMPLETE')
        self.assertEqual(store.values[daily.PREFIX + 'manifest.json'], old)
        self.assertEqual(store.source_reads, 0)

    @patch.object(daily, 'latest_completed_session', return_value=AS_OF)
    def test_independent_live_lease_prevents_duplicate_writer(self, _latest):
        store = FakeStore()
        store.seed(daily.PREFIX + 'lease.json', {'owner': 'another-writer', 'expires': time.time() + 500})
        with self.assertRaisesRegex(RuntimeError, 'WRITER_BUSY'):
            daily.run(store, progress=None)
        self.assertEqual(store.source_reads, 0)
        self.assertEqual(store.writes, [])

    @patch.object(daily, 'latest_completed_session', return_value=AS_OF)
    def test_current_manifest_read_failure_releases_its_acquired_lease(self, _latest):
        class FailingCurrent(FakeStore):
            def get(self, key, **kwargs):
                if key == daily.PREFIX + 'manifest.json':
                    raise OSError('fictional failed current publication read')
                return super().get(key, **kwargs)

        store = FailingCurrent()
        with self.assertRaises(OSError):
            daily.run(store, progress=None)
        self.assertEqual(store.source_reads, 0)
        self.assertEqual(store.values[daily.PREFIX + 'lease.json']['expires'], 0)
        self.assertEqual(store.values[daily.PREFIX + 'status.json']['state'], 'FAILED')


if __name__ == '__main__':
    unittest.main()
