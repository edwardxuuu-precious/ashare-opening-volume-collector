"""Frozen local OHLC4-mode intervention; never writes the production model."""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from types import ModuleType

from backend.chips import model as production
from tooling.analyze_eastmoney_decay2 import em_mass, our_mass
from tooling.analyze_eastmoney_zero_init import (
    ALL_DATES, CODES, DATES, FROZEN, PRIOR, ROOT,
    dump, load_zero_variant, local_peaks, own_em_anchors, read, sha,
)

BASE = ROOT / 'outputs/chips-eastmoney-zero-init-20261019'
OUT = ROOT / 'outputs/chips-eastmoney-ohlc4-20261019'
EXPECTED = {
    ROOT / 'backend/chips/model.py': 'fdcbac40a87bc5ee99e411e78bffb440c8abfdd41dbf07fcdef813a03f1a439a',
    ROOT / 'tooling/analyze_eastmoney_zero_init.py': 'acdc970ceced4444d19dbadf19780438cc53673663ae9a6fa8cce2c1055adc23',
    BASE / 'comparison.json': '62719469713636d1a840a7ae373fa286784530d125c8f77983f342ff8eb84831',
    BASE / 'profiles.json': 'c71f6e6030989d01e0630234e8c4dc88b24734b1d87872cba0460af058c145a9',
    FROZEN / 'eastmoney-browser-histories.json': '59a6c78a94712608c9aa5eca2bcc563673d127ba6a2e1de44cbcf5efa4dc9a62',
    PRIOR / 'eastmoney-local-decay2.json': '6752ae424d2dad49615bc0e7e2e6dd2c1c7933f85229700783a4bc97da09ae61',
}


def load_ohlc4_variant(zero_receipt):
    source = (ROOT / 'backend/chips/model.py').read_text(encoding='utf-8')
    for patch in zero_receipt['patches']:
        assert source.count(patch['before']) == 1
        source = source.replace(patch['before'], patch['after'])
    assert hashlib.sha256(source.encode()).hexdigest() == zero_receipt['in_memory_variant_sha256']
    patches = [
        ('identity',
         'VERSION = "chip-estimate.triangle-vwap.fixed-cent.decay2.zero-start.local.v1"',
         'VERSION = "chip-estimate.triangle-ohlc4.fixed-cent.decay2.zero-start.local.v1"'),
        ('metadata',
         '"daily_allocation": "triangle on [low,high], mode=amount_cny/volume_shares"',
         '"daily_allocation": "triangle on [low,high], mode=(open+high+low+close)/4; no mode rounding"'),
        ('metadata',
         '"vwap_rounding": "outside OHLC only accepted if rounding to source price tick puts it on a boundary; adjustment disclosed"',
         '"vwap_rounding": "not applicable to OHLC4; existing source validation retained"'),
        ('daily_triangle_mode',
         'raw = self.amount_cny / self.volume_shares',
         'raw = (self.open + self.high + self.low + self.close) / 4'),
    ]
    modified, receipts = source, []
    for kind, before, after in patches:
        assert modified.count(before) == 1, before
        modified = modified.replace(before, after)
        receipts.append(dict(kind=kind, before=before, after=after, replacements=1))
    reverse = modified
    for _, before, after in reversed(patches):
        assert reverse.count(after) == 1
        reverse = reverse.replace(after, before)
    assert reverse == source
    name = '_local_chip_ohlc4_zero_start_experiment'
    module = ModuleType(name)
    module.__file__ = '<in-memory-only:ohlc4-mode>'
    sys.modules[name] = module
    exec(compile(modified, module.__file__, 'exec'), module.__dict__)
    assert module.COEFFICIENT == Decimal(2) and module.STEP == Decimal('.01')
    assert module.MODEL['unchanged_threshold'] is None
    return module, {
        'baseline_zero_variant_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'new_in_memory_variant_sha256': hashlib.sha256(modified.encode()).hexdigest(),
        'baseline_initialization_patch': zero_receipt,
        'additional_patches': receipts,
        'reverse_additional_patches_restores_entire_zero_baseline': True,
        'only_new_numerical_change': 'daily positive-volume triangle mode: VWAP -> unrounded OHLC4',
        'fixed_cent_cdf_grid_and_initialization_and_recurrence_unchanged': True,
    }


