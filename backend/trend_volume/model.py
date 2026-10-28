"""Adapt validated saved daily bars to the unchanged V3 classifier."""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re

from .frozen_v3 import V3Parameters, RULE_ID, PARAMETER_SHA256, parameter_values, screen_v3_day, result_dict
from .types import DailyBar, DailySeries

CONTRACT_SHA256 = '95e4d6d1770257d8783524f50809efd482799aca3d628b835be7dd9630f3596d'
ORIGINAL_V3_SHA256 = 'fa49e8d74c35ca359570b50babd9561b382b51c1414074aa96e69f484c7fc79d'
ORIGINAL_TYPES_SOURCE_SHA256 = '016176d4dae3542adb2588b8cbc5005643ebd50a80c5a3d49896d4bf39de761a'
PARAMETERS = V3Parameters(contract_sha256=CONTRACT_SHA256)
SIGNAL_STATES = ('SELECTED', 'WATCHING', 'NOT_MATCHED', 'UNKNOWN')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def display_number(value):
    if value is None or isinstance(value, bool):
        return value
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('NONFINITE_DISPLAY_METRIC')
    return result


def gate(value, kind):
    value = value or {}
    windows = value.get('windows', {})
    result = {'status': value.get('status', 'UNKNOWN'),
              'metrics': {key: display_number(number) for key, number in value.get('metrics', {}).items()},
              'reasons': value.get('reasons', [])}
    if kind == 'trend':
        result['dates'] = windows.get('context', windows.get('trend', []))
    else:
        result['recent_dates'] = windows.get('recent', [])
        result['reference_dates'] = windows.get('reference', [])
    return result


def screen_detail(value):
    conditions = value.get('conditions', {})
    setup, surge = conditions.get('setup', {}), conditions.get('surge', {})
    metrics = surge.get('metrics', {})
    candidates = []
    for candidate in setup.get('checked_candidates', []):
        if candidate.get('date') is None:
            continue
        trend, shrink = candidate.get('trend_at_setup', {}), candidate.get('shrink_at_setup', {})
        tm, sm = trend.get('metrics', {}), shrink.get('metrics', {})
        candidates.append({'date': candidate['date'], 'status': candidate['status'],
                           'trend_status': trend.get('status', 'UNKNOWN'), 'shrink_status': shrink.get('status', 'UNKNOWN'),
                           'pullback_pct': display_number(tm.get('pullback_pct')),
                           'context_slope': display_number(tm.get('context_close_ols_slope')),
                           'local_slope': display_number(tm.get('local_close_ols_slope')),
                           'shrink_ratio': display_number(sm.get('recent_to_reference_volume')),
                           'reasons': candidate.get('reasons', [])})
    return {'status': value.get('status', 'UNKNOWN'), 'rule_id': value.get('rule_id', RULE_ID),
            'setup_date': setup.get('date'), 'setup_age': setup.get('age_bars'),
            'baseline_mean': display_number(setup.get('baseline_mean_volume')),
            'volume_multiple': display_number(metrics.get('volume_to_setup_mean')),
            'close_return_pct': display_number(metrics.get('close_return_pct')), 'bullish': metrics.get('close_gt_open'),
            'trend_gate': gate(setup.get('trend_at_setup'), 'trend'),
            'shrink_gate': gate(setup.get('shrink_at_setup'), 'shrink'),
            'surge_status': surge.get('status', 'UNKNOWN'),
            'reasons': list(dict.fromkeys(reason for part in conditions.values() for reason in part.get('reasons', []))),
            'candidates': candidates}


def exact_number(value, positive=False):
    if not isinstance(value, str) or not 0 < len(value) <= 80:
        raise ValueError('EXACT_DECIMAL_STRING_REQUIRED')
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('INVALID_DECIMAL') from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError('INVALID_PRICE_OR_VOLUME')
    return number


def iso_day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('INVALID_SOURCE_DATE')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('INVALID_SOURCE_DATE')
    return parsed


