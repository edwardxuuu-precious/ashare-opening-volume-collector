"""Screen the latest committed private daily universe without provider requests."""
from __future__ import annotations
import argparse
from collections import Counter
import concurrent.futures
from datetime import datetime, timedelta, timezone
import gzip
import io
import json
from pathlib import Path
import re
import threading
import time
import uuid

from backend.chips.calendar import sessions
from .model import (RULE_ID, PARAMETER_SHA256, CONTRACT_SHA256, ORIGINAL_V3_SHA256,
                    SIGNAL_STATES, classify_frame, digest, encoded, iso_day, parameter_values, unknown_detail)

PREFIX = 'data/trend-volume/daily/'
SOURCE_MANIFEST = 'data/chips/daily/manifest.json'
SOURCE_PREFIX = 'data/chips/daily/'
MAX_FRAME_BYTES = 3 * 1024 * 1024
MAX_FRAME_DECODED = 8 * 1024 * 1024
MAX_SOURCE_ROWS = 10000


def now():
    return datetime.now(timezone.utc).isoformat()


def latest_completed_session(moment=None):
    cn = (moment or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=8)))
    eligible = cn.date() if (cn.hour, cn.minute) >= (15, 40) else cn.date() - timedelta(days=1)
    available = sessions('2026-01-01', eligible.isoformat())
    if not available:
        raise ValueError('COMPLETED_OFFICIAL_SESSION_UNAVAILABLE')
    return available[-1]


def implementation_identity():
    root = Path(__file__).parent
    files = {name: digest((root / name).read_text(encoding='utf-8').replace('\r\n', '\n').encode())
             for name in ('types.py', 'frozen_v3.py', 'model.py', 'daily.py')}
    return {'sha256': digest(encoded(files)), 'files': files, 'original_v3_sha256': ORIGINAL_V3_SHA256,
            'parameter_sha256': PARAMETER_SHA256, 'contract_sha256': CONTRACT_SHA256}


class Storage:
    def __init__(self, bucket, region='us-east-1', profile=None, max_source_bytes=1024 * 1024 * 1024):
        import boto3
        from botocore.config import Config
        session = boto3.Session(profile_name=profile, region_name=region)
        self.s3 = session.client('s3', config=Config(connect_timeout=7, read_timeout=25,
                                                   retries={'total_max_attempts': 1}, max_pool_connections=24))
        self.bucket = bucket
        self.max_source_bytes = max_source_bytes
        self.source_bytes = 0
        self.source_reads = 0
        self.lock = threading.Lock()

    def get(self, key, *, max_bytes=MAX_FRAME_BYTES, max_decoded_bytes=MAX_FRAME_DECODED, source=False):
        from botocore.exceptions import ClientError
        if source:
            with self.lock:
                self.source_reads += 1
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response['Error']['Code'] in ('NoSuchKey', '404'):
                return None, None, None
            raise
        body = response['Body']
        try:
            raw = body.read(max_bytes + 1)
        finally:
            body.close()
        if source:
            with self.lock:
                self.source_bytes += len(raw)
                if self.source_bytes > self.max_source_bytes:
                    raise RuntimeError('SOURCE_TRANSFER_BUDGET_EXCEEDED')
        if len(raw) > max_bytes:
            raise ValueError('SOURCE_OBJECT_ENCODED_BOUND')
        if raw[:2] == b'\x1f\x8b':
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(max_decoded_bytes + 1)
        if len(raw) > max_decoded_bytes:
            raise ValueError('SOURCE_OBJECT_DECODED_BOUND')
        return json.loads(raw), response['ETag'], digest(raw)

    def put(self, key, value, *, match=None, new=False, compressed=False):
        raw = encoded(value)
        args = {'Bucket': self.bucket, 'Key': key, 'Body': gzip.compress(raw, mtime=0) if compressed else raw,
                'ContentType': 'application/json; charset=utf-8', 'CacheControl': 'no-store, private',
                'ServerSideEncryption': 'AES256'}
        if compressed:
            args['ContentEncoding'] = 'gzip'
        if match:
            args['IfMatch'] = match
        if new:
            args['IfNoneMatch'] = '*'
        return self.s3.put_object(**args)['ETag']

    def put_immutable(self, key, value):
        from botocore.exceptions import ClientError
        try:
            return self.put(key, value, new=True, compressed=True)
        except ClientError as exc:
            if exc.response['Error']['Code'] not in ('PreconditionFailed', 'ConditionalRequestConflict'):
                raise
            existing, etag, _ = self.get(key, max_bytes=6 * 1024 * 1024, max_decoded_bytes=16 * 1024 * 1024)
            if existing != value:
                raise RuntimeError('IMMUTABLE_TREND_RESULT_CONFLICT') from exc
            return etag


