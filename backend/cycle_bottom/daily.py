from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time

from .model import build_snapshot, encoded
from .sources import CN, fetch_sources

PREFIX = 'data/cycle-bottom/'
SCHEDULE = '每日08:40、16:40、22:40（北京时间）更新；页面每60秒检查已发布数据。'
LIMIT = 8 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


class S3Storage:
    def __init__(self, bucket, *, profile=None, region='us-east-1'):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.client = boto3.Session(profile_name=profile, region_name=region).client(
            's3', config=Config(connect_timeout=7, read_timeout=25, retries={'total_max_attempts': 1}))

    def read(self, path):
        from botocore.exceptions import ClientError
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=PREFIX + path)
        except ClientError as error:
            if error.response['Error']['Code'] == 'NoSuchKey':
                return None, None
            raise
        with response['Body'] as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('CYCLE_OBJECT_BOUND')
        if raw[:2] == b'\x1f\x8b':
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError('CYCLE_DECODED_BOUND')
        return raw, response['ETag']

    def write(self, path, raw, *, immutable=False, match=None, compress=False, content_type='application/json; charset=utf-8'):
        from botocore.exceptions import ClientError
        args = {'Bucket': self.bucket, 'Key': PREFIX + path, 'Body': gzip.compress(raw, mtime=0) if compress else raw,
                'ContentType': content_type, 'CacheControl': 'no-store, private',
                'ServerSideEncryption': 'AES256'}
        if compress:
            args['ContentEncoding'] = 'gzip'
        if immutable:
            args['IfNoneMatch'] = '*'
        elif match is not None:
            args['IfMatch'] = match
        try:
            self.client.put_object(**args)
        except ClientError as error:
            if not immutable or error.response['Error']['Code'] not in ('PreconditionFailed', 'ConditionalRequestConflict'):
                raise
        if immutable:
            actual, _ = self.read(path)
            if actual != raw:
                raise ValueError('CYCLE_IMMUTABLE_READBACK_MISMATCH')


def publication_status(snapshot, *, checked_at, last_success_at=None):
    lagging = [source['name'] for source in snapshot['sources'] if source['lastDate'] < snapshot['expectedAsOf']]
    calendar_pending = any(source.get('calendarVerified') is False for source in snapshot['sources'])
    partial = bool(lagging) or calendar_pending
    return {'state': 'partial' if partial else 'ready', 'checkedAt': checked_at,
            'lastSuccessAt': last_success_at or snapshot['generatedAt'], 'schedule': SCHEDULE,
            'message': ('当前年度交易日历待核对，仅按来源实有日期更新，不能确认最新完整交易日。' if calendar_pending else
                        '以下来源尚未更新到最新交易日：' + '、'.join(lagging) + '；只按真实共同日期计算。' if lagging
                        else '可用公开序列已核对到最新观察交易日。Wind二级债基授权分支单独保留待接入状态。')}


def publish(storage, snapshot, raw_files):
    previous_raw, previous_etag = storage.read('manifest.json')
    previous = json.loads(previous_raw) if previous_raw else None
    if previous and previous.get('version') == snapshot['version']:
        previous_analysis, _ = storage.read(snapshot['version'] + '/analysis.json')
        if previous_analysis is None:
            raise ValueError('PUBLISHED_ANALYSIS_MISSING')
        actual = json.loads(previous_analysis)
        if actual.get('version') != snapshot['version']:
            raise ValueError('PUBLISHED_ANALYSIS_IDENTITY')
        storage.write('status.json', encoded(publication_status(actual, checked_at=now(), last_success_at=previous.get('publishedAt') or actual['generatedAt'])))
        return {'state': 'unchanged', 'version': snapshot['version'], 'asOf': snapshot['expectedAsOf']}
    for raw in raw_files.values():
        digest = hashlib.sha256(raw).hexdigest()
        storage.write('raw/' + digest + '.raw', raw, immutable=True, compress=True, content_type='application/octet-stream')
    path = snapshot['version'] + '/analysis.json'
    storage.write(path, encoded(snapshot), immutable=True, compress=True)
    manifest = {'schema': 'stock.cycle-bottom.manifest.v1', 'version': snapshot['version'],
                'generatedAt': snapshot['generatedAt'], 'publishedAt': now(), 'expectedAsOf': snapshot['expectedAsOf']}
    # Do not replace a concurrently published version. Analysis and raw inputs
    # have already been read back before this single publication pointer moves.
    storage.write('manifest.json', encoded(manifest), match=previous_etag, immutable=previous is None)
    actual, _ = storage.read('manifest.json')
    if actual != encoded(manifest):
        raise ValueError('CYCLE_MANIFEST_READBACK_MISMATCH')
    storage.write('status.json', encoded(publication_status(snapshot, checked_at=now(), last_success_at=manifest['publishedAt'])))
    return {'state': 'published', 'version': snapshot['version'], 'asOf': snapshot['expectedAsOf']}


def write_local(directory, snapshot):
    directory = Path(directory)
    path = directory / snapshot['version'] / 'analysis.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(snapshot))
    (directory / 'manifest.json').write_bytes(encoded({'version': snapshot['version']}))
    (directory / 'status.json').write_bytes(encoded(publication_status(snapshot, checked_at=now())))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--bucket')
    parser.add_argument('--profile')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--raw-dir', type=Path)
    parser.add_argument('--include-nonpure', action='store_true')
    parser.add_argument('--max-seconds', type=int, default=240)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args(argv)
    if not (args.out or args.bucket) or not 60 <= args.max_seconds <= 600:
        parser.error('An output or private bucket and bounded 60..600 second budget are required')
    started = time.monotonic()
    storage = S3Storage(args.bucket, profile=args.profile, region=args.region) if args.bucket else None
    try:
        data, sources, raw_files, expected = fetch_sources(include_nonpure=args.include_nonpure)
        if time.monotonic() - started > args.max_seconds:
            raise TimeoutError('CYCLE_SOURCE_DEADLINE')
        snapshot = build_snapshot(data, sources, expected_as_of=expected,
                                  today=datetime.now(CN).date().isoformat())
        if args.raw_dir:
            args.raw_dir.mkdir(parents=True, exist_ok=True)
            for key, raw in raw_files.items():
                (args.raw_dir / (key + '-' + hashlib.sha256(raw).hexdigest()[:16] + '.raw')).write_bytes(raw)
        if time.monotonic() - started > args.max_seconds:
            raise TimeoutError('CYCLE_MODEL_DEADLINE')
        if args.out:
            write_local(args.out, snapshot)
        result = publish(storage, snapshot, raw_files) if storage else {'state': 'local', 'version': snapshot['version'], 'asOf': expected}
        result.update(sourceDates={source['code']: source['lastDate'] for source in sources},
                      elapsedSeconds=round(time.monotonic() - started, 3), windAutomated=False)
        if args.receipt:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_bytes(encoded(result))
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        if storage:
            status = {'state': 'failed', 'checkedAt': now(), 'lastSuccessAt': None,
                      'message': '本轮数据核对未完成，保留上次已发布序列。错误类型：' + type(error).__name__,
                      'schedule': SCHEDULE}
            try:
                previous, _ = storage.read('manifest.json')
                if previous:
                    previous = json.loads(previous)
                    status['lastSuccessAt'] = previous.get('publishedAt') or previous.get('generatedAt')
                storage.write('status.json', encoded(status))
            except Exception:
                pass
        raise


if __name__ == '__main__':
    main()
