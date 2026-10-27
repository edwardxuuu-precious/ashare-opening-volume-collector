"""Bound the Linux production runner and all of its worker descendants.

The runner retains its 285-minute computation budget. One minute of the
existing cleanup allowance lets normal checkpoint/finalization finish before
the complete isolated process group is terminated. No new run is dispatched.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import gzip
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STATUS = 'data/chips/daily/status.json'
LEASE = 'collector/chips/lease.json'
GRACE_SECONDS = 60


def supervise(command, minutes, *, popen=subprocess.Popen, kill_group=None):
    if type(minutes) is not int or not 2 <= minutes <= 285:
        raise ValueError('ORIGINAL_COMPUTE_BUDGET_REQUIRED')
    if kill_group is None:
        if os.name != 'posix':
            raise RuntimeError('PRODUCTION_PROCESS_GROUP_REQUIRES_POSIX')
        kill_group = os.killpg
    child = popen(command, cwd=ROOT, start_new_session=True)
    try:
        return child.wait(timeout=minutes * 60 + GRACE_SECONDS), False, True
    except subprocess.TimeoutExpired:
        try:
            kill_group(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        # The leader can exit before a descendant that ignores SIGTERM. Kill
        # the remaining isolated group even when the leader was already reaped.
        try:
            kill_group(child.pid, getattr(signal, 'SIGKILL', 9))
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return 124, True, False
        return 124, True, True


class Metadata:
    """Best-effort timeout cleanup has its own small, finite network allowance."""
    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    def get(self, key):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('NoSuchKey', '404'):
                return None, None
            raise
        with response['Body'] as body:
            raw = body.read(1000001)
        if len(raw) > 1000000:
            raise ValueError('TIMEOUT_METADATA_BOUND')
        if raw[:2] == b'\x1f\x8b':
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(1000001)
        if len(raw) > 1000000:
            raise ValueError('TIMEOUT_METADATA_BOUND')
        return json.loads(raw), response['ETag']

    def put(self, key, value, *, match):
        self.client.put_object(Bucket=self.bucket, Key=key,
            Body=json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(),
            ContentType='application/json', ServerSideEncryption='AES256',
            CacheControl='no-store, private', IfMatch=match)


def mark_timeout(store, workflow_run_id, minutes, *, timestamp=None, group_stopped=True):
    """Only mark/release the stopped run's exact generation and lease owner."""
    if not isinstance(workflow_run_id, str) or not workflow_run_id.isdigit():
        return {'timeout_status_updated': False, 'reason': 'NO_TRUSTED_RUN_ID'}
    status, status_etag = store.get(STATUS)
    lease, lease_etag = store.get(LEASE)
    if (not isinstance(status, dict) or status.get('workflow_run_id') != workflow_run_id
        or not isinstance(lease, dict) or not status.get('lease_owner')
        or status['lease_owner'] != lease.get('owner')
        or not status_etag or not lease_etag):
        return {'timeout_status_updated': False, 'reason': 'STOPPED_RUN_OWNERSHIP_NOT_ESTABLISHED'}
    if status.get('state') == 'COMMITTED':
        return {'timeout_status_updated': False, 'reason': 'PUBLICATION_ALREADY_COMMITTED'}
    value = {**status, 'state': 'PAUSED_WORKER_TIMEOUT',
        'updated_at': timestamp or datetime.now(timezone.utc).isoformat(),
        'timeout_compute_budget_minutes': minutes, 'timeout_cleanup_grace_seconds': GRACE_SECONDS,
        'checkpoint_preserved': True, 'worker_cleanup_complete': group_stopped}
    store.put(STATUS, value, match=status_etag)
    if group_stopped:
        store.put(LEASE, {**lease, 'expires': 0}, match=lease_etag)
    return {'timeout_status_updated': True, 'as_of': status.get('as_of'),
        'dataset_id': status.get('dataset_id'), 'scanned_count': status.get('scanned_count'),
        'lease_released': group_stopped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--as-of')
    parser.add_argument('--max-minutes', type=int, default=285)
    args = parser.parse_args()
    command = [sys.executable, '-m', 'backend.chips.runner', '--bucket', args.bucket,
        '--region', args.region, '--max-minutes', str(args.max_minutes)]
    if args.as_of:
        command.extend(['--as-of', args.as_of])
    code, timed_out, group_stopped = supervise(command, args.max_minutes)
    if timed_out:
        result = {'outcome': 'WORKER_GROUP_TIMEOUT', 'last_saved_state_untouched': True,
            'budget_minutes': args.max_minutes, 'cleanup_grace_seconds': GRACE_SECONDS,
            'worker_cleanup_complete': group_stopped}
        try:
            import boto3
            from botocore.config import Config
            client = boto3.client('s3', region_name=args.region, config=Config(connect_timeout=3,
                read_timeout=5, retries={'total_max_attempts': 1}))
            result.update(mark_timeout(Metadata(client, args.bucket), os.environ.get('GITHUB_RUN_ID'),
                args.max_minutes, group_stopped=group_stopped))
        except Exception as exc:
            result['timeout_status_error_type'] = type(exc).__name__
        print(json.dumps(result), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
