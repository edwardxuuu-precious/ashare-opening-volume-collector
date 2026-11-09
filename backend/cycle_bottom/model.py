from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import csr_matrix, eye, hstack

MODEL_VERSION = 'cycle-quantile-highs-2010-2019-v1'
TRAIN_START = '2010-01-01'
TRAIN_END = '2019-12-31'
QUANTILE = 0.10
THRESHOLD = 3.0
COOLDOWN = 63
YEARS = (2012, 2016, 2018, 2022, 2024)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def quantile_fit(x, y):
    if len(x) < 500 or len(x) != len(y):
        raise ValueError('FIT_HISTORY_INSUFFICIENT')
    x, y = np.log(np.array(x, dtype=float)), np.log(np.array(y, dtype=float))
    if not np.isfinite(x).all() or not np.isfinite(y).all() or np.var(x) < 1e-8:
        raise ValueError('FIT_NOT_IDENTIFIABLE')
    count = len(x)
    design = hstack([csr_matrix(np.column_stack([np.ones(count), x])), eye(count), -eye(count)], format='csr')
    objective = np.concatenate([[0., 0.], np.full(count, QUANTILE), np.full(count, 1 - QUANTILE)])
    solution = linprog(objective, A_eq=design, b_eq=y,
                       bounds=[(None, None), (None, None)] + [(0, None)] * (2 * count),
                       method='highs', options={'time_limit': 10})
    if not solution.success or solution.status != 0:
        raise ValueError('QUANTILE_OPTIMIZATION_FAILED')
    alpha, beta = map(float, solution.x[:2])
    residual = y - beta * x
    low, high = map(float, np.quantile(residual, [.05, .15]))
    return alpha, beta, low, high


def forward_outcomes(day, equity):
    days = sorted(equity)
    index = days.index(day)
    results = []
    for horizon in (126, 252):
        complete = index + horizon < len(days)
        if not complete:
            results.append({'sessions': horizon, 'complete': False, 'returnPct': None, 'downsidePct': None})
            continue
        values = [equity[d] for d in days[index:index + horizon + 1]]
        results.append({'sessions': horizon, 'complete': True,
                        'returnPct': 100 * (values[-1] / values[0] - 1),
                        'downsidePct': 100 * (min(values) / values[0] - 1)})
    return results


def entry_events(points, equity, *, field):
    result, last_entry = [], -COOLDOWN
    for index in range(1, len(points)):
        point, previous = points[index], points[index - 1]
        if point['date'] < TRAIN_START or index - last_entry < COOLDOWN:
            continue
        entering = (abs(point[field]) <= THRESHOLD < abs(previous[field]) if field == 'rawGapPct'
                    else point[field] <= THRESHOLD < previous[field])
        if entering:
            result.append({'date': point['date'], 'gapPct': point[field],
                           'phase': 'training' if point['date'] <= TRAIN_END else 'holdout',
                           'outcomes': forward_outcomes(point['date'], equity)})
            last_entry = index
    return result


