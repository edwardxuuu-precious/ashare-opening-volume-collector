"""Publish complete concentration results without advancing the main chip scan."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import gzip
import io
import json
import re
import time

from . import concentration_two_runner as runner
from .concentration import encoded
from .concentration_runner import MAX_DECODED, MAX_ENCODED, SAFE_RESPONSE, Storage, digest, validate_source
from .concentration_two import concentration_document, supported_sessions
from .display import response_upper_bound

PUBLICATION = 'data/chips/daily/concentration-publication.json'
SCHEMA = 'stock.chips.concentration.publication.v1'


class PublicationUncertain(runner.PublicationUncertain):
    def __init__(self, fingerprint, cause):
        super().__init__(PUBLICATION, fingerprint, cause)
        self.args = ('CONCENTRATION_COMPLETE_PUBLICATION_UNCERTAIN',)


class PublicationStorage(Storage):
    def get_publication(self):
        self.admit_rpc()
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=PUBLICATION)
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('NoSuchKey', '404'):
                return None, None
            raise
        try:
            raw = response['Body'].read(MAX_ENCODED + 1)
        finally:
            response['Body'].close()
        if len(raw) > MAX_ENCODED:
            raise ValueError('CONCENTRATION_PUBLICATION_READ_BOUND')
        if raw[:2] == b'\x1f\x8b':
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(MAX_DECODED + 1)
        if len(raw) > MAX_DECODED:
            raise ValueError('CONCENTRATION_PUBLICATION_READ_BOUND')
        return json.loads(raw), response['ETag']

    def replace_publication(self, value, etag):
        self.admit_rpc()
        raw = encoded(value)
        if len(raw) > MAX_DECODED or response_upper_bound(value) > SAFE_RESPONSE:
            raise ValueError('CONCENTRATION_PUBLICATION_RESPONSE_BOUND')
        condition = {'IfMatch': etag} if etag is not None else {'IfNoneMatch': '*'}
        self.s3.put_object(Bucket=self.bucket, Key=PUBLICATION, Body=gzip.compress(raw, mtime=0),
            ContentType='application/json; charset=utf-8', ContentEncoding='gzip',
            CacheControl='no-store', **condition)


def source_identity(source):
    if not isinstance(source, dict):
        raise ValueError('CONCENTRATION_PUBLICATION_SOURCE_INVALID')
    try:
        day = date.fromisoformat(source['as_of'])
        generated = datetime.fromisoformat(source['generated_at'].replace('Z', '+00:00'))
        if generated.tzinfo is None or not re.fullmatch('[a-f0-9]{16}', source['dataset_id']):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError('CONCENTRATION_PUBLICATION_SOURCE_IDENTITY_INVALID') from exc
    return day, generated


def validate_publication(value):
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        raise ValueError('CONCENTRATION_PUBLICATION_INVALID')
    source = value.get('source')
    identity = source_identity(source)
    dataset = source['dataset_id']
    if (any(value.get(key) != source.get(key) for key in ('dataset_id', 'as_of', 'generated_at'))
            or source.get('active_slot') != dataset
            or value.get('payload_key') != f'data/chips/daily/{dataset}/concentration-two-v1.json'
            or any(not isinstance(value.get(key), str) or not re.fullmatch('[a-f0-9]{64}', value[key])
                   for key in ('input_fingerprint', 'payload_sha256'))):
        raise ValueError('CONCENTRATION_PUBLICATION_BINDING_CONFLICT')
    return identity


def publication_decision(current, candidate):
    candidate_identity = validate_publication(candidate)
    if current is None:
        return 'replace'
    current_identity = validate_publication(current)
    if current == candidate:
        return 'verified'
    if current.get('dataset_id') == candidate['dataset_id']:
        raise ValueError('CONCENTRATION_PUBLICATION_SAME_DATASET_CONFLICT')
    if current_identity == candidate_identity:
        raise ValueError('CONCENTRATION_PUBLICATION_SAME_GENERATION_CONFLICT')
    return 'superseded' if current_identity > candidate_identity else 'replace'


def complete_publication(store, source, *, expected_input_fingerprint=None):
    source_identity(source)
    dataset = source['dataset_id']
    screening = store.get(f'data/chips/daily/{dataset}/screening.json')
    members = validate_source(source, screening, dataset)
    payload_key = f'data/chips/daily/{dataset}/concentration-two-v1.json'
    document = store.get(payload_key)
    if not isinstance(document, dict):
        raise ValueError('CONCENTRATION_COMPLETE_PAYLOAD_MISSING')
    fingerprint = runner.expected_fingerprint(
        source, members, supported_sessions(source['as_of']), runner.source_closure()) \
        if expected_input_fingerprint is None else expected_input_fingerprint
    if not isinstance(fingerprint, str) or not re.fullmatch('[a-f0-9]{64}', fingerprint):
        raise ValueError('CONCENTRATION_COMPLETE_FINGERPRINT_INVALID')
    runner.validate_lineage(document.get('lineage'), source)
    if document['lineage'] != runner.FRESH_LINEAGE:
        projected, lineage = runner.read_verified_v2(store, source, members)
        if document.get('rows') != projected or document['lineage'] != lineage:
            raise ValueError('CONCENTRATION_COMPLETE_MIGRATION_CONFLICT')
    rows = document.get('rows', [])
    if (document.get('input_fingerprint') != fingerprint
            or concentration_document(source, rows, input_fingerprint=fingerprint, lineage=document['lineage']) != document
            or [(row.get('security_id'), row.get('name')) for row in rows]
                != [(member['security_id'], member.get('name')) for member in members]
            or len(encoded(document)) > MAX_DECODED or response_upper_bound(document) > SAFE_RESPONSE):
        raise ValueError('CONCENTRATION_COMPLETE_PAYLOAD_CONFLICT')
    return {'schema': SCHEMA, **{key: source[key] for key in ('dataset_id', 'as_of', 'generated_at')},
            'source': source, 'payload_key': payload_key, 'input_fingerprint': fingerprint,
            'payload_sha256': digest(document)}


def publish_complete(store, source, *, expected_input_fingerprint=None):
    candidate = complete_publication(store, source, expected_input_fingerprint=expected_input_fingerprint)
    current, etag = store.get_publication()
    decision = publication_decision(current, candidate)
    if decision != 'replace':
        return {'state': decision, 'key': PUBLICATION, 'dataset_id': current['dataset_id']}
    try:
        store.replace_publication(candidate, etag)
    except Exception as cause:
        try:
            observed, _ = store.get_publication()
            decision = publication_decision(observed, candidate)
        except Exception:
            raise PublicationUncertain(candidate['input_fingerprint'], cause) from cause
        if decision != 'replace':
            return {'state': decision, 'key': PUBLICATION, 'dataset_id': observed['dataset_id']}
        raise PublicationUncertain(candidate['input_fingerprint'], cause) from cause
    try:
        observed, _ = store.get_publication()
        decision = publication_decision(observed, candidate)
        if decision == 'replace':
            raise ValueError('CONCENTRATION_PUBLICATION_READBACK_MISMATCH')
    except Exception as cause:
        raise PublicationUncertain(candidate['input_fingerprint'], cause) from cause
    return {'state': decision, 'key': PUBLICATION, 'dataset_id': observed['dataset_id']}


def run_and_publish(store, *, dataset=None, max_minutes=20, workers=2, publish=False,
                    out=None, reuse_verified_v2=False, clock=time.monotonic, progress=lambda value: print(json.dumps(value), flush=True)):
    if not 1 <= max_minutes <= 20:
        raise ValueError('TWO_BOUNDED_BUDGET_REQUIRED')
    deadline = clock() + max_minutes * 60
    if isinstance(store, Storage):
        store.deadline = deadline
    source = store.get(runner.MANIFEST)
    if source is None:
        raise ValueError('TWO_SOURCE_MANIFEST_MISSING')
    if dataset is not None and source.get('dataset_id') != dataset:
        raise ValueError('CONCENTRATION_SOURCE_MANIFEST_INVALID_OR_CHANGED')
    remaining = (deadline - clock()) / 60
    if remaining < 1:
        raise runner.BudgetExpired('CONCENTRATION_PUBLICATION_BUDGET_EXHAUSTED')
    result = runner.run(store, dataset=source['dataset_id'], max_minutes=remaining, workers=workers,
                        publish=publish, out=out, reuse_verified_v2=reuse_verified_v2, clock=clock, progress=progress)
    if isinstance(store, Storage):
        store.deadline = deadline
    if publish and result['publication_verified']:
        publication = publish_complete(store, source)
        return {**result, 'complete_publication': publication}
    return result


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
    parser.add_argument('--reuse-verified-v2', action='store_true')
    args = parser.parse_args()
    try:
        result = run_and_publish(PublicationStorage(args.bucket, args.region, args.profile), dataset=args.dataset,
            max_minutes=args.max_minutes, workers=args.workers, publish=args.publish, out=args.out,
            reuse_verified_v2=args.reuse_verified_v2)
        print(json.dumps(result), flush=True)
        return 3 if result['outcome'] == 'CHECKPOINT_SAVED' else 0
    except runner.PublicationUncertain as exc:
        report = {'outcome': 'NEEDS_ATTENTION', 'reason': str(exc), 'cause_type': exc.cause_type,
                  'publication_state': 'unknown', 'publication_verified': False, 'final_key': exc.key,
                  'input_fingerprint': exc.fingerprint}
    except Exception as exc:
        report = {'outcome': 'NEEDS_ATTENTION', 'error_type': type(exc).__name__, 'reason': str(exc)[:240],
                  'publication_state': 'not_requested', 'publication_verified': False}
    print(json.dumps(report), flush=True)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
