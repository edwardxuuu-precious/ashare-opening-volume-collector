"""Date-bound concentration and strict trailing declines from saved inputs only."""
from __future__ import annotations

import hashlib
import json
import math
import re

from .calendar import SOURCES, sessions as calendar_sessions
from .eastmoney_model import VERSION, WINDOW, calculate
from .listing import trusted_listing
from .model import Bar, iso_day, number
from .ohlc4_input import prepare_eastmoney, validate_bar

GROUPS = (3, 5, 10, 15, 20)
RULE = 'CENTRAL_90_STRICT_DECLINE_INDEPENDENT_210_V1'
SCHEMA = 'stock.chips.concentration.v1'
WINDOW_POLICY = 'EACH_DATE_INDEPENDENT_TRAILING_210_OBSERVED_BARS'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def trailing_sessions(as_of):
    days = calendar_sessions(as_of[:4] + '-01-01', iso_day(as_of))
    if len(days) < 21 or days[-1] != as_of:
        raise ValueError('TWENTY_ONE_COMPLETED_MARKET_SESSIONS_REQUIRED')
    return days[-21:]


def concentration_value(profile):
    if not isinstance(profile, list) or not 0 < len(profile) <= 100000:
        raise ValueError('COMPLETE_PRICE_PROFILE_REQUIRED')
    points = []
    previous = -1.0
    for row in profile:
        price, share = float(row['price_cny']), float(row['share_percent'])
        if not math.isfinite(price) or not math.isfinite(share) or price <= 0 or price <= previous or not 0 <= share <= 100:
            raise ValueError('INVALID_PRICE_PROFILE')
        points.append((price, share))
        previous = price
    total = math.fsum(share for _, share in points)
    if abs(total - 100) > 1e-6:
        raise ValueError('INCOMPLETE_PRICE_PROFILE')
    low = high = None
    cumulative = []
    tolerance = total * 16 * math.ulp(1.0)
    for price, share in points:
        if not share:
            continue
        cumulative.append(share)
        mass = math.fsum(cumulative)
        if low is None and mass + tolerance >= total * .05:
            low = price
        if mass + tolerance >= total * .95:
            high = price
            break
    if low is None or high is None or low + high <= 0:
        raise ValueError('CONCENTRATION_COST_RANGE_UNAVAILABLE')
    return (high - low) / (high + low) * 100


def decisions(values, reasons):
    if len(values) != 21:
        raise ValueError('EXACT_SESSION_AXIS_REQUIRED')
    if any(v is not None and (type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 100) for v in values):
        raise ValueError('FINITE_CONCENTRATION_VALUES_REQUIRED')
    groups = {}
    for count in GROUPS:
        unknown = False
        state = 'PASS'
        for index in range(21 - count, 21):
            before, after = values[index - 1], values[index]
            if reasons.get(str(index)) == 'NO_TRADE' or (before is not None and after is not None and after >= before):
                state = 'FAIL'
                break
            if before is None or after is None:
                unknown = True
        groups[str(count)] = 'UNKNOWN' if state == 'PASS' and unknown else state
    streak = 0
    boundary = 'WINDOW_LIMIT'
    for index in range(20, 0, -1):
        before, after = values[index - 1], values[index]
        if reasons.get(str(index)) == 'NO_TRADE':
            boundary = 'NO_TRADE'
            break
        if before is None or after is None:
            boundary = 'UNAVAILABLE'
            break
        if after >= before:
            boundary = 'NON_DECREASE'
            break
        streak += 1
    return {'groups': groups, 'streak_days': streak, 'streak_boundary': boundary}


def unavailable_row(code, name, reason, close=None):
    values = [None] * 21
    reasons = {str(i): reason for i in range(21)}
    return {'security_id': code, 'name': name, 'close_cny': close, 'values': values,
            'point_reasons': reasons, 'short_history_days': 0, **decisions(values, reasons)}


def _published_bars(frame):
    rows = frame.get('history')
    if not isinstance(rows, list) or not 0 < len(rows) <= 500:
        raise ValueError('PUBLISHED_HISTORY_REQUIRED')
    bars = []
    for row in rows:
        bar = Bar(iso_day(row['date']), *(number(row[k]) for k in ('open', 'high', 'low', 'close', 'volume_shares', 'amount_cny', 'turnover_percent')))
        if bars and bars[-1].source_date >= bar.source_date:
            raise ValueError('ORDERED_UNIQUE_HISTORY_REQUIRED')
        if bar.source_date > frame['as_of']:
            raise ValueError('HISTORY_AFTER_PUBLICATION')
        validate_bar(bar)
        bars.append(bar)
    return bars


def needs_archive(frame, days):
    if frame.get('chip_status') != 'READY':
        return False
    bars = _published_bars(frame)
    audit = frame.get('source_audit', {})
    complete = (audit.get('used_rows') == len(bars)
                and audit.get('used_first_source_date') == bars[0].source_date
                and audit.get('used_last_source_date') == bars[-1].source_date)
    return not complete and len([b for b in bars if b.source_date <= days[0]]) < WINDOW


