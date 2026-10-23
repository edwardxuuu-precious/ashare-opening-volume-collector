"""Best-effort, aggregate-only restore failure publication under the caller's lease.

The caller must pass its existing lease-guarded client while it still owns the
writer lease. This module never acquires a lease, publishes market data, modifies
scheduling state, or retries a conditional-write conflict. Its return value is
operational evidence only; a failed restore must still raise its original error.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime


STATUS_KEY = 'data/collection-status.json'
MAX_STATUS_BYTES = 2 * 1024 * 1024
COLLECTION_MODES = frozenset(('auto', 'daily', 'history'))
SUCCESS_TIMESTAMPS = ('latestPublishedAt', 'lastSuccessfulUpdate', 'fullyPublishedAt')


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64 or 'T' not in value:
        raise ValueError('Invalid status timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('Status timestamp must include its timezone')
    return result


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Invalid status date')
    date.fromisoformat(value)
    return value


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate status field')
        value[key] = item
    return value


def _failure_status(previous, updated_at):
    if not isinstance(previous, dict):
        raise ValueError('Invalid previous status')
    identifier = previous.get('id')
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', identifier):
        raise ValueError('Invalid previous status identity')
    completed, total = previous.get('completedStocks'), previous.get('totalStocks')
    if (type(completed) is not int or type(total) is not int or
            not 0 <= completed <= total <= 10000 or total == 0):
        raise ValueError('Invalid previous progress')
    failed_at = _timestamp(updated_at)
    if 'updatedAt' in previous and _timestamp(previous['updatedAt']) > failed_at:
        raise ValueError('Previous status is newer than this failure')
    visible = dict(id=identifier, state='failed', status='failed', outcome='failed',
                   phase='restore', exitReason='restore_failed', errorType='RestoreError',
                   completedStocks=completed, totalStocks=total,
                   latestDate=_date(previous.get('latestDate')), updatedAt=updated_at,
                   message='采集启动失败，已有行情仍可查询')
    # These fields are copied only from existing publication evidence. A target
    # date and the current failure timestamp never become a successful update.
    if 'latestPublishedDate' in previous:
        published_date = previous['latestPublishedDate']
        visible['latestPublishedDate'] = None if published_date is None else _date(published_date)
    for field in SUCCESS_TIMESTAMPS:
        if field in previous:
            value = previous[field]
            if value is not None:
                _timestamp(value)
            visible[field] = value
    return visible


def publish_restore_failure_status(client, bucket, *, mode, stage, error_type, updated_at):
    """Conditionally replace an existing banner; never obscure the restore error.

    ``client`` must already enforce the caller's active lease on ``put_object``.
    Scope is intentionally limited to ordinary collection RestoreError failures.
    Invalid/missing old state, lease loss, access failures, and CAS conflicts all
    return False without any application-level retry or alternate write path.
    """
    if mode not in COLLECTION_MODES or stage != 'restore' or error_type != 'RestoreError':
        return False
    try:
        _timestamp(updated_at)
        response = client.get_object(Bucket=bucket, Key=STATUS_KEY)
        stream = response['Body']
        try:
            if response.get('ContentLength', 0) > MAX_STATUS_BYTES:
                return False
            raw = stream.read(MAX_STATUS_BYTES + 1)
            if len(raw) > MAX_STATUS_BYTES:
                return False
        finally:
            stream.close()
        etag = response.get('ETag')
        if (not isinstance(etag, str) or not 1 <= len(etag) <= 256 or
                '\r' in etag or '\n' in etag):
            return False
        previous = json.loads(raw, object_pairs_hook=_unique_object)
        visible = _failure_status(previous, updated_at)
        body = json.dumps(visible, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(',', ':')).encode('utf-8')
        client.put_object(Bucket=bucket, Key=STATUS_KEY, Body=body,
                          IfMatch=etag, ContentType='application/json; charset=utf-8',
                          CacheControl='no-store', ServerSideEncryption='AES256',
                          Metadata={'sha256': hashlib.sha256(body).hexdigest()})
        return True
    except Exception:
        # Never print source contents, exception text, credentials or tracebacks.
        # The caller retains and re-raises the original RestoreError.
        return False
