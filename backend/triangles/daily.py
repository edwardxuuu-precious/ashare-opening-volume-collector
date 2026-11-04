from __future__ import annotations

import argparse
import concurrent.futures
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import uuid

from backend.chips.calendar import sessions

ROOT = Path(__file__).resolve().parents[2]
PREFIX = 'data/triangles/daily/'
MAX_META = 4 * 1024 * 1024
MAX_DAY = 32 * 1024 * 1024
MAX_RESULT = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 3 * 1024 * 1024 * 1024


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def latest_completed_session(moment=None):
    cn = (moment or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=8)))
    eligible = cn.date() if (cn.hour, cn.minute) >= (15, 40) else cn.date() - timedelta(days=1)
    available = sessions('2026-01-01', eligible.isoformat())
    if not available:
        raise ValueError('COMPLETED_OFFICIAL_SESSION_UNAVAILABLE')
    return available[-1]


class SourcePending(Exception):
    pass


class Storage:
    def __init__(self, bucket, region='us-east-1', profile=None):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.s3 = boto3.Session(profile_name=profile, region_name=region).client(
            's3', endpoint_url=f'https://s3.{region}.amazonaws.com',
            config=Config(connect_timeout=7, read_timeout=25, retries={'total_max_attempts': 1}, max_pool_connections=16))
        self.source_bytes = 0
        self.lock = threading.Lock()

    def head(self, key):
        from botocore.exceptions import ClientError
        try:
            response = self.s3.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response['Error']['Code'] in ('404', 'NoSuchKey'):
                return None
            raise
        return {'etag': response['ETag'], 'versionId': response.get('VersionId'), 'bytes': response['ContentLength']}

    def get(self, key, *, limit=MAX_META, expected=None, source=False):
        from botocore.exceptions import ClientError
        args = {'Bucket': self.bucket, 'Key': key}
        if expected:
            args['IfMatch'] = expected['etag']
        try:
            response = self.s3.get_object(**args)
        except ClientError as exc:
            if exc.response['Error']['Code'] in ('404', 'NoSuchKey'):
                return None, None
            if exc.response['Error']['Code'] == 'PreconditionFailed':
                raise SourcePending('SOURCE_VERSION_CHANGED') from None
            raise
        if expected and response.get('VersionId') != expected.get('versionId'):
            response['Body'].close()
            raise SourcePending('SOURCE_VERSION_CHANGED')
        body = response['Body']
        try:
            raw = body.read(limit + 1)
        finally:
            body.close()
        if source:
            with self.lock:
                self.source_bytes += len(raw)
                if self.source_bytes > MAX_SOURCE_BYTES:
                    raise RuntimeError('SOURCE_TRANSFER_BUDGET_EXCEEDED')
        if len(raw) > limit:
            raise ValueError('OBJECT_ENCODED_BOUND')
        if raw[:2] == b'\x1f\x8b':
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(MAX_RESULT + 1)
            if len(raw) > MAX_RESULT:
                raise ValueError('OBJECT_DECODED_BOUND')
        return raw, {'etag': response['ETag'], 'versionId': response.get('VersionId'), 'bytes': response['ContentLength']}

    def put(self, key, raw, *, match=None, new=False, compressed=False):
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

    def put_immutable(self, key, raw):
        from botocore.exceptions import ClientError
        try:
            self.put(key, raw, new=True, compressed=True)
        except ClientError as exc:
            if exc.response['Error']['Code'] not in ('PreconditionFailed', 'ConditionalRequestConflict'):
                raise
        actual, _ = self.get(key, limit=MAX_RESULT)
        if actual != raw:
            raise RuntimeError('IMMUTABLE_PUBLICATION_READBACK_MISMATCH')


