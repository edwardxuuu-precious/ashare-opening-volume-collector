"""Bounded native index inputs for the daily price projection publisher."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import time

from backend.chips.calendar import sessions
from backend.trend_volume.types import DailyBar, DailySeries


PREFIX = 'data/golden-projection/daily/'
FRAME_SCHEMA = 'stock.golden-projection.index-source.v1'
FIELDS = ('date', 'code', 'open', 'high', 'low', 'close', 'volume', 'amount')
MAX_ROWS = 400
MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class IndexSpec:
    code: str
    name: str
    source_code: str


INDEX_SPECS = (IndexSpec('sh000001', '上证指数', 'sh.000001'),
               IndexSpec('sz399006', '创业板指数', 'sz.399006'))
INDEX_SYMBOLS = tuple(spec.code for spec in INDEX_SPECS)


@dataclass(frozen=True)
class ReadyIndex:
    spec: IndexSpec
    key: str
    sha256: str
    frame: dict


@dataclass(frozen=True)
class MissingIndex:
    spec: IndexSpec
    reason: str


@dataclass(frozen=True)
class IndexSnapshot:
    dataset_id: str
    fingerprint: str
    inputs: tuple[ReadyIndex | MissingIndex, ...]
    provider_queries: int
    attempts: int


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _spec(code):
    for spec in INDEX_SPECS:
        if code == spec.code:
            return spec
    raise ValueError('INDEX_IDENTITY')


def _number(value, *, positive=False):
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        raise ValueError('INDEX_NUMBER')
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('INDEX_NUMBER') from exc
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise ValueError('INDEX_NUMBER')
    return result


def frame_from_sdk(payload, spec, as_of, ingested_at):
    if (not isinstance(payload, dict) or payload.get('symbol') != spec.source_code
            or payload.get('fields') != list(FIELDS) or payload.get('end_date') != as_of):
        raise ValueError('INDEX_SOURCE_IDENTITY')
    try:
        start = payload['start_date']
        if (not isinstance(start, str) or datetime.fromisoformat(start).date().isoformat() != start
                or not '2026-01-01' <= start <= as_of):
            raise ValueError('INDEX_SOURCE_START_DATE')
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('INDEX_SOURCE_START_DATE') from exc
    raw = encoded(payload)
    if len(raw) > MAX_BYTES:
        raise ValueError('INDEX_RESPONSE_BOUND')
    rows = payload.get('rows')
    if not isinstance(rows, list) or not 60 <= len(rows) <= MAX_ROWS:
        raise ValueError('INDEX_HISTORY_COUNT')
    allowed = set(sessions('2026-01-01', as_of))
    history, last = [], ''
    for values in rows:
        if not isinstance(values, list) or len(values) != len(FIELDS):
            raise ValueError('INDEX_SOURCE_FIELDS')
        row = dict(zip(FIELDS, values))
        day = row['date']
        if (row['code'] != spec.source_code or not isinstance(day, str)
                or day not in allowed or day <= last or day < payload.get('start_date', '')):
            raise ValueError('INDEX_SOURCE_DATE_OR_CODE')
        op, hi, lo, cl = (_number(row[key], positive=True) for key in ('open', 'high', 'low', 'close'))
        volume, amount = (_number(row[key]) for key in ('volume', 'amount'))
        if hi < max(op, cl, lo) or lo > min(op, cl) or volume != volume.to_integral_value():
            raise ValueError('INDEX_SOURCE_OHLC_OR_VOLUME')
        history.append(dict(date=day, open=row['open'], high=row['high'], low=row['low'],
                            close=row['close'], volume_shares=row['volume'], amount_cny=row['amount']))
        last = day
    if last != as_of or _number(history[-1]['volume_shares']) == 0:
        raise ValueError('INDEX_TARGET_SESSION_NOT_READY')
    if [row['date'] for row in history] != sessions(history[0]['date'], as_of):
        raise ValueError('INDEX_SOURCE_SESSION_GAP')
    raw_sha = hashlib.sha256(raw).hexdigest()
    return {'schema': FRAME_SCHEMA, 'code': spec.code, 'name': spec.name, 'as_of': as_of,
            'history': history, 'historical_known_at': None,
            'source': {'provider': 'BAOSTOCK_INDEX', 'source_code': spec.source_code,
                       'price_unit': 'INDEX_POINTS', 'volume_unit': 'SHARES',
                       'amount_unit': 'CNY', 'adjustment': 'INDEX_NOT_ADJUSTED',
                       'ingested_at': ingested_at, 'raw_sha256': raw_sha,
                       'normalized_history_sha256': digest(history),
                       'raw_key': PREFIX + 'index-raw/' + raw_sha + '.json',
                       'raw_representation': 'Original SDK field names and row strings, not original HTTP bytes',
                       'turnover_available': False}}


def _semantic(frame):
    return {key: frame[key] for key in ('schema', 'code', 'name', 'as_of', 'history')} | {
        'source': {key: frame['source'][key] for key in
                   ('provider', 'source_code', 'price_unit', 'volume_unit', 'amount_unit',
                    'raw_sha256', 'normalized_history_sha256')}}


def series_from_index_frame(value, *, code, as_of, source_dataset_id, source_sha256):
    spec = _spec(code)
    if (not isinstance(value, dict) or value.get('schema') != FRAME_SCHEMA
            or value.get('code') != spec.code or value.get('name') != spec.name
            or value.get('as_of') != as_of or value.get('dataset_id') != source_dataset_id):
        raise ValueError('INDEX_FRAME_IDENTITY')
    if not isinstance(source_dataset_id, str) or not re.fullmatch(r'[a-f0-9]{16}', source_dataset_id):
        raise ValueError('INDEX_SNAPSHOT_IDENTITY')
    source = value.get('source')
    if (not isinstance(source, dict) or source.get('provider') != 'BAOSTOCK_INDEX'
            or source.get('source_code') != spec.source_code or source.get('price_unit') != 'INDEX_POINTS'
            or source.get('volume_unit') != 'SHARES' or source.get('amount_unit') != 'CNY'
            or not re.fullmatch(r'[a-f0-9]{64}', source.get('raw_sha256', ''))
            or source.get('raw_key') != PREFIX + 'index-raw/' + source['raw_sha256'] + '.json'):
        raise ValueError('INDEX_SOURCE_UNITS_OR_PROVENANCE')
    try:
        acquired = datetime.fromisoformat(source['ingested_at'])
        if acquired.tzinfo is None or acquired.utcoffset() is None:
            raise ValueError('INDEX_INGESTED_AT')
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('INDEX_INGESTED_AT') from exc
    history = value.get('history')
    if not isinstance(history, list) or not 60 <= len(history) <= MAX_ROWS:
        raise ValueError('INDEX_HISTORY_COUNT')
    allowed = set(sessions('2026-01-01', as_of))
    bars, last = [], ''
    for row in history:
        if not isinstance(row, dict) or row.get('date') not in allowed or row['date'] <= last:
            raise ValueError('INDEX_HISTORY_DATE')
        op, cl, hi, lo = (_number(row.get(key), positive=True) for key in ('open', 'close', 'high', 'low'))
        volume = _number(row.get('volume_shares'))
        _number(row.get('amount_cny'))
        if hi < max(op, cl, lo) or lo > min(op, cl) or volume != volume.to_integral_value():
            raise ValueError('INDEX_SOURCE_OHLC_OR_VOLUME')
        if row.get('complete', True) is not True:
            raise ValueError('INDEX_INCOMPLETE_BAR')
        last = row['date']
        bars.append(DailyBar(datetime.fromisoformat(last).date(), op, cl, hi, lo, volume, True))
    if last != as_of or bars[-1].volume == 0:
        raise ValueError('INDEX_TARGET_SESSION_NOT_READY')
    if ([row['date'] for row in history] != sessions(history[0]['date'], as_of)
            or digest(history) != source.get('normalized_history_sha256')):
        raise ValueError('INDEX_HISTORY_IDENTITY_OR_SESSION_GAP')
    return DailySeries(code, tuple(bars), source_sha256, source['raw_sha256'],
                       source['provider'], 'shares', source['ingested_at'], False)


class BaoIndexSource:
    def __init__(self):
        self.query_count = 0

    def fetch(self, spec, as_of, deadline):
        from backend.scripts.baostock_source import BaoStockSession
        start = max('2026-01-01', (datetime.fromisoformat(as_of) - timedelta(days=450)).date().isoformat())
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise TimeoutError('INDEX_ACQUISITION_DEADLINE')
        with BaoStockSession(timeout=min(12, (remaining - .1) / 2), interval=.8) as session:
            def call(function, *args, **kwargs):
                left = deadline - time.monotonic()
                if left <= 1:
                    raise TimeoutError('INDEX_ACQUISITION_DEADLINE')
                session.timeout = min(12, left - 1)
                if session._wire:
                    session._wire.timeout = session.timeout
                return session._call(function, *args, **kwargs)

            try:
                session._ensure_connected()
                self.query_count += 1
                response = call(session._sdk.query_history_k_data_plus, spec.source_code, ','.join(FIELDS),
                                start_date=start, end_date=as_of, frequency='d', adjustflag='3')
                if response.error_code != '0' or response.fields != list(FIELDS):
                    raise ValueError('INDEX_SOURCE_FIELDS')
                rows, pages = [], set()
                while call(response.next):
                    pages.add(str(response.cur_page_num))
                    if len(rows) >= MAX_ROWS or len(pages) > 4:
                        raise ValueError('INDEX_SOURCE_QUERY_BOUND')
                    values = response.get_row_data()
                    if len(values) != len(FIELDS) or any(not isinstance(x, str) or len(x) > 64 for x in values):
                        raise ValueError('INDEX_SOURCE_FIELDS')
                    rows.append(list(values))
                if response.error_code != '0':
                    raise ValueError('INDEX_SOURCE_INCOMPLETE')
                return {'fields': list(FIELDS), 'rows': rows, 'symbol': spec.source_code,
                        'start_date': start, 'end_date': as_of}
            finally:
                if session._wire:
                    left = deadline - time.monotonic()
                    if left <= 1:
                        session._wire.failure = 'INDEX_ACQUISITION_DEADLINE'
                    else:
                        session._wire.timeout = min(12, left - 1)


def prepare_indices(store, as_of, *, deadline, source_factory=BaoIndexSource):
    deadline = min(deadline, time.monotonic() + 120)
    source, attempts, entries = None, 0, []
    for spec in INDEX_SPECS:
        cache_key = PREFIX + 'index-inputs/' + as_of + '/' + spec.code + '.json'
        cached, _, _ = store.get(cache_key, max_bytes=MAX_BYTES, max_decoded_bytes=MAX_BYTES, source=True)
        if cached is not None:
            try:
                series_from_index_frame(dict(cached, dataset_id='0' * 16), code=spec.code, as_of=as_of,
                                        source_dataset_id='0' * 16, source_sha256=digest(cached))
                raw, _, raw_sha = store.get(cached['source']['raw_key'], max_bytes=MAX_BYTES,
                                            max_decoded_bytes=MAX_BYTES, source=True)
                if raw is None or raw_sha != cached['source']['raw_sha256']:
                    raise ValueError('INDEX_RAW_SOURCE_MISSING_OR_CHANGED')
                reconstructed = frame_from_sdk(raw, spec, as_of, cached['source']['ingested_at'])
                if _semantic(reconstructed) != _semantic(cached):
                    raise ValueError('INDEX_SOURCE_NORMALIZATION_CHANGED')
            except (ValueError, KeyError, TypeError) as exc:
                raise RuntimeError('INDEX_SUCCESS_CACHE_INVALID') from exc
        else:
            if time.monotonic() >= deadline:
                entries.append(MissingIndex(spec, 'INDEX_SOURCE_BUDGET_EXHAUSTED'))
                continue
            if source is None:
                source = source_factory()
            attempts += 1
            try:
                payload = source.fetch(spec, as_of, deadline)
                cached = frame_from_sdk(payload, spec, as_of, datetime.now(timezone.utc).isoformat())
            except Exception as exc:
                entries.append(MissingIndex(spec, 'INDEX_SOURCE_NOT_READY:' + type(exc).__name__ + ':' + str(exc)[:150]))
                continue
            store.put_immutable(cached['source']['raw_key'], payload)
            store.put_immutable(cache_key, cached)
        entries.append(ReadyIndex(spec, cache_key, digest(cached), cached))
    semantic = [{'code': item.spec.code, 'input_sha256': digest(_semantic(item.frame))} if isinstance(item, ReadyIndex)
                else {'code': item.spec.code, 'state': 'UNKNOWN'} for item in entries]
    fingerprint = digest({'as_of': as_of, 'inputs': semantic})
    dataset = fingerprint[:16]
    snapshot_prefix = PREFIX + 'index-snapshots/' + dataset + '/'
    manifest_key = snapshot_prefix + 'manifest.json'
    previous, _, _ = store.get(manifest_key)
    if previous is not None:
        if (previous.get('fingerprint') != fingerprint or previous.get('as_of') != as_of
                or previous.get('index_symbols') != list(INDEX_SYMBOLS) or previous.get('inputs') != semantic):
            raise RuntimeError('INDEX_SNAPSHOT_IDENTITY_CONFLICT')
        frozen = []
        for item in entries:
            if isinstance(item, MissingIndex):
                reason = previous.get('missing_reasons', {}).get(item.spec.code)
                if not isinstance(reason, str) or not 0 < len(reason) <= 250:
                    raise RuntimeError('INDEX_SNAPSHOT_MISSING_REASON_INVALID')
                frozen.append(MissingIndex(item.spec, reason))
                continue
            key = snapshot_prefix + item.spec.code + '.json'
            frame, _, frame_sha = store.get(key, max_bytes=MAX_BYTES, max_decoded_bytes=MAX_BYTES, source=True)
            if frame is None or _semantic(frame) != _semantic(item.frame):
                raise RuntimeError('INDEX_SNAPSHOT_FRAME_MISSING_OR_CHANGED')
            series_from_index_frame(frame, code=item.spec.code, as_of=as_of,
                                    source_dataset_id=dataset, source_sha256=frame_sha)
            frozen.append(ReadyIndex(item.spec, key, frame_sha, frame))
        entries = frozen
    else:
        frozen = []
        for item in entries:
            if isinstance(item, MissingIndex):
                frozen.append(item)
                continue
            frame = dict(item.frame, dataset_id=dataset)
            key = snapshot_prefix + item.spec.code + '.json'
            store.put_immutable(key, frame)
            frozen.append(ReadyIndex(item.spec, key, digest(frame), frame))
        entries = frozen
        store.put_immutable(manifest_key, {'schema': 'stock.golden-projection.index-snapshot.v1',
                                         'dataset_id': dataset, 'fingerprint': fingerprint, 'as_of': as_of,
                                         'index_symbols': list(INDEX_SYMBOLS), 'inputs': semantic,
                                         'missing_reasons': {item.spec.code: item.reason for item in entries
                                                            if isinstance(item, MissingIndex)}})
    return IndexSnapshot(dataset, fingerprint, tuple(entries), getattr(source, 'query_count', 0), attempts)
