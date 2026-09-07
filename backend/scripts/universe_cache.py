"""Bounded reuse of a validated current-list snapshot; never renew stale asOf."""
from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import re
import tempfile


PROVIDER = 'AKShare.stock_info_a_code_name'


class UniverseUnavailable(RuntimeError):
    """Neither a fresh complete list nor a sufficiently recent valid cache exists."""


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('asOf must be an ISO calendar date')
    return date.fromisoformat(value)


def _market(code):
    if code.startswith(('60', '68')):
        return 'SH'
    if code.startswith(('00', '30')):
        return 'SZ'
    if code.startswith(('43', '83', '87', '88', '92')):
        return 'BJ'
    raise ValueError('Unsupported A-share market')


def validate_universe(payload, *, minimum_count=1000):
    """Validate the whole snapshot, including old collector-format snapshots.

    Names must be nonempty; codes must be unique. Different codes may legitimately
    have the same display name, so a name is never an identity key.
    """
    if not isinstance(payload, dict):
        raise ValueError('Universe must be an object')
    _day(payload.get('asOf'))
    rows = payload.get('rows')
    if not isinstance(rows, list) or len(rows) < minimum_count or not rows:
        raise ValueError('Universe is empty or below the minimum complete-list size')
    if type(payload.get('total')) is not int or payload['total'] != len(rows):
        raise ValueError('Universe total does not match its rows')
    if payload.get('historicalMembership') is not False:
        raise ValueError('Universe must identify current-list, not historical membership')
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Universe row must be an object')
        code, name = row.get('code'), row.get('name')
        if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
            raise ValueError('Universe code must contain six digits')
        if code in seen:
            raise ValueError('Duplicate universe code')
        seen.add(code)
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError('Universe name must be a nonempty trimmed string')
        expected_market = _market(code)
        if 'market' in row and row['market'] != expected_market:
            raise ValueError('Universe market does not match its code')
    return payload


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as f:
            temporary = Path(f.name)
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_universe(cache_path, as_of, fetcher, *, max_age_days=7, minimum_count=1000):
    """Return validated rows with explicit provenance, atomically caching refreshes.

    ``fetcher`` is called at most once and may return AKShare's DataFrame or a
    list of code/name records. Network timeouts/retries belong to the caller.
    Cache age is measured in calendar days, inclusive of day seven. On fallback
    the original asOf is retained, and the refresh failure is returned and saved.
    An unavailable/invalid cache does not prevent a successful fresh fetch.
    """
    today = _day(as_of)
    if type(max_age_days) is not int or not 0 <= max_age_days <= 7:
        raise ValueError('Cache max_age_days must be between zero and seven')
    if type(minimum_count) is not int or minimum_count < 1:
        raise ValueError('minimum_count must be a positive integer')
    path = Path(cache_path)
    cached, cache_error, age = None, None, None
    try:
        candidate = validate_universe(json.loads(path.read_text(encoding='utf-8')),
                                      minimum_count=minimum_count)
        age = (today - _day(candidate['asOf'])).days
        if age < 0:
            raise ValueError('Cache asOf is in the future')
        cached = candidate
    except (OSError, ValueError, TypeError) as error:
        cache_error = f'{type(error).__name__}: {error}'
    if cached is not None and age == 0:
        result = dict(cached, source='cache_same_day', cacheAgeDays=0)
        result.pop('refreshError', None)
        return result
    try:
        fetched = fetcher()
        rows = fetched.to_dict('records') if hasattr(fetched, 'to_dict') else fetched
        if not isinstance(rows, list):
            raise ValueError('Fetcher must return records or a DataFrame')
        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Fetched universe row must be an object')
            code = row.get('code')
            if type(code) is int and code >= 0:
                code = str(code).zfill(6)
            elif isinstance(code, str) and code.isascii() and code.isdigit():
                code = code.zfill(6)
            normalized.append(dict(row, code=code))
        result = validate_universe({'asOf': as_of, 'historicalMembership': False,
                                    'total': len(normalized), 'rows': normalized,
                                    'provider': PROVIDER, 'source': 'akshare_live',
                                    'cacheAgeDays': 0}, minimum_count=minimum_count)
    except Exception as error:
        refresh_error = f'{type(error).__name__}: {error}'[:500]
        if cached is None or age > max_age_days:
            reason = cache_error if cached is None else f'Cache is {age} days old; limit is {max_age_days}'
            raise UniverseUnavailable(f'Universe refresh failed ({refresh_error}); {reason}') from error
        result = dict(cached, source='cache_fallback', cacheAgeDays=age,
                      refreshError=refresh_error, refreshAttemptedAsOf=as_of)
    _write(path, result)
    return result
