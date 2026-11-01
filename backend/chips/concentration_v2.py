"""Independent-endpoint concentration with full-precision nonincreasing runs."""
from __future__ import annotations

import hashlib
import json
import math
import re

from .calendar import SOURCES, sessions as calendar_sessions
from .concentration import WINDOW_POLICY, concentration_value, encoded, trailing_sessions
from .eastmoney_model import VERSION, WINDOW, calculate
from .listing import trusted_listing
from .model import Bar, iso_day, number
from .ohlc4_input import prepare_eastmoney, validate_bar

SCHEMA = 'stock.chips.concentration.v2'
RULE = 'CENTRAL_90_NONINCREASING_INDEPENDENT_210_V2'
GROUPS = {'3': 3, '5': 5, '10': 10, '15': 15, '20': 20, 'LONG': 21}
SEGMENT_SIZE = 32


def supported_sessions(as_of):
    trailing_sessions(as_of)
    return calendar_sessions(as_of[:4] + '-01-01', as_of)


def validate_values(values):
    if any(v is not None and (type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100) for v in values):
        raise ValueError('V2_FINITE_CONCENTRATION_VALUES_REQUIRED')


def classify(values, point_reasons):
    validate_values(values)
    result = {}
    for key, count in GROUPS.items():
        unknown = len(values) <= count
        failed = False
        for index in range(max(1, len(values) - count), len(values)):
            before, after = values[index - 1:index + 1]
            if point_reasons.get(str(index)) == 'NO_TRADE' or (before is not None and after is not None and after > before):
                failed = True
                break
            unknown |= before is None or after is None
        result[key] = 'FAIL' if failed else 'UNKNOWN' if unknown else 'PASS'
    return result


def finish_row(recent_row, days, values, reasons):
    """The input series is contiguous and ends at the publication endpoint."""
    if len(days) != len(values) or len(values) < 21:
        raise ValueError('V2_EXACT_SESSION_AXIS_REQUIRED')
    validate_values(values)
    if values[-21:] != recent_row['values']:
        raise ValueError('V2_RECENT_VALUES_CHANGED')
    streak = 0
    boundary = {'kind': 'CALENDAR_LIMIT', 'session': days[0], 'reason': 'SUPPORTED_CALENDAR_START'}
    for index in range(len(values) - 1, 0, -1):
        before, after = values[index - 1:index + 1]
        if reasons.get(str(index)) == 'NO_TRADE':
            boundary = {'kind': 'NO_TRADE', 'session': days[index], 'reason': 'NO_TRADE'}
            break
        if before is None or after is None:
            missing = index if after is None else index - 1
            reason = reasons.get(str(missing), 'SOURCE_SESSION_MISSING')
            boundary = {'kind': 'WARMUP' if reason == 'ARCHIVED_WARMUP_UNAVAILABLE' else 'UNAVAILABLE',
                        'session': days[missing], 'reason': reason}
            break
        if after > before:
            boundary = {'kind': 'INCREASE', 'previous_session': days[index - 1], 'previous_value': before,
                        'anchor_session': days[index], 'anchor_value': after}
            break
        streak += 1
    row = {key: recent_row[key] for key in ('security_id', 'name', 'close_cny', 'values', 'point_reasons', 'short_history_days')}
    row.update(groups=classify(values, reasons), streak_days=streak, streak_boundary=boundary,
               streak_start_session=days[-streak - 1] if values[-1] is not None else None)
    if row['groups']['20'] == 'PASS':
        row['streak_values'] = values[-streak - 1:]
    return row


def recent_row(row, days):
    return finish_row(row, days, row['values'], row['point_reasons'])