def build_comparison(equity_rows, reference_rows, *, identifier, name, code):
    equity = {row['date']: row['close'] for row in equity_rows}
    reference = {row['date']: row['close'] for row in reference_rows}
    days = sorted(equity.keys() & reference.keys())
    train = [day for day in days if TRAIN_START <= day <= TRAIN_END]
    alpha, beta, low, high = quantile_fit([reference[d] for d in train], [equity[d] for d in train])
    base = days[0]
    points = []
    for day in days:
        predicted = math.exp(alpha + beta * math.log(reference[day]))
        normalized_equity = equity[day] / equity[base] * 100
        normalized_reference = reference[day] / reference[base] * 100
        points.append({'date': day, 'equity': normalized_equity, 'reference': normalized_reference,
                       'fitted': predicted / equity[base] * 100,
                       'lower': math.exp(low + beta * math.log(reference[day])) / equity[base] * 100,
                       'upper': math.exp(high + beta * math.log(reference[day])) / equity[base] * 100,
                       'gapPct': 100 * (equity[day] / predicted - 1),
                       'rawGapPct': 100 * (normalized_equity / normalized_reference - 1)})
    point_by_date = {point['date']: point for point in points}
    training_gaps = [point_by_date[d]['gapPct'] for d in train]
    holdout = [p for p in points if p['date'] > TRAIN_END]
    training_min = min(training_gaps)
    cases = []
    for year in YEARS:
        year_days = [d for d in equity if d.startswith(str(year))]
        if not year_days:
            continue
        day = min(year_days, key=equity.get)
        if day not in reference:
            continue
        point = point_by_date[day]
        cases.append({'year': year, 'date': day, 'equityClose': equity[day], 'referenceClose': reference[day],
                      'fittedClose': point['fitted'] * equity[base] / 100,
                      'gapPct': point['gapPct'], 'rawGapPct': point['rawGapPct'],
                      'phase': 'training' if day <= TRAIN_END else 'holdout',
                      'outcomes': forward_outcomes(day, equity)})
    latest = points[-1]
    return {'id': identifier, 'name': name, 'code': code, 'asOf': days[-1], 'baseDate': base,
            'points': points,
            'fit': {'method': 'log-quantile-highs-v1', 'trainStart': TRAIN_START, 'trainEnd': TRAIN_END,
                    'quantile': QUANTILE, 'alpha': alpha, 'beta': beta, 'samples': len(train),
                    'optimizer': 'scipy.optimize.linprog/highs', 'residualBandQuantiles': [.05, .15],
                    'trainingMinGapPct': training_min,
                    'holdoutMinGapPct': min(p['gapPct'] for p in holdout) if holdout else None,
                    'holdoutBelowTrainingDays': sum(p['gapPct'] < training_min - 1e-6 for p in holdout),
                    'comparisonEpsilonPp': 1e-6},
            'latest': {'equityClose': equity[days[-1]], 'referenceClose': reference[days[-1]],
                       'fittedClose': latest['fitted'] * equity[base] / 100,
                       'gapPct': latest['gapPct'], 'rawGapPct': latest['rawGapPct'],
                       'gapChange20Pp': latest['gapPct'] - points[-21]['gapPct'] if len(points) > 20 else None},
            'cases': cases, 'events': entry_events(points, equity, field='gapPct'),
            'rawTouchEvents': entry_events(points, equity, field='rawGapPct'),
            'thresholdPct': THRESHOLD, 'cooldownSessions': COOLDOWN}


def build_monthly(rows, *, today):
    groups = {}
    for row in rows:
        groups.setdefault(row['date'][:7], []).append(row)
    monthly = []
    for month, bars in sorted(groups.items()):
        monthly.append({'month': month, 'date': bars[-1]['date'], 'open': bars[0]['open'],
                        'high': max(bar['high'] for bar in bars), 'low': min(bar['low'] for bar in bars),
                        'close': bars[-1]['close'], 'incomplete': month >= today[:7]})
    start, end, origin = '1997-07-01', '2019-12-31', date(1997, 7, 1)
    years = lambda day: (date.fromisoformat(day) - origin).days / 365.2425
    train = [bar for bar in monthly if start <= bar['date'] <= end]
    if len(train) < 200:
        raise ValueError('MONTHLY_TRAINING_HISTORY_INSUFFICIENT')
    x = np.array([years(bar['date']) for bar in train])
    y = np.log([bar['close'] for bar in train])
    slope = float(np.sum((x - x.mean()) * (y - y.mean())) / np.sum((x - x.mean()) ** 2))
    intercept = float(y.mean() - slope * x.mean())
    quantiles = list(map(float, np.quantile(y - intercept - slope * x, [.1, .5, .9])))
    for bar in monthly:
        center = intercept + slope * years(bar['date'])
        for key, residual in zip(('lower', 'middle', 'upper'), quantiles):
            bar[key] = math.exp(center + residual)
    return {'asOf': rows[-1]['date'], 'points': monthly,
            'fit': {'method': 'log-time-ols-with-residual-10-50-90', 'trainStart': start, 'trainEnd': end,
                    'samples': len(train), 'annualLogSlope': slope, 'intercept': intercept,
                    'originDate': origin.isoformat(), 'residualQuantiles': quantiles},
            'latestGapPct': 100 * (monthly[-1]['close'] / monthly[-1]['lower'] - 1)}


