"""Publish daily projection references from the already committed private bars."""
from __future__ import annotations

import argparse
from collections import Counter
import concurrent.futures
from datetime import datetime, timedelta, timezone
import gzip
from pathlib import Path
import time
import uuid

from backend.chips.calendar import sessions
from backend.trend_volume.daily import Storage, validate_source, latest_completed_session
from .model import RULE_ID, POLICY_SHA256, classify_frame, unknown_detail, digest, encoded, parameter_values
from .index_sources import INDEX_SPECS, INDEX_SYMBOLS, BaoIndexSource, ReadyIndex, prepare_indices

PREFIX = 'data/golden-projection/daily/'
SOURCE_PREFIX = 'data/chips/daily/'
SOURCE_MANIFEST = SOURCE_PREFIX + 'manifest.json'
CATEGORIES = ('ABOVE', 'BELOW', 'NEAR_M', 'NEAR_L', 'NEAR_H', 'FAR', 'AMBIGUOUS', 'HISTORY', 'NO_DRAW', 'UNKNOWN')
STATES = ('CURRENT', 'NEW_REFERENCE', 'HISTORY', 'NO_DRAW', 'UNKNOWN')
RULE_SOURCE_ID = 'OWNER_V21_CALIBRATION_WITH_ASSISTANT_ENGINEERING_INTERPRETATION_20261102'


def now():
    return datetime.now(timezone.utc).isoformat()


def local_day(moment):
    return datetime.fromisoformat(moment).astimezone(timezone(timedelta(hours=8))).date().isoformat()


def next_effective_session(as_of, recorded_at):
    recorded_day = local_day(recorded_at)
    after = max(as_of, recorded_day)
    until = (datetime.fromisoformat(after) + timedelta(days=35)).date().isoformat()
    candidates = [day for day in sessions(after, until) if day > after]
    if not candidates:
        raise ValueError('NEXT_OFFICIAL_SESSION_UNKNOWN')
    return candidates[0]


def implementation_identity():
    root = Path(__file__).parent
    files = {name: digest((root / name).read_bytes().replace(b'\r\n', b'\n'))
             for name in ('__init__.py', 'policy.json', 'model.py', 'daily.py', 'index_sources.py')}
    return {'sha256': digest(encoded(files)), 'files': files, 'rule_id': RULE_ID, 'policy_sha256': POLICY_SHA256}


def previous_publication(store, current, requested):
    if current is None:
        return None
    if current.get('schema') != 'stock.golden-projection.daily.manifest.v1':
        raise ValueError('CURRENT_PROJECTION_MANIFEST_INVALID')
    if current['as_of'] < requested:
        return current
    previous_id = current.get('previous_dataset_id')
    if previous_id is None:
        return None
    previous, _, _ = store.get(PREFIX + previous_id + '/manifest.json')
    if not previous or previous.get('dataset_id') != previous_id or previous.get('as_of', requested) >= requested:
        raise ValueError('PREVIOUS_PROJECTION_PUBLICATION_INVALID')
    return previous