class EndpointSource:
    def __init__(self, frame, archived_raw=None):
        code = frame.get('security_id')
        if (frame.get('schema') != 'stock.chips.market.stock.v1' or not isinstance(code, str)
                or not re.fullmatch(r'\d{6}', code) or frame.get('model', {}).get('version') != VERSION
                or frame.get('model', {}).get('historical_turnover_decay_coefficient') != '2'):
            raise ValueError('V2_FRAME_IDENTITY_MISMATCH')
        self.frame, self.code = frame, code
        self.bars = []
        for item in frame.get('history', []):
            bar = Bar(iso_day(item['date']), *(number(item[k]) for k in ('open', 'high', 'low', 'close', 'volume_shares', 'amount_cny', 'turnover_percent')))
            validate_bar(bar)
            if (self.bars and self.bars[-1].source_date >= bar.source_date) or bar.source_date > frame['as_of']:
                raise ValueError('V2_ORDERED_PUBLISHED_HISTORY_REQUIRED')
            self.bars.append(bar)
        if frame.get('chip_status') == 'READY' and not 0 < len(self.bars) <= 500:
            raise ValueError('V2_PUBLISHED_HISTORY_REQUIRED')
        audit = frame.get('source_audit', {})
        self.complete = bool(self.bars and audit.get('used_rows') == len(self.bars)
            and audit.get('used_first_source_date') == self.bars[0].source_date
            and audit.get('used_last_source_date') == self.bars[-1].source_date)
        self.no_trade = set(audit.get('confirmed_no_trade_sessions', []))
        self.decoded = None
        if archived_raw is not None:
            if hashlib.sha256(archived_raw).hexdigest() != frame.get('source', {}).get('sha256'):
                raise ValueError('V2_ARCHIVE_HASH_MISMATCH')
            self.decoded = json.loads(archived_raw)
            self.no_trade.update(self.decoded.get('confirmed_no_trade_sessions', []))
        latest = frame.get('snapshots', {}).get(frame['as_of'], {})
        if latest.get('market_session_status') == 'SOURCE_CONFIRMED_NO_TRADE':
            self.no_trade.add(frame['as_of'])
        context = frame.get('listing_context', {})
        self.listed = trusted_listing({'code': code, 'name': frame['name'], 'listed': context.get('listed'),
                                      'listing_evidence': context.get('evidence')}, frame['as_of'])

    def requires_archive(self, day):
        return not self.complete and len([b for b in self.bars if b.source_date <= day]) < WINDOW

    def point(self, day):
        if self.frame.get('chip_status') != 'READY':
            return None, 'MODEL_NOT_READY'
        if self.listed and day < self.listed:
            return None, 'PRE_LISTING'
        eligible = [b for b in self.bars if b.source_date <= day]
        if self.decoded is not None and (len(eligible) < WINDOW or not eligible or eligible[-1].source_date != day):
            normalized = encoded({'rc': self.decoded.get('rc'), 'data': self.decoded.get('data')})
            try:
                eligible, _ = prepare_eastmoney(normalized, expected_code=self.code, cutoff=day, allow_valid_suffix=True)
            except ValueError:
                return None, 'HISTORICAL_SOURCE_UNAVAILABLE'
        if day in self.no_trade or (eligible and eligible[-1].source_date == day and eligible[-1].volume_shares == 0):
            return None, 'NO_TRADE'
        if not eligible or eligible[-1].source_date != day:
            return None, 'SOURCE_SESSION_MISSING'
        if not self.complete and self.decoded is None and len(eligible) < WINDOW:
            return None, 'ARCHIVED_WARMUP_UNAVAILABLE'
        window = eligible[-WINDOW:]
        try:
            snapshot = calculate(window, security_id=self.code, window_start=window[0].source_date, window_rows=len(window))
            if day == self.frame['as_of'] and any(snapshot.get(k) != self.frame['snapshots'][day].get(k) for k in ('raw_node_masses', 'price_grid', 'profile')):
                raise ValueError('V2_SEALED_SNAPSHOT_MISMATCH')
            return concentration_value(snapshot['profile']), None
        except ArithmeticError:
            return None, 'MODEL_CALCULATION_UNAVAILABLE'