def build_snapshot(data, sources, *, expected_as_of, generated_at=None, today=None):
    if any(source['id'] == 'equity' and source['missingSessions'] > 0 for source in sources):
        raise ValueError('EQUITY_HISTORY_GAP_CANNOT_CERTIFY_FORWARD_WINDOWS')
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    today = today or generated_at[:10]
    names = {source['id']: source for source in sources}
    comparisons = [build_comparison(data['equity'], data[key], identifier=key, name=names[key]['name'], code=names[key]['code'])
                   for key in ('nonpure', 'government') if key in data]
    monthly = build_monthly(data['shanghai'], today=today)
    observations = []
    if 'nonpure' in data:
        observations.append({'title': '公开参照与原图的区别',
                             'detail': '当前使用930897中证非纯债债券型基金指数独立观察，它不是原图Wind885007.WI。不同样本、权重与基期会改变差距和触线日期；本页不把这条曲线续接或估算为Wind官方行情。'})
    for comparison in comparisons:
        value, fit = comparison['latest'], comparison['fit']
        observations.append({'title': comparison['name'] + '相对位置',
                             'detail': f"共同截至{comparison['asOf']}，沪深300全收益距固定历史拟合线{value['gapPct']:+.2f}%；近20个共同观察日变化{value['gapChange20Pp']:+.2f}个百分点。该数值是模型相对位置，不是预计下跌幅度。"})
        if fit['holdoutBelowTrainingDays']:
            observations.append({'title': comparison['name'] + '训练外反证',
                                 'detail': f"2020年后已有{fit['holdoutBelowTrainingDays']}个共同观察日低于全部训练样本的最低相对位置；训练外最低差距{fit['holdoutMinGapPct']:+.2f}%。历史拟合线不能视作不会跌破的底部。"})
        matured = [event for event in comparison['events'] if event['phase'] == 'holdout' and event['outcomes'][0]['complete']]
        if matured:
            returns = [event['outcomes'][0]['returnPct'] for event in matured]
            downsides = [event['outcomes'][0]['downsidePct'] for event in matured]
            observations.append({'title': comparison['name'] + '历史触线后表现',
                                 'detail': f"2020年后进入拟合观察区且已满126交易日的{len(matured)}次记录，126日收益中位数{float(np.median(returns)):+.2f}%，其后最深下探{min(downsides):+.2f}%。逐次记录保留，未成熟窗口不补零。"})
    observations.append({'title': '上证月线结构',
                         'detail': f"实际行情截至{monthly['asOf']}，最新收盘{monthly['points'][-1]['close']:.2f}，距固定历史通道下沿{monthly['latestGapPct']:+.2f}%；当前月尚未收完时，月K随已收盘交易日变化。"})
    unavailable = [{'code': '885007.WI', 'name': '万得混合债券型二级基金指数',
                    'reason': '原图指数尚未接入持续数据源，当前未自动更新。此前同名指数历史已做本地离线复核；930897公开参照与其不同，不用于补齐或延长原Wind曲线。'}]
    core = {'schema': 'stock.cycle-bottom.v1', 'modelVersion': MODEL_VERSION, 'expectedAsOf': expected_as_of,
            'sources': sources, 'comparisons': comparisons, 'monthly': monthly,
            'observations': observations, 'unavailable': unavailable}
    identity = {**core, 'sources': [{key: value for key, value in source.items() if key != 'url'} for source in sources]}
    version = hashlib.sha256(encoded(identity)).hexdigest()[:16]
    return {**core, 'version': version, 'generatedAt': generated_at}
