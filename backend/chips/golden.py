"""Descriptive research60/5 grids from supplied daily candles, without I/O.

Port of Filter's adopted structure_at geometry (design SHA below). No signal,
volume/chip/price screening, order, policy optimization or historical grant.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from .calendar import sessions

VERSION = 'golden-grid.research60-5.tiers20-50.v1'
DESIGN_SHA = 'ba5ceafce205c8a674d74c36f1b5062835a3e1ba87073af3052a8d0e298a182b'


def number(value):
    if type(value) not in (str, int, Decimal):
        raise ValueError('EXACT_NUMERIC_INPUT_REQUIRED')
    try:
        value = Decimal(value)
    except (ValueError, InvalidOperation):
        raise ValueError('EXACT_NUMERIC_INPUT_REQUIRED') from None
    if not value.is_finite():
        raise ValueError('FINITE_PRICE_REQUIRED')
    return Fraction(value)


def price(value):
    # Grid levels have terminating decimal representations. Percentages are
    # separately display-rounded; all comparisons below use exact fractions.
    return format(Decimal(value.numerator) / Decimal(value.denominator), 'f')


def percent(value):
    return format(Decimal(value.numerator * 100) / Decimal(value.denominator), '.8f')


def _structure(history, axis, index):
    if index < 64:
        return {'status': 'UNKNOWN', 'reason': 'INSUFFICIENT_60_5_WARMUP'}
    rows = {}
    try:
        for j in range(index - 64, index + 1):
            row = history[axis[j]]
            o, h, l, c = (number(row[key]) for key in ('open', 'high', 'low', 'close'))
            if not 0 < l <= min(o, c) <= max(o, c) <= h:
                raise ValueError('INVALID_DAILY_PRICES')
            rows[j] = (o, h, l, c)
    except (KeyError, ValueError, TypeError):
        return {'status': 'UNKNOWN', 'reason': 'MISSING_OR_INVALID_PRICE_SESSION'}
    hi = max(range(index - 64, index - 4), key=lambda j: (rows[j][1], j))
    later = range(hi + 1, index - 4)
    if not later:
        return {'status': 'UNKNOWN', 'reason': 'NO_LOW_STRICTLY_AFTER_HIGH'}
    lo = min(later, key=lambda j: (rows[j][2], -j))
    h, l = rows[hi][1], rows[lo][2]
    if not h > l > 0:
        return {'status': 'UNKNOWN', 'reason': 'INVALID_ANCHOR_VALUES'}
    depth = (h - l) / h
    ratio = Fraction(1, 2) if depth <= Fraction(1, 5) else Fraction(1, 4) if depth <= Fraction(1, 2) else Fraction(1, 8)
    result = {'status': 'PASS', 'reason': 'DECLARED_60_5_STRUCTURE', 'H': price(h), 'L': price(l),
              'H_date': axis[hi], 'L_date': axis[lo], 'ratio': price(ratio),
              'midpoint': price(l + ratio * (h - l)), 'upper': price(l + 2 * ratio * (h - l)),
              'drawdown_percent': percent(depth)}
    if depth < Fraction(1, 5):
        result.update(status='FAIL', reason='BELOW_MINIMUM_20_PERCENT_DEPTH')
    elif any(rows[j][2] < l for j in range(lo + 1, index + 1)):
        result.update(status='FAIL', reason='LATER_LOW_BREACH')
    return result


def grid_row(frame):
    """Use the declared 2026 exchange calendar; missing rows stay UNKNOWN."""
    result = {'security_id': frame['security_id'], 'name': frame['name'], 'as_of': frame['as_of'],
              'version': VERSION, 'status': 'UNKNOWN', 'reason': 'HISTORY_UNAVAILABLE',
              'geometry': None, 'known_at': None, 'research_only': True}
    as_of = frame['as_of']
    try:
        if date.fromisoformat(as_of).year != 2026:
            raise ValueError('OFFICIAL_CALENDAR_YEAR_UNAVAILABLE')
        axis = sessions('2026-01-01', as_of)
        if not axis or axis[-1] != as_of:
            raise ValueError('AS_OF_NOT_EXCHANGE_SESSION')
        rows = {}
        for row in frame.get('history', []):
            day = row['date']
            if day in rows:
                raise ValueError('DUPLICATE_PRICE_SESSION')
            rows[day] = row
        current = _structure(rows, axis, len(axis) - 1)
        result.update(status=current['status'], reason=current['reason'])
        if 'H' not in current:
            return result
        previous = _structure(rows, axis, len(axis) - 2)
        close = number(rows[as_of]['close'])
        low, high = (number(rows[as_of][key]) for key in ('low', 'high'))
        h, l, r = (number(current[key]) for key in ('H', 'L', 'midpoint'))
        identity = ('H_date', 'L_date', 'H', 'L', 'ratio')
        both_valid = current['status'] == previous['status'] == 'PASS'
        same = both_valid and all(current[key] == previous.get(key) for key in identity)
        crossing = 'UNKNOWN'
        comparison = 'STRUCTURE_CHANGED' if both_valid and not same else 'STRUCTURE_UNAVAILABLE_OR_INVALID'
        if same:
            prior = number(rows[axis[-2]]['close'])
            crossing = 'UP' if prior <= r < close else 'DOWN' if prior >= r > close else 'NONE'
            comparison = 'SAME_VALID_STRUCTURE'
        result['geometry'] = {**{key: current[key] for key in current if key not in ('status', 'reason')},
            'close': price(close), 'main_midpoint': price((h + l) / 2),
            'quarter': price(l + (h - l) / 4), 'eighth': price(l + (h - l) / 8),
            'close_side': 'ABOVE' if close > r else 'BELOW' if close < r else 'AT',
            'distance_percent': percent(close / r - 1),
            'main_space_position_percent': percent((close - l) / (h - l)),
            'intraday_touch': low <= r <= high, 'crossing': crossing, 'comparison': comparison}
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        result.update(status='UNKNOWN', reason=str(exc)[:100], geometry=None)
    return result


def document(manifest, rows):
    from collections import Counter
    if len(rows) != manifest['universe_total'] or len({r['security_id'] for r in rows}) != len(rows):
        raise ValueError('INCOMPLETE_OR_DUPLICATE_UNIVERSE')
    if any(r['as_of'] != manifest['as_of'] or r['version'] != VERSION for r in rows):
        raise ValueError('MIXED_GRID_BATCH')
    counts = Counter(r['status'] for r in rows)
    if set(counts) - {'PASS', 'FAIL', 'UNKNOWN'}:
        raise ValueError('INVALID_GRID_STATE')
    return {'schema': 'stock.golden-grid.market.v1', 'dataset_id': manifest['dataset_id'],
            'as_of': manifest['as_of'], 'version': VERSION, 'policy_sha256': DESIGN_SHA,
            'universe_total': len(rows), 'universe_coverage': manifest['universe_coverage'],
            'counts': {key: counts[key] for key in ('PASS', 'FAIL', 'UNKNOWN')},
            'rows': sorted(rows, key=lambda r: r['security_id']), 'research_only': True,
            'original_owner_fidelity': False, 'can_generate_order': False}
