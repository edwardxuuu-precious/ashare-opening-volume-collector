"""Bounded two-group saved-source derivation. Never collects vendor data."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import hashlib
import json
from pathlib import Path
import time

from .concentration import encoded
from .concentration_runner import (BudgetExpired, MANIFEST, MAX_DECODED, RPC_DRAIN_SECONDS,
                                   SAFE_RESPONSE, Storage, digest, validate_source)
from .concentration_v2_runner import read_frame
from .concentration_two import (RULE, EndpointSource, concentration_document, segment,
                                supported_sessions, validate_state)
from .display import response_upper_bound

ROOT = Path(__file__).resolve().parents[2]
PINNED_DATASET = '7c69a62fbacf8468'
PINNED_FP = '28bd5e849c589ece3e1698351867d2f316bce42de3b59f08423e5d5f1913afe4'
PINNED_SHA = '48ea1a1ac09a212467241afd093f54e8d4b03f6d8da9462b86f080b67aec17e3'
FRESH_LINEAGE = {'kind': 'FRESH_SAVED_INPUTS'}


class PublicationUncertain(RuntimeError):
    def __init__(self, key, fingerprint, cause):
        super().__init__('CONCENTRATION_TWO_PUBLICATION_UNCERTAIN')
        self.key, self.fingerprint, self.cause_type = key, fingerprint, type(cause).__name__


def source_closure():
    from .concentration_v2_runner import source_closure as frozen_closure
    return {**frozen_closure(), **{name: hashlib.sha256((ROOT / 'backend/chips' / name).read_bytes()).hexdigest()
            for name in ('concentration_two.py', 'concentration_two_runner.py')}}


def expected_fingerprint(manifest, members, axis, closure):
    return digest({'manifest': manifest, 'members': [r['security_id'] for r in members],
                   'rule': RULE, 'closure': closure, 'sessions': axis[-21:], 'streak_sessions': axis})


def migration_lineage(manifest):
    return {'kind': 'VERIFIED_V2_NUMERIC_ROWS', 'summary_key': f'data/chips/daily/{manifest["active_slot"]}/concentration-v2.json',
            'summary_sha256': PINNED_SHA, 'input_fingerprint': PINNED_FP}


def validate_lineage(lineage, manifest):
    if lineage == FRESH_LINEAGE:
        return
    if (manifest['dataset_id'] != PINNED_DATASET or manifest['as_of'] != '2026-10-30'
            or manifest['universe_total'] != 5577 or lineage != migration_lineage(manifest)):
        raise ValueError('TWO_LINEAGE_CONFLICT')


def read_verified_v2(store, manifest, members):
    if manifest['dataset_id'] != PINNED_DATASET or manifest['as_of'] != '2026-10-30' or len(members) != 5577:
        raise ValueError('TWO_REUSE_ONLY_PINNED_RELEASE_SOURCE')
    lineage = migration_lineage(manifest)
    old = store.get(lineage['summary_key'])
    if old is None or digest(old) != PINNED_SHA or old.get('input_fingerprint') != PINNED_FP:
        raise ValueError('TWO_PINNED_V2_HASH_CONFLICT')
    from .concentration_v2 import concentration_document as v2_document
    from .concentration_v2 import RULE as V2_RULE
    from .concentration_v2_runner import source_closure as v2_closure
    axis = supported_sessions(manifest['as_of'])
    old_fp = digest({'manifest': manifest, 'members': [r['security_id'] for r in members], 'rule': V2_RULE,
                     'closure': v2_closure(), 'sessions': axis[-21:], 'streak_sessions': axis, 'lineage': old['lineage']})
    if (old_fp != PINNED_FP or old != v2_document(manifest, old.get('rows', []), input_fingerprint=old_fp, lineage=old['lineage'])
            or [r['security_id'] for r in old['rows']] != [m['security_id'] for m in members]):
        raise ValueError('TWO_PINNED_V2_SOURCE_OR_UNIVERSE_CONFLICT')
    rows = [{**row, 'groups': {key: row['groups'][key] for key in ('20', 'LONG')}} for row in old['rows']]
    return rows, lineage


def load_stock(store, manifest, member, axis):
    frame, frame_sha = read_frame(store, manifest, member)
    raw = None
    if EndpointSource(frame).requires_archive(axis[0]):
        try:
            raw = store.archive(frame)
        except ValueError as exc:
            if str(exc) != 'HASH_BOUND_HISTORICAL_SOURCE_UNAVAILABLE':
                raise
    EndpointSource(frame, raw)
    binding = {'security_id': member['security_id'], 'frame_sha256': frame_sha,
               'archive_sha256': hashlib.sha256(raw).hexdigest() if raw is not None else None,
               'archive_source_sha256': frame.get('source', {}).get('sha256')}
    return frame, raw, binding


def run(store, *, dataset=None, max_minutes=20, workers=2, publish=False, out=None,
        reuse_verified_v2=False, clock=time.monotonic, progress=lambda value: print(json.dumps(value), flush=True)):
    if not 1 <= max_minutes <= 20 or not 1 <= workers <= 4:
        raise ValueError('TWO_BOUNDED_BUDGET_REQUIRED')
    deadline = clock() + max_minutes * 60
    if isinstance(store, Storage):
        store.deadline = deadline
    manifest = store.get(MANIFEST)
    if manifest is None:
        raise ValueError('TWO_SOURCE_MANIFEST_MISSING')
    screening_key = f'data/chips/daily/{manifest["active_slot"]}/screening.json'
    screening = store.get(screening_key)
    members = validate_source(manifest, screening, dataset)
    axis = supported_sessions(manifest['as_of']); closure = source_closure()
    fingerprint = expected_fingerprint(manifest, members, axis, closure)
    final_key = f'data/chips/daily/{manifest["active_slot"]}/concentration-two-v1.json'
    prefix = f'collector/chips/concentration-two-v1/{manifest["dataset_id"]}/{fingerprint}'
    base = {'dataset_id': manifest['dataset_id'], 'total': len(members), 'final_key': final_key,
            'input_fingerprint': fingerprint, 'publication_state': 'not_requested', 'publication_verified': False}
    existing = store.get(final_key)
    if existing is not None:
        validate_lineage(existing.get('lineage'), manifest)
        if existing['lineage'] != FRESH_LINEAGE:
            projected, checked_lineage = read_verified_v2(store, manifest, members)
            if existing['rows'] != projected or existing['lineage'] != checked_lineage:
                raise ValueError('TWO_EXISTING_MIGRATION_NUMERIC_CONFLICT')
        if (existing.get('input_fingerprint') != fingerprint
                or concentration_document(manifest, existing.get('rows', []), input_fingerprint=fingerprint, lineage=existing['lineage']) != existing
                or [r['security_id'] for r in existing['rows']] != [m['security_id'] for m in members]):
            raise ValueError('TWO_EXISTING_RULE_OR_SOURCE_CONFLICT')
        save_output(out, existing)
        return {**base, 'outcome': 'ALREADY_PRESENT', 'processed': len(members), 'publication_state': 'verified', 'publication_verified': True}
    rows = {}; lineage = FRESH_LINEAGE
    def checkpoint():
        return {**base, 'outcome': 'CHECKPOINT_SAVED', 'processed': len(rows), 'inflight_rpc_drain_seconds': RPC_DRAIN_SECONDS}
    if reuse_verified_v2:
        migrated, lineage = read_verified_v2(store, manifest, members)
        rows = {row['security_id']: row for row in migrated}
    else:
        pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
        pending = {}; stocks = iter(members)
        def next_segment(stock):
            frame, raw, binding, previous, parent, index = stock
            key = f'{prefix}/stocks/{binding["security_id"]}/{index:04d}.json'
            while clock() < deadline - RPC_DRAIN_SECONDS:
                saved = store.get(key)
                if saved is None:
                    if pool:
                        future = pool.submit(segment, frame, axis, previous, raw)
                        pending[future] = (stock, key)
                        return
                    state = segment(frame, axis, previous, raw)
                    persist(stock, key, state)
                    return
                if (saved.get('schema') != 'stock.chips.concentration.two.segment.v1'
                        or saved.get('input_fingerprint') != fingerprint or saved.get('binding') != binding
                        or saved.get('index') != index or saved.get('parent_sha256') != parent):
                    raise ValueError('TWO_CHECKPOINT_BINDING_CONFLICT')
                validate_state(saved.get('state'), previous, frame, axis)
                if saved['state']['complete']:
                    rows[binding['security_id']] = saved['state']['row']
                    return
                previous, parent, index = saved['state'], digest(saved), index + 1
                stock = frame, raw, binding, previous, parent, index
                key = f'{prefix}/stocks/{binding["security_id"]}/{index:04d}.json'
        ready = []
        def persist(stock, key, state):
            frame, raw, binding, previous, parent, index = stock
            validate_state(state, previous, frame, axis)
            if clock() >= deadline - RPC_DRAIN_SECONDS:
                raise BudgetExpired('TWO_WRITE_DRAIN_RESERVED')
            saved = {'schema': 'stock.chips.concentration.two.segment.v1', 'input_fingerprint': fingerprint,
                     'binding': binding, 'index': index, 'parent_sha256': parent, 'state': state}
            store.create(key, saved)
            if state['complete']:
                rows[binding['security_id']] = state['row']
                progress({'outcome': 'PROGRESS', 'processed': len(rows), 'total': len(members), 'security_id': binding['security_id']})
            else:
                ready.append((frame, raw, binding, state, digest(saved), index + 1))
        try:
            exhausted = False
            while clock() < deadline - RPC_DRAIN_SECONDS:
                while len(pending) < workers and clock() < deadline - RPC_DRAIN_SECONDS:
                    if ready:
                        stock = ready.pop(0)
                    else:
                        member = next(stocks, None)
                        if member is None:
                            exhausted = True
                            break
                        frame, raw, binding = load_stock(store, manifest, member, axis)
                        stock = frame, raw, binding, None, None, 0
                    next_segment(stock)
                    if pool is None and ready:
                        continue
                if not pending:
                    if exhausted and not ready:
                        break
                    continue
                done, _ = wait(pending, timeout=max(0, min(1, deadline - RPC_DRAIN_SECONDS - clock())), return_when=FIRST_COMPLETED)
                for future in done:
                    stock, key = pending.pop(future)
                    persist(stock, key, future.result())
            if len(rows) != len(members):
                return checkpoint()
        except (BudgetExpired, TimeoutError):
            return checkpoint()
        finally:
            if pool:
                pool.shutdown(wait=False, cancel_futures=True)
    document = concentration_document(manifest, list(rows.values()), input_fingerprint=fingerprint, lineage=lineage)
    if len(encoded(document)) > MAX_DECODED or response_upper_bound(document) > SAFE_RESPONSE:
        raise ValueError('TWO_AUTHENTICATED_RESPONSE_BOUND_EXCEEDED')
    if store.get(MANIFEST) != manifest or store.get(screening_key) != screening or source_closure() != closure:
        raise ValueError('TWO_SOURCE_CHANGED_BEFORE_COMMIT')
    save_output(out, document)
    if publish:
        try:
            store.create(final_key, document)
            if store.get(final_key) != document:
                raise ValueError('TWO_FINAL_READBACK_MISMATCH')
        except Exception as exc:
            raise PublicationUncertain(final_key, fingerprint, exc) from exc
    return {**base, 'outcome': 'COMMITTED' if publish else 'DRAFT_COMPLETE', 'processed': len(rows),
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
    parser.add_argument('--reuse-verified-v2', action='store_true', help='Release-only hash-bound Oct30 V2 numeric reuse')
    args = parser.parse_args()
    try:
        result = run(Storage(args.bucket, args.region, args.profile), dataset=args.dataset, max_minutes=args.max_minutes,
                     workers=args.workers, publish=args.publish, out=args.out, reuse_verified_v2=args.reuse_verified_v2)
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
