"""Bounded retrospective comparison. Does not mutate production model/settings."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from decimal import Decimal, localcontext
from datetime import datetime, timezone
from backend.chips.model import (MODEL, PRECISION, number, Bar, triangle,
    estimate_snapshots, prepare_eastmoney, compare_snapshots)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/chips-eastmoney-20261019'
CAL = ROOT / 'outputs/chips-daily-20261018/calibration-four'
DATES = ['2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16']
ALL_DATES = ['2026-10-12', *DATES]
CODES = ['000573', '600812', '000725', '000001']

def read(p): return json.loads(p.read_bytes())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding='utf-8')

def bar(row):
    return Bar(row['date'], *[Decimal(row[k]) for k in
        ['open', 'high', 'low', 'close', 'volume_shares', 'amount_cny', 'turnover_percent']])

def recover_prior_snapshot(first, source_bar):
    """Algebraic inverse of the fixed production recurrence, with rounding audit.

    Required only for the Oct 13 vs Oct 12 baseline because the original
    four-stock delivery stored Oct 13-16. This is not new market evidence.
    """
    a = Decimal(first['effective_replacement'])
    assert 0 < a < 1 and a == 2 * source_bar.turnover_pct / 100
    with localcontext() as ctx:
        ctx.prec = PRECISION
        current = {int(Decimal(r['price_cny']) * 100): Decimal(r['share_percent']) / 100
                   for r in first['profile']}
        daily = triangle(source_bar)
        previous = {i:(current.get(i, Decimal(0)) - a*daily.get(i, Decimal(0))) / (1-a)
                    for i in current.keys() | daily.keys()}
        negative = {i:v for i,v in previous.items() if v < 0}
        assert all(abs(v) < Decimal('1e-45') for v in negative.values())
        previous = {i:max(v, Decimal(0)) for i,v in previous.items()}
        peak = max(previous.values())
        reconstructed = {i:(1-a)*v + a*daily.get(i, Decimal(0)) for i,v in previous.items()}
        error = max(abs(current.get(i, Decimal(0))-v) for i,v in reconstructed.items())
        assert error < Decimal('1e-45')
        total = sum(previous.values())
        normalized_previous = {i:v/total for i,v in previous.items()}
        normalized_reconstructed = {i:(1-a)*v + a*daily.get(i, Decimal(0))
                                    for i,v in normalized_previous.items()}
        normalized_error = max(abs(current.get(i, Decimal(0))-v)
                               for i,v in normalized_reconstructed.items())
        assert normalized_error < Decimal('1e-45')
        # A derived profile is not a copy of the Oct 13 snapshot. In particular,
        # do not attach the next day's quote, turnover, mean or input digest.
        result = {
            'schema':'chip-comparison.derived-profile.v1',
            'source_date':'2026-10-12',
            'model_version':first['model_version'],
            'security_id':first['security_id'],
            'seed_source_date':first['seed_source_date'],
            'coefficient':'2', 'unchanged_threshold':None,
            'peak_prices_cny':[f'{i/100:.2f}' for i,v in sorted(previous.items()) if v==peak],
            'peak_bin_share_percent':str(peak / total * 100),
            'profile':[{'price_cny':f'{i/100:.2f}', 'share_percent':str(v*100)}
                       for i,v in sorted(normalized_previous.items())],
            'derivation':'ALGEBRAIC_INVERSE_OF_OCT13_SNAPSHOT_AND_SAME_SAVED_SOURCE_BAR',
            'historical_PIT_authenticated':False,
            'not_reconstructed_metadata':['close_cny','turnover_percent','effective_replacement',
                'model_mean_cost_cny','history_rows_processed','normalized_input_prefix_sha256'],
        }
        audit = {'state':'DERIVED_NOT_INDEPENDENT_SOURCE', 'negative_roundoff_cells':len(negative),
                 'maximum_pre_normalization_forward_error_fraction':str(error),
                 'maximum_post_normalization_forward_error_fraction':str(normalized_error),
                 'discarded_negative_roundoff_fraction':str(sum(abs(v) for v in negative.values())),
                 'mass_before_normalization':str(total)}
    return result, audit

def native_mass(snapshot, lower, upper):
    # Same point-mass summation convention as native getBenefitPart, without
    # inventing or interpolating sub-grid probabilities.
    return sum(p['share_percent'] for p in snapshot['profile'] if lower <= p['price'] < upper)

def our_mass(snapshot, lower, upper):
    # Common bands below lie on cent-cell boundaries: whole original cells only.
    return float(sum(Decimal(p['share_percent']) for p in snapshot['profile']
        if Decimal(str(lower)) <= Decimal(p['price_cny']) < Decimal(str(upper))))

def main():
    assert MODEL['historical_turnover_decay_coefficient']=='2' and MODEL['unchanged_threshold'] is None
    native = read(OUT/'eastmoney-native-calculated.json')
    native_ui = read(OUT/'native-historical-ui.json')
    raw_by_code = {x['code']:x for x in read(OUT/'eastmoney-browser-histories.json')}
    rows, inputs, inverse_audit, controls, validation = [], {}, {}, {}, []
    all_profiles = {}
    for code in CODES:
        original = read(CAL/f'{code}.json')
        snapshots = dict(original['snapshots'])
        history = original['history']
        source13 = bar(next(h for h in history if h['date']==DATES[0]))
        snapshots['2026-10-12'], inverse_audit[code] = recover_prior_snapshot(snapshots[DATES[0]], source13)
        raw = json.dumps(raw_by_code[code]['data'], ensure_ascii=False, separators=(',',':')).encode()
        em_bars, em_audit = prepare_eastmoney(raw, expected_code=code, cutoff=DATES[-1])
        em_same_input = estimate_snapshots(em_bars, ALL_DATES, security_id=code)
        source_rows = {h['date']:h for h in history}
        source_diffs = []
        for b in em_bars:
            a = source_rows[b.source_date]
            fields = {'open':b.open,'close':b.close,'high':b.high,'low':b.low,
                'volume_shares':b.volume_shares,'amount_cny':b.amount_cny,'turnover_percent':b.turnover_pct}
            diffs = {k:{'ours_source':a[k],'eastmoney_source':str(v)} for k,v in fields.items() if Decimal(a[k]) != v}
            if diffs: source_diffs.append({'date':b.source_date,'fields':diffs})
        stock = native['stocks'][code]
        inputs[code] = {'name':original['name'], 'our_saved_baseline_file':str(CAL/f'{code}.json'),
            'our_saved_baseline_sha256':sha(CAL/f'{code}.json'),
            'our_seed_date':snapshots[DATES[0]]['seed_source_date'],
            'our_last_snapshot_history_rows':snapshots[DATES[-1]]['history_rows_processed'],
            'eastmoney_first_date':stock['first_date'],'eastmoney_loaded_rows':len(stock['bars']),
            'native_node_count':150,'source_differences_on_common_210_dates':source_diffs,
            'eastmoney_source_audit':em_audit}
        all_profiles[code] = {'ours':snapshots,'eastmoney':stock['snapshots']}
        controls[code] = {}
        for date in ALL_DATES:
            n = stock['snapshots'][date]
            ui = next(u for u in native_ui if u['code']==code and u['date']==date)
            expected = [date, f"{n['benefit_percent']:.2f}%", n['median_cost'],
                '-'.join(n['percent_chips']['90']['priceRange']),'-'.join(n['percent_chips']['70']['priceRange']),
                f"{n['percent_chips']['90']['concentration']*100:.2f}%",
                f"{n['percent_chips']['70']['concentration']*100:.2f}%"]
            assert all(t in ui['text'] for t in expected), (code,date,expected,ui['text'])
            assert abs(sum(p['share_percent'] for p in n['profile'])-100) < 1e-10
            assert all(p['share_percent'] >= 0 for p in n['profile'])
            assert n['rows'] == ui['source_rows']
            validation.append({'code':code,'date':date,'native_ui_cost_winner_and_concentration_match':True})
        for ix,date in enumerate(DATES):
            prev=ALL_DATES[ix]
            ours, before = snapshots[date], snapshots[prev]
            n, nb = stock['snapshots'][date], stock['snapshots'][prev]
            comparison=compare_snapshots(before,ours)
            native_anchors=[]
            for peak_index in nb['peak_indices']:
                center=nb['profile'][peak_index]['price']
                lower,upper=center-nb['step']/2,center+nb['step']/2
                old,new=native_mass(nb,lower,upper),native_mass(n,lower,upper)
                native_anchors.append({'previous_peak_price':nb['profile'][peak_index]['display_price'],
                    'fixed_band_lower_inclusive':lower,'fixed_band_upper_exclusive':upper,
                    'previous_share_percent':old,'current_share_percent':new,'change_percentage_points':new-old,
                    'grid_changed':abs(n['step']-nb['step'])>1e-12 or n['low']!=nb['low']})
            common=[]
            # Display-only 0.11 CNY comparison bands, not any stability threshold.
            for p in before['peak_prices_cny']:
                # 11 cent cells including p, [p-.055,p+.055), exactly cell-aligned.
                lower=float(Decimal(p)-Decimal('.055'));upper=float(Decimal(p)+Decimal('.055'))
                a,b=our_mass(before,lower,upper),our_mass(ours,lower,upper)
                c,d=native_mass(nb,lower,upper),native_mass(n,lower,upper)
                common.append({'center_our_previous_peak':p,'lower_inclusive':lower,'upper_exclusive':upper,
                    'width_cny':'0.11','purpose':'DISPLAY_ONLY_COMMON_PRICE_BAND_NOT_STABILITY_THRESHOLD',
                    'ours_before':a,'ours_after':b,'ours_change_pp':b-a,
                    'eastmoney_before':c,'eastmoney_after':d,'eastmoney_change_pp':d-c})
            controls[code][date]={'ours_same_eastmoney_210_input_peak':em_same_input[date]['peak_prices_cny'],
                'ours_same_eastmoney_210_input_peak_share':em_same_input[date]['peak_bin_share_percent'],
                'native_peak':n['peak_prices'], 'baseline_ours_peak':ours['peak_prices_cny']}
            rows.append({'code':code,'name':original['name'],'date':date,'previous_date':prev,
                'ours_peak':ours['peak_prices_cny'],'eastmoney_peak':n['peak_prices'],
                'peak_difference_ours_minus_eastmoney_cny':float(ours['peak_prices_cny'][0])-n['peak_prices'][0],
                'ours_original_peaks':comparison['anchors'],'eastmoney_original_peak_bands':native_anchors,
                'eastmoney_current_grid_width_cny':n['step'],'common_price_bands':common})
    result={'schema':'eastmoney-chip-comparison.v1','observed_at':datetime.now(timezone.utc).isoformat(),
        'scope':{'stocks':CODES,'dates':DATES,'adjustment':'unadjusted','ours_coefficient':'2','unchanged_threshold':None,
            'our_reference':'Existing four-stock calibration dataset, not a replacement of live production files',
            'eastmoney_reference':'Actual quotechart2022 native calculator, default 150 nodes, all 210 loaded bars; native renderer numerically checked',
            'original_peak_definition':'Each side uses its own previous-day peak; original fixed band retained across dates; widths differ',
            'native_band_semantics':'Sum existing native discrete cost-node masses within yesterday node +/- yesterday step/2; no interpolation',
            'common_band_definition':'11 original cent cells around OUR previous peak, [p-.055,p+.055); display only',
            'not_original_holder_retention':True,'not_point_in_time_archive':True},
        'inputs':inputs,'oct12_inverse_audit':inverse_audit,'rows':rows,'same_input_controls':controls,
        'validation':{'native_renderer_checks':validation,'all_20_native_renderer_checks_passed':True,
            'source_max_date':'2026-10-16','production_files_modified':False},
        'source_hashes':{name:sha(OUT/name) for name in ['quotechart2022.js','concept.js','eastmoney-browser-histories.json','native-historical-ui.json']}}
    dump(OUT/'comparison.json',result)
    dump(OUT/'comparison-profiles.json',all_profiles)
    lines=['# 东方财富网页版筹码自动对照','',
        '四只股票，2026-10-13—16；双方不复权。我们的历史换手衰减系数固定为2，未设置“没动”阈值。',
        '东方财富使用实际筹码页面 quotechart2022.js 原生计算模块，150个价格节点；固定截止16日加载210根日线，各日只用此前缀计算。20组股日（含12日基准）的获利比例、成本区间及集中度与原生渲染器数值一致。',
        '我们的对照基线是先前交付的四股校准数据，保留其完整历史计算结果；不替换线上数据。',
        '原峰占比：各自昨日原峰所在固定价格带，前日占比→当日占比（%）；双方档宽不同，不能直接相减当作同档误差。',
        '10月13日的我方10月12日状态由13日快照及同一保存行情按既定递推公式反解，并校验正向重建；这是数学推导，不是另取的行情证据。','',
        '|股票|日期|我们的峰价|东财峰价|我们的原峰价：占比前→后|东财原峰价：占比前→后|',
        '|---|---|---:|---:|---|---|']
    for r in rows:
        a=r['ours_original_peaks'][0];b=r['eastmoney_original_peak_bands'][0]
        lines.append(f"|{r['name']} {r['code']}|{r['date'][5:]}|{'/'.join(r['ours_peak'])}|{'/'.join(f'{p:.2f}' for p in r['eastmoney_peak'])}|{a['anchor_price_cny']}：{float(a['previous_share_percent']):.4f}→{float(a['current_same_price_share_percent']):.4f}|{b['previous_peak_price']:.2f}：{b['previous_share_percent']:.4f}→{b['current_share_percent']:.4f}{'（网格变化）' if b['grid_changed'] else ''}|")
    lines+=['','## 同一价格带复核','',
        '以下价格带为昨日我方峰价上下各0.055元，含11个一分钱档；只用于对齐测量范围，不是“没动”的筛选阈值。两边均求原始节点/价格档之和，没有插值创造东财不存在的一分钱档。','',
        '|股票|日期|同一固定价格带|我们的占比前→后|东财占比前→后|',
        '|---|---|---|---:|---:|']
    for r in rows:
        b=r['common_price_bands'][0]
        lines.append(f"|{r['name']}|{r['date'][5:]}|[{b['lower_inclusive']:.3f}, {b['upper_exclusive']:.3f})|{b['ours_before']:.4f}→{b['ours_after']:.4f}|{b['eastmoney_before']:.4f}→{b['eastmoney_after']:.4f}|")
    lines+=['','## 已核实的算法区别','',
        '- 我们每日替换比例为 min(1, 2×换手率)，当前东财网页源码为 min(1, 换手率)。',
        '- 我们当日三角分布顶点使用成交额÷成交量；东财使用（开+收+高+低）÷4。',
        '- 我们固定一分钱价格档并积分分配；东财为150节点动态网格，按节点高度采样后整体归一化。华北制药16日最高价从6.63抬到6.90，网格从0.016913变为0.018725元，旧价位不能直接按数组下标追踪。',
        '- 我们从首根有效成交日的完整三角分布初始化；东财从零数组逐日注入筹码，再按累计节点值归一化。两者的初始化和每日注入质量口径不同。',
        '- 东财实际页面初始显示90根K线，加载210根日线用于筹码；本次为2025-12-02至2026-10-16。我方四股校准基线从2001年或更早历史开始。页面缩放、拖动加载更多数据，可能改变东财结果。',
        '- 本轮前三只股票在共同210个日期的开高低收、量、额、换手率全部一致，不能用行情来源不同解释其差异。平安银行的保存基线与东财在210个日期存在字段差异，包括换手率精度和成交量取整；逐日差异保留在 comparison.json。',
        '- 东财“平均成本”在当前代码中取50%累计成本位置，属于中位数，不能把它当作主峰。',
        '- 粤宏远A16日为4.47元一字成交；旧峰3.85不在当日成交区间。东财对一字日的注入公式与非一字日采样尺度不同，最终归一化后的原峰占比变化不必精确等于1减换手率。',
        '', '以上是源码及受控计算确认的区别；没有把每个原因拆成可相加的贡献百分比，也没有据此认定哪套估算更接近实际股东持仓。',
        '', '## 各股差异解释','',
        '|股票|实际观察|原因与结论边界|','|---|---|---|',
        '|粤宏远A|四天主峰均为3.85，但原峰占比变化不同|相同210日行情下峰价仍一致，但分布不同；系数2与网页直接换手、1分钱与1.2349分钱网格、16日一字成交的原生归一化都存在区别，不能由峰价相同判定模型一致。|',
        '|华北制药|14/15日我们主峰6.04，东财4.90；16日6.49对6.26|换成同一210日输入后差异仍保留，说明算法差异本身已足以产生不同峰价；16日新高还重设动态网格。未把衰减与每日分配的贡献分别量化。|',
        '|京东方A|四天5.91对5.90，东财原始单档占比明显更大|东财每档约0.037919元，我们0.01元，原始单档百分比不等宽；同一0.11元价格带仍有分布差异，不能只用一分钱峰价差或单档占比判断是否一致。|',
        '|平安银行|我们11.68/11.67，东财11.13/11.35|同一210日输入下，我们为11.65，仍明显不同于东财；历史长度与源字段共同改变我方基线峰价约0.02—0.03元，但不能解释全部差异。两者尚未分别隔离。|',
        '', '## 同输入控制（仍保持我们的系数2）','',
        '|股票|日期|既有我方基线峰价|同东财210日输入的我方峰价|原生东财峰价|','|---|---|---:|---:|---:|']
    for r in rows:
        c=controls[r['code']][r['date']]
        lines.append(f"|{r['name']}|{r['date'][5:]}|{'/'.join(c['baseline_ours_peak'])}|{'/'.join(c['ours_same_eastmoney_210_input_peak'])}|{'/'.join(f'{p:.2f}' for p in c['native_peak'])}|")
    lines+=['','## 证据与复现','',
        '- 原生页面：https://quote.eastmoney.com/concept/sz000573.html#chart-k-cyq',
        '- 实际库：https://quote.eastmoney.com/newstatic/libs/quotechart2022.js',
        '- 从经典页的筹码入口进入概念版后，实际使用上述 quotechart2022.js；本轮没有把旧 quotekchart/1.0.6.js 当作当前筹码页面算法。',
        '- 当前网页及当前下载历史不是当时采集的PIT归档，不代表桌面版/手机版或同花顺。',
        '- 计算：`node tooling/compare_eastmoney_chips.cjs outputs/chips-eastmoney-20261019`',
        '- 分析：`python -m tooling.analyze_eastmoney_comparison`',
        '- 完整数值、输入哈希、原生UI校验及10月12日反解误差见同目录 comparison.json。']
    lines+=['','## 10月16日原生渲染截图','',
        '在东财页面内使用其原生图表库，统一不复权、截止16日、加载210根日线；截图标题为采集标签，K线、成交量、筹码图和成本面板由东财原生库绘制。','']
    for code in CODES:
        name=inputs[code]['name']
        lines.append(f'- [{name} {code}]({(OUT/f"native-chip-{code}-20261016.png").as_posix()})')
    (OUT/'对照结果.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'stocks':len(CODES),'rows':len(rows),'native_ui_checks':len(validation),
        'coefficient':MODEL['historical_turnover_decay_coefficient'],'unchanged_threshold':MODEL['unchanged_threshold'],
        'report':str(OUT/'对照结果.md')},ensure_ascii=False))

if __name__=='__main__': main()
