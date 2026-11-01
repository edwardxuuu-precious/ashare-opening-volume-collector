"""Bounded V2 derivation and immutable per-stock trace recovery. No vendor IO."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
import hashlib
import json
from pathlib import Path
import re
import time

from .concentration import (RULE as LEGACY_RULE, concentration_document as legacy_document,
                            concentration_row, encoded, needs_archive, trailing_sessions)
from .concentration_runner import (BudgetExpired, CHUNK_SIZE, MANIFEST, MAX_DECODED, RPC_DRAIN_SECONDS,
                                   SAFE_RESPONSE, Storage, digest, source_closure as legacy_closure,
                                   validate_chunk as validate_legacy_chunk, validate_source)
from .concentration_v2 import (RULE, EndpointSource, concentration_document, recent_row,
                               supported_sessions, trace_segment, validate_row)
from .display import response_upper_bound

ROOT = Path(__file__).resolve().parents[2]
LEGACY_DATASET = '7c69a62fbacf8468'
LEGACY_FINGERPRINT = '264c355a8d8e2bb1ef020fa40e022e1e67920e7e1fcbb0397e6805637d2d5271'


class PublicationUncertain(RuntimeError):
    def __init__(self, key, fingerprint, cause):
        super().__init__('CONCENTRATION_V2_PUBLICATION_UNCERTAIN')
        self.key, self.fingerprint, self.cause_type = key, fingerprint, type(cause).__name__


def source_closure():
    return {**legacy_closure(), **{name: hashlib.sha256((ROOT / 'backend/chips' / name).read_bytes()).hexdigest()
            for name in ('concentration_v2.py', 'concentration_v2_runner.py')}}


def read_legacy(store, manifest, members, days):
    key = f'data/chips/daily/{manifest["active_slot"]}/concentration.json'
    document = store.get(key)
    if document is None:
        return None, {'kind': 'FRESH_SAVED_INPUTS'}
    fingerprint = digest({'manifest': manifest, 'members': [r['security_id'] for r in members],
                          'rule': LEGACY_RULE, 'closure': legacy_closure(), 'sessions': days})
    if (manifest['dataset_id'] == LEGACY_DATASET and fingerprint != LEGACY_FINGERPRINT
            or document.get('input_fingerprint') != fingerprint
            or legacy_document(manifest, document.get('rows', []), input_fingerprint=fingerprint) != document
            or [r['security_id'] for r in document['rows']] != [m['security_id'] for m in members]):
        raise ValueError('V2_LEGACY_SUMMARY_OR_SOURCE_CONFLICT')
    results, hashes = [], []
    for index, offset in enumerate(range(0, len(members), CHUNK_SIZE)):
        chunk_key = f'collector/chips/concentration/{manifest["dataset_id"]}/{fingerprint}/{index:04d}.json'
        chunk = store.get(chunk_key)
        batch = members[offset:offset + CHUNK_SIZE]
        checked = validate_legacy_chunk(chunk, fingerprint, index, [r['security_id'] for r in batch])
        if [r['row'] for r in checked] != document['rows'][offset:offset + len(batch)]:
            raise ValueError('V2_LEGACY_CHUNK_ROW_CONFLICT')
        results.extend(checked)
        hashes.append({'key': chunk_key, 'sha256': digest(chunk)})
    return results, {'kind': 'VERIFIED_LEGACY_NUMERIC_ROWS', 'summary_key': key, 'summary_sha256': digest(document),
                     'input_fingerprint': fingerprint, 'chunks': hashes}


def read_frame(store, manifest, member, expected_sha=None):
    code = member['security_id']
    frame = store.get(f'data/chips/daily/{manifest["active_slot"]}/{code}.json')
    if frame is None:
        raise ValueError('V2_REFERENCED_PAYLOAD_MISSING:' + code)
    if (frame.get('dataset_id') != manifest['dataset_id'] or frame.get('as_of') != manifest['as_of']
            or frame.get('security_id') != code or frame.get('name') != member.get('name')
            or frame.get('chip_status') != member.get('chip_status')):
        raise ValueError('V2_REFERENCED_FRAME_IDENTITY_MISMATCH:' + code)
    sha = digest(frame)
    if expected_sha is not None and sha != expected_sha:
        raise ValueError('V2_LEGACY_FRAME_HASH_CONFLICT:' + code)
    EndpointSource(frame)
    return frame, sha


def archive_for(store, frame, days):
    if not needs_archive(frame, days):
        return None
    try:
        return store.archive(frame)
    except ValueError as exc:
        if str(exc) != 'HASH_BOUND_HISTORICAL_SOURCE_UNAVAILABLE':
            raise
        return None


def calculate_recent(payload):
    frame, days, raw = payload
    return concentration_row(frame, sessions=days, archived_raw=raw)


def validate_recent(chunk, fingerprint, index, codes, days):
    if (not isinstance(chunk, dict) or chunk.get('schema') != 'stock.chips.concentration.recent.v2'
            or chunk.get('input_fingerprint') != fingerprint or chunk.get('index') != index
            or not isinstance(chunk.get('results'), list)
            or [r.get('row', {}).get('security_id') for r in chunk['results']] != codes):
        raise ValueError('V2_RECENT_CHECKPOINT_IDENTITY_MISMATCH')
    for result in chunk['results']:
        if not re.fullmatch('[a-f0-9]{64}', result.get('frame_sha256', '')):
            raise ValueError('V2_RECENT_FRAME_HASH_MISSING')
        if recent_row(result['row'], days) != result['row']:
            raise ValueError('V2_RECENT_CHECKPOINT_DECISION_MISMATCH')
    return chunk['results']


def extend_stock(store, manifest, member, recent, axis, prefix, fingerprint, deadline, clock, calculator):
    frame, frame_sha = read_frame(store, manifest, member, recent['frame_sha256'])
    source = EndpointSource(frame)
    raw = None
    if source.requires_archive(axis[0]):
        try:
            raw = store.archive(frame)
        except ValueError as exc:
            if str(exc) != 'HASH_BOUND_HISTORICAL_SOURCE_UNAVAILABLE':
                raise
    # Validate archive identity before reading any resumable numeric state.
    EndpointSource(frame, raw)
    binding = {'input_fingerprint': fingerprint, 'security_id': member['security_id'],
               'recent_sha256': digest(recent), 'frame_sha256': frame_sha,
               'archive_sha256': hashlib.sha256(raw).hexdigest() if raw is not None else None,
               'archive_source_sha256': frame.get('source', {}).get('sha256')}
    previous, parent = None, None
    index = 0
    while True:
        if clock() >= deadline - RPC_DRAIN_SECONDS:
            return None
        key = f'{prefix}/traces/{member["security_id"]}/{index:04d}.json'
        saved = store.get(key)
        if saved is not None:
            if (saved.get('schema') != 'stock.chips.concentration.trace.v2' or saved.get('binding') != binding
                    or saved.get('index') != index or saved.get('parent_sha256') != parent):
                raise ValueError('V2_TRACE_CHECKPOINT_BINDING_CONFLICT')
            state = saved.get('state')
            validate_trace(state, previous, recent['row'], axis)
        else:
            payload = (frame, recent['row'], axis, previous, raw)
            if calculator:
                future = calculator.submit(trace_segment, *payload)
                remaining = deadline - clock()
                if remaining <= 0:
                    future.cancel()
                    return None
                try:
                    state = future.result(timeout=remaining)
                except TimeoutError:
                    future.cancel()
                    return None
            else:
                state = trace_segment(*payload)
            if clock() >= deadline - RPC_DRAIN_SECONDS:
                return None
            validate_trace(state, previous, recent['row'], axis)
            saved = {'schema': 'stock.chips.concentration.trace.v2', 'binding': binding,
                     'index': index, 'parent_sha256': parent, 'state': state}
            store.create(key, saved)
        if saved['state']['complete']:
            validate_row(saved['state']['row'], axis[-21:], axis)
            return {'row': saved['state']['row'], 'frame_sha256': frame_sha, 'trace_sha256': digest(saved)}
        previous, parent = saved['state'], digest(saved)
        index += 1


def validate_trace(state, previous, recent, axis):
    from .concentration_v2 import SEGMENT_SIZE, finish_row
    if not isinstance(state, dict) or not isinstance(state.get('values'), list):
        raise ValueError('V2_TRACE_STATE_INVALID')
    values, reasons = state['values'], state['point_reasons']
    old_values = previous['values'] if previous else recent['values']
    added = len(values) - len(old_values)
    if not 0 <= added <= SEGMENT_SIZE or (added == 0 and len(values) != len(axis)) or values[-len(old_values):] != old_values:
        raise ValueError('V2_TRACE_SEGMENT_CONTINUITY_INVALID')
    old_reasons = previous['point_reasons'] if previous else recent['point_reasons']
    if {str(int(k) - added): v for k, v in reasons.items() if int(k) >= added} != old_reasons:
        raise ValueError('V2_TRACE_REASON_CONTINUITY_INVALID')
    if any(not k.isdigit() or int(k) >= len(values) or values[int(k)] is not None for k in reasons):
        raise ValueError('V2_TRACE_REASON_INVALID')
    if any(v is None and str(i) not in reasons for i, v in enumerate(values)):
        raise ValueError('V2_TRACE_MISSING_REASON')
    row = finish_row(recent, axis[-len(values):], values, reasons)
    complete = len(values) == len(axis) or row['streak_days'] < len(values) - 1
    if state['row'] != row or state['complete'] is not complete or (not complete and added != SEGMENT_SIZE):
        raise ValueError('V2_TRACE_DECISION_MISMATCH')


def run(store, *, dataset=None, max_minutes=20, workers=2, publish=False, out=None,
        clock=time.monotonic, progress=lambda value: print(json.dumps(value), flush=True)):
    if not 1 <= max_minutes <= 20 or not 1 <= workers <= 4:
        raise ValueError('V2_BOUNDED_BUDGET_REQUIRED')
    deadline = clock() + max_minutes * 60
    if isinstance(store, Storage):
        store.deadline = deadline
    manifest = store.get(MANIFEST)
    if manifest is None:
        raise ValueError('V2_SOURCE_MANIFEST_MISSING')
    members = validate_source(manifest, store.get(f'data/chips/daily/{manifest["active_slot"]}/screening.json'), dataset)
    axis = supported_sessions(manifest['as_of']); days = axis[-21:]
    closure = source_closure()
    legacy, lineage = read_legacy(store, manifest, members, days)
    fingerprint = digest({'manifest': manifest, 'members': [r['security_id'] for r in members], 'rule': RULE,
                          'closure': closure, 'sessions': days, 'streak_sessions': axis, 'lineage': lineage})
    final_key = f'data/chips/daily/{manifest["active_slot"]}/concentration-v2.json'
    prefix = f'collector/chips/concentration-v2/{manifest["dataset_id"]}/{fingerprint}'
    base_result = {'dataset_id': manifest['dataset_id'], 'total': len(members), 'final_key': final_key,
                   'input_fingerprint': fingerprint, 'publication_state': 'not_requested', 'publication_verified': False}
    existing = store.get(final_key)
    if existing is not None:
        if existing.get('input_fingerprint') != fingerprint or concentration_document(manifest, existing.get('rows', []), input_fingerprint=fingerprint, lineage=lineage) != existing:
            raise ValueError('V2_EXISTING_RULE_OR_SOURCE_CONFLICT')
        if [r['security_id'] for r in existing['rows']] != [m['security_id'] for m in members]:
            raise ValueError('V2_EXISTING_UNIVERSE_CONFLICT')
        save_output(out, existing)
        return {**base_result, 'outcome': 'ALREADY_PRESENT', 'processed': len(members), 'publication_state': 'verified', 'publication_verified': True}
    recent_results = []; results = {}
    reader = ThreadPoolExecutor(max_workers=workers)
    calculator = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    def checkpoint():
        return {**base_result, 'outcome': 'CHECKPOINT_SAVED', 'processed': len(results),
                'recent_processed': len(recent_results), 'inflight_rpc_drain_seconds': RPC_DRAIN_SECONDS}
    try:
        for index, offset in enumerate(range(0, len(members), CHUNK_SIZE)):
            if clock() >= deadline - RPC_DRAIN_SECONDS:
                return checkpoint()
            batch = members[offset:offset + CHUNK_SIZE]
            key = f'{prefix}/recent/{index:04d}.json'
            chunk = store.get(key)
            if chunk is None:
                computed = []
                for local, member in enumerate(batch):
                    if clock() >= deadline - RPC_DRAIN_SECONDS:
                        return checkpoint()
                    if legacy is not None:
                        item = legacy[offset + local]
                        computed.append({'row': recent_row(item['row'], days), 'frame_sha256': item['frame_sha256']})
                    else:
                        frame, sha = read_frame(store, manifest, member)
                        payload = (frame, days, archive_for(store, frame, days))
                        row = calculator.submit(calculate_recent, payload).result(timeout=max(0.01, deadline - clock())) if calculator else calculate_recent(payload)
                        computed.append({'row': recent_row(row, days), 'frame_sha256': sha})
                chunk = {'schema': 'stock.chips.concentration.recent.v2', 'input_fingerprint': fingerprint, 'index': index, 'results': computed}
                validate_recent(chunk, fingerprint, index, [m['security_id'] for m in batch], days)
                store.create(key, chunk)
            recent_results.extend(validate_recent(chunk, fingerprint, index, [m['security_id'] for m in batch], days))
        candidates = []
        for member, recent in zip(members, recent_results):
            if recent['row']['groups']['20'] == 'PASS':
                candidates.append((member, recent))
            else:
                results[member['security_id']] = recent
        pending = {}; items = iter(candidates)
        while True:
            while len(pending) < workers and clock() < deadline - RPC_DRAIN_SECONDS:
                item = next(items, None)
                if item is None:
                    break
                member, recent = item
                future = reader.submit(extend_stock, store, manifest, member, recent, axis, prefix, fingerprint, deadline, clock, calculator)
                pending[future] = member['security_id']
            if not pending:
                break
            done, _ = wait(pending, timeout=max(0, min(1, deadline - clock())), return_when=FIRST_COMPLETED)
            if clock() >= deadline:
                return checkpoint()
            for future in done:
                code = pending.pop(future)
                value = future.result()
                if value is None:
                    return checkpoint()
                results[code] = value
                progress({'outcome': 'PROGRESS', 'processed': len(results), 'total': len(members), 'security_id': code})
        if len(results) != len(members):
            return checkpoint()
    except (BudgetExpired, TimeoutError):
        return checkpoint()
    finally:
        reader.shutdown(wait=False, cancel_futures=True)
        if calculator:
            calculator.shutdown(wait=False, cancel_futures=True)
    document = concentration_document(manifest, [r['row'] for r in results.values()], input_fingerprint=fingerprint, lineage=lineage)
    if len(encoded(document)) > MAX_DECODED or response_upper_bound(document) > SAFE_RESPONSE:
        raise ValueError('V2_AUTHENTICATED_RESPONSE_BOUND_EXCEEDED')
    if store.get(MANIFEST) != manifest or source_closure() != closure:
        raise ValueError('V2_SOURCE_CHANGED_BEFORE_COMMIT')
    save_output(out, document)
    if publish:
        try:
            store.create(final_key, document)
            if store.get(final_key) != document:
                raise ValueError('V2_FINAL_READBACK_MISMATCH')
        except Exception as exc:
            raise PublicationUncertain(final_key, fingerprint, exc) from exc
    return {**base_result, 'outcome': 'COMMITTED' if publish else 'DRAFT_COMPLETE', 'processed': len(results),
            'as_of': manifest['as_of'], 'groups': document['groups'], 'response_upper_bound_bytes': response_upper_bound(document),
            'publication_state': 'verified' if publish else 'not_requested', 'publication_verified': publish}


def save_output(out, document):
    if out:
        target = Path(out); target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + '.tmp')
        temporary.write_bytes(encoded(document)); temporary.replace(target)


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
        result = run(Storage(args.bucket, args.region, args.profile), dataset=args.dataset, max_minutes=args.max_minutes,
                     workers=args.workers, publish=args.publish, out=args.out)
        print(json.dumps(result), flush=True)
        return 3 if result['outcome'] == 'CHECKPOINT_SAVED' else 0
    except PublicationUncertain as exc:
        report = {'outcome': 'NEEDS_ATTENTION', 'reason': str(exc), 'cause_type': exc.cause_type,
                  'publication_state': 'unknown', 'publication_verified': False, 'final_key': exc.key, 'input_fingerprint': exc.fingerprint}
    except Exception as exc:
        report = {'outcome': 'NEEDS_ATTENTION', 'error_type': type(exc).__name__, 'reason': str(exc)[:240],
                  'publication_state': 'not_requested', 'publication_verified': False}
    print(json.dumps(report), flush=True)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