def trace_segment(frame, recent, axis, previous, archived_raw=None):
    """Calculate at most 32 earlier endpoints and preserve every market date."""
    source = EndpointSource(frame, archived_raw)
    values = list(previous['values']) if previous else list(recent['values'])
    reasons = dict(previous['point_reasons']) if previous else dict(recent['point_reasons'])
    start = len(axis) - len(values)
    for _ in range(SEGMENT_SIZE):
        if start == 0:
            break
        value, reason = source.point(axis[start - 1])
        values.insert(0, value)
        reasons = {str(int(k) + 1): v for k, v in reasons.items()}
        if reason:
            reasons['0'] = reason
        start -= 1
        row = finish_row(recent, axis[start:], values, reasons)
        if row['streak_days'] < len(values) - 1:
            break
    row = finish_row(recent, axis[start:], values, reasons)
    complete = start == 0 or row['streak_days'] < len(values) - 1
    return {'values': values, 'point_reasons': reasons, 'complete': complete, 'row': row}


def validate_row(row, days, axis):
    if len(row['values']) != 21:
        raise ValueError('V2_RECENT_AXIS_INVALID')
    values = row.get('streak_values')
    if values is not None:
        if len(values) != row['streak_days'] + 1 or len(values) > len(axis):
            raise ValueError('V2_STREAK_CURVE_LENGTH_INVALID')
        validate_values(values)
        if any(v is None for v in values) or any(b > a for a, b in zip(values, values[1:])):
            raise ValueError('V2_STREAK_CURVE_NOT_NONINCREASING')
        if values[-21:] != row['values']:
            raise ValueError('V2_STREAK_RECENT_SUFFIX_MISMATCH')
        if row['streak_start_session'] != axis[-len(values)]:
            raise ValueError('V2_STREAK_START_INVALID')
        boundary = row['streak_boundary']
        evidence = [boundary['previous_value'], *values] if boundary['kind'] == 'INCREASE' else values
        expected = classify(evidence, {})
        if row['groups'] != expected:
            raise ValueError('V2_STREAK_GROUPS_INVALID')
    else:
        expected = recent_row(row, days)
        if row != expected:
            raise ValueError('V2_RECENT_DECISION_MISMATCH')
    boundary = row['streak_boundary']
    if boundary['kind'] == 'INCREASE':
        if (not 0 <= boundary['previous_value'] < boundary['anchor_value'] <= 100
                or boundary['anchor_session'] != row['streak_start_session']
                or (values is not None and boundary['anchor_value'] != values[0])
                or axis.index(boundary['previous_session']) + 1 != axis.index(boundary['anchor_session'])):
            raise ValueError('V2_INCREASE_BOUNDARY_INVALID')
    elif boundary['kind'] not in ('UNAVAILABLE', 'NO_TRADE', 'WARMUP', 'CALENDAR_LIMIT'):
        raise ValueError('V2_STREAK_BOUNDARY_INVALID')


def concentration_document(manifest, rows, *, input_fingerprint, lineage):
    axis = supported_sessions(manifest['as_of'])
    days = axis[-21:]
    ordered = sorted(rows, key=lambda r: r['security_id'])
    if len(ordered) != manifest['universe_total'] or len({r['security_id'] for r in ordered}) != len(ordered):
        raise ValueError('V2_COMPLETE_UNIQUE_UNIVERSE_REQUIRED')
    for row in ordered:
        validate_row(row, days, axis)
    counts = {key: {s: sum(r['groups'][key] == s for r in ordered) for s in ('PASS', 'FAIL', 'UNKNOWN')} for key in GROUPS}
    return {'schema': SCHEMA, 'rule_version': RULE, 'dataset_id': manifest['dataset_id'],
            'active_slot': manifest['active_slot'], 'as_of': manifest['as_of'], 'coefficient': 2,
            'model_version': VERSION, 'window_policy': WINDOW_POLICY, 'sessions': days,
            'streak_sessions': axis, 'calendar_sources': SOURCES, 'universe_total': manifest['universe_total'],
            'processed_count': len(ordered), 'source_universe_coverage': manifest.get('universe_coverage', 'UNKNOWN'),
            'input_fingerprint': input_fingerprint, 'lineage': lineage, 'groups': counts, 'rows': ordered}
