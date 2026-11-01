"""Bounded, resumable derivation from immutable saved chip frames. No sources."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import time

from .concentration import (RULE, VERSION, concentration_document, concentration_row,
                            decisions, encoded, needs_archive, trailing_sessions)
from .display import response_upper_bound
from .history import load_archive

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = 'data/chips/daily/manifest.json'
CHUNK_SIZE = 64
MAX_ENCODED = 16 * 1024 * 1024
MAX_DECODED = 32 * 1024 * 1024
SAFE_RESPONSE = int(5.3 * 1024 * 1024)
RPC_DRAIN_SECONDS = 30


class BudgetExpired(RuntimeError):
    pass


class PublicationUncertain(RuntimeError):
    def __init__(self, key, fingerprint, cause):
        super().__init__('CONCENTRATION_PUBLICATION_UNCERTAIN')
        self.key = key
        self.fingerprint = fingerprint
        self.cause_type = type(cause).__name__


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def source_closure():
    names = ('concentration.py', 'concentration_runner.py', 'eastmoney_model.py',
             'model.py', 'ohlc4_input.py', 'calendar.py', 'history.py', 'listing.py', 'display.py')
    return {name: hashlib.sha256((ROOT / 'backend/chips' / name).read_bytes()).hexdigest() for name in names}


class Storage:
    def __init__(self, bucket, region='us-east-1', profile=None):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.deadline = None
        self.s3 = boto3.Session(profile_name=profile, region_name=region).client('s3',
            config=Config(connect_timeout=5, read_timeout=10, retries={'total_max_attempts': 1}))

    def admit_rpc(self):
        if self.deadline is not None and time.monotonic() >= self.deadline - RPC_DRAIN_SECONDS:
            raise BudgetExpired('CONCENTRATION_RPC_ADMISSION_BUDGET_EXHAUSTED')

    def get(self, key):
        self.admit_rpc()
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('NoSuchKey', '404'):
                return None
            raise
        try:
            raw = response['Body'].read(MAX_ENCODED + 1)
        finally:
            response['Body'].close()
        if len(raw) > MAX_ENCODED:
            raise ValueError('CONCENTRATION_ENCODED_READ_BOUND')
        if raw[:2] == b'\x1f\x8b':
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(MAX_DECODED + 1)
        if len(raw) > MAX_DECODED:
            raise ValueError('CONCENTRATION_DECODED_READ_BOUND')
        return json.loads(raw)

    def create(self, key, value):
        raw = encoded(value)
        if len(raw) > MAX_DECODED:
            raise ValueError('CONCENTRATION_OBJECT_WRITE_BOUND')
        self.admit_rpc()
        try:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=gzip.compress(raw, mtime=0),
                ContentType='application/json; charset=utf-8', ContentEncoding='gzip',
                CacheControl='no-store', IfNoneMatch='*')
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') not in ('PreconditionFailed', '412', 'ConditionalRequestConflict', '409'):
                raise
            if self.get(key) != value:
                raise ValueError('CONCENTRATION_IMMUTABLE_OBJECT_CONFLICT') from exc
        if self.get(key) != value:
            raise ValueError('CONCENTRATION_PRIVATE_READBACK_MISMATCH')

    def archive(self, frame):
        owner = self
        class ArchiveReader:
            def get_object(self, **kwargs):
                owner.admit_rpc()
                return owner.s3.get_object(**kwargs)
        raw, _ = load_archive(ArchiveReader(), self.bucket, frame)
        return raw


def validate_source(manifest, screening, dataset=None):
    if (not isinstance(manifest, dict) or manifest.get('schema') != 'stock.chips.market.manifest.v1'
            or not re.fullmatch('[a-f0-9]{16}', manifest.get('dataset_id', ''))
            or manifest.get('active_slot') != manifest['dataset_id']
            or manifest.get('coefficient') != 2 or manifest.get('model_version') != VERSION
            or type(manifest.get('universe_total')) is not int or not 0 < manifest['universe_total'] <= 15000
            or manifest.get('scanned_count') != manifest['universe_total']
            or (dataset is not None and manifest['dataset_id'] != dataset)):
        raise ValueError('CONCENTRATION_SOURCE_MANIFEST_INVALID_OR_CHANGED')
    trailing_sessions(manifest['as_of'])
    rows = screening.get('rows', []) if isinstance(screening, dict) else []
    if (not isinstance(screening, dict) or screening.get('schema') != 'stock.chips.market.screen.v1'
            or screening.get('dataset_id') != manifest['dataset_id'] or screening.get('as_of') != manifest['as_of']
            or len(rows) != manifest['universe_total']):
        raise ValueError('CONCENTRATION_SCREEN_DENOMINATOR_INVALID')
    codes = [r.get('security_id') for r in rows]
    if len(set(codes)) != len(codes) or any(not isinstance(c, str) or not re.fullmatch(r'\d{6}', c) for c in codes):
        raise ValueError('CONCENTRATION_SCREEN_MEMBERS_INVALID')
    return sorted(rows, key=lambda row: row['security_id'])


def _read_frame(store, manifest, member, days):
    code = member['security_id']
    frame = store.get(f'data/chips/daily/{manifest["active_slot"]}/{code}.json')
    if frame is None:
        raise ValueError('CONCENTRATION_REFERENCED_PAYLOAD_MISSING:' + code)
    if (frame.get('dataset_id') != manifest['dataset_id'] or frame.get('as_of') != manifest['as_of']
            or frame.get('security_id') != code or frame.get('name') != member.get('name')
            or frame.get('chip_status') != member.get('chip_status')):
        raise ValueError('CONCENTRATION_REFERENCED_FRAME_IDENTITY_MISMATCH:' + code)
    raw = None
    if needs_archive(frame, days):
        try:
            raw = store.archive(frame)
        except ValueError as exc:
            if str(exc) != 'HASH_BOUND_HISTORICAL_SOURCE_UNAVAILABLE':
                raise
    return frame, days, raw, digest(frame)


def _calculate(payload):
    frame, days, raw, sha = payload
    return {'row': concentration_row(frame, sessions=days, archived_raw=raw), 'frame_sha256': sha}


def _bounded_batch(store, manifest, batch, days, *, workers, reader, calculator, deadline, clock):
    pending = {}
    completed = {}
    members = iter(enumerate(batch))
    exhausted = False

    def admit():
        nonlocal exhausted
        while not exhausted and len(pending) < workers and clock() < deadline - RPC_DRAIN_SECONDS:
            item = next(members, None)
            if item is None:
                exhausted = True
                break
            index, member = item
            future = reader.submit(_read_frame, store, manifest, member, days)
            pending[future] = ('read', index)

    try:
        admit()
        while pending:
            remaining = deadline - clock()
            if remaining <= 0:
                return None
            finished, _ = wait(pending, timeout=min(1, remaining), return_when=FIRST_COMPLETED)
            for future in finished:
                stage, index = pending.pop(future)
                value = future.result()
                if stage == 'read':
                    if clock() >= deadline:
                        return None
                    if calculator:
                        pending[calculator.submit(_calculate, value)] = ('calculate', index)
                    else:
                        completed[index] = _calculate(value)
                else:
                    completed[index] = value
            admit()
        return [completed[i] for i in range(len(batch))] if len(completed) == len(batch) else None
    finally:
        for future in pending:
            future.cancel()


def validate_chunk(chunk, fingerprint, index, codes):
    if (not isinstance(chunk, dict) or chunk.get('schema') != 'stock.chips.concentration.chunk.v1'
            or chunk.get('input_fingerprint') != fingerprint or chunk.get('index') != index
            or not isinstance(chunk.get('results'), list)
            or [x.get('row', {}).get('security_id') for x in chunk['results']] != codes):
        raise ValueError('CONCENTRATION_CHECKPOINT_IDENTITY_MISMATCH')
    for result in chunk['results']:
        row = result['row']
        if not re.fullmatch('[a-f0-9]{64}', result.get('frame_sha256', '')):
            raise ValueError('CONCENTRATION_CHECKPOINT_INPUT_HASH_MISSING')
        if decisions(row['values'], row['point_reasons']) != {k: row[k] for k in ('groups', 'streak_days', 'streak_boundary')}:
            raise ValueError('CONCENTRATION_CHECKPOINT_DECISION_MISMATCH')
    return chunk['results']


def run(store, *, dataset=None, max_minutes=20, workers=2, publish=False, out=None,
        clock=time.monotonic, progress=lambda value: print(json.dumps(value), flush=True)):
    if not 1 <= max_minutes <= 20 or not 1 <= workers <= 4:
        raise ValueError('CONCENTRATION_BOUNDED_BUDGET_REQUIRED')
    started = clock()
    deadline = started + max_minutes * 60
    if isinstance(store, Storage):
        store.deadline = deadline
    manifest = store.get(MANIFEST)
    if not manifest:
        raise ValueError('CONCENTRATION_SOURCE_MANIFEST_MISSING')
    screening = store.get(f'data/chips/daily/{manifest["active_slot"]}/screening.json')
    members = validate_source(manifest, screening, dataset)
    days = trailing_sessions(manifest['as_of'])
    closure = source_closure()
    fingerprint = digest({'manifest': manifest, 'members': [r['security_id'] for r in members],
                          'rule': RULE, 'closure': closure, 'sessions': days})
    final_key = f'data/chips/daily/{manifest["active_slot"]}/concentration.json'
    existing = store.get(final_key)
    if existing is not None:
        if (existing.get('input_fingerprint') != fingerprint
                or existing.get('processed_count') != len(members)
                or [r['security_id'] for r in existing.get('rows', [])] != [r['security_id'] for r in members]):
            raise ValueError('CONCENTRATION_EXISTING_RULE_OR_SOURCE_CONFLICT')
        expected = concentration_document(manifest, existing['rows'], input_fingerprint=fingerprint)
        if expected != existing:
            raise ValueError('CONCENTRATION_EXISTING_SUMMARY_INVALID')
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(encoded(existing))
        return {'outcome': 'ALREADY_PRESENT', 'dataset_id': manifest['dataset_id'], 'processed': len(members), 'total': len(members),
                'publication_state': 'verified', 'publication_verified': True,
                'final_key': final_key, 'input_fingerprint': fingerprint}
    results = []
    process_pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    reader = ThreadPoolExecutor(max_workers=workers)
    def checkpoint_result():
        return {'outcome': 'CHECKPOINT_SAVED', 'dataset_id': manifest['dataset_id'], 'processed': len(results),
                'total': len(members), 'publication_state': 'not_requested', 'publication_verified': False,
                'final_key': final_key, 'input_fingerprint': fingerprint,
                'inflight_rpc_drain_seconds': RPC_DRAIN_SECONDS}
    try:
        for index, offset in enumerate(range(0, len(members), CHUNK_SIZE)):
            if clock() >= deadline - RPC_DRAIN_SECONDS:
                return checkpoint_result()
            batch = members[offset:offset + CHUNK_SIZE]
            key = f'collector/chips/concentration/{manifest["dataset_id"]}/{fingerprint}/{index:04d}.json'
            chunk = store.get(key)
            if chunk is not None:
                results.extend(validate_chunk(chunk, fingerprint, index, [r['security_id'] for r in batch]))
                continue
            computed = _bounded_batch(store, manifest, batch, days, workers=workers, reader=reader,
                calculator=process_pool, deadline=deadline, clock=clock)
            if computed is None:
                return checkpoint_result()
            chunk = {'schema': 'stock.chips.concentration.chunk.v1', 'input_fingerprint': fingerprint,
                     'index': index, 'results': computed}
            validate_chunk(chunk, fingerprint, index, [r['security_id'] for r in batch])
            store.create(key, chunk)
            results.extend(computed)
            progress({'outcome': 'PROGRESS', 'dataset_id': manifest['dataset_id'], 'processed': len(results), 'total': len(members)})
    except BudgetExpired:
        return checkpoint_result()
    finally:
        reader.shutdown(wait=False, cancel_futures=True)
        if process_pool:
            process_pool.shutdown(wait=False, cancel_futures=True)
    document = concentration_document(manifest, [x['row'] for x in results], input_fingerprint=fingerprint)
    if len(encoded(document)) > MAX_DECODED or response_upper_bound(document) > SAFE_RESPONSE:
        raise ValueError('CONCENTRATION_AUTHENTICATED_RESPONSE_BOUND_EXCEEDED')
    if store.get(MANIFEST) != manifest or source_closure() != closure:
        raise ValueError('CONCENTRATION_SOURCE_CHANGED_BEFORE_COMMIT')
    if out:
        target = Path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + '.tmp')
        temporary.write_bytes(encoded(document))
        temporary.replace(target)
    if publish:
        try:
            store.create(final_key, document)
        except Exception as exc:
            raise PublicationUncertain(final_key, fingerprint, exc) from exc
    return {'outcome': 'COMMITTED' if publish else 'DRAFT_COMPLETE', 'dataset_id': manifest['dataset_id'],
            'as_of': manifest['as_of'], 'processed': len(results), 'total': len(members),
            'groups': document['groups'], 'response_upper_bound_bytes': response_upper_bound(document),
            'publication_state': 'verified' if publish else 'not_requested', 'publication_verified': publish,
            'final_key': final_key, 'input_fingerprint': fingerprint}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--profile')
    parser.add_argument('--dataset')
    parser.add_argument('--max-minutes', type=int, default=20)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--out')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    try:
        result = run(Storage(args.bucket, args.region, args.profile), dataset=args.dataset,
            max_minutes=args.max_minutes, workers=args.workers, publish=args.publish, out=args.out)
        print(json.dumps(result), flush=True)
        return 3 if result['outcome'] == 'CHECKPOINT_SAVED' else 0
    except PublicationUncertain as exc:
        print(json.dumps({'outcome': 'NEEDS_ATTENTION', 'reason': str(exc), 'cause_type': exc.cause_type,
            'publication_state': 'unknown', 'publication_verified': False, 'final_key': exc.key,
            'input_fingerprint': exc.fingerprint}), flush=True)
        return 1
    except Exception as exc:
        print(json.dumps({'outcome': 'NEEDS_ATTENTION', 'error_type': type(exc).__name__,
                          'reason': str(exc)[:240], 'publication_state': 'not_requested',
                          'publication_verified': False}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
