"""Two-group independent endpoints and immutable eight-point stock states."""
from __future__ import annotations

from .concentration import WINDOW_POLICY, encoded
from .concentration_v2 import EndpointSource, supported_sessions, validate_values
from .calendar import SOURCES
from .eastmoney_model import VERSION, WINDOW
from .ohlc4_input import prepare_eastmoney

SCHEMA = 'stock.chips.concentration.two.v1'
RULE = 'CENTRAL_90_NONINCREASING_INDEPENDENT_210_TWO_V1'
GROUPS = {'20': 20, 'LONG': 21}
SEGMENT_SIZE = 8

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


def state_row(frame, axis, values, reasons, short_days):
    recent_values = values[-21:]
    offset = len(values) - 21
    recent = {'security_id': frame['security_id'], 'name': frame['name'],
              'close_cny': frame.get('snapshots', {}).get(frame['as_of'], {}).get('close_cny'),
              'values': recent_values,
              'point_reasons': {str(int(k) - offset): v for k, v in reasons.items() if int(k) >= offset},
              'short_history_days': sum(day in axis[-21:] for day in short_days)}
    return finish_row(recent, axis[-len(values):], values, reasons)


def segment(frame, axis, previous=None, archived_raw=None):
    source = EndpointSource(frame, archived_raw)
    values = list(previous['values']) if previous else []
    reasons = dict(previous['point_reasons']) if previous else {}
    short_days = list(previous['short_sessions']) if previous else []
    start = len(axis) - len(values)
    for _ in range(SEGMENT_SIZE):
        if start == 0:
            break
        day = axis[start - 1]
        value, reason = source.point(day)
        values.insert(0, value)
        reasons = {str(int(k) + 1): v for k, v in reasons.items()}
        if reason:
            reasons['0'] = reason
        if value is not None and day in axis[-21:]:
            eligible = [b for b in source.bars if b.source_date <= day]
            if source.decoded is not None and len(eligible) < WINDOW:
                raw = encoded({'rc': source.decoded.get('rc'), 'data': source.decoded.get('data')})
                eligible, _ = prepare_eastmoney(raw, expected_code=source.code, cutoff=day, allow_valid_suffix=True)
            if len(eligible) < WINDOW:
                short_days.append(day)
        start -= 1
        if len(values) >= 21:
            row = state_row(frame, axis, values, reasons, short_days)
            if row['groups']['20'] != 'PASS' or row['streak_days'] < len(values) - 1:
                break
    row = state_row(frame, axis, values, reasons, short_days) if len(values) >= 21 else None
    complete = row is not None and (start == 0 or row['groups']['20'] != 'PASS' or row['streak_days'] < len(values) - 1)
    return {'values': values, 'point_reasons': reasons, 'short_sessions': short_days,
            'complete': complete, 'row': row}


def validate_state(state, previous, frame, axis):
    if not isinstance(state, dict) or not isinstance(state.get('values'), list):
        raise ValueError('TWO_STATE_INVALID')
    values = state['values']; reasons = state.get('point_reasons')
    old_values = previous['values'] if previous else []
    added = len(values) - len(old_values)
    if (not 1 <= added <= SEGMENT_SIZE or len(values) > len(axis)
            or (old_values and values[added:] != old_values) or not isinstance(reasons, dict)):
        raise ValueError('TWO_SEGMENT_CONTINUITY_INVALID')
    validate_values(values)
    if any(not k.isdigit() or int(k) >= len(values) or values[int(k)] is not None or not isinstance(v, str) or not v for k,v in reasons.items()):
        raise ValueError('TWO_REASON_INVALID')
    if any(v is None and str(i) not in reasons for i,v in enumerate(values)):
        raise ValueError('TWO_REASON_MISSING')
    old_reasons = previous['point_reasons'] if previous else {}
    if {str(int(k)-added): v for k,v in reasons.items() if int(k) >= added} != old_reasons:
        raise ValueError('TWO_REASON_CONTINUITY_INVALID')
    short = state.get('short_sessions')
    if (not isinstance(short, list) or len(short) != len(set(short))
            or any(day not in axis[-21:] or day not in axis[-len(values):] for day in short)
            or (previous and short[:len(previous['short_sessions'])] != previous['short_sessions'])):
        raise ValueError('TWO_SHORT_HISTORY_INVALID')
    row = state_row(frame, axis, values, reasons, short) if len(values) >= 21 else None
    complete = row is not None and (len(values) == len(axis) or row['groups']['20'] != 'PASS' or row['streak_days'] < len(values)-1)
    if state.get('row') != row or state.get('complete') is not complete or (not complete and added != SEGMENT_SIZE):
        raise ValueError('TWO_STATE_DECISION_MISMATCH')
    if row is not None:
        validate_row(row, axis[-21:], axis)