def concentration_row(frame, *, sessions, archived_raw=None):
    code = frame.get('security_id')
    if (frame.get('schema') != 'stock.chips.market.stock.v1' or not isinstance(code, str)
            or not re.fullmatch(r'\d{6}', code) or frame.get('model', {}).get('version') != VERSION
            or frame.get('model', {}).get('historical_turnover_decay_coefficient') != '2'
            or sessions != trailing_sessions(frame['as_of'])):
        raise ValueError('CONCENTRATION_FRAME_IDENTITY_MISMATCH')
    latest = frame.get('snapshots', {}).get(frame['as_of'], {})
    close = latest.get('close_cny')
    if frame.get('chip_status') != 'READY':
        return unavailable_row(code, frame['name'], 'MODEL_NOT_READY', close)
    bars = _published_bars(frame)
    need_archive = needs_archive(frame, sessions)
    decoded = None
    if archived_raw is not None:
        if hashlib.sha256(archived_raw).hexdigest() != frame.get('source', {}).get('sha256'):
            raise ValueError('CONCENTRATION_SOURCE_HASH_MISMATCH')
        decoded = json.loads(archived_raw)
    audit = frame.get('source_audit', {})
    no_trade = set(audit.get('confirmed_no_trade_sessions', []))
    if latest.get('market_session_status') == 'SOURCE_CONFIRMED_NO_TRADE':
        no_trade.add(frame['as_of'])
    if decoded:
        no_trade.update(decoded.get('confirmed_no_trade_sessions', []))
    context = frame.get('listing_context', {})
    listed = trusted_listing({'code': code, 'name': frame['name'], 'listed': context.get('listed'),
                              'listing_evidence': context.get('evidence')}, frame['as_of'])
    values, reasons, short = [], {}, 0
    for index, day in enumerate(sessions):
        if listed and day < listed:
            values.append(None); reasons[str(index)] = 'PRE_LISTING'; continue
        eligible = [b for b in bars if b.source_date <= day]
        if decoded is not None and (len(eligible) < WINDOW or not eligible or eligible[-1].source_date != day):
            normalized = encoded({'rc': decoded.get('rc'), 'data': decoded.get('data')})
            try:
                eligible, _ = prepare_eastmoney(normalized, expected_code=code, cutoff=day, allow_valid_suffix=True)
            except ValueError:
                values.append(None); reasons[str(index)] = 'HISTORICAL_SOURCE_UNAVAILABLE'; continue
        if day in no_trade or (eligible and eligible[-1].source_date == day and eligible[-1].volume_shares == 0):
            values.append(None); reasons[str(index)] = 'NO_TRADE'; continue
        if not eligible or eligible[-1].source_date != day:
            values.append(None); reasons[str(index)] = 'SOURCE_SESSION_MISSING'; continue
        if need_archive and decoded is None and len(eligible) < WINDOW:
            values.append(None); reasons[str(index)] = 'ARCHIVED_WARMUP_UNAVAILABLE'; continue
        window = eligible[-WINDOW:]
        try:
            snapshot = calculate(window, security_id=code, window_start=window[0].source_date, window_rows=len(window))
            if day == frame['as_of'] and any(snapshot.get(key) != latest.get(key) for key in ('raw_node_masses', 'price_grid', 'profile')):
                values.append(None); reasons[str(index)] = 'SEALED_SNAPSHOT_MISMATCH'; continue
            values.append(concentration_value(snapshot['profile']))
            short += int(len(window) < WINDOW)
        except (ValueError, ArithmeticError):
            values.append(None); reasons[str(index)] = 'MODEL_CALCULATION_UNAVAILABLE'
    return {'security_id': code, 'name': frame['name'], 'close_cny': close, 'values': values,
            'point_reasons': reasons, 'short_history_days': short, **decisions(values, reasons)}


def concentration_document(manifest, rows, *, input_fingerprint):
    if (manifest.get('coefficient') != 2 or manifest.get('model_version') != VERSION
            or manifest.get('active_slot') != manifest.get('dataset_id')):
        raise ValueError('CONCENTRATION_MANIFEST_IDENTITY_MISMATCH')
    ordered = sorted(rows, key=lambda row: row['security_id'])
    if len(ordered) != manifest['universe_total'] or len({r['security_id'] for r in ordered}) != len(ordered):
        raise ValueError('COMPLETE_UNIQUE_UNIVERSE_REQUIRED')
    for row in ordered:
        if {**decisions(row['values'], row['point_reasons'])} != {k: row[k] for k in ('groups', 'streak_days', 'streak_boundary')}:
            raise ValueError('CONCENTRATION_ROW_DECISION_MISMATCH')
    counts = {str(n): {state: sum(r['groups'][str(n)] == state for r in ordered)
                         for state in ('PASS', 'FAIL', 'UNKNOWN')} for n in GROUPS}
    return {'schema': SCHEMA, 'rule_version': RULE, 'dataset_id': manifest['dataset_id'],
            'active_slot': manifest['active_slot'], 'as_of': manifest['as_of'], 'coefficient': 2,
            'model_version': VERSION, 'window_policy': WINDOW_POLICY,
            'sessions': trailing_sessions(manifest['as_of']), 'calendar_sources': SOURCES,
            'universe_total': manifest['universe_total'], 'processed_count': len(ordered),
            'source_universe_coverage': manifest.get('universe_coverage', 'UNKNOWN'),
            'input_fingerprint': input_fingerprint, 'groups': counts, 'rows': ordered}