def source_plan(manifest, requested):
    if not isinstance(manifest, dict) or manifest.get('scope') != 'full':
        raise ValueError('FULL_SOURCE_MANIFEST_REQUIRED')
    calendar = manifest.get('tradingDates')
    if not isinstance(calendar, list) or not calendar:
        raise ValueError('SOURCE_CALENDAR_REQUIRED')
    for day in calendar:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError('SOURCE_CALENDAR_DATE')
    calendar = sorted(set(day for day in calendar if day <= requested))
    if not calendar or calendar[-1] != requested:
        raise SourcePending('LATEST_COMPLETED_PRICE_SESSION_NOT_PUBLISHED')
    if len(calendar) > 2000:
        raise ValueError('FULL_HISTORY_BOUND_EXCEEDED')
    if calendar != sessions(calendar[0], requested):
        raise ValueError('SOURCE_CALENDAR_OMITS_OFFICIAL_SESSION')
    entries = manifest.get('dates')
    if not isinstance(entries, list):
        raise ValueError('SOURCE_DATE_DIRECTORY_REQUIRED')
    saved = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('date'), str):
            raise ValueError('SOURCE_DATE_DIRECTORY_FORMAT')
        day = entry['date']
        if date.fromisoformat(day).isoformat() != day or entry.get('file') != day + '.json' or day in saved:
            raise ValueError('SOURCE_DATE_DIRECTORY_IDENTITY')
        if day <= requested and entry.get('status') != 'not_downloaded':
            if day not in calendar:
                raise ValueError('SAVED_DATE_OUTSIDE_CALENDAR')
            saved[day] = entry
    latest = saved.get(requested)
    if (not latest or latest.get('scope') != 'full' or latest.get('priceDataComplete') is not True
            or latest.get('status') != 'complete' or latest.get('priceMissing') != 0):
        raise SourcePending('LATEST_COMPLETED_PRICE_SESSION_NOT_READY')
    return calendar, saved


def node_identity(node='node'):
    value = subprocess.run([node, '--experimental-strip-types', str(ROOT / 'tooling/build-triangle-daily.mjs'), '--identity'],
                           capture_output=True, text=True, timeout=30, check=True)
    result = json.loads(value.stdout)
    result['publisher_sha256'] = digest(Path(__file__).read_bytes())
    return result


