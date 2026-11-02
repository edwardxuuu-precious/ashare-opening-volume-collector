"""Pure price selection and classification over a supplied daily prefix."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from backend.chips.calendar import sessions
from backend.trend_volume.model import series_from_frame


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


_POLICY = json.loads(Path(__file__).with_name('policy.json').read_bytes())
RULE_ID = _POLICY['rule_id']
POLICY_SHA256 = digest(encoded(_POLICY))
CATEGORIES = ('ABOVE', 'BELOW', 'NEAR_M', 'NEAR_L', 'NEAR_H', 'FAR', 'AMBIGUOUS', 'HISTORY', 'NO_DRAW', 'UNKNOWN')
DRAWING_STATES = ('CURRENT', 'NEW_REFERENCE', 'HISTORY', 'NO_DRAW', 'UNKNOWN')


def parameter_values():
    return deepcopy(_POLICY)


def number(value):
    if isinstance(value, bool):
        raise ValueError('INVALID_PRICE')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('INVALID_PRICE') from exc
    if not result.is_finite() or result <= 0:
        raise ValueError('INVALID_PRICE')
    return result


def price(value):
    return format(value, 'f')


@dataclass(frozen=True)
class Pivot:
    index: int
    kind: str
    value: Decimal
    confirmed: int
    ambiguous: bool = False


def _pivots(rows):
    points = []
    left = _POLICY['candidate_turn_left_bars']
    right = _POLICY['candidate_turn_right_bars']
    for i in range(left, len(rows) - right):
        neighbors = rows[i-left:i] + rows[i+1:i+right+1]
        on_bar = []
        for kind, field in (('H', 'high'), ('L', 'low')):
            value = number(rows[i][field])
            values = [number(row[field]) for row in neighbors]
            if value != (max(values + [value]) if kind == 'H' else min(values + [value])):
                continue
            on_bar.append(Pivot(i, kind, value, i + right))
        if len(on_bar) == 2:
            points.append(Pivot(i, 'X', Decimal(0), i + right, True))
        else:
            points.extend(on_bar)
    compressed = []
    for point in points:
        if compressed and point.kind == compressed[-1].kind and point.kind != 'X':
            old = compressed[-1]
            if point.value == old.value:
                compressed[-1] = Pivot(point.index, point.kind, point.value, point.confirmed)
            elif point.value > old.value if point.kind == 'H' else point.value < old.value:
                compressed[-1] = point
        else:
            compressed.append(point)
    return compressed


def _field(point):
    return 'high' if point.kind == 'H' else 'low'


def _proof(rows, point, role):
    return {'date': rows[point.index]['date'], 'field': _field(point), 'value': price(point.value), 'role': role}


def _origin_family(rows, points, before_index, direction):
    kind = 'H' if direction == 'DOWN' else 'L'
    eligible = []
    for point in points:
        if point.index >= before_index:
            continue
        if point.kind == kind:
            eligible.append(point)
        elif point.kind == 'X':
            field = 'high' if kind == 'H' else 'low'
            eligible.append(Pivot(point.index, kind, number(rows[point.index][field]), point.confirmed))
    if not eligible:
        return []
    cursor = len(eligible) - 1
    family = []
    trace = []
    while cursor >= 0:
        current = eligible[cursor]
        if current.ambiguous or current.kind != kind:
            break
        trace.append(current)
        family.append((current, tuple(trace)))
        if cursor == 0:
            break
        previous = eligible[cursor-1]
        between = [point for point in points if previous.index < point.index < current.index]
        after = [point for point in points if current.index < point.index < before_index]
        opposite = 'L' if kind == 'H' else 'H'
        old_opposites = [point for point in between if point.kind == opposite and not point.ambiguous]
        near_opposites = [point for point in after if point.kind == opposite and not point.ambiguous]
        if (previous.ambiguous or any(point.kind == 'X' for point in between)
                or not old_opposites or not near_opposites):
            break
        continues = current.value < previous.value if direction == 'DOWN' else current.value > previous.value
        if not continues:
            break
        trace.extend((near_opposites[-1], old_opposites[-1]))
        cursor -= 1
    return family


def _is_secondary(family, value, direction):
    return any(origin.value > value if direction == 'DOWN' else origin.value < value for origin, _ in family)


def _episodes(rows, points):
    episodes = []
    for i in range(1, len(points)):
        before, axis = points[i-1:i+1]
        if (axis.kind == 'X' or before.kind == 'X' or axis.kind == before.kind
                or any(p.ambiguous for p in (before, axis))):
            continue
        direction = 'DOWN' if axis.kind == 'H' else 'UP'
        family = _origin_family(rows, points, axis.index, direction)
        if not family or not _is_secondary(family, axis.value, direction):
            continue
        threshold = (min(number(rows[j]['low']) for j in (axis.index-1, axis.index)) if direction == 'DOWN'
                     else max(number(rows[j]['high']) for j in (axis.index-1, axis.index)))
        departure = next((j for j in range(axis.confirmed, len(rows))
                          if (number(rows[j]['close']) < threshold if direction == 'DOWN'
                              else number(rows[j]['close']) > threshold)), None)
        if departure is None:
            continue
        episodes.append({'direction': direction, 'axis': axis, 'source': axis, 'reaction': axis.index,
                         'evidence_index': max(before.confirmed, axis.confirmed, departure),
                         'route': 'TURN', 'family': family,
                         'proofs': [_proof(rows, before, '本轮反向波段起点'), _proof(rows, axis, '完整反向波段实际极值'),
                                    {'date': rows[departure]['date'], 'field': 'close', 'value': rows[departure]['close'],
                                     'role': '右侧收盘已离开峰谷及前一根K线的范围'}],
                         'reason': '左侧反弹／回落有独立起点，中轴取本段实际极值；右侧收盘已越过峰谷及其前一根K线的反向边界，确认回转，不额外等待另一个完整新低／新高。'})
    for support in points:
        if support.kind == 'X' or support.ambiguous:
            continue
        direction = 'DOWN' if support.kind == 'L' else 'UP'
        p = support.value
        for reaction in range(support.confirmed + 1, len(rows)):
            row = rows[reaction]
            failed = (number(row['high']) == p and number(row['close']) < p if direction == 'DOWN'
                      else number(row['low']) == p and number(row['close']) > p)
            if not failed:
                continue
            breaks = [j for j in range(support.confirmed + 1, reaction)
                      if (number(rows[j-1]['close']) >= p > number(rows[j]['close']) if direction == 'DOWN'
                          else number(rows[j-1]['close']) <= p < number(rows[j]['close']))]
            if not breaks:
                continue
            broken = breaks[-1]
            reclaimed = any(number(rows[j]['close']) > p if direction == 'DOWN' else number(rows[j]['close']) < p
                            for j in range(broken + 1, reaction + 1))
            if reclaimed:
                continue
            family = _origin_family(rows, points, reaction, direction)
            if not family or not _is_secondary(family, p, direction):
                continue
            axis = Pivot(reaction, 'H' if direction == 'DOWN' else 'L', p, reaction)
            episode = {'direction': direction, 'axis': axis, 'source': support, 'reaction': reaction, 'break_index': broken,
                       'evidence_index': max(support.confirmed, reaction), 'route': 'ROLE', 'family': family,
                       'proofs': [_proof(rows, support, '已有结构支撑／阻力'),
                                  {'date': rows[broken]['date'], 'field': 'close', 'value': rows[broken]['close'], 'role': '本波段收盘失守／收复'},
                                  _proof(rows, axis, '独立回测到原价'),
                                  {'date': row['date'], 'field': 'close', 'value': row['close'], 'role': '回测收盘仍在失败一侧'}],
                       'reason': '已有结构价位经收盘穿越后，另一个交易日精确回测并收在失败一侧；角色来源可以在原点左侧，但失守和回测必须属于当前结构。'}
            episodes.append(episode)
    return episodes + _pressure_episodes(rows, points)


def _pressure_episodes(rows, points):
    output = []
    for direction in ('DOWN', 'UP'):
        groups, active, shared_low, shared_high = [], [], None, None
        for index, row in enumerate(rows):
            body_low, body_high = sorted((number(row['open']), number(row['close'])))
            low, high = ((body_high, number(row['high'])) if direction == 'DOWN'
                         else (number(row['low']), body_low))
            if low >= high:
                if len(active) >= 2:
                    groups.append(active)
                active, shared_low, shared_high = [], None, None
                continue
            new_low = max(shared_low, low) if shared_low is not None else low
            new_high = min(shared_high, high) if shared_high is not None else high
            if active and new_low > new_high:
                if len(active) >= 2:
                    groups.append(active)
                active, new_low, new_high = [], low, high
            active.append(index)
            shared_low, shared_high = new_low, new_high
        if len(active) >= 2:
            groups.append(active)
        for group in groups:
            field, kind = ('high', 'H') if direction == 'DOWN' else ('low', 'L')
            values = [number(rows[j][field]) for j in group]
            value = max(values) if direction == 'DOWN' else min(values)
            peak = next(j for j in reversed(group) if number(rows[j][field]) == value)
            if peak < 2 or peak + 2 >= len(rows):
                continue
            neighbors = rows[peak-2:peak] + rows[peak+1:peak+3]
            if any(number(row[field]) > value if direction == 'DOWN' else number(row[field]) < value for row in neighbors):
                continue
            zone_edge = min(values) if direction == 'DOWN' else max(values)
            if any(number(rows[j]['close']) >= zone_edge if direction == 'DOWN'
                   else number(rows[j]['close']) <= zone_edge for j in group):
                continue
            boundary = (min(min(number(rows[j]['open']), number(rows[j]['close'])) for j in group)
                        if direction == 'DOWN' else
                        max(max(number(rows[j]['open']), number(rows[j]['close'])) for j in group))
            departure = None
            for j in range(max(group[-1]+1, peak+2), len(rows)):
                if number(rows[j][field]) > value if direction == 'DOWN' else number(rows[j][field]) < value:
                    break
                if number(rows[j]['close']) < boundary if direction == 'DOWN' else number(rows[j]['close']) > boundary:
                    departure = j
                    break
            if departure is None:
                continue
            family = _origin_family(rows, points, group[0], direction)
            if not family or not _is_secondary(family, value, direction):
                continue
            axis = Pivot(peak, kind, value, peak+2)
            proofs = []
            for j in group:
                proofs.extend(({'date': rows[j]['date'], 'field': field, 'value': rows[j][field], 'role': '同段反复上探／下探'},
                               {'date': rows[j]['date'], 'field': 'close', 'value': rows[j]['close'], 'role': '收盘未穿过该段共同压力／支撑区域'}))
            proofs.append({'date': rows[departure]['date'], 'field': 'close', 'value': rows[departure]['close'],
                           'role': '收盘离开整段反复测试的实体范围'})
            output.append({'direction': direction, 'axis': axis, 'source': axis, 'reaction': peak,
                           'evidence_index': departure, 'route': 'ROLE', 'role_kind': 'REPEATED_REACTION',
                           'family': family, 'proofs': proofs,
                           'zone': {'lower': price(zone_edge if direction == 'DOWN' else value),
                                    'upper': price(value if direction == 'DOWN' else zone_edge),
                                    'start_date': rows[group[0]]['date'], 'end_date': rows[group[-1]]['date']},
                           'reason': '同段至少两次上影／下影存在共同价位交集，收盘均未穿过压力／支撑区，随后收盘离开整段实体范围；区域由实际K线交集形成，中轴取该段实际极值，不用2%或均价聚类。'})
    return output


def evaluate_structure(rows, structure):
    result = deepcopy(structure)
    result.update(life_state='OPEN', guides_end_on=None)
    direction = structure['direction']
    h, m, low = (number(structure['levels'][key]) for key in ('H', 'M', 'L'))
    for row in rows:
        if row['date'] <= structure['evidence_through']:
            continue
        reached = number(row['low']) <= low if direction == 'DOWN' else number(row['high']) >= h
        origin_lost = number(row['high']) > h if direction == 'DOWN' else number(row['low']) < low
        axis_lost = number(row['close']) > m if direction == 'DOWN' else number(row['close']) < m
        if not (reached or origin_lost or axis_lost):
            continue
        if reached and (origin_lost or axis_lost):
            state, reason = 'INVALID', '同一日线既触及目标又失去原点／中轴，先后未知，停止当前参考。'
        elif reached:
            state, reason = 'REACHED', '参考端在证据完成后首次触达，线段到该日停止。'
        else:
            state, reason = 'INVALID', '原点被越过或价格先收盘跨回中轴另一侧，停止当前参考。'
        result.update(life_state=state, guides_end_on=row['date'], end_reason=reason)
        break
    return result


def _rejection(rows, episode, origin, code, reason):
    source = episode['source']
    return {'direction': episode['direction'], 'origin_date': rows[origin.index]['date'],
            'origin_price': price(origin.value), 'axis_date': rows[source.index]['date'],
            'axis_price': price(source.value), 'route': episode['route'],
            'reaction_date': rows[episode['reaction']]['date'], 'reason_code': code, 'reason': reason}


def _assess_episode(rows, episode):
    rejected = []
    direction, axis = episode['direction'], episode['axis']
    for position, (origin, trace) in enumerate(episode['family']):
        evidence_index = max(episode['evidence_index'], *(p.confirmed for p in trace))
        if episode['route'] == 'ROLE' and 'break_index' in episode and origin.index > episode['break_index']:
            rejected.append(_rejection(rows, episode, origin, 'ROLE_OUTSIDE_WAVE', '失守／收复发生在原点之前，不能拼接成同一波段。'))
            continue
        target = 2 * axis.value - origin.value
        ordered = origin.value > axis.value > target > 0 if direction == 'DOWN' else target > axis.value > origin.value > 0
        if not ordered:
            rejected.append(_rejection(rows, episode, origin, 'INVALID_GEOMETRY', '该结构的原点与中轴不能形成正价格的等高区间。'))
            continue
        prefix = rows[origin.index:evidence_index+1]
        breached = any(number(row['high']) > origin.value if direction == 'DOWN' else number(row['low']) < origin.value
                       for row in prefix)
        if breached:
            rejected.append(_rejection(rows, episode, origin, 'ORIGIN_ALREADY_BREACHED', '原点在证据齐备前已被越过，需要复查既有父结构。'))
            continue
        pre_reached = any(number(row['low']) <= target if direction == 'DOWN' else number(row['high']) >= target
                          for row in prefix)
        if pre_reached:
            rejected.append(_rejection(rows, episode, origin, 'TARGET_BEFORE_EVIDENCE', '从原点至全部选点证据完成日已经到过推算端；仅复查预先形成的父结构，不沿价格向左搜索。'))
            continue
        source = episode['source']
        origin_data = {'date': rows[origin.index]['date'], 'field': _field(origin), 'price': price(origin.value), 'kind': origin.kind}
        axis_data = {'date': rows[source.index]['date'], 'field': _field(source), 'price': price(source.value)}
        levels = {'H': price(origin.value if direction == 'DOWN' else target), 'M': price(axis.value),
                  'L': price(target if direction == 'DOWN' else origin.value)}
        identity = {'direction': direction, 'origin': origin_data, 'axis': axis_data,
                    'reaction_on': rows[episode['reaction']]['date'], 'levels': levels}
        proofs = list(episode['proofs']) + [_proof(rows, point, '波段起点及父子延续依据') for point in trace]
        unique = {(item['date'], item['field'], item['value'], item['role']): item for item in proofs}
        scope = ('近端组合未通过提前到价检查；采用已在高低点同向延续链中的父起点。'
                 if position else '采用当前反应段之前的近端结构起点，没有直接套用窗口最大／最小值。')
        result = {'id': digest(encoded(identity))[:24], 'direction': direction, 'levels': levels,
                  'origin': origin_data, 'axis': axis_data, 'route': episode['route'],
                  'reaction_on': rows[episode['reaction']]['date'],
                  'evidence_through': rows[evidence_index]['date'], 'recorded_at': None, 'effective_from': None,
                  'life_state': 'OPEN', 'guides_end_on': None, 'reasons': [scope, episode['reason']],
                  'evidence': sorted(unique.values(), key=lambda item: (item['date'], item['field'], item['role'])),
                  'role_zone': None}
        if episode['route'] == 'ROLE':
            result['role_zone'] = episode.get('zone', {'lower': price(axis.value), 'upper': price(axis.value),
                                                       'start_date': rows[source.index]['date'], 'end_date': rows[episode['reaction']]['date']})
        return evaluate_structure(rows, result), rejected
    return None, rejected


def select_structure(history):
    rows = history[-_POLICY['analysis_bars']:]
    if len(rows) < _POLICY['analysis_bars']:
        return {'state': 'NO_DRAW', 'structure': None, 'rejected_candidates': [],
                'reasons': ['实际日线不足60根，波段及阶段转折背景不足，不强画。']}
    points = _pivots(rows)
    episodes = _episodes(rows, points)
    rejected = []
    latest_reaction = max((item['reaction'] for item in episodes), default=None)
    for episode in episodes:
        if episode['reaction'] != latest_reaction:
            rejected.append(_rejection(rows, episode, episode['family'][0][0], 'EARLIER_REACTION_NOT_CURRENT',
                                       '属于更早的独立反应段，不用旧段替换当前波段的判断，也不为保留未到价目标回捞旧组合。'))
    for reaction in ([] if latest_reaction is None else [latest_reaction]):
        accepted = []
        for episode in (item for item in episodes if item['reaction'] == reaction):
            structure, reasons = _assess_episode(rows, episode)
            rejected.extend(reasons)
            if structure is not None:
                accepted.append(structure)
        merged = {}
        for candidate in accepted:
            key = encoded({'direction': candidate['direction'], 'origin': candidate['origin'], 'levels': candidate['levels']})
            previous = merged.get(key)
            if previous is None or candidate['evidence_through'] < previous['evidence_through']:
                merged[key] = candidate
            elif (candidate['evidence_through'] == previous['evidence_through'] and candidate['route'] != previous['route']):
                previous['route'] = 'BOTH'
                previous['reasons'] = list(dict.fromkeys(previous['reasons'] + candidate['reasons']))
                proofs = {(v['date'], v['field'], v['value'], v['role']): v for v in previous['evidence'] + candidate['evidence']}
                previous['evidence'] = sorted(proofs.values(), key=lambda v: (v['date'], v['field'], v['role']))
        if len(merged) > 1:
            for candidate in merged.values():
                rejected.append({'direction': candidate['direction'], 'origin_date': candidate['origin']['date'],
                                 'origin_price': candidate['origin']['price'], 'axis_date': candidate['axis']['date'],
                                 'axis_price': candidate['axis']['price'], 'route': candidate['route'],
                                 'reaction_date': candidate['reaction_on'], 'reason_code': 'COMPETING_CURRENT_STRUCTURES',
                                 'reason': '同一最新反应段存在不同但均可成立的原点／中轴组合，不按目标大小强选。'})
            return {'state': 'AMBIGUOUS', 'structure': None, 'rejected_candidates': rejected,
                    'reasons': ['最新独立反应段存在同等可解释的不同组合，中轴与波段归属待核，不强画。']}
        if merged:
            structure = next(iter(merged.values()))
            return {'state': 'DRAW', 'structure': structure, 'rejected_candidates': rejected,
                    'reasons': structure['reasons']}
    reason = ('已有局部候选，但原点、完整阶段或提前到价检查未能形成一致组合，不强画。' if episodes
              else '尚无同时具备完整阶段转折或清楚角色反应与可配对波段的中轴，不强画。')
    return {'state': 'NO_DRAW', 'structure': None, 'rejected_candidates': rejected, 'reasons': [reason]}


def classify_position(bar, structure, previous_bar=None, *, same_structure=False, near_percent='2'):
    levels = {key: number(structure['levels'][key]) for key in ('H', 'M', 'L')}
    if not levels['H'] > levels['M'] > levels['L']:
        raise ValueError('UNORDERED_LEVELS')
    close, high, low = (number(bar[key]) for key in ('close', 'high', 'low'))
    near = number(near_percent)
    distances = {key: (close - value) / value * 100 for key, value in levels.items()}
    tags = [key for key in ('H', 'M', 'L') if abs(close-levels[key]) * 100 <= levels[key] * near]
    if close > levels['H']:
        category = 'ABOVE'
    elif close < levels['L']:
        category = 'BELOW'
    elif tags:
        proportional = {key: Fraction(abs(close-levels[key])) / Fraction(levels[key]) for key in tags}
        nearest = min(proportional.values())
        tied = [key for key in tags if proportional[key] == nearest]
        category = 'NEAR_' + tied[0] if len(tied) == 1 else 'AMBIGUOUS'
    else:
        category = 'FAR'
    events = []
    if high > levels['H'] and close <= levels['H']:
        events.append('UPPER_REJECTED')
    if low < levels['L'] and close >= levels['L']:
        events.append('LOWER_RECOVERED')
    events.extend('TOUCH_' + key for key, value in levels.items() if low <= value <= high)
    crossing = 'UNKNOWN'
    if same_structure and previous_bar is not None:
        previous_close = number(previous_bar['close'])
        crossing = ('UP' if previous_close <= levels['H'] < close else
                    'DOWN' if previous_close >= levels['L'] > close else 'NONE')
    return {'category': category, 'close': price(close), 'near_tags': ['NEAR_' + key for key in tags],
            'intraday_tags': events, 'new_crossing': crossing,
            'distance_percent': {key: price(value) for key, value in distances.items()}}


def unknown_detail(*, code, name, as_of, dataset_id, source_dataset_id, reason,
                   source_sha256=None, previous=None, recorded_at=None, effective_from=None):
    messages = {
        'SOURCE_FRAME_IDENTITY_MISMATCH': '行情批次、股票代码或交易日身份不一致，暂不分类。',
        'SOURCE_HISTORY_INVALID': '历史日线格式或数量无法核实，暂不分类。',
        'SOURCE_HISTORY_ORDER_OR_DUPLICATE': '历史日线日期重复或顺序异常，暂不分类。',
        'SIGNAL_DAY_MISSING': '缺少所选交易日的行情，保留待更新状态。',
        'SIGNAL_DAY_INCOMPLETE': '所选交易日行情尚不完整，暂不分类。',
        'INCOMPLETE_SOURCE_PREFIX': '选点背景中存在不完整日线，暂不画线。',
        'SOURCE_OHLC_INCONSISTENT': '开高低收相互矛盾，无法核实这份行情。',
        'ZERO_VOLUME_TRADE_STATUS_UNVERIFIED': '当日成交量为零但交易状态尚未核实，不擅自认作停牌或正常行情。',
        'SOURCE_FRAME_MISSING': '这只股票的已发布行情暂缺，等待完整数据。',
        'SOURCE_OBJECT_READ_FAILED': '这只股票的行情暂时读取失败，保留待核状态。',
        'SOURCE_FRAME_INVALID': '必要行情字段无法核实，暂不分类。'
    }
    message = messages.get(reason, reason if any(ord(char) > 127 for char in str(reason))
                           else '所需行情无法核实，本轮保留待更新状态；具体原因码可展开查看。')
    row = {'code': code, 'name': name, 'as_of': as_of, 'source_status': 'UNKNOWN',
           'drawing_state': 'UNKNOWN', 'category': 'UNKNOWN', 'close': None,
           'H': None, 'M': None, 'L': None, 'direction': None, 'basis': 'NONE',
           'near_tags': [], 'intraday_tags': [], 'new_crossing': 'UNKNOWN',
           'distance_percent': {'H': None, 'M': None, 'L': None}, 'reasons': [message], 'structure_id': None}
    return {'schema': 'stock.golden-projection.daily.stock.v1', 'dataset_id': dataset_id,
            'source_dataset_id': source_dataset_id, 'as_of': as_of, 'code': code, 'name': name,
            'rule_id': RULE_ID, 'policy_sha256': POLICY_SHA256, 'row': row, 'history': [],
            'structure': None, 'candidate_next_session': None, 'rejected_candidates': [],
            'source': {'key': f'data/chips/daily/{source_dataset_id}/{code}.json',
                       'sha256': source_sha256, 'last_bar_date': None, 'provider': 'UNKNOWN'},
            'reason_code': reason, 'historical_known_at': None, 'can_generate_order': False}


def _identity(structure):
    return {'direction': structure['direction'], 'origin': structure['origin'], 'axis': structure['axis'],
            'reaction_on': structure['reaction_on'], 'levels': structure['levels']}


def _verified_prior_structures(previous, rows, code, as_of):
    if (not isinstance(previous, dict) or previous.get('code') != code
            or previous.get('schema') != 'stock.golden-projection.daily.stock.v1'
            or previous.get('rule_id') != RULE_ID or previous.get('policy_sha256') != POLICY_SHA256
            or not isinstance(previous.get('as_of'), str) or previous['as_of'] >= as_of):
        return []
    by_date = {row['date']: row for row in rows}
    results = []
    for structure in (previous.get('candidate_next_session'), previous.get('structure')):
        if not isinstance(structure, dict):
            continue
        try:
            h, m, low = (number(structure['levels'][key]) for key in ('H', 'M', 'L'))
            origin, axis = structure['origin'], structure['axis']
            direction = structure['direction']
            if (not h > m > low or direction not in ('DOWN', 'UP') or number(axis['price']) != m
                    or number(origin['price']) != (h if direction == 'DOWN' else low)
                    or 2 * m != h + low or digest(encoded(_identity(structure)))[:24] != structure['id']
                    or structure['evidence_through'] > previous['as_of']):
                continue
            for point in (origin, axis):
                bar = by_date.get(point['date'])
                if bar is None or point['field'] not in ('open', 'high', 'low', 'close') or number(bar[point['field']]) != number(point['price']):
                    raise ValueError('PRIOR_POINT_NOT_IN_SOURCE')
            for proof in structure['evidence']:
                bar = by_date.get(proof['date'])
                if (bar is None or proof['date'] > structure['evidence_through']
                        or proof['field'] not in ('open', 'high', 'low', 'close')
                        or number(bar[proof['field']]) != number(proof['value'])):
                    raise ValueError('PRIOR_EVIDENCE_NOT_IN_SOURCE')
            if structure['life_state'] not in ('OPEN', 'REACHED', 'INVALID'):
                continue
            if not any(value['id'] == structure['id'] for value in results):
                results.append(deepcopy(structure))
        except (KeyError, ValueError, TypeError, ArithmeticError):
            continue
    return results


def _pre_open_effective(structure, as_of):
    try:
        effective = structure['effective_from']
        recorded = datetime.fromisoformat(structure['recorded_at'].replace('Z', '+00:00'))
        if not isinstance(effective, str) or effective > as_of or recorded.tzinfo is None:
            return False
        opening = datetime.combine(datetime.fromisoformat(as_of).date(), time(9, 30), timezone(timedelta(hours=8)))
        return recorded < opening
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _adjacent_previous(previous, rows, as_of):
    try:
        calendar = sessions('2026-01-01', as_of)
        return (len(calendar) > 1 and calendar[-1] == as_of and previous['as_of'] == calendar[-2]
                and len(rows) > 1 and rows[-2]['date'] == previous['as_of'])
    except (ValueError, TypeError, KeyError):
        return False


def classify_frame(value, *, code, name, as_of, dataset_id, source_dataset_id, source_sha256,
                   previous=None, recorded_at=None, effective_from=None):
    context = {'code': code, 'name': name, 'as_of': as_of, 'dataset_id': dataset_id,
               'source_dataset_id': source_dataset_id, 'source_sha256': source_sha256}
    try:
        series = series_from_frame(value, code=code, as_of=as_of,
                                   source_dataset_id=source_dataset_id, source_sha256=source_sha256)
        if any(not bar.complete for bar in series.bars[-_POLICY['analysis_bars']:]):
            raise ValueError('INCOMPLETE_SOURCE_PREFIX')
        rows = [{'date': bar.day.isoformat(), 'open': price(bar.open), 'high': price(bar.high),
                 'low': price(bar.low), 'close': price(bar.close), 'volume_shares': price(bar.volume)}
                for bar in series.bars]
        if series.bars[-1].volume == 0:
            raise ValueError('ZERO_VOLUME_TRADE_STATUS_UNVERIFIED')
        selection = select_structure(rows)
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else 'SOURCE_FRAME_INVALID'
        return unknown_detail(**context, reason=reason)
    priors = _verified_prior_structures(previous, rows, code, as_of)
    candidate = selection['structure']
    if candidate is not None:
        known = next((old for old in priors if old['id'] == candidate['id']), None)
        if known is not None:
            candidate = (evaluate_structure(rows, known) if known['life_state'] == 'OPEN' else known)
        else:
            candidate['recorded_at'] = recorded_at
            candidate['effective_from'] = effective_from
    effective = next((old for old in priors if old['life_state'] == 'OPEN'
                      and _pre_open_effective(old, as_of)), None)
    structure = evaluate_structure(rows, effective) if effective is not None else candidate
    basis = 'PREVIOUS_LOCK' if effective is not None else 'CURRENT_REFERENCE' if structure is not None else 'NONE'
    reasons = list(selection['reasons'])
    if structure is None and priors:
        ended = next((old for old in priors if old['life_state'] != 'OPEN'), None)
        if ended is not None:
            structure, basis = ended, 'CURRENT_REFERENCE'
            reasons = ['当前没有新的合理组合；保留已结束的旧结构供历史复盘。'] + reasons
    if structure is None:
        state, category = 'NO_DRAW', 'NO_DRAW'
        position = {'category': category, 'close': rows[-1]['close'], 'near_tags': [],
                    'intraday_tags': [], 'new_crossing': 'UNKNOWN',
                    'distance_percent': {'H': None, 'M': None, 'L': None}}
    else:
        ended = structure['life_state'] != 'OPEN'
        ended_today = effective is not None and structure['guides_end_on'] == as_of
        state = ('HISTORY' if ended and not ended_today else 'CURRENT' if effective is not None else 'NEW_REFERENCE')
        previous_row = previous.get('row', {}) if isinstance(previous, dict) else {}
        same_structure = (effective is not None and _adjacent_previous(previous, rows, as_of)
                          and previous_row.get('structure_id') == effective['id']
                          and previous_row.get('source_status') == 'KNOWN'
                          and previous_row.get('drawing_state') in ('CURRENT', 'NEW_REFERENCE'))
        position = classify_position(rows[-1], structure, rows[-2] if same_structure else None,
                                     same_structure=same_structure, near_percent=_POLICY['near_percent'])
        if state == 'HISTORY':
            position['category'] = 'HISTORY'
            position['new_crossing'] = 'UNKNOWN'
            reasons = [structure.get('end_reason', '结构已经结束，仅供历史复盘。')] + reasons
        elif ended_today:
            reasons = [structure.get('end_reason', '该结构在今日结束，保留当天位置事件。')] + reasons
        elif state == 'NEW_REFERENCE':
            reasons = ['首批／新组合只描述当前收盘位置，不能冒充此前已锁线或今日新突破。'] + reasons
        else:
            reasons = ['当日采用开盘前已经生效的锁线；新组合下一交易日启用。'] + reasons
    levels = structure['levels'] if structure is not None else {'H': None, 'M': None, 'L': None}
    row = {'code': code, 'name': name, 'as_of': as_of, 'source_status': 'KNOWN', 'drawing_state': state,
           'H': levels['H'], 'M': levels['M'], 'L': levels['L'],
           'direction': structure['direction'] if structure is not None else None,
           'basis': basis, 'reasons': list(dict.fromkeys(reasons)),
           'structure_id': structure['id'] if structure is not None else None, **position}
    return {'schema': 'stock.golden-projection.daily.stock.v1', 'dataset_id': dataset_id,
            'source_dataset_id': source_dataset_id, 'as_of': as_of, 'code': code, 'name': name,
            'rule_id': RULE_ID, 'policy_sha256': POLICY_SHA256, 'row': row,
            'history': rows[-_POLICY['display_bars']:], 'structure': structure,
            'candidate_next_session': candidate if candidate is not None and candidate['life_state'] == 'OPEN' else None,
            'rejected_candidates': selection['rejected_candidates'],
            'source': {'key': f'data/chips/daily/{source_dataset_id}/{code}.json',
                       'sha256': source_sha256, 'last_bar_date': rows[-1]['date'], 'provider': series.provider},
            'historical_known_at': None, 'can_generate_order': False}
