"""Unadopted, pure local candle-anchor proposal for supplied source cases.

No I/O, market engine, trading decisions, active policy, threshold search or
owner-reference prices. Dates are observed candle labels, not verified clocks.
"""
from __future__ import annotations

from decimal import Decimal

VERSION = 'candle-anchor.source-case-proposal.precision-v2'
PARAMETERS = {
    'turn_left_bars': 2,
    'turn_right_bars': 2,
    'turn_price_fields': ['high', 'low'],
    'equal_extreme': 'AMBIGUOUS_KEEP_ALL',
    'axis_candidate_fields': ['open', 'high', 'low', 'close'],
    'support_touch_min_bars': 2,
    'support_test': 'low <= M <= high and close >= M',
    'break_test': 'previous_close >= M and close < M',
    'retest_test': 'later low <= M <= high and close < M',
    'retest_min_bars': 1,
    'price_tolerance': '0',
    'same_break_bar_counts_as_retest': False,
    'parent_high': 'highest confirmed turn in supplied case window; lower turns are children',
    'low': 'lowest confirmed turn after parent H; retain earlier versions and breaches',
    'axis_winner': None,
    'axis_switch': None,
    'automatic_case_window': None,
    'policy_status': 'PROPOSED_NOT_ADOPTED',
}


def dec(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Non-finite price')
    return result


def price(value):
    # Equities in the reference samples use cent prices. Index vendors may
    # supply four decimal places: never round those before comparisons, IDs,
    # grouping or 2M-H. Formatting for display belongs to the UI.
    p=dec(value)
    return format(p,'.2f') if p==p.quantize(Decimal('.01')) else format(p,'f')


def validate(rows):
    dates = [r['date'] for r in rows]
    if dates != sorted(set(dates)):
        raise ValueError('Candles must have unique increasing dates')
    for row in rows:
        o, h, l, c = (dec(row[f]) for f in ('open', 'high', 'low', 'close'))
        if not (0 < l <= min(o, c) <= max(o, c) <= h):
            raise ValueError('Invalid OHLC: ' + row['date'])


def metadata(rule):
    return dict(rule_id=rule, source_id='ASSISTANT_PROPOSAL_FOR_USER_REVIEW',
                version=VERSION, historical_known_at=None,
                eligibility='UNKNOWN', policy_status='PROPOSED_NOT_ADOPTED')


def turns(rows):
    """A turn cannot be available before the required right candles close."""
    result = []
    left, right = PARAMETERS['turn_left_bars'], PARAMETERS['turn_right_bars']
    for i in range(left, len(rows) - right):
        neighbors = rows[i-left:i] + rows[i+1:i+right+1]
        for field, kind in [('high', 'H'), ('low', 'L')]:
            p = dec(rows[i][field])
            comparisons = [dec(v[field]) for v in neighbors]
            extreme = max(comparisons) if kind == 'H' else min(comparisons)
            if (kind == 'H' and p < extreme) or (kind == 'L' and p > extreme):
                continue
            tied = p in comparisons
            confirmed_index = i + right
            breaches = [r['date'] for r in rows[confirmed_index+1:]
                        if (dec(r[field]) > p if kind == 'H' else dec(r[field]) < p)]
            result.append(dict(id=f'{kind}:{rows[i]["date"]}', kind=kind,
                date=rows[i]['date'], field=field, price=price(p),
                body_edge=price(max(dec(rows[i]['open']), dec(rows[i]['close']))
                                if kind == 'H' else min(dec(rows[i]['open']), dec(rows[i]['close']))),
                available_after_close_on=rows[confirmed_index]['date'],
                left_dates=[r['date'] for r in rows[i-left:i]],
                right_dates=[r['date'] for r in rows[i+1:i+right+1]],
                status='EQUAL_EXTREME_AMBIGUOUS' if tied else 'CONFIRMED_LOCAL_TURN',
                breached_at=breaches[0] if breaches else None,
                **metadata('PROPOSAL-TURN-2-2')))
    return result


def parent_high(turn_list, as_of):
    highs = [v for v in turn_list if v['kind'] == 'H'
             and v['available_after_close_on'] <= as_of]
    if not highs:
        return None
    peak = max(dec(v['price']) for v in highs)
    candidates = [v for v in highs if dec(v['price']) == peak]
    # Widely separated equal highs are also ambiguous; do not silently pick last.
    if len(candidates) != 1 or candidates[0]['status'] != 'CONFIRMED_LOCAL_TURN':
        return None
    result = candidates[0]
    if result['breached_at'] is not None and result['breached_at'] <= as_of:
        return None
    return result


def role_at(row, level):
    p = dec(level)
    o, h, l, c = (dec(row[f]) for f in ('open', 'high', 'low', 'close'))
    if l <= p < min(o, c):
        contact = 'LOWER_WICK'
    elif max(o, c) < p <= h:
        contact = 'UPPER_WICK'
    elif min(o, c) <= p <= max(o, c):
        contact = 'BODY_OR_EDGE'
    else:
        contact = 'NO_TOUCH'
    return dict(date=row['date'], open=price(o), high=price(h), low=price(l), close=price(c),
                contact=contact, close_side='ABOVE' if c > p else 'BELOW' if c < p else 'AT',
                available_after_close_on=row['date'], historical_known_at=None)


def resistance_candidates(rows, turn_list):
    """Prices are generated from the prefix at each break, never owner labels.

    Support is a touch and close at/above the price in the immediately preceding
    at/above run. Same-day evidence cannot confirm a later failed retest.
    Reclaim observations are retained, without automatically adopting a new axis.
    """
    output, origins = [], {}
    for b, row in enumerate(rows):
        for field in PARAMETERS['axis_candidate_fields']:
            p = dec(row[field])
            origins.setdefault(p, []).append(dict(date=row['date'], field=field, value=price(p)))
        if b == 0:
            continue
        high = parent_high(turn_list, row['date'])
        if high is None or high['date'] >= row['date']:
            continue
        for p in sorted(origins, reverse=True):
            if p >= dec(high['price']):
                continue
            if not (dec(rows[b-1]['close']) >= p > dec(row['close'])):
                continue
            # Do not join support observations separated by an earlier close below M.
            start = b - 1
            while start >= 0 and dec(rows[start]['close']) >= p:
                start -= 1
            support = [role_at(r, p) for r in rows[start+1:b]
                       if dec(r['low']) <= p <= dec(r['high']) and dec(r['close']) >= p]
            if len(support) < PARAMETERS['support_touch_min_bars']:
                continue
            events, failed, reclaim = [], [], None
            for later in rows[b+1:]:
                event = role_at(later, p)
                if reclaim is None and dec(later['close']) > p:
                    reclaim = later['date']
                if dec(later['low']) <= p <= dec(later['high']) and dec(later['close']) < p:
                    events.append(event)
                    if reclaim is None:
                        failed.append(event)
            available = failed[0]['date'] if failed else None
            status = 'RECLAIMED_OBSERVED' if reclaim else 'RESISTANCE_CANDIDATE' if failed else 'AWAIT_RETEST'
            output.append(dict(id=f'M:{row["date"]}:{price(p)}', price=price(p),
                source_fields=list(origins[p]), H_ref=dict(id=high['id'], date=high['date'], price=high['price']),
                supports=support, break_event=role_at(row, p),
                candidate_created_after_close_on=row['date'],
                retests_before_first_reclaim=failed, later_touch_below_events=events,
                available_after_close_on=available, reclaim_observed_on=reclaim,
                status=status, role_confirmed=False,
                B_conditional=price(2*p-dec(high['price'])),
                geometry_condition='Only if this H/M equal-height interpretation is accepted; B is not observed L',
                **metadata('PROPOSAL-SUPPORT-BREAK-RETEST')))
    return output


def analyze(rows):
    validate(rows)
    if not rows:
        return dict(as_of=None, turns=[], axes=[], parent_H=None,
                    selected_L=None, geometry=[], axis_winner=None, **metadata('PROPOSAL-CASE'))
    points = turns(rows)
    high = parent_high(points, rows[-1]['date'])
    lows = [] if high is None else [v for v in points if v['kind'] == 'L' and v['date'] > high['date']]
    low = None
    if lows:
        bottom = min(dec(v['price']) for v in lows)
        equal = [v for v in lows if dec(v['price']) == bottom]
        low = equal[0] if len(equal) == 1 and equal[0]['status']=='CONFIRMED_LOCAL_TURN' else None
        if low and low['breached_at'] is not None:
            low = None
    geometry = []
    if high and low:
        geometry = [dict(ratio=ratio, value=format(dec(low['price'])+dec(ratio)*(dec(high['price'])-dec(low['price'])), 'f'),
                         role='GEOMETRIC_REFERENCE_NOT_SELECTED_RESISTANCE') for ratio in ['0.5','0.25','0.125']]
    pending = []
    # End-of-window bars cannot silently be called confirmed turns.
    for i in range(max(PARAMETERS['turn_left_bars'], len(rows)-PARAMETERS['turn_right_bars']), len(rows)):
        previous = rows[i-PARAMETERS['turn_left_bars']:i]
        for field, kind in [('high','H'),('low','L')]:
            values = [dec(v[field]) for v in previous]
            if (dec(rows[i][field]) > max(values) if kind == 'H' else dec(rows[i][field]) < min(values)):
                pending.append(dict(date=rows[i]['date'], kind=kind, price=price(rows[i][field]),
                                    status='RIGHT_CANDLES_INCOMPLETE', historical_known_at=None))
    return dict(as_of=rows[-1]['date'], parameters=PARAMETERS,
        turns=points, pending_turns=pending, parent_H=high, selected_L=low,
        low_versions=lows, axes=resistance_candidates(rows, points), geometry=geometry,
        axis_winner=None, axis_switch=None, automatic_case_window=None,
        calendar_status='UNVERIFIED_OBSERVED_CANDLE_ROWS',
        conclusion='LOCAL_SOURCE_CASE_PROPOSAL_ONLY', **metadata('PROPOSAL-CASE'))