def validate_source(manifest, screening):
    if not isinstance(manifest, dict) or manifest.get('schema') != 'stock.chips.market.manifest.v1':
        raise ValueError('SOURCE_MANIFEST_SCHEMA')
    dataset = manifest.get('dataset_id')
    if not isinstance(dataset, str) or not re.fullmatch(r'[a-f0-9]{16}', dataset) or manifest.get('active_slot') != dataset:
        raise ValueError('SOURCE_SLOT_IDENTITY')
    as_of = manifest.get('as_of')
    iso_day(as_of)
    count = manifest.get('universe_total')
    if type(count) is not int or not 1 <= count <= MAX_SOURCE_ROWS or manifest.get('scanned_count') != count:
        raise ValueError('SOURCE_UNIVERSE_INCOMPLETE')
    if (not isinstance(screening, dict) or screening.get('schema') != 'stock.chips.market.screen.v1'
            or screening.get('dataset_id') != dataset or screening.get('as_of') != as_of):
        raise ValueError('SOURCE_SCREEN_IDENTITY')
    rows = screening.get('rows')
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError('SOURCE_SCREEN_ROW_COUNT')
    codes, stocks = set(), []
    for row in rows:
        code = row.get('security_id') if isinstance(row, dict) else None
        name = row.get('name') if isinstance(row, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code) or code in codes:
            raise ValueError('SOURCE_SCREEN_SECURITY_IDENTITY')
        if not isinstance(name, str) or not 1 <= len(name) <= 80:
            raise ValueError('SOURCE_SCREEN_SECURITY_NAME')
        codes.add(code)
        stocks.append({'code': code, 'name': name})
    return sorted(stocks, key=lambda value: value['code'])