def independent_recurrence_checks(module, bars, snapshots):
    """Direct raw-Q recurrence, separate from the model's lazy weights/scale."""
    checks = {}
    with localcontext() as context:
        context.prec = production.PRECISION
        q = {}
        survival = Decimal(1)
        for bar in bars:
            a = min(Decimal(1), 2 * bar.turnover_pct / 100)
            expected_mode = (bar.open + bar.high + bar.low + bar.close) / 4
            mode, adjustment = bar.mode()
            assert mode == (expected_mode if bar.volume_shares else None)
            assert adjustment is None
            t = module.triangle(bar) if mode is not None else {}
            if t:
                assert abs(sum(t.values()) - 1) < Decimal('1e-44')
            q = {i: mass * (1-a) for i, mass in q.items()}
            for i, mass in t.items():
                q[i] = q.get(i, Decimal(0)) + a * mass
            survival *= 1-a
            if bar.source_date not in snapshots:
                continue
            snapshot = snapshots[bar.source_date]
            total = sum(q.values())
            assert abs(total - (1-survival)) < Decimal('1e-44')
            reported = {int(Decimal(p['price_cny'])*100): Decimal(p['share_percent'])/100
                        for p in snapshot['profile']}
            error = max(abs(q.get(i, Decimal(0))/total - reported.get(i, Decimal(0)))
                        for i in q.keys() | reported.keys())
            assert error < Decimal('1e-44'), error
            assert abs(sum(reported.values())-1) < Decimal('1e-44')
            assert all(x >= 0 for x in reported.values())
            checks[bar.source_date] = {
                'raw_mass_fraction': str(total),
                'raw_mass_equals_one_minus_survival': True,
                'direct_recurrence_max_error_fraction': str(error),
                'all_prior_daily_modes_equal_unrounded_ohlc4': True,
            }
    return checks


def band(old_before, old_after, new_before, new_after, em_before, em_after, anchor, origin):
    lo, hi = Fraction(anchor)-Fraction(55, 1000), Fraction(anchor)+Fraction(55, 1000)
    a, b = our_mass(old_before, lo, hi), our_mass(old_after, lo, hi)
    c, d = our_mass(new_before, lo, hi), our_mass(new_after, lo, hi)
    e, f = em_mass(em_before, lo, hi), em_mass(em_after, lo, hi)
    return {
        'anchor_price_cny': anchor, 'anchor_origin': origin,
        'lower_inclusive': str(lo), 'upper_exclusive': str(hi), 'width_cny': '0.11',
        'vwap_before_percent': a, 'vwap_after_percent': b, 'vwap_change_pp': b-a,
        'ohlc4_before_percent': c, 'ohlc4_after_percent': d, 'ohlc4_change_pp': d-c,
        'em2_before_percent': e, 'em2_after_percent': f, 'em2_change_pp': f-e,
        'vwap_current_gap_pp': b-f, 'ohlc4_current_gap_pp': d-f,
        'vwap_daily_change_error_pp': (b-a)-(f-e),
        'ohlc4_daily_change_error_pp': (d-c)-(f-e),
        'absolute_current_gap_reduction_pp': abs(b-f)-abs(d-f),
    }


