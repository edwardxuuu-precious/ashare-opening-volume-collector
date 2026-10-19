"""210-bar, zero-start Eastmoney-source triangle-node replay, coefficient two.

Pure computation. The legacy model remains intact for archived calibration.
Every batch uses one shared trailing window; historical snapshots use only its
as-of prefixes, just as the reviewed webpage source does.
"""
from __future__ import annotations
import hashlib
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from .model import Bar, prepare_eastmoney

VERSION = 'chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1'
WINDOW = 210
NODES = 150
MODEL = {
    'version': VERSION, 'historical_turnover_decay_coefficient': '2',
    'history_window_bars': WINDOW, 'price_node_count': NODES,
    'price_grid_kind': 'EASTMONEY_150_DYNAMIC_NODES',
    'price_bin_width_cny': None,
    'price_bin_definition': '150 raw nodes; step=max(.01,(prefix_high-prefix_low)/149)',
    'daily_allocation': 'OHLC4 triangle heights sampled at raw nodes; flat-day height=149/2 at floor index',
    'effective_replacement': 'min(1,2*turnover_percent/100)',
    'update': 'Q[t]=(1-A)*Q[t-1]+A*H[t]; no daily H/sum(H)',
    'initialization': 'Q[0]=0; first-day injection is A*H, not a full initial distribution',
    'normalization': 'Output shares only; raw state is never normalized between days',
    'window_policy': 'One trailing 210 observed-bar window per batch; previous-day snapshot uses its prefix',
    'short_history': 'Use available validated bars, disclose actual count; no synthetic history',
    'corporate_action_treatment': 'unadjusted',
    'peak_ties': 'all exactly equal maximum nodes retained',
    'same_price_definition': 'Yesterday raw peak +/- half yesterday step, fixed interval on grid changes',
    'unchanged_threshold': None, 'historical_PIT_authenticated': False,
    'vendor_equivalence': 'FOUR_STOCK_FROZEN_SOURCE_REPLAY_MATCH; NOT_OFFICIAL_CLIENT_CERTIFICATION',
    'can_generate_order': False,
}


def total(values):
    # Python 3.12 sum uses compensated arithmetic; preserve source JS addition order.
    result = 0.0
    for value in values:
        result += value
    return result


def text(value):
    if not math.isfinite(value):
        raise ValueError('FINITE_MODEL_NUMBER_REQUIRED')
    return '0' if value == 0 else str(value)