def run(store, *, as_of=None, max_seconds=900, workers=8, progress=print,
        index_source_factory=BaoIndexSource):
    if type(max_seconds) is not int or not 120 <= max_seconds <= 900 or type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError('BOUNDED_RUNTIME_AND_WORKERS_REQUIRED')
    requested = as_of or latest_completed_session()
    if requested > latest_completed_session() or requested not in sessions('2026-01-01', requested):
        raise ValueError('COMPLETED_OFFICIAL_SESSION_REQUIRED')
    started = time.monotonic()
    deadline = started + max_seconds
    source_reads_before = getattr(store, 'source_reads', 0)
    owner = uuid.uuid4().hex
    lease_key = PREFIX + 'lease.json'
    lease, lease_etag, _ = store.get(lease_key)
    if lease and lease.get('expires', 0) > time.time():
        raise RuntimeError('PROJECTION_WRITER_BUSY')
    store.put(lease_key, {'owner': owner, 'expires': time.time() + max_seconds + 120},
              match=lease_etag, new=lease_etag is None)
    current, source_dataset, dataset = None, None, None
    scanned, total = 0, 0

    def owned():
        value, _, _ = store.get(lease_key)
        if not value or value.get('owner') != owner or value.get('expires', 0) <= time.time():
            raise RuntimeError('PROJECTION_WRITER_LEASE_LOST')

    def status(state, message):
        owned()
        store.put(PREFIX + 'status.json', {
            'schema': 'stock.golden-projection.daily.status.v1', 'state': state,
            'as_of': requested, 'source_dataset_id': source_dataset, 'dataset_id': dataset,
            'universe_total': total, 'scanned_count': scanned, 'updated_at': now(), 'message': message,
            'last_committed_date': current.get('as_of') if current else None})

    try:
        current, current_etag, _ = store.get(PREFIX + 'manifest.json')
        if current and current.get('as_of', '') > requested:
            raise ValueError('REFUSE_TO_ROLL_BACK_PROJECTION_DATE')
        source, _, source_manifest_sha = store.get(SOURCE_MANIFEST)
        if not source or source.get('as_of', '') < requested:
            status('SOURCE_PENDING', '等待本交易日完整行情批次，保留已发布结果。')
            return {'outcome': 'SOURCE_PENDING', 'requested_as_of': requested,
                    'last_committed_date': current.get('as_of') if current else None}
        if source.get('as_of') != requested:
            raise ValueError('SOURCE_DATE_DIFFERS_FROM_REQUESTED_SESSION')
        source_dataset = source.get('active_slot')
        screening, _, source_screen_sha = store.get(SOURCE_PREFIX + str(source_dataset) + '/screening.json',
                                                     max_bytes=6 * 1024 * 1024, max_decoded_bytes=64 * 1024 * 1024)
        stocks = validate_source(source, screening)
        stock_total = len(stocks)
        total = stock_total + len(INDEX_SPECS)
        status('RUNNING', '正在核对股票来源并准备两个指数的同日行情。')
        indices = prepare_indices(store, requested, deadline=deadline - 75,
                                  source_factory=index_source_factory)
        index_inputs = {item.spec.code: item for item in indices.inputs}
        identity = implementation_identity()
        if (current and current.get('source_dataset_id') == source_dataset
                and current.get('execution_identity', {}).get('sha256') == identity['sha256']
                and current.get('source_manifest_sha256') == source_manifest_sha
                and current.get('source_screening_sha256') == source_screen_sha
                and current.get('index_source_fingerprint') == indices.fingerprint
                and current.get('index_source_dataset_id') == indices.dataset_id
                and current.get('as_of') == requested):
            dataset = current['dataset_id']
            scanned = total
            status('COMMITTED', '行情和画线规则未变化，保留同一完整批次。')
            return {'outcome': 'ALREADY_COMMITTED', 'as_of': requested, 'dataset_id': current['dataset_id'],
                    'source_dataset_id': source_dataset, 'index_source_dataset_id': indices.dataset_id,
                    'universe_total': total,
                    'source_reads': getattr(store, 'source_reads', 0) - source_reads_before,
                    'new_market_source_requests': indices.provider_queries, 'index_source_attempts': indices.attempts}
        previous = previous_publication(store, current, requested)
        previous_id = previous['dataset_id'] if previous else None
        generation_day = local_day(now())
        dataset = digest(encoded({'source_manifest_sha256': source_manifest_sha, 'source_screening_sha256': source_screen_sha,
                                  'previous_dataset_id': previous_id, 'generation_day': generation_day,
                                  'index_source_fingerprint': indices.fingerprint,
                                  'execution_identity': identity['sha256']}))[:16]
        meta_key = PREFIX + dataset + '/batch.json'
        metadata, _, _ = store.get(meta_key)
        binding = {'dataset_id': dataset, 'source_dataset_id': source_dataset, 'as_of': requested,
                   'previous_dataset_id': previous_id, 'generation_day': generation_day, 'execution_identity': identity,
                   'index_source_dataset_id': indices.dataset_id, 'index_source_fingerprint': indices.fingerprint}
        if metadata is None:
            recorded_at = now()
            metadata = {**binding, 'recorded_at': recorded_at,
                        'effective_from': next_effective_session(requested, recorded_at)}
            store.put_immutable(meta_key, metadata)
        elif any(metadata.get(key) != value for key, value in binding.items()):
            raise ValueError('PARTIAL_BATCH_IDENTITY_CONFLICT')
        status('RUNNING', '正在检查波段与中轴，生成每日画线和收盘位置分类。')
        rows, input_hashes = [], []

        def calculate(stock):
            code, name = stock['code'], stock['name']
            index_input = index_inputs.get(code)
            source_key = (index_input.key if isinstance(index_input, ReadyIndex) else None) if index_input else SOURCE_PREFIX + source_dataset + '/' + code + '.json'
            source_sha, previous_sha = None, None
            kwargs = dict(code=code, name=name, as_of=requested, dataset_id=dataset,
                          source_dataset_id=indices.dataset_id if index_input else source_dataset, source_sha256=None,
                          recorded_at=metadata['recorded_at'], effective_from=metadata['effective_from'])
            if index_input:
                kwargs.update(source_key=source_key, source_metadata=index_input.frame['source'] if isinstance(index_input, ReadyIndex) else None)
            try:
                if isinstance(index_input, ReadyIndex):
                    frame, source_sha = index_input.frame, index_input.sha256
                elif index_input:
                    frame = None
                else:
                    frame, _, source_sha = store.get(source_key, source=True)
                old = None
                missing_previous = False
                if previous_id and (not index_input or code in previous.get('index_symbols', [])):
                    old, _, previous_sha = store.get(PREFIX + previous_id + '/' + code + '.json', source=True)
                    missing_previous = old is None
                kwargs['source_sha256'] = source_sha
                if frame is None:
                    detail = unknown_detail(**kwargs, reason=index_input.reason if index_input else 'SOURCE_FRAME_MISSING')
                elif missing_previous:
                    detail = unknown_detail(**kwargs, reason='PREVIOUS_PROJECTION_FRAME_MISSING')
                else:
                    detail = classify_frame(frame, **kwargs, previous=old)
            except RuntimeError:
                raise
            except (ValueError, UnicodeError, OSError, EOFError) as exc:
                detail = unknown_detail(**kwargs, reason='INPUT_INVALID:' + type(exc).__name__)
            except Exception as exc:
                from botocore.exceptions import BotoCoreError, ClientError
                if not isinstance(exc, (BotoCoreError, ClientError)):
                    raise
                raise RuntimeError('SOURCE_STORAGE_UNAVAILABLE_RETRY_WITHOUT_FREEZING_UNKNOWN') from exc
            detail.update(source_id=RULE_SOURCE_ID, rule_version=parameter_values()['version'], known_at=None)
            store.put_immutable(PREFIX + dataset + '/' + code + '.json', detail)
            return detail['row'], {'code': code, 'source_key': source_key, 'source_sha256': source_sha,
                                   'previous_result_sha256': previous_sha, 'result_sha256': digest(encoded(detail))}

        iterator = iter(stocks + [{'code': spec.code, 'name': spec.name} for spec in INDEX_SPECS])
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            active = {}

            def fill():
                while len(active) < workers and time.monotonic() < deadline - 75:
                    try:
                        stock = next(iterator)
                    except StopIteration:
                        break
                    active[pool.submit(calculate, stock)] = stock['code']

            fill()
            saved_at = time.monotonic()
            while active:
                finished, _ = concurrent.futures.wait(active, timeout=10, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in finished:
                    active.pop(future)
                    row, audit = future.result()
                    rows.append(row)
                    input_hashes.append(audit)
                    scanned += 1
                if scanned >= 20 and all('SOURCE_STORAGE_READ_FAILED' in row['reasons'] for row in rows):
                    raise RuntimeError('SOURCE_STORAGE_UNAVAILABLE_STOPPED')
                if finished and scanned % 100 < workers or time.monotonic() - saved_at >= 30:
                    status('RUNNING', '正在检查波段与中轴，保留选用及弃画依据。')
                    if progress:
                        progress(encoded({'state': 'RUNNING', 'as_of': requested, 'scanned': scanned, 'total': total}).decode(), flush=True)
                    saved_at = time.monotonic()
                fill()
        if scanned != total:
            status('FAILED', '本轮未在时限内完成全部标的，未替换上次完整结果。')
            return {'outcome': 'INCOMPLETE', 'as_of': requested, 'dataset_id': dataset, 'scanned': scanned, 'total': total}
        if local_day(now()) != generation_day:
            raise RuntimeError('CALCULATION_CROSSED_LOCAL_DAY_RESTART_WITH_NEW_GENERATION')
        rows.sort(key=lambda row: row['code'])
        if len(rows) != total or len({row['code'] for row in rows}) != total:
            raise ValueError('OUTPUT_UNIVERSE_IDENTITY')
        if any(row['category'] not in CATEGORIES or row['drawing_state'] not in STATES for row in rows):
            raise ValueError('OUTPUT_CATEGORY_INVALID')
        summary = {'schema': 'stock.golden-projection.daily.screen.v1', 'dataset_id': dataset,
                   'source_dataset_id': source_dataset, 'as_of': requested, 'rule_id': RULE_ID,
                   'index_source_dataset_id': indices.dataset_id, 'index_symbols': list(INDEX_SYMBOLS),
                   'stock_total': stock_total, 'index_total': len(INDEX_SPECS),
                   'policy_sha256': POLICY_SHA256, 'source_id': RULE_SOURCE_ID,
                   'rule_version': parameter_values()['version'], 'known_at': None, 'universe_total': total, 'rows': rows}
        if len(gzip.compress(encoded(summary), mtime=0)) * 4 // 3 + 8192 > 5900000:
            raise ValueError('SCREENING_EXCEEDS_AUTHENTICATED_RESPONSE_BOUND')
        owned()
        store.put_immutable(PREFIX + dataset + '/screening.json', summary)
        saved, _, _ = store.get(PREFIX + dataset + '/screening.json', max_bytes=6 * 1024 * 1024, max_decoded_bytes=16 * 1024 * 1024)
        if saved != summary:
            raise ValueError('SCREENING_READBACK_MISMATCH')
        latest_source, _, _ = store.get(SOURCE_MANIFEST)
        coverage = source.get('universe_coverage', 'UNKNOWN')
        category_counts, state_counts = Counter(row['category'] for row in rows), Counter(row['drawing_state'] for row in rows)
        archives = [{key: value[key] for key in ('as_of', 'dataset_id', 'generated_at')} for value in (current or {}).get('archives', [])
                    if value['dataset_id'] != dataset]
        if current and not any(value['dataset_id'] == current['dataset_id'] for value in archives):
            archives.append({key: current[key] for key in ('as_of', 'dataset_id', 'generated_at')})
        limitations = ['自动选点按V21的波段、角色或完整转折思路作保守工程判断；有歧义不强画。',
                       '首批新组合仅显示当前收盘位置参考；实际记录时间与次日生效日期单独保存，不补造历史锁线。',
                       '2%只定义展示距离，盘中越线与收盘位置分别标记，不生成买卖指令或收益结论。',
                       '股票沿用已发布收盘日线；两个指数使用独立保存的指数日线，缺失与陈旧行情保留待更新。']
        if coverage in ('SITE_CATALOG_UNVERIFIED', 'UNKNOWN'):
            limitations.append('覆盖当前网站股票清单，尚未独立核定全部在市证券。')
        publication = {
            'schema': 'stock.golden-projection.daily.manifest.v1', 'dataset_id': dataset,
            'source_dataset_id': source_dataset, 'as_of': requested, 'requested_as_of': requested,
            'index_source_dataset_id': indices.dataset_id, 'index_source_fingerprint': indices.fingerprint,
            'index_symbols': list(INDEX_SYMBOLS), 'stock_total': stock_total, 'index_total': len(INDEX_SPECS),
            'generated_at': metadata['recorded_at'], 'published_at': now(), 'universe_total': total,
            'counts': {key: category_counts[key] for key in CATEGORIES}, 'state_counts': {key: state_counts[key] for key in STATES},
            'rule_id': RULE_ID, 'policy_sha256': POLICY_SHA256, 'rule_parameters': parameter_values(),
            'source_id': RULE_SOURCE_ID, 'rule_version': parameter_values()['version'], 'known_at': None,
            'scope': '当前网站股票清单及两个指数（未独立核定全部在市证券）' if coverage in ('SITE_CATALOG_UNVERIFIED', 'UNKNOWN') else '当前已发布股票清单及两个指数',
            'source_generation_stale': not latest_source or latest_source.get('dataset_id') != source_dataset,
            'source_universe_coverage': coverage, 'limitations': limitations, 'execution_identity': identity,
            'source_manifest_sha256': source_manifest_sha, 'source_screening_sha256': source_screen_sha,
            'screening_sha256': digest(encoded(summary)), 'previous_dataset_id': previous_id,
            'input_and_result_identities_sha256': digest(encoded(sorted(input_hashes, key=lambda item: item['code']))),
            'archives': sorted(archives, key=lambda item: (item['as_of'], item['generated_at']), reverse=True)[:60],
            'new_market_source_requests': indices.provider_queries, 'index_source_attempts': indices.attempts,
            'can_generate_order': False}
        saved_publication, _, _ = store.get(PREFIX + dataset + '/manifest.json')
        if saved_publication is not None:
            for key in ('dataset_id', 'source_dataset_id', 'as_of', 'rule_id', 'policy_sha256',
                        'screening_sha256', 'previous_dataset_id', 'execution_identity',
                        'index_source_dataset_id', 'index_source_fingerprint'):
                if saved_publication.get(key) != publication[key]:
                    raise ValueError('PARTIAL_PUBLICATION_IDENTITY_CONFLICT')
            publication = saved_publication
        else:
            store.put_immutable(PREFIX + dataset + '/manifest.json', publication)
        owned()
        store.put(PREFIX + 'manifest.json', publication, match=current_etag, new=current_etag is None)
        saved, _, _ = store.get(PREFIX + 'manifest.json')
        if saved != publication:
            raise ValueError('PROJECTION_MANIFEST_READBACK_MISMATCH')
        current = publication
        status('COMMITTED', '已完成所标注交易日的每日画线与分类。')
        return {'outcome': 'COMMITTED', 'as_of': requested, 'dataset_id': dataset, 'source_dataset_id': source_dataset,
                'index_source_dataset_id': indices.dataset_id,
                'universe_total': total, 'counts': publication['counts'], 'state_counts': publication['state_counts'],
                'source_reads': getattr(store, 'source_reads', None), 'source_encoded_bytes': getattr(store, 'source_bytes', None),
                'new_market_source_requests': indices.provider_queries, 'index_source_attempts': indices.attempts,
                'execution_identity': identity,
                'input_and_result_identities': sorted(input_hashes, key=lambda item: item['code']),
                'elapsed_seconds': round(time.monotonic() - started, 3)}
    except Exception:
        try:
            status('FAILED', '本轮未完成，保留上次完整结果。')
        except Exception:
            pass
        raise
    finally:
        last, last_etag, _ = store.get(lease_key)
        if last and last.get('owner') == owner:
            store.put(lease_key, {'owner': owner, 'expires': 0}, match=last_etag)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--profile')
    parser.add_argument('--as-of')
    parser.add_argument('--max-seconds', type=int, default=900)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--max-source-mib', type=int, default=1024)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    if not 32 <= args.max_source_mib <= 2048:
        parser.error('max-source-mib must be 32..2048')
    store = Storage(args.bucket, args.region, args.profile, args.max_source_mib * 1024 * 1024)
    result = run(store, as_of=args.as_of, max_seconds=args.max_seconds, workers=args.workers)
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_bytes(encoded(result))
    print(encoded({key: value for key, value in result.items() if key not in ('input_and_result_identities', 'execution_identity')}).decode(), flush=True)
    if result['outcome'] not in ('COMMITTED', 'ALREADY_COMMITTED', 'SOURCE_PENDING'):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
