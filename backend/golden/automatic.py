"""Uniform local drawing diagnostics around the unchanged candle proposal.

The only evaluate input is the observed OHLC prefix. No stock code, target
price, hand-picked date, expected drawing, trade or performance input exists.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
import hashlib
import json

from .anchors import analyze, PARAMETERS, VERSION as BASE_VERSION

POLICY = {
    'proposal_id': 'TWENTY-AUTO-DAILY-DRAWING-REVIEW-20261021-01',
    'version': 'twenty-auto-daily-drawings.v1',
    'status': 'DESCRIPTIVE_CANDIDATE_NOT_FORMALLY_ADOPTED_AS_STRATEGY',
    'calculation_window_observed_rows': 60,
    'background_rows': 120,
    'base_algorithm': BASE_VERSION,
    'base_parameters': deepcopy(PARAMETERS),
    'axis_fields': ['open','high','low','close'],
    'axis_groups': 'all candidate formation records under current parent H, grouped by exact price',
    'reclaim_handling': 'retain all price groups and events; never discard a group after reclaim',
    'axis_winner': None,
    'display_order': 'exact price ascending, then formation date ascending; not a ranking',
    'unconfirmed_point_fallback': None,
    'manual_stock_overrides': None,
    'view_zoom_changes_calculation': False,
    'trading_rules': None,
    'formal_policy_adoption': False,
    'price_precision': 'preserve source precision internally; cent input remains identical to reference',
}


def policy_bytes():
    return json.dumps(POLICY, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def policy_sha():
    return hashlib.sha256(policy_bytes()).hexdigest()


def evaluate(rows):
    base = analyze(rows)
    h, l = base['parent_H'], base['selected_L']
    highs = [p for p in base['turns'] if p['kind']=='H']
    top = max((Decimal(p['price']) for p in highs), default=None)
    h_nominees = [p for p in highs if Decimal(p['price'])==top]
    h_reasons = []
    if h is None:
        if not h_nominees:
            h_reasons.append('NO_CONFIRMED_HIGH')
        if len(h_nominees)>1 or any(p['status']=='EQUAL_EXTREME_AMBIGUOUS' for p in h_nominees):
            h_reasons.append('EQUAL_HIGHS_AMBIGUOUS')
        if any(p['breached_at'] is not None for p in h_nominees):
            h_reasons.append('CONFIRMED_HIGH_LATER_EXCEEDED')
    low_versions = base.get('low_versions', [])
    bottom = min((Decimal(p['price']) for p in low_versions), default=None)
    l_nominees = [p for p in low_versions if Decimal(p['price'])==bottom]
    l_reasons = []
    if l is None:
        if h is None:
            l_reasons.append('NO_CURRENT_PARENT_H')
        elif not l_nominees:
            l_reasons.append('NO_CONFIRMED_LOW_AFTER_H')
        else:
            if len(l_nominees)>1 or any(p['status']=='EQUAL_EXTREME_AMBIGUOUS' for p in l_nominees):
                l_reasons.append('EQUAL_LOWS_AMBIGUOUS')
            if any(p['breached_at'] is not None for p in l_nominees):
                l_reasons.append('CONFIRMED_LOW_LATER_BREACHED')
    observed_lows = []
    if h:
        later = [r for r in rows if r['date']>h['date']]
        low_price = min((Decimal(str(r['low'])) for r in later), default=None)
        observed_lows = [dict(date=r['date'], field='low', price=str(r['low']),
                             right_rows_available=sum(v['date']>r['date'] for v in rows),
                             role='OBSERVED_LOW_NOT_A_SELECTED_TURN', historical_known_at=None)
                         for r in later if Decimal(str(r['low']))==low_price]
    groups = defaultdict(list)
    for a in base['axes']:
        if h is not None and a['H_ref']['id']==h['id']:
            groups[a['price']].append(a)
    price_groups = []
    for p in sorted(groups, key=Decimal):
        events = sorted(groups[p], key=lambda a:(a['break_event']['date'],a['id']))
        price_groups.append(dict(price=p, records=events,
            qualified_formation_records=sum(a['available_after_close_on'] is not None for a in events),
            reclaim_observed_records=sum(a['reclaim_observed_on'] is not None for a in events),
            interpretation='AUTOMATIC_PRICE_CANDIDATE_NOT_CONFIRMED_OWNER_AXIS'))
    return dict(policy_sha256=policy_sha(), as_of=base['as_of'], result_origin='PROGRAM_ONLY_SAME_RULE',
        H=h, L=l, h_nominees=h_nominees, l_nominees=l_nominees,
        H_unknown_reasons=h_reasons, L_unknown_reasons=l_reasons,
        observed_lows=observed_lows, pending_turns=base['pending_turns'], turns=base['turns'],
        price_groups=price_groups, retained_current_H_records=sum(len(v['records']) for v in price_groups),
        other_H_formation_records=len(base['axes'])-sum(len(v['records']) for v in price_groups),
        HL_geometry=base['geometry'], axis_winner=None, automatic_axis_switch=None,
        owner_fidelity='NOT_VERIFIED', historical_known_at=None,
        full_market_runs=0, performance_runs=0, production_changes=0)