def main():
    for p, expected in EXPECTED.items():
        assert sha(p) == expected, str(p)
    previous = read(BASE / 'profiles.json')
    em_bundle = read(PRIOR / 'eastmoney-local-decay2.json')
    assert em_bundle['settings']['coefficient'] == 2
    assert em_bundle['patch']['after'] == 'A=Math.min(1,2*d.hsl/100||0)'
    fixtures = {f['code']: f for f in read(FROZEN / 'eastmoney-browser-histories.json')}
    zero, zero_receipt = load_zero_variant()
    new_model, patch = load_ohlc4_variant(zero_receipt)
    rows, profiles, inputs, checks = [], {}, {}, []
    for code in CODES:
        fixture = fixtures[code]
        payload = json.dumps(fixture['data'], ensure_ascii=False, separators=(',', ':')).encode()
        bars, audit = production.prepare_eastmoney(payload, expected_code=code, cutoff=DATES[-1])
        new_bars, new_audit = new_model.prepare_eastmoney(payload, expected_code=code, cutoff=DATES[-1])
        # New class instances are essential: passing production.Bar would still dispatch its VWAP mode().
        assert all(isinstance(b, new_model.Bar) for b in new_bars)
        assert [asdict(b) for b in bars] == [asdict(b) for b in new_bars]
        assert len(bars) == 210 and bars[0].source_date == '2025-12-02'
        assert not audit['invalid_source_rows'] and not audit['excluded_prefix_rows']
        assert not new_audit['invalid_source_rows'] and not new_audit['excluded_prefix_rows']
        old = zero.estimate_snapshots(bars, ALL_DATES, security_id=code)
        assert old == previous[code]['new_zero_initialization']
        new = new_model.estimate_snapshots(new_bars, ALL_DATES, security_id=code)
        em = em_bundle['stocks'][code]['snapshots']
        assert em == previous[code]['em2_zero_initialization']
        direct = independent_recurrence_checks(new_model, new_bars, new)
        first = new_bars[0]
        _, state = new_model.estimate_snapshots([first], [first.source_date], security_id=code, export_state=True)
        with localcontext() as context:
            context.prec = production.PRECISION
            raw_first = sum(Decimal(x) for x in state['weights'].values())*Decimal(state['scale'])
            a0 = min(Decimal(1), 2*first.turnover_pct/100)
            assert abs(raw_first-a0) < Decimal('1e-44') and raw_first < 1
        for date in ALL_DATES:
            n, o, e = new[date], old[date], em[date]
            assert n['history_rows_processed'] == o['history_rows_processed'] == e['rows']
            assert n['normalized_input_prefix_sha256'] == o['normalized_input_prefix_sha256']
            assert n['coefficient'] == '2' and n['unchanged_threshold'] is None
            assert n['unchanged_classification'] is None
            assert Decimal(n['zero_start_initial_inventory_survival_fraction']) == 0
            prefix = new_bars[:n['history_rows_processed']]
            assert new_model.estimate_snapshots(prefix, [date], security_id=code)[date] == n
            # The frozen reference and all observations have unique peaks; report all ties if changed.
            assert len(n['peak_prices_cny']) == len(o['peak_prices_cny']) == len(e['peak_prices']) == 1
            checks.append(dict(code=code, date=date, old_control_exactly_reproduced=True,
                               same_history_and_input_digest=True, future_suffix_invariant=True,
                               direct_raw_recurrence=direct[date]))
        inputs[code] = {
            'name': fixture['data']['data']['name'], 'bars': len(bars),
            'first_date': bars[0].source_date, 'last_date': bars[-1].source_date,
            'source_audit': audit, 'ohlc4_source_audit': new_audit,
            'first_daily_injection_weight': str(a0), 'observed_first_raw_mass': str(raw_first),
            'new_top_local_peaks_20261016': local_peaks(new[DATES[-1]]),
        }
        profiles[code] = {'vwap_zero_start': old, 'ohlc4_zero_start': new, 'em2_zero_start': em}
        for i, date in enumerate(DATES):
            before = ALL_DATES[i]
            args = old[before], old[date], new[before], new[date], em[before], em[date]
            old_gap = Decimal(old[date]['peak_prices_cny'][0])-Decimal(str(em[date]['peak_prices'][0]))
            new_gap = Decimal(new[date]['peak_prices_cny'][0])-Decimal(str(em[date]['peak_prices'][0]))
            rows.append({
                'code': code, 'name': inputs[code]['name'], 'date': date, 'previous_date': before,
                'vwap_peak': old[date]['peak_prices_cny'], 'ohlc4_peak': new[date]['peak_prices_cny'],
                'em2_peak': em[date]['peak_prices'],
                'vwap_peak_gap_cny': str(old_gap), 'ohlc4_peak_gap_cny': str(new_gap),
                'absolute_peak_gap_reduction_cny': str(abs(old_gap)-abs(new_gap)),
                'frozen_previous_vwap_peak_bands': [band(*args, p, 'PRE_INTERVENTION_VWAP_YESTERDAY_PEAK')
                                                   for p in old[before]['peak_prices_cny']],
                'fresh_ohlc4_peak_bands': [band(*args, p, 'CURRENT_OHLC4_YESTERDAY_PEAK')
                                           for p in new[before]['peak_prices_cny']],
                'continuity_old_1165_band': band(*args, '11.65', 'EARLIER_FULL_SEED_BANK_ANCHOR') if code == '000001' else None,
                'vwap_original_peak_cells': zero.compare_snapshots(old[before], old[date])['anchors'],
                'ohlc4_original_peak_cells': new_model.compare_snapshots(new[before], new[date])['anchors'],
                'em2_original_peak_bands': own_em_anchors(em[before], em[date]),
            })
    stats = {}
    for label, subset in [('all_16', rows), *[(c, [r for r in rows if r['code'] == c]) for c in CODES]]:
        def mae(key):
            return sum(abs(Decimal(r[key])) for r in subset)/len(subset)
        def band_mae(key):
            return sum(abs(r['frozen_previous_vwap_peak_bands'][0][key]) for r in subset)/len(subset)
        stats[label] = {
            'vwap_peak_mae_cny': str(mae('vwap_peak_gap_cny')),
            'ohlc4_peak_mae_cny': str(mae('ohlc4_peak_gap_cny')),
            'vwap_frozen_band_current_share_mae_pp': band_mae('vwap_current_gap_pp'),
            'ohlc4_frozen_band_current_share_mae_pp': band_mae('ohlc4_current_gap_pp'),
            'vwap_frozen_band_daily_change_mae_pp': band_mae('vwap_daily_change_error_pp'),
            'ohlc4_frozen_band_daily_change_mae_pp': band_mae('ohlc4_daily_change_error_pp'),
        }
    for p, expected in EXPECTED.items():
        assert sha(p) == expected, str(p)
    assert len(rows) == 16 and len(checks) == 20
    result = {
        'schema': 'chip-ohlc4-mode-intervention.v1', 'comparison_date': '2026-10-19',
        'scope': {
            'codes': CODES, 'dates': DATES, 'shared_history_start': '2025-12-02',
            'shared_history_end': DATES[-1], 'shared_loaded_bars': 210,
            'snapshot_prefix_rows': {d: 206+i for i, d in enumerate(ALL_DATES)},
            'adjustment': 'UNADJUSTED', 'coefficient': '2', 'raw_initial_mass': '0',
            'new_mode': '(open+high+low+close)/4, unrounded',
            'our_price_bins': 'unchanged fixed .01CNY centered cells, triangle CDF integration',
            'reference': 'unchanged frozen Eastmoney-source local coefficient2, 150 dynamic nodes',
            'normalization': 'snapshots only, no daily raw-state normalization',
            'band_width_cny': '0.11', 'band_is_measurement_not_selection_threshold': True,
            'unchanged_threshold': None, 'production_changed': False,
            'historical_PIT_authenticated': False, 'official_vendor_client_equivalence': 'NOT_CERTIFIED',
        },
        'patch': patch, 'inputs': inputs, 'rows': rows, 'statistics': stats,
        'validation': {'checks': checks, 'daily_ohlc4_modes_checked': 840,
                       'all_20_baseline_snapshots_exactly_reproduced': True,
                       'all_20_direct_raw_recurrence_checks_passed': True,
                       'four_first_raw_mass_checks_passed': True,
                       'frozen_sources_and_production_unchanged': True},
        'source_hashes': {str(p.relative_to(ROOT)): sha(p) for p in EXPECTED},
        'analysis_code_sha256': sha(Path(__file__)),
    }
    OUT.mkdir(exist_ok=True)
    dump(OUT / 'comparison.json', result)
    dump(OUT / 'profiles.json', profiles)
    dump(OUT / 'mode-patch.json', patch)
    report(result)
    print(json.dumps({'rows': len(rows), 'statistics': stats,
                      'bank_focus': [r for r in rows if r['code'] == '000001'],
                      'report': str(OUT / '四价均值顶点对照.md')}, ensure_ascii=False))