def run(store, *, as_of=None, max_seconds=900, workers=8, progress=print):
    if type(max_seconds) is not int or not 120 <= max_seconds <= 900 or type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError('BOUNDED_RUNTIME_AND_WORKERS_REQUIRED')
    requested = as_of or latest_completed_session()
    iso_day(requested)
    if requested > latest_completed_session() or requested not in sessions('2026-01-01', requested):
        raise ValueError('COMPLETED_OFFICIAL_SESSION_REQUIRED')
    started = time.monotonic()
    deadline = started + max_seconds
    owner = uuid.uuid4().hex
    lease_key = PREFIX + 'lease.json'
    lease, lease_etag, _ = store.get(lease_key)
    if lease and lease.get('expires', 0) > time.time():
        raise RuntimeError('TREND_DAILY_WRITER_BUSY')
    lease_etag = store.put(lease_key, {'owner': owner, 'expires': time.time() + max_seconds + 120},
                           match=lease_etag, new=lease_etag is None)
    current, current_etag = None, None
    manifest, source_dataset, dataset = None, None, None
    scanned, total, output_rows, input_hashes = 0, 0, [], []

    def owned():
        value, _, _ = store.get(lease_key)
        if not value or value.get('owner') != owner or value.get('expires', 0) <= time.time():
            raise RuntimeError('TREND_DAILY_WRITER_LEASE_LOST')

    def status(state, message):
        owned()
        value = {'schema': 'stock.trend-volume.daily.status.v1', 'state': state, 'as_of': requested,
                 'source_dataset_id': source_dataset, 'dataset_id': dataset, 'universe_total': total,
                 'scanned_count': scanned, 'updated_at': now(), 'message': message,
                 'last_committed_date': current.get('as_of') if current else None}
        store.put(PREFIX + 'status.json', value)

    try:
        current, current_etag, _ = store.get(PREFIX + 'manifest.json')
        if current and current.get('as_of', '') > requested:
            raise ValueError('REFUSE_TO_ROLL_BACK_CURRENT_TREND_DATE')
        manifest, _, manifest_sha = store.get(SOURCE_MANIFEST)
        if manifest is None or manifest.get('as_of', '') < requested:
            status('SOURCE_PENDING', '最新交易日的完整行情批次尚未发布，保留上次结果。')
            return {'outcome': 'SOURCE_PENDING', 'requested_as_of': requested,
                    'last_committed_date': current.get('as_of') if current else None}
        if manifest.get('as_of') != requested:
            raise ValueError('SOURCE_DATE_DIFFERS_FROM_REQUESTED_SESSION')
        source_dataset = manifest.get('active_slot')
        if not isinstance(source_dataset, str) or not re.fullmatch(r'[a-f0-9]{16}', source_dataset):
            raise ValueError('SOURCE_SLOT_IDENTITY')
        screening, _, screen_sha = store.get(SOURCE_PREFIX + source_dataset + '/screening.json',
                                             max_bytes=6 * 1024 * 1024, max_decoded_bytes=64 * 1024 * 1024)
        stocks = validate_source(manifest, screening)
        total = len(stocks)
        identity = implementation_identity()
        dataset = digest(encoded({'source_manifest_sha256': manifest_sha, 'source_screening_sha256': screen_sha,
                                  'source_dataset_id': source_dataset, 'as_of': requested,
                                  'execution_identity': identity['sha256']}))[:16]
        if current and current.get('dataset_id') == dataset and current.get('source_dataset_id') == source_dataset:
            scanned = total
            status('COMMITTED', '最新已发布行情批次已完成筛选，规则与输入未变。')
            return {'outcome': 'ALREADY_COMMITTED', 'as_of': requested, 'dataset_id': dataset,
                    'source_dataset_id': source_dataset, 'universe_total': total, 'source_reads': 0}
        status('RUNNING', '正在按冻结V3筛选最新已发布行情。')

        def calculate(stock):
            code, name = stock['code'], stock['name']
            source_key = SOURCE_PREFIX + source_dataset + '/' + code + '.json'
            try:
                value, _, source_sha = store.get(source_key, source=True)
                if value is None:
                    detail = unknown_detail(code=code, name=name, as_of=requested, dataset_id=dataset,
                                            source_dataset_id=source_dataset, reason='SOURCE_FRAME_MISSING')
                else:
                    detail = classify_frame(value, code=code, name=name, as_of=requested, dataset_id=dataset,
                                            source_dataset_id=source_dataset, source_sha256=source_sha)
            except RuntimeError:
                raise
            except (ValueError, UnicodeError, OSError, EOFError) as exc:
                source_sha = None
                detail = unknown_detail(code=code, name=name, as_of=requested, dataset_id=dataset,
                                        source_dataset_id=source_dataset, reason='SOURCE_OBJECT_INVALID:' + type(exc).__name__)
            except Exception as exc:
                from botocore.exceptions import BotoCoreError, ClientError
                if not isinstance(exc, (BotoCoreError, ClientError)):
                    raise
                source_sha = None
                detail = unknown_detail(code=code, name=name, as_of=requested, dataset_id=dataset,
                                        source_dataset_id=source_dataset, reason='SOURCE_OBJECT_READ_FAILED')
            store.put_immutable(PREFIX + dataset + '/' + code + '.json', detail)
            return detail['row'], {'code': code, 'source_key': source_key, 'source_sha256': source_sha,
                                   'result_sha256': digest(encoded(detail))}

        iterator = iter(stocks)
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
                    output_rows.append(row)
                    input_hashes.append(audit)
                    scanned += 1
                if scanned >= 20 and all('SOURCE_OBJECT_READ_FAILED' in row['reasons'] for row in output_rows):
                    raise RuntimeError('SOURCE_STORAGE_UNAVAILABLE_STOPPED')
                if (finished and scanned % 100 < workers) or time.monotonic() - saved_at >= 30:
                    status('RUNNING', '正在按冻结V3筛选最新已发布行情。')
                    if progress:
                        progress(json.dumps({'state': 'RUNNING', 'as_of': requested, 'scanned': scanned, 'total': total}), flush=True)
                    saved_at = time.monotonic()
                fill()
        if scanned != total:
            status('FAILED', '本轮未在时限内完成全名单，未发布部分结果；保留上次完整批次。')
            return {'outcome': 'INCOMPLETE', 'as_of': requested, 'dataset_id': dataset, 'scanned': scanned, 'total': total}
        rows = sorted(output_rows, key=lambda row: row['code'])
        if len(rows) != len({row['code'] for row in rows}) or len(rows) != total:
            raise ValueError('CALCULATED_UNIVERSE_IDENTITY')
        counts = Counter(row['signal_state'] for row in rows)
        summary = {'schema': 'stock.trend-volume.daily.screen.v1', 'dataset_id': dataset,
                   'source_dataset_id': source_dataset, 'as_of': requested, 'rule_id': RULE_ID,
                   'parameter_sha256': PARAMETER_SHA256, 'universe_total': total, 'rows': rows}
        owned()
        store.put_immutable(PREFIX + dataset + '/screening.json', summary)
        saved_screen, _, _ = store.get(PREFIX + dataset + '/screening.json', max_bytes=6 * 1024 * 1024,
                                      max_decoded_bytes=16 * 1024 * 1024)
        if saved_screen != summary:
            raise ValueError('SCREENING_READBACK_MISMATCH')
        latest_source, _, _ = store.get(SOURCE_MANIFEST)
        source_stale = not latest_source or latest_source.get('dataset_id') != source_dataset
        coverage = manifest.get('universe_coverage', 'UNKNOWN')
        limitations = ['筛选当前已发布收盘行情；不包含盘中预判、交易执行或收益评价。',
                       '冻结V3仅检查趋势准备、缩量准备和放量大涨；新的下跌持续结构定义尚未采用。',
                       '缺失、过期、损坏或历史不足的数据不补造为零；无有效缩量基准时放量倍数保持未知。',
                       '观察中表示前5个有效交易日内存在准备日，尚未同时满足放量与大涨，不等于已确认反转。']
        if coverage in ('SITE_CATALOG_UNVERIFIED', 'UNKNOWN'):
            limitations.append('覆盖当前网站股票清单，尚未独立核定全部在市证券，不宣称全A股完整覆盖。')
        if source_stale:
            limitations.append('行情服务已有更新批次，本次仍绑定所标注的完整来源批次，等待下一次自动更新。')
        publication = {'schema': 'stock.trend-volume.daily.manifest.v1', 'dataset_id': dataset,
                       'source_dataset_id': source_dataset, 'as_of': requested, 'requested_as_of': requested,
                       'generated_at': now(), 'universe_total': total,
                       'counts': {state: counts[state] for state in SIGNAL_STATES},
                       'source_known_count': sum(row['source_status'] == 'KNOWN' for row in rows),
                       'rule_id': RULE_ID, 'parameter_sha256': PARAMETER_SHA256, 'contract_sha256': CONTRACT_SHA256,
                       'source_generation_stale': source_stale,
                       'scope': '当前网站股票清单（未独立核定全部在市）' if coverage in ('SITE_CATALOG_UNVERIFIED', 'UNKNOWN') else '当前已发布沪深京在市股票清单',
                       'rule_parameters': parameter_values(), 'limitations': limitations,
                       'source_universe_coverage': coverage, 'execution_identity': identity,
                       'source_manifest_sha256': manifest_sha, 'source_screening_sha256': screen_sha,
                       'screening_sha256': digest(encoded(summary)),
                       'input_and_result_identities_sha256': digest(encoded(sorted(input_hashes, key=lambda value: value['code']))),
                       'new_market_source_requests': 0, 'can_generate_order': False}
        owned()
        store.put(PREFIX + 'manifest.json', publication, match=current_etag, new=current_etag is None)
        saved_manifest, _, _ = store.get(PREFIX + 'manifest.json')
        if saved_manifest != publication:
            raise ValueError('DAILY_MANIFEST_READBACK_MISMATCH')
        current = publication
        status('COMMITTED', '已完成所标注交易日的当前网站清单筛选。')
        return {'outcome': 'COMMITTED', 'as_of': requested, 'dataset_id': dataset, 'source_dataset_id': source_dataset,
                'universe_total': total, 'counts': publication['counts'], 'source_known_count': publication['source_known_count'],
                'source_generation_stale': source_stale, 'source_universe_coverage': coverage,
                'source_reads': getattr(store, 'source_reads', None), 'source_encoded_bytes': getattr(store, 'source_bytes', None),
                'new_market_source_requests': 0, 'input_and_result_identities': sorted(input_hashes, key=lambda value: value['code']),
                'execution_identity': identity, 'elapsed_seconds': round(time.monotonic() - started, 3)}
    except Exception:
        try:
            status('FAILED', '本轮筛选未完成，未替换上次完整结果。')
        except Exception:
            pass
        raise
    finally:
        last_lease, last_etag, _ = store.get(lease_key)
        if last_lease and last_lease.get('owner') == owner:
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
    public = {key: value for key, value in result.items() if key not in ('input_and_result_identities', 'execution_identity')}
    print(json.dumps(public, ensure_ascii=False), flush=True)
    if result['outcome'] not in ('COMMITTED', 'ALREADY_COMMITTED', 'SOURCE_PENDING'):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