def display_price(value):
    return format(Decimal.from_float(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f')


def calculate(bars, *, security_id, window_start, window_rows):
    low = min(float(b.low) for b in bars)
    high = max(float(b.high) for b in bars)
    step = max(.01, (high-low)/149)
    raw = [0.0]*NODES
    for bar in bars:
        op, close, hi, lo = (float(x) for x in (bar.open, bar.close, bar.high, bar.low))
        apex = (op+close+hi+lo)/4
        a = min(1.0, 2*float(bar.turnover_pct)/100)
        for i in range(NODES):
            raw[i] *= 1-a
        if hi == lo:
            index = math.floor((apex-low)/step)
            if not 0 <= index < NODES:
                raise ValueError('NODE_INDEX_OUT_OF_RANGE')
            raw[index] += (149/2)*a
        else:
            height = 2/(hi-lo)
            for i in range(math.ceil((lo-low)/step), math.floor((hi-low)/step)+1):
                if not 0 <= i < NODES:
                    raise ValueError('NODE_INDEX_OUT_OF_RANGE')
                price = low+step*i
                if price <= apex:
                    shape = height if abs(apex-lo)<1e-8 else (price-lo)/(apex-lo)*height
                else:
                    shape = height if abs(hi-apex)<1e-8 else (hi-price)/(hi-apex)*height
                raw[i] += shape*a
    mass = total(raw)
    if not mass > 0 or not all(math.isfinite(x) and x >= 0 for x in raw):
        raise ValueError('EMPTY_OR_INVALID_SAMPLED_DISTRIBUTION')
    peak = max(raw)
    peaks = [i for i, value in enumerate(raw) if value == peak]
    shares = [value/mass*100 for value in raw]
    profile = [{'price_cny': text(low+step*i), 'share_percent': text(value)} for i, value in enumerate(shares)]
    digest = hashlib.sha256(json.dumps([[str(getattr(b, k)) for k in Bar.__dataclass_fields__]
        for b in bars], separators=(',', ':')).encode()).hexdigest()
    return {
        'source_date': bars[-1].source_date, 'security_id': security_id, 'model_version': VERSION,
        'seed_source_date': window_start, 'window_source_start_date': window_start,
        'history_window_bars': WINDOW, 'loaded_window_rows': window_rows,
        'history_rows_processed': len(bars), 'normalized_input_prefix_sha256': digest,
        'price_grid_kind': MODEL['price_grid_kind'],
        'price_grid': {'low_cny': text(low), 'high_cny': text(high), 'step_cny': text(step), 'node_count': NODES},
        'raw_total_mass': text(mass), 'raw_node_masses': [text(x) for x in raw],
        'peak_node_indices': peaks, 'peak_unrounded_prices_cny': [text(low+step*i) for i in peaks],
        'peak_prices_cny': [display_price(low+step*i) for i in peaks],
        'peak_bin_share_percent': text(peak/mass*100),
        'model_mean_cost_cny': text(total((low+step*i)*shares[i]/100 for i in range(NODES))),
        'close_cny': str(bars[-1].close), 'turnover_percent': str(bars[-1].turnover_pct),
        'effective_replacement': text(min(1.0, 2*float(bars[-1].turnover_pct)/100)), 'coefficient': '2',
        'initial_distribution_observed': False, 'profile': profile,
        'unchanged_threshold': None, 'unchanged_classification': None,
        'historical_PIT_authenticated': False, 'vendor_equivalence': MODEL['vendor_equivalence'],
    }


def estimate_snapshots(bars, targets, *, security_id, state=None, export_state=False):
    if not security_id or not targets:
        raise ValueError('SECURITY_AND_TARGETS_REQUIRED')
    # Recompute rather than extend state: both the bounded window and dynamic grid change.
    if state and state.get('security_id') != security_id:
        raise ValueError('MODEL_STATE_IDENTITY_MISMATCH')
    maximum = max(targets)
    eligible = [b for b in bars if b.source_date <= maximum]
    if any(a.source_date >= b.source_date for a,b in zip(eligible, eligible[1:])):
        raise ValueError('ORDERED_UNIQUE_BARS_REQUIRED')
    window = eligible[-WINDOW:]
    if not window:
        raise ValueError('EMPTY_SOURCE_HISTORY')
    snapshots = {}
    for day in sorted(set(targets)):
        prefix = [b for b in window if b.source_date <= day]
        if not prefix or prefix[-1].source_date != day:
            raise ValueError('REQUESTED_SOURCE_DATES_MISSING:'+day)
        snapshots[day] = calculate(prefix, security_id=security_id,
                                  window_start=window[0].source_date, window_rows=len(window))
    saved = {'schema': 'chip-estimate.node150.state.v1', 'model_version': VERSION, 'security_id': security_id,
        'last_date': window[-1].source_date, 'first_date': window[0].source_date, 'rows_processed': len(window),
        'history_window_bars': WINDOW, 'recompute_dynamic_grid_each_batch': True}
    return (snapshots, saved) if export_state else snapshots


def coordinate_numerator(snapshot, index):
    grid = snapshot['price_grid']
    low = int(Decimal(grid['low_cny'])*100)
    high = int(Decimal(grid['high_cny'])*100)
    return low*149+max(149, high-low)*index


def anchor_band(snapshot, index):
    grid = snapshot['price_grid']
    span = max(149, int(Decimal(grid['high_cny'])*100)-int(Decimal(grid['low_cny'])*100))
    center = 2*coordinate_numerator(snapshot, index)
    return center-span, center+span


def share_in_band(snapshot, lower, upper):
    return total(float(p['share_percent']) for i,p in enumerate(snapshot['profile'])
                 if lower <= 2*coordinate_numerator(snapshot,i) < upper)


def compare_snapshots(before, after, *, anchors=None):
    if before['source_date'] >= after['source_date']:
        raise ValueError('ORDERED_COMPARISON_DATES_REQUIRED')
    if before.get('model_version') != VERSION or after.get('model_version') != VERSION:
        raise ValueError('SAME_MODEL_VERSION_REQUIRED')
    if not before.get('security_id') or before.get('security_id') != after.get('security_id'):
        raise ValueError('SAME_SECURITY_REQUIRED')
    if before['seed_source_date'] != after['seed_source_date']:
        raise ValueError('SAME_INITIALIZATION_REQUIRED')
    indices = before['peak_node_indices']
    if anchors is not None:
        selected = set(anchors)
        indices = [i for i in indices if display_price(float(before['profile'][i]['price_cny'])) in selected]
        if len(indices) != len(selected):
            raise ValueError('ANCHOR_MUST_BE_A_PREVIOUS_PEAK_NODE')
    rows = []
    for index in indices:
        lower, upper = anchor_band(before, index)
        old, new = share_in_band(before, lower, upper), share_in_band(after, lower, upper)
        rows.append({'anchor_price_cny': display_price(float(before['profile'][index]['price_cny'])),
            'anchor_unrounded_price_cny': before['profile'][index]['price_cny'],
            'lower_numerator': lower, 'upper_numerator': upper, 'denominator': 29800,
            'previous_share_percent': text(old), 'current_same_price_share_percent': text(new),
            'change_percentage_points': text(new-old),
            'same_price_occupancy_ratio_percent': text(new/old*100) if old else None,
            'original_holder_retention': None})
    return {'security_id': before['security_id'], 'from_date': before['source_date'], 'to_date': after['source_date'],
        'previous_peak_prices_cny': before['peak_prices_cny'], 'current_peak_prices_cny': after['peak_prices_cny'],
        'anchors': rows, 'unchanged_threshold': None, 'unchanged_classification': None,
        'same_price_definition': MODEL['same_price_definition']}