def series_from_frame(value, *, code, as_of, source_dataset_id, source_sha256):
    if (not isinstance(value, dict) or value.get('schema') != 'stock.chips.market.stock.v1'
            or value.get('dataset_id') != source_dataset_id or value.get('security_id') != code
            or value.get('as_of') != as_of):
        raise ValueError('SOURCE_FRAME_IDENTITY_MISMATCH')
    history = value.get('history')
    if not isinstance(history, list) or len(history) > 1000:
        raise ValueError('SOURCE_HISTORY_INVALID')
    bars, cutoff = [], iso_day(as_of)
    for item in history:
        if not isinstance(item, dict):
            raise ValueError('SOURCE_BAR_INVALID')
        day = iso_day(item.get('date'))
        if day > cutoff:
            continue
        if bars and day <= bars[-1].day:
            raise ValueError('SOURCE_HISTORY_ORDER_OR_DUPLICATE')
        op, cl, hi, lo = (exact_number(item.get(key), positive=True) for key in ('open', 'close', 'high', 'low'))
        volume = exact_number(item.get('volume_shares'))
        if hi < max(op, cl, lo) or lo > min(op, cl):
            raise ValueError('SOURCE_OHLC_INCONSISTENT')
        complete = item.get('complete', True)
        if type(complete) is not bool:
            raise ValueError('DAILY_COMPLETENESS_INVALID')
        bars.append(DailyBar(day, op, cl, hi, lo, volume, complete))
    if not bars or bars[-1].day != cutoff:
        raise ValueError('SIGNAL_DAY_MISSING')
    if not bars[-1].complete:
        raise ValueError('SIGNAL_DAY_INCOMPLETE')
    source = value.get('source') or {}
    provider = source.get('provider', 'SAVED_PUBLISHED_HISTORY') if isinstance(source, dict) else 'SAVED_PUBLISHED_HISTORY'
    return DailySeries(code, tuple(bars), source_sha256, source_sha256, provider, 'shares',
                       str(value.get('recorded_at', '')), False)


def unknown_detail(*, code, name, as_of, dataset_id, source_dataset_id, reason, source_sha256=None):
    value = {'status': 'UNKNOWN', 'rule_id': RULE_ID,
             'conditions': {'setup': {'reasons': [reason]}, 'surge': {'reasons': [reason]}}}
    row = {'code': code, 'name': name, 'date': as_of, 'source_status': 'UNKNOWN', 'status': 'UNKNOWN',
           'signal_state': 'UNKNOWN', 'close_cny': None, 'close_return_pct': None, 'setup_date': None,
           'shrink_ratio': None, 'volume_multiple': None, 'reasons': [reason]}
    return {'schema': 'stock.trend-volume.daily.stock.v1', 'dataset_id': dataset_id,
            'source_dataset_id': source_dataset_id, 'as_of': as_of, 'code': code, 'name': name,
            'source_status': 'UNKNOWN', 'provider': 'UNKNOWN', 'volume_unit': 'shares',
            'adjustment': 'unadjusted', 'history': [], 'row': row, 'screen': screen_detail(value),
            'source_sha256': source_sha256, 'parameter_sha256': PARAMETER_SHA256,
            'contract_sha256': CONTRACT_SHA256, 'can_generate_order': False}


def classify_frame(value, *, code, name, as_of, dataset_id, source_dataset_id, source_sha256):
    try:
        series = series_from_frame(value, code=code, as_of=as_of, source_dataset_id=source_dataset_id,
                                   source_sha256=source_sha256)
    except (ValueError, TypeError, KeyError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else 'SOURCE_FRAME_INVALID'
        return unknown_detail(code=code, name=name, as_of=as_of, dataset_id=dataset_id,
                              source_dataset_id=source_dataset_id, reason=reason, source_sha256=source_sha256)
    original = result_dict(screen_v3_day(series, iso_day(as_of), PARAMETERS))
    screen = screen_detail(original)
    state = ('SELECTED' if original['status'] == 'PASS' else 'UNKNOWN' if original['status'] == 'UNKNOWN'
             else 'WATCHING' if original['conditions']['setup']['status'] == 'PASS' else 'NOT_MATCHED')
    row = {'code': code, 'name': name, 'date': as_of, 'source_status': 'KNOWN', 'status': original['status'],
           'signal_state': state, 'close_cny': display_number(series.bars[-1].close),
           'close_return_pct': screen['close_return_pct'], 'setup_date': screen['setup_date'],
           'shrink_ratio': screen['shrink_gate']['metrics'].get('recent_to_reference_volume'),
           'volume_multiple': screen['volume_multiple'], 'reasons': screen['reasons']}
    history = [{'date': bar.day.isoformat(), 'open': display_number(bar.open), 'high': display_number(bar.high),
                'low': display_number(bar.low), 'close': display_number(bar.close), 'volume': display_number(bar.volume)}
               for bar in series.bars[-65:]]
    return {'schema': 'stock.trend-volume.daily.stock.v1', 'dataset_id': dataset_id,
            'source_dataset_id': source_dataset_id, 'as_of': as_of, 'code': code, 'name': name,
            'source_status': 'KNOWN', 'provider': series.provider, 'volume_unit': 'shares',
            'adjustment': 'unadjusted', 'history': history, 'row': row, 'screen': screen,
            'source_sha256': source_sha256, 'used_prefix_sha256': original['used_prefix_sha256'],
            'rule_id': RULE_ID, 'parameter_sha256': PARAMETER_SHA256, 'contract_sha256': CONTRACT_SHA256,
            'can_generate_order': False}
