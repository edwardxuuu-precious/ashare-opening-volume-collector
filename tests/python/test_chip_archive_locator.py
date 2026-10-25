"""Invented archive provenance regressions; no source or cloud requests."""
import base64
import hashlib
import io
import json
import socket
import time
import unittest
from unittest.mock import patch

from backend.chips.calendar import last_ten
from backend.chips.history import load_archive, load_history
from backend.chips.runner import _calculate, implementation_identity
from test_chip_current_quote import quote
from test_chip_daily_v1 import Memory


CODE = '000573'
STOCK = {'code': CODE, 'name': 'ALL_VALUES_INVENTED'}
FIRST = '1111111111111111'
SECOND = '2222222222222222'
THIRD = '3333333333333333'


class NoSource:
    def __init__(self):
        self.calls = 0

    def history(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError('No new history request is authorized')

    confirmed_history = history


class ArchiveStore(Memory):
    def __init__(self):
        super().__init__()
        self.reads = []

    def get(self, key):
        self.reads.append(key)
        return super().get(key)

    def get_object(self, *, Bucket, Key):
        value, _ = self.get(Key)
        if value is None:
            error = RuntimeError('INVENTED_MISSING')
            error.response = {'Error': {'Code': 'NoSuchKey'}}
            raise error
        return {'Body': io.BytesIO(json.dumps(value).encode())}


class ArchiveLocator(unittest.TestCase):
    def setUp(self):
        self.deny_network = patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden'))
        self.deny_network.start()
        self.addCleanup(self.deny_network.stop)
        self.store = ArchiveStore()
        self.owner = 'ALL_VALUES_INVENTED'
        self.store.put('collector/chips/lease.json', {'owner': self.owner, 'expires': time.time() + 600})
        self.identity = implementation_identity()

    def ctx(self, day, slot, previous=None):
        return {'slot': slot, 'dataset': slot, 'days': last_ten(day), 'as_of': day,
                'owner': self.owner, 'pools': {}, 'actual_limits': {},
                'execution_identity': self.identity, 'previous_slot': previous}

    def make_overlay(self):
        lines = [f'{d},4.47,4.47,4.50,4.40,1000,447000,,0,0,3'
                 for d in last_ten('2026-10-16')]
        raw = json.dumps({'rc': 0, 'data': {'code': CODE, 'klines': lines}}).encode()

        class Source:
            def history(self, *args, **kwargs):
                return json.loads(raw), {'provider': 'EASTMONEY', 'sha256': hashlib.sha256(raw).hexdigest()}, raw

        _calculate(self.store, Source(), STOCK, self.ctx('2026-10-16', FIRST))
        ctx = self.ctx('2026-10-19', SECOND)
        ctx['actual_limits'] = {'2026-10-19': {CODE: quote()}}
        self.assertEqual(_calculate(self.store, NoSource(), STOCK, ctx)['chip_status'], 'READY')
        return ctx

    def published(self, slot):
        return self.store.get(f'data/chips/daily/{slot}/{CODE}.json')[0]

    def make_ready(self, *, price_excluded, legacy=False):
        price = '4.50' if price_excluded else '14.50'
        amount = '450000' if price_excluded else '1450000'
        days = last_ten('2026-10-19')
        raw = json.dumps({'rc': 0, 'data': {'code': CODE, 'klines': [
            f'{day},{price},{price},{price},{price},1000,{amount},,0,0,2' for day in days]}}).encode()

        class Source:
            def history(self, *args, **kwargs):
                return json.loads(raw), {'provider': 'EASTMONEY', 'sha256': hashlib.sha256(raw).hexdigest()}, raw

        ctx = self.ctx(days[-1], FIRST)
        ctx['actual_limits'] = {days[-1]: {CODE: {'date': days[-1], 'close': price,
                                                'reference_close': price, 'upper_limit': '15.95'}}}
        self.assertEqual(_calculate(self.store, Source(), STOCK, ctx)['chip_status'], 'READY')
        if legacy:
            key = f'data/chips/daily/{FIRST}/{CODE}.json'
            previous, etag = self.store.get(key)
            prefix_key = f'collector/chips/raw/aaaaaaaaaaaaaaaa/{CODE}.json'
            prefix = raw + b' '
            self.store.put(prefix_key, {'receipt': {'sha256': hashlib.sha256(prefix).hexdigest()},
                                        'raw_base64': base64.b64encode(prefix).decode()})
            previous['source'].update(same_session_cached_raw_key=prefix_key,
                                      original_history_raw_key=prefix_key)
            self.store.put(key, previous, match=etag)
        return ctx

    def test_final_overlay_archive_is_the_actual_loader_source(self):
        self.make_overlay()
        value = self.published(SECOND)
        final_key = f'collector/chips/raw/{SECOND}/{CODE}-current-quote.json'
        self.assertEqual(value['source']['same_session_cached_raw_key'], final_key)
        self.assertEqual(value['source']['original_history_raw_key'], f'collector/chips/raw/{FIRST}/{CODE}.json')
        raw, key = load_archive(self.store, 'INVENTED', value)
        self.assertEqual(key, final_key)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), value['source']['sha256'])

    def test_old_wrong_prefix_recovers_and_survives_three_ready_reuses(self):
        for excluded in (True, False):
            with self.subTest(price_excluded=excluded):
                self.store = ArchiveStore()
                self.store.put('collector/chips/lease.json', {'owner': self.owner, 'expires': time.time() + 600})
                ctx = self.make_ready(price_excluded=excluded, legacy=True)
                original = self.published(FIRST)
                previous = FIRST
                for slot in (SECOND, THIRD, '4444444444444444'):
                    current = {**ctx, 'slot': slot, 'dataset': slot, 'previous_slot': previous}
                    self.assertEqual(_calculate(self.store, NoSource(), STOCK, current)['chip_status'], 'READY')
                    value = self.published(slot)
                    self.assertEqual(value['snapshots'], original['snapshots'])
                    self.assertNotIn('price_exclusion_reuse', value)
                    self.assertEqual(value['source']['original_history_raw_key'], original['source']['original_history_raw_key'])
                    previous = slot
                raw, key = load_archive(self.store, 'INVENTED', value)
                self.assertEqual(key, f'collector/chips/raw/{FIRST}/{CODE}.json')
                self.assertEqual(hashlib.sha256(raw).hexdigest(), value['source']['sha256'])
                replay = load_history(self.store, 'INVENTED', previous, CODE, ctx['days'][-2])
                self.assertEqual(replay['comparison_status'], 'READY')
                self.assertEqual(replay['input_archive_key'], key)

    def test_good_locator_needs_one_raw_read_and_model_cache_for_rescreen(self):
        ctx = self.make_ready(price_excluded=True)
        raw_key = f'collector/chips/raw/{FIRST}/{CODE}.json'
        self.store.reads.clear()
        _calculate(self.store, NoSource(), STOCK, {**ctx, 'slot': SECOND, 'dataset': SECOND, 'previous_slot': FIRST})
        self.assertEqual(self.store.reads.count(raw_key), 1)
        self.assertEqual(self.store.reads.count(f'collector/chips/model/{CODE}.json'), 1)
        self.assertEqual(self.published(SECOND)['source']['same_session_cached_raw_key'], raw_key)

    def test_unbound_cache_cannot_repair_legacy_prefix_or_request_a_source(self):
        changes = {
            '_source_bundle_sha256': '0' * 64,
            '_history_adapter_sha256': '0' * 64,
            '_model_code_sha256': '0' * 64,
            'security_id': '000574',
            '_calculated_as_of': '2026-10-16',
            '_source_raw_key': 'collector/chips/raw/../../credentials.json',
        }
        for excluded in (True, False):
            for field, wrong in changes.items():
                with self.subTest(price_excluded=excluded, field=field):
                    self.store = ArchiveStore()
                    self.store.put('collector/chips/lease.json', {'owner': self.owner, 'expires': time.time() + 600})
                    ctx = self.make_ready(price_excluded=excluded, legacy=True)
                    key = f'collector/chips/model/{CODE}.json'
                    cache, etag = self.store.get(key)
                    cache[field] = wrong
                    self.store.put(key, cache, match=etag)
                    source = NoSource()
                    with self.assertRaisesRegex(RuntimeError, 'SOURCE_ARCHIVE_BINDING_UNAVAILABLE'):
                        _calculate(self.store, source, STOCK, {**ctx, 'slot': SECOND, 'dataset': SECOND, 'previous_slot': FIRST})
                    self.assertEqual(source.calls, 0)
                    self.assertIsNone(self.published(SECOND))
                    self.assertNotIn('collector/chips/raw/../../credentials.json', self.store.reads)

    def test_missing_or_corrupt_archive_never_publishes_a_reused_ready_frame(self):
        for excluded in (True, False):
            for problem in ('missing', 'receipt_sha', 'raw_sha', 'wrong_code', 'wrong_key', 'wrong_provider', 'missing_cache'):
                with self.subTest(price_excluded=excluded, problem=problem):
                    self.store = ArchiveStore()
                    self.store.put('collector/chips/lease.json', {'owner': self.owner, 'expires': time.time() + 600})
                    ctx = self.make_ready(price_excluded=excluded, legacy=True)
                    raw_key = f'collector/chips/raw/{FIRST}/{CODE}.json'
                    wrapper, etag = self.store.get(raw_key)
                    frame_key = f'data/chips/daily/{FIRST}/{CODE}.json'
                    previous, frame_etag = self.store.get(frame_key)
                    cache_key = f'collector/chips/model/{CODE}.json'
                    cache, cache_etag = self.store.get(cache_key)
                    if problem == 'missing':
                        del self.store.data[raw_key]
                    elif problem == 'receipt_sha':
                        wrapper['receipt']['sha256'] = '0' * 64
                    elif problem == 'raw_sha':
                        wrapper['raw_base64'] = base64.b64encode(b'{}').decode()
                    elif problem == 'wrong_code':
                        decoded = json.loads(base64.b64decode(wrapper['raw_base64']))
                        decoded['data']['code'] = '000574'
                        raw = json.dumps(decoded).encode()
                        digest = hashlib.sha256(raw).hexdigest()
                        wrapper['receipt']['sha256'] = digest
                        wrapper['raw_base64'] = base64.b64encode(raw).decode()
                        previous['source']['sha256'] = digest
                        cache['_source_bundle_sha256'] = digest
                    elif problem == 'wrong_key':
                        cache['_source_raw_key'] = f'collector/chips/raw/{FIRST}/000574.json'
                    elif problem == 'wrong_provider':
                        wrapper['receipt']['provider'] = 'THS_UNADJUSTED'
                    elif problem == 'missing_cache':
                        del self.store.data[cache_key]
                    if problem != 'missing':
                        self.store.put(raw_key, wrapper, match=etag)
                    self.store.put(frame_key, previous, match=frame_etag)
                    if problem != 'missing_cache':
                        self.store.put(cache_key, cache, match=cache_etag)
                    source = NoSource()
                    with self.assertRaisesRegex(RuntimeError, 'SOURCE_ARCHIVE_'):
                        _calculate(self.store, source, STOCK, {**ctx, 'slot': SECOND, 'dataset': SECOND, 'previous_slot': FIRST})
                    self.assertEqual(source.calls, 0)
                    self.assertIsNone(self.published(SECOND))
                    self.assertNotIn(f'collector/chips/raw/{FIRST}/000574.json', self.store.reads)

    def make_saved_trade_status(self, *, conflicting_alias=False):
        ctx = self.ctx('2026-10-19', FIRST)
        lines = [f'{day},14.50,14.50,14.50,14.50,1000,1450000,,0,0,2' for day in ctx['days']]
        primary = json.dumps({'rc': 0, 'data': {'code': CODE,
                              'klines': [line for line in lines if not line.startswith('2026-10-16')]}}).encode()
        alternate = json.dumps({'rc': 0, 'data': {'code': CODE, 'klines': lines}}).encode()
        def wrapper(raw, provider):
            return {'receipt': {'provider': provider, 'sha256': hashlib.sha256(raw).hexdigest()},
                    'raw_base64': base64.b64encode(raw).decode()}
        self.store.put(f'collector/chips/raw/{FIRST}/{CODE}.json', wrapper(primary, 'THS_UNADJUSTED'))
        self.store.put(f'collector/chips/raw/{FIRST}/{CODE}-trade-status.json', wrapper(alternate, 'BAOSTOCK_0.9.3'))
        alias = f'collector/chips/raw/{FIRST}/{CODE}-{hashlib.sha256(alternate).hexdigest()[:16]}.json'
        if conflicting_alias:
            self.store.put(alias, wrapper(primary, 'THS_UNADJUSTED'))
        return ctx, alias, alternate

    def test_saved_trade_status_gets_one_legal_immutable_archive_alias(self):
        ctx, alias, raw = self.make_saved_trade_status()
        source = NoSource()
        self.assertEqual(_calculate(self.store, source, STOCK, ctx)['chip_status'], 'READY')
        value = self.published(FIRST)
        self.assertEqual(value['source']['same_session_cached_raw_key'], alias)
        loaded, key = load_archive(self.store, 'INVENTED', value)
        self.assertEqual((loaded, key), (raw, alias))
        self.assertEqual(self.store.writes.count(alias), 1)
        self.assertEqual(source.calls, 0)
        # A resumed frame calculation can reuse the identical immutable alias.
        del self.store.data[f'data/chips/daily/{FIRST}/{CODE}.json']
        _calculate(self.store, source, STOCK, ctx)
        self.assertEqual(self.store.writes.count(alias), 1)
        self.assertEqual(source.calls, 0)

    def test_conflicting_trade_status_alias_is_not_overwritten_or_published(self):
        ctx, alias, _ = self.make_saved_trade_status(conflicting_alias=True)
        before = self.store.get(alias)[0]
        source = NoSource()
        with self.assertRaisesRegex(RuntimeError, 'IMMUTABLE_SOURCE_ARCHIVE_ALIAS_CONFLICT'):
            _calculate(self.store, source, STOCK, ctx)
        self.assertEqual(self.store.get(alias)[0], before)
        self.assertEqual(self.store.writes.count(alias), 1)
        self.assertIsNone(self.published(FIRST))
        self.assertEqual(source.calls, 0)


if __name__ == '__main__':
    unittest.main()