def build(input_dir, output_dir, *, dataset_id, as_of, source, timeout, node='node'):
    source_file = input_dir / 'triangle-source.json'
    source_file.write_bytes(encoded(source))
    command = [node, '--experimental-strip-types', str(ROOT / 'tooling/build-triangle-daily.mjs'),
               '--input', str(input_dir), '--out', str(output_dir), '--dataset-id', dataset_id,
               '--as-of', as_of, '--source', str(source_file)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('TRIANGLE_ENGINE_BUILD_FAILED: ' + result.stderr[-1000:])
    publication = json.loads((output_dir / 'manifest.json').read_bytes())
    if publication.get('dataset_id') != dataset_id or publication.get('as_of') != as_of:
        raise ValueError('BUILDER_PUBLICATION_IDENTITY')
    return publication


def run(store, *, as_of=None, max_seconds=900, workers=8, cache_dir=None, builder=build, identity=None, progress=print):
    if type(max_seconds) is not int or not 120 <= max_seconds <= 900 or type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError('BOUNDED_RUNTIME_AND_WORKERS_REQUIRED')
    requested = as_of or latest_completed_session()
    if requested > latest_completed_session() or requested not in sessions('2026-01-01', requested):
        raise ValueError('COMPLETED_OFFICIAL_SESSION_REQUIRED')
    deadline = time.monotonic() + max_seconds
    owner, lease_key = uuid.uuid4().hex, PREFIX + 'lease.json'
    lease_raw, lease_meta = store.get(lease_key)
    lease = json.loads(lease_raw) if lease_raw else None
    if lease and lease.get('expires', 0) > time.time():
        raise RuntimeError('TRIANGLE_DAILY_WRITER_BUSY')
    lease_etag = store.put(lease_key, encoded({'owner': owner, 'expires': time.time() + max_seconds + 120}),
                           match=lease_meta['etag'] if lease_meta else None, new=lease_meta is None)
    current, current_meta, dataset_id = None, None, None

    def check():
        if time.monotonic() >= deadline - 5:
            raise TimeoutError('TRIANGLE_DAILY_RUNTIME_EXCEEDED')
        raw, _ = store.get(lease_key)
        value = json.loads(raw) if raw else None
        if not value or value.get('owner') != owner or value.get('expires', 0) <= time.time():
            raise RuntimeError('TRIANGLE_DAILY_WRITER_LEASE_LOST')

    def status(state, message):
        check()
        value = {'schema': 'stock.triangles.daily.status.v1', 'state': state, 'as_of': requested,
                 'updated_at': now(), 'dataset_id': dataset_id,
                 'last_committed_date': current.get('as_of') if current else None, 'message': message}
        store.put(PREFIX + 'status.json', encoded(value))

    try:
        raw, current_meta = store.get(PREFIX + 'manifest.json')
        current = json.loads(raw) if raw else None
        if current and current.get('as_of', '') > requested:
            raise ValueError('REFUSE_TO_ROLL_BACK_TRIANGLE_DATE')
        manifest_raw, manifest_meta = store.get('data/manifest.json', source=True)
        stocks_raw, stocks_meta = store.get('data/stocks.json', source=True)
        if manifest_raw is None or stocks_raw is None:
            raise SourcePending('RAW_SOURCE_DIRECTORY_UNAVAILABLE')
        manifest, stocks = json.loads(manifest_raw), json.loads(stocks_raw)
        calendar, saved = source_plan(manifest, requested)
        if not isinstance(stocks, dict) or not isinstance(stocks.get('rows'), list) or not 1 <= len(stocks['rows']) <= 10000:
            raise ValueError('CURRENT_STOCK_UNIVERSE_REQUIRED')
        heads = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for day, head in pool.map(lambda day: (day, store.head('data/' + saved[day]['file'])), sorted(saved)):
                if head is None or not head.get('versionId') or head['bytes'] > MAX_DAY:
                    raise SourcePending('SAVED_DAY_VERSION_UNAVAILABLE')
                heads[day] = head
        check()
        execution = identity or node_identity()
        versions = {'manifest': {**manifest_meta, 'sha256': digest(manifest_raw)},
                    'stocks': {**stocks_meta, 'sha256': digest(stocks_raw)}, 'dates': heads,
                    'sessions': calendar, 'as_of': requested}
        input_identity = digest(encoded(versions))
        dataset_id = digest(encoded({'input_identity': input_identity, 'execution': execution}))[:16]
        if (current and current.get('dataset_id') == dataset_id
                and current.get('source', {}).get('input_identity') == input_identity):
            verify_source(store, versions, saved)
            status('COMMITTED', '最新完整行情和规则未变化，已发布结果继续可用。')
            return {'outcome': 'ALREADY_COMMITTED', 'dataset_id': dataset_id, 'as_of': requested,
                    'universe_total': current['universe_total'], 'source_bytes': getattr(store, 'source_bytes', None)}
        status('RUNNING', '正在计算默认全市场结果与个股全部已保存日线历史。')
        with tempfile.TemporaryDirectory(prefix='stock-triangle-daily-') as directory:
            folder = Path(directory)
            input_dir, output_dir = folder / 'input', folder / 'output'
            input_dir.mkdir()
            input_dir.joinpath('manifest.json').write_bytes(manifest_raw)
            input_dir.joinpath('stocks.json').write_bytes(stocks_raw)

            def freeze(day):
                if time.monotonic() >= deadline - 90:
                    raise TimeoutError('SOURCE_SNAPSHOT_RUNTIME_EXCEEDED')
                entry, head = saved[day], heads[day]
                raw = None
                if cache_dir:
                    cached = Path(cache_dir)
                    metadata_file = cached / (day + '.meta.json')
                    if metadata_file.is_file() and cached.joinpath(entry['file']).is_file():
                        metadata = json.loads(metadata_file.read_bytes())
                        if metadata.get('versionId') == head['versionId'] and metadata.get('etag') == head['etag']:
                            candidate = cached.joinpath(entry['file']).read_bytes()
                            if len(candidate) == metadata.get('bytes') and digest(candidate) == metadata.get('sha256'):
                                raw = candidate
                if raw is None:
                    raw, _ = store.get('data/' + entry['file'], limit=MAX_DAY, expected=head, source=True)
                if raw is None:
                    raise SourcePending('SAVED_DAY_DISAPPEARED')
                value = json.loads(raw)
                if value.get('date') != day or value.get('generatedAt') != entry.get('generatedAt'):
                    raise SourcePending('SAVED_DAY_NOT_COMMITTED_GENERATION')
                input_dir.joinpath(entry['file']).write_bytes(raw)
                return {'date': day, 'file': entry['file'], **head, 'sha256': digest(raw), 'generated_at': entry.get('generatedAt')}

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                dates = list(pool.map(freeze, sorted(saved)))
            check()
            source = {'input_identity': input_identity, 'manifest_sha256': digest(manifest_raw),
                      'stocks_sha256': digest(stocks_raw), 'manifest_version_id': manifest_meta.get('versionId'),
                      'stocks_version_id': stocks_meta.get('versionId'), 'dates': dates, 'execution': execution}
            publication = builder(input_dir, output_dir, dataset_id=dataset_id, as_of=requested, source=source,
                                  timeout=max(1, int(deadline - time.monotonic() - 45)))
            if publication.get('source') != source or publication.get('universe_total') != len(stocks['rows']):
                raise ValueError('BUILD_SOURCE_OR_UNIVERSE_MISMATCH')
            outputs = [publication['screening'], *publication['bundles']]
            for item in outputs:
                check()
                filename = item['file']
                if not re.fullmatch(r'(?:screening|bundle-\d{3})\.json', filename):
                    raise ValueError('OUTPUT_FILENAME')
                content = output_dir.joinpath(filename).read_bytes()
                if len(content) != item['bytes'] or digest(content) != item['sha256']:
                    raise ValueError('OUTPUT_CONTENT_CHANGED')
                store.put_immutable(PREFIX + dataset_id + '/' + filename, content)
            store.put_immutable(PREFIX + dataset_id + '/manifest.json', encoded(publication))
            check()
            verify_source(store, versions, saved)
            check()
            store.put(PREFIX + 'manifest.json', encoded(publication),
                      match=current_meta['etag'] if current_meta else None, new=current_meta is None)
            readback, _ = store.get(PREFIX + 'manifest.json')
            if readback != encoded(publication):
                raise RuntimeError('COMMITTED_MANIFEST_READBACK_MISMATCH')
            current = publication
            status('COMMITTED', '默认全市场筛选及个股完整历史已发布，页面自动读取。')
            result = {'outcome': 'COMMITTED', 'dataset_id': dataset_id, 'as_of': requested,
                      'universe_total': publication['universe_total'], 'history_sessions': len(publication['detail_sessions']),
                      'bundles': len(publication['bundles']), 'source_bytes': getattr(store, 'source_bytes', None)}
            if progress:
                progress(json.dumps(result), flush=True)
            return result
    except SourcePending as exc:
        status('SOURCE_PENDING', '最新完整行情尚未就绪或正在更新，保留上一批结果。')
        return {'outcome': 'SOURCE_PENDING', 'as_of': requested, 'reason': str(exc),
                'last_committed_date': current.get('as_of') if current else None}
    except Exception:
        try:
            status('FAILED', '本轮未完成发布，保留上一批完整结果。')
        except Exception:
            pass
        raise
    finally:
        try:
            raw, meta = store.get(lease_key)
            if raw and json.loads(raw).get('owner') == owner:
                store.put(lease_key, encoded({'owner': owner, 'expires': 0}), match=meta['etag'])
        except Exception:
            pass


def verify_source(store, versions, saved):
    targets = [('data/manifest.json', versions['manifest']), ('data/stocks.json', versions['stocks'])]
    targets.extend(('data/' + saved[day]['file'], head) for day, head in versions['dates'].items())
    for key, expected in targets:
        actual = store.head(key)
        if not actual or actual.get('versionId') != expected.get('versionId') or actual.get('etag') != expected.get('etag'):
            raise SourcePending('FROZEN_SOURCE_CHANGED_BEFORE_COMMIT')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--profile')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--as-of')
    parser.add_argument('--max-seconds', type=int, default=900)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    result = run(Storage(args.bucket, args.region, args.profile), as_of=args.as_of, max_seconds=args.max_seconds,
                 workers=args.workers, cache_dir=args.cache_dir)
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_bytes(encoded(result))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