def report(result):
    rows = result['rows']
    bank = [r for r in rows if r['code'] == '000001']
    focused = [r for r in bank if r['date'] in ('2026-10-15', '2026-10-16')]
    lines = ['# 每日三角顶点改为OHLC四价均值：本地对照', '',
             '四只股票，2026年10月13—16日。沿用同一210根不复权行情（2025-12-02至2026-10-16），各日只读取截至当日的行情前缀，另计算10月12日作为昨日基线。',
             '双方历史换手衰减系数2，原始筹码质量从0开始。本轮仅把我方三角顶点从成交额/成交股数改为(开+高+低+收)/4，不预先舍入；一分钱CDF积分分档、后续递推及仅快照归一化不变。',
             '东方财富对照为已冻结网页源码的本地系数2副本，150动态节点分档保持不变。不是官方客户端系数2数据认证。线上算法未修改，“没动”阈值未设置。', '',
             '## 平安银行15、16日：峰价差缩小多少', '',
             '|日期|此前峰价|本轮峰价|东财2峰价|此前绝对差|本轮绝对差|',
             '|---|---:|---:|---:|---:|---:|']
    for r in focused:
        lines.append(f"|{r['date'][5:]}|{r['vwap_peak'][0]}|{r['ohlc4_peak'][0]}|{r['em2_peak'][0]:.2f}|{abs(Decimal(r['vwap_peak_gap_cny'])):.2f}|{abs(Decimal(r['ohlc4_peak_gap_cny'])):.2f}|")
    lines += ['',
             '两日峰价显示差均由0.35元缩至0元，减少0.35元、100%。此前峰价11.34元、本轮11.69元；本轮银行四日均为11.69元。',
             '只改每日分配顶点就改变了累计分布中最高档的排序，说明本样本此前的银行峰价偏差对这一分配假设敏感。这不是对整条分布或真实持仓准确性的认证。', '',
             '## 峰价：改动前后与东财副本', '',
             '差值为我方减东财；绝对差缩小为负数表示扩大。单位：元。', '',
             '|股票|日期|此前VWAP|本轮OHLC4|东财2|此前差|本轮差|绝对差缩小|',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"|{r['name']} {r['code']}|{r['date'][5:]}|{'/'.join(r['vwap_peak'])}|{'/'.join(r['ohlc4_peak'])}|{'/'.join(f'{p:.2f}' for p in r['em2_peak'])}|{Decimal(r['vwap_peak_gap_cny']):+.2f}|{Decimal(r['ohlc4_peak_gap_cny']):+.2f}|{Decimal(r['absolute_peak_gap_reduction_cny']):+.2f}|")
    lines += ['', '## 固定改动前的昨日主峰带：占比及每日变化', '',
              '以改动前零初始化VWAP模型的昨日主峰为中心，冻结0.11元价格带；改动前后和东财均在同一带中计数。下列区间是对照测量范围，不是“没动”筛选阈值。占比为%，差为百分点。', '',
              '|股票|日期|固定锚价|此前前→后|本轮前→后|东财2前→后|此前当日差|本轮当日差|本轮日变化差|',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        b = r['frozen_previous_vwap_peak_bands'][0]
        lines.append(f"|{r['name']}|{r['date'][5:]}|{b['anchor_price_cny']}|{b['vwap_before_percent']:.4f}→{b['vwap_after_percent']:.4f}|{b['ohlc4_before_percent']:.4f}→{b['ohlc4_after_percent']:.4f}|{b['em2_before_percent']:.4f}→{b['em2_after_percent']:.4f}|{b['vwap_current_gap_pp']:+.4f}|{b['ohlc4_current_gap_pp']:+.4f}|{b['ohlc4_daily_change_error_pp']:+.4f}|")
    lines += ['', '## 本轮模型自己的昨日主峰：原档位与同价格带', '',
              '原档位为我方一分钱档；东财档宽可能不同，原档位不能直接相减。本轮新锚与上表冻结旧锚分别展示，不能跨价格带声称差异缩小。', '',
              '|股票|日期|本轮昨日主峰|我方原1分钱档前→后%|我方0.11元带前→后%|东财同带前→后%|',
              '|---|---|---:|---:|---:|---:|']
    for r in rows:
        c = r['ohlc4_original_peak_cells'][0]
        b = r['fresh_ohlc4_peak_bands'][0]
        lines.append(f"|{r['name']}|{r['date'][5:]}|{c['anchor_price_cny']}|{float(c['previous_share_percent']):.4f}→{float(c['current_same_price_share_percent']):.4f}|{b['ohlc4_before_percent']:.4f}→{b['ohlc4_after_percent']:.4f}|{b['em2_before_percent']:.4f}→{b['em2_after_percent']:.4f}|")
    lines += ['', '## 平安银行：延续更早11.65元锚点', '',
              '为便于与零初始化那一轮报告衔接，以下始终使用[11.595,11.705)元；不是当前模型新主峰。', '',
              '|日期|此前占比%|本轮占比%|东财2占比%|此前差百分点|本轮差百分点|',
              '|---|---:|---:|---:|---:|---:|']
    for r in (r for r in rows if r['code'] == '000001'):
        b = r['continuity_old_1165_band']
        lines.append(f"|{r['date'][5:]}|{b['vwap_after_percent']:.4f}|{b['ohlc4_after_percent']:.4f}|{b['em2_after_percent']:.4f}|{b['vwap_current_gap_pp']:+.4f}|{b['ohlc4_current_gap_pp']:+.4f}|")
    lines += ['', '## 统计和解释边界', '',
              '|样本|此前峰价MAE元|本轮峰价MAE元|此前固定带占比MAE百分点|本轮固定带占比MAE百分点|',
              '|---|---:|---:|---:|---:|']
    for label, s in result['statistics'].items():
        lines.append(f"|{label}|{Decimal(s['vwap_peak_mae_cny']):.6f}|{Decimal(s['ohlc4_peak_mae_cny']):.6f}|{s['vwap_frozen_band_current_share_mae_pp']:.6f}|{s['ohlc4_frozen_band_current_share_mae_pp']:.6f}|")
    lines += ['',
              '四股16行峰价平均绝对差由0.073125元降至0.008125元，减少88.89%。粤宏远A差值由0变为0.01元，京东方A由0.01变为0.02元；改善并非逐股一致。',
              '同样沿用改动前各日固定原峰价格带时，16行当前占比平均绝对差由0.372659扩大至0.432350个百分点；每日占比变化差则由0.309059降至0.105291个百分点。不能以峰价匹配代替占比匹配。',
              '例如银行16日冻结旧锚11.34元的同一价格带，占比差由-0.006465变为-0.266943个百分点；新锚11.69元带的差为-0.136620个百分点。二者锚不同，需分别理解。',
              '这轮是单一顶点公式改动的结果，并未调整其他参数。峰价一致只表示最大档位置接近；不代表整条分布或实际持仓相同。',
              '剩余算法差异仍包括我方一分钱CDF积分与东财150动态节点采样、每日注入质量/归一化口径，以及一字日处理。上述仍是待分项对照的差异，不能将全部残差直接归因于其中某一项。',
              '“原峰筹码占比”是原价格区间在次日分布中的总占比，可能含新注入筹码，不是实际原股东留存率。', '',
              '## 复现与核验', '',
              '- 运行：`python -m tooling.analyze_eastmoney_ohlc4`。',
              '- 20个此前零初始化VWAP控制快照逐字段完全复现；东财系数2冻结快照保持不变。',
              '- 840个日顶点逐个验证等于未舍入OHLC4；4个首日原始质量等于A，不是1。',
              '- 20个新快照用独立直接原始质量递推核验，非负、归一化、历史前缀及未来后缀不变性均检查。',
              '- 内存补丁可完整逆转至此前零初始化副本；生产源文件、冻结行情与历史结果哈希保持不变。',
              '- comparison.json保留16行峰价、固定旧带、新原峰带、每日变化及核验；profiles.json保留60个快照；mode-patch.json保留精确补丁。',
              '- 结论限于这四只股票和共同冻结行情，不作全市场、官方软件认证或投资绩效外推。']
    (OUT / '四价均值顶点对照.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
