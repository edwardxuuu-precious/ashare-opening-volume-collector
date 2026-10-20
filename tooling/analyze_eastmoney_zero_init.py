"""Local zero-initialization intervention; frozen source/history, coefficient 2.

The production module and the two preceding result sets are read-only inputs.
Only initialization semantics change in a separate in-memory module.
"""
from __future__ import annotations
import hashlib
import json
import sys
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from types import ModuleType

from backend.chips import model as production
from tooling.analyze_eastmoney_decay2 import em_mass, our_mass, node_position, node_step

ROOT=Path(__file__).resolve().parents[1]
FROZEN=ROOT/'outputs/chips-eastmoney-20261019'
PRIOR=ROOT/'outputs/chips-eastmoney-decay2-20261019'
OUT=ROOT/'outputs/chips-eastmoney-zero-init-20261019'
CODES=['000573','600812','000725','000001']
ALL_DATES=['2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16']
DATES=ALL_DATES[1:]
EXPECTED={
    ROOT/'backend/chips/model.py':'fdcbac40a87bc5ee99e411e78bffb440c8abfdd41dbf07fcdef813a03f1a439a',
    PRIOR/'comparison.json':'cf9c7f557aea8de36381b1aea93f0b13525d09f8af91341670737130de3acabb',
    FROZEN/'comparison.json':'11ff64d6d7e617ec76c017e49ea82c386a4f25ea0a54212ce72086d00d9ccba8',
}

def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def fraction(text):return Fraction(str(text))
def mean_abs(values):return sum(abs(v) for v in values)/len(values)

def load_zero_variant():
    source=(ROOT/'backend/chips/model.py').read_text(encoding='utf-8')
    patches=[
        ('identity', 'VERSION = "chip-estimate.triangle-vwap.fixed-cent.decay2.v1"',
            'VERSION = "chip-estimate.triangle-vwap.fixed-cent.decay2.zero-start.local.v1"'),
        ('metadata', '"initialization": "first valid observed positive-volume bar is an estimated full triangle"',
            '"initialization": "raw cost mass starts at zero; first positive A injects A*Triangle; snapshot normalization only"'),
        ('metadata', '"update": "D[t]=(1-a[t])*D[t-1]+a[t]*Triangle[t]"',
            '"update": "Q[t]=(1-a[t])*Q[t-1]+a[t]*Triangle[t], Q[0]=0; report Q[t]/sum(Q[t])"'),
        ('metadata', '"initial_seed_survival_upper_bound": str(seed_survival)',
            '"zero_start_initial_inventory_survival_fraction": str(seed_survival)'),
        ('initialization', 'seed_survival = Decimal(1)\n    first_date = previous = None',
            'seed_survival = Decimal(0)\n    first_date = previous = None'),
        ('initialization',
            '            if not weights:\n                if mode is not None:\n                    weights = _triangle(bar, mode)\n                    first_date = bar.source_date',
            '            if not weights:\n                if mode is not None and a > 0:\n                    weights = {i: a * mass for i, mass in _triangle(bar, mode).items()}\n                    first_date = bar.source_date'),
    ]
    modified=source
    receipts=[]
    for kind,before,after in patches:
        assert modified.count(before)==1,(kind,before)
        modified=modified.replace(before,after)
        receipts.append({'kind':kind,'before':before,'after':after,'replacements':1})
    reverse=modified
    for _,before,after in reversed(patches):
        assert reverse.count(after)==1
        reverse=reverse.replace(after,before)
    assert reverse==source
    name='_local_chip_zero_start_experiment'
    module=ModuleType(name)
    module.__file__='<in-memory-only:zero-start-initialization>'
    sys.modules[name]=module
    exec(compile(modified,module.__file__,'exec'),module.__dict__)
    assert module.COEFFICIENT==Decimal(2) and module.MODEL['unchanged_threshold'] is None
    return module,{'source_file_sha256':sha(ROOT/'backend/chips/model.py'),
        'normalized_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'in_memory_variant_sha256':hashlib.sha256(modified.encode()).hexdigest(),
        'patches':receipts,'reverse_patch_restores_entire_source':True,
        'no_daily_state_renormalization_added':True,
        'semantic_boundary':'Only raw zero initialization/first injection and its metadata changed. VWAP, triangle integration, price grid, coefficient, later recurrence and snapshot normalization are unchanged.'}

def profile_error_against_identity(old,new,first_triangle,a0):
    # For the identical linear recurrence and daily allocations:
    # Q_zero = D_full - (1-a0)*seed_survival*T_first.
    # Reported D_zero is Q_zero divided by its remaining total mass.
    with localcontext() as context:
        context.prec=production.PRECISION
        survival=Decimal(old['initial_seed_survival_upper_bound'])
        removed=(1-a0)*survival
        total=1-removed
        assert total>0
        left={int(Decimal(p['price_cny'])*100):Decimal(p['share_percent'])/100 for p in old['profile']}
        right={int(Decimal(p['price_cny'])*100):Decimal(p['share_percent'])/100 for p in new['profile']}
        predicted={i:(left.get(i,Decimal(0))-removed*first_triangle.get(i,Decimal(0)))/total
                   for i in left.keys()|right.keys()|first_triangle.keys()}
        error=max(abs(value-right.get(i,Decimal(0))) for i,value in predicted.items())
        assert error<Decimal('1e-44'),str(error)
        return {'removed_excess_initial_mass_fraction':str(removed),
            'raw_zero_start_total_fraction':str(total),
            'old_first_day_component_percent':str(survival*100),
            'new_first_day_component_percent':str(a0*survival/total*100),
            'maximum_profile_identity_error_fraction':str(error)}

def own_em_anchors(before,after):
    output=[]
    for index in before['peak_indices']:
        center=node_position(before,index);half=node_step(before)/2
        a,b=em_mass(before,center-half,center+half),em_mass(after,center-half,center+half)
        output.append({'anchor_price_display':before['profile'][index]['display_price'],
            'lower_inclusive':str(center-half),'upper_exclusive':str(center+half),
            'previous_share_percent':a,'current_share_percent':b,'change_pp':b-a,
            'grid_changed':node_step(before)!=node_step(after) or before['low']!=after['low']})
    return output

def price_band(old_before,old_after,new_before,new_after,em_before,em_after,lower,upper):
    a,b=our_mass(old_before,lower,upper),our_mass(old_after,lower,upper)
    c,d=our_mass(new_before,lower,upper),our_mass(new_after,lower,upper)
    e,f=em_mass(em_before,lower,upper),em_mass(em_after,lower,upper)
    oldgap,newgap=b-f,d-f
    oldchange,newchange=(b-a)-(f-e),(d-c)-(f-e)
    reduction=abs(oldgap)-abs(newgap)
    return {'lower_inclusive':float(lower),'upper_exclusive':float(upper),'width_cny':'0.11',
        'old_ours_before_percent':a,'old_ours_after_percent':b,'old_ours_change_pp':b-a,
        'zero_ours_before_percent':c,'zero_ours_after_percent':d,'zero_ours_change_pp':d-c,
        'em2_before_percent':e,'em2_after_percent':f,'em2_change_pp':f-e,
        'old_after_gap_ours_minus_em2_pp':oldgap,'zero_after_gap_ours_minus_em2_pp':newgap,
        'old_daily_change_error_pp':oldchange,'zero_daily_change_error_pp':newchange,
        'absolute_current_gap_reduction_pp':reduction,
        'absolute_current_gap_reduction_percent':reduction/abs(oldgap)*100 if oldgap else None}

def local_peaks(snapshot):
    p={int(Decimal(x['price_cny'])*100):Decimal(x['share_percent']) for x in snapshot['profile']}
    candidates=[(value,index) for index,value in p.items()
        if value>0 and value>=p.get(index-1,Decimal(0)) and value>=p.get(index+1,Decimal(0))
        and (value>p.get(index-1,Decimal(0)) or value>p.get(index+1,Decimal(0)))]
    return [{'price_cny':f'{Decimal(index)/100:.2f}','cent_bin_share_percent':str(value)}
            for value,index in sorted(candidates,reverse=True)[:5]]

def main():
    for p,expected in EXPECTED.items():assert sha(p)==expected,str(p)
    prior=read(PRIOR/'comparison.json')
    assert sha(ROOT/'tooling/analyze_eastmoney_decay2.py')==prior['source_hashes']['python_analysis']
    old_profiles=read(PRIOR/'profiles.json')
    em_bundle=read(PRIOR/'eastmoney-local-decay2.json')
    assert em_bundle['patch']['after']=='A=Math.min(1,2*d.hsl/100||0)'
    assert em_bundle['settings']['coefficient']==2 and em_bundle['settings']['unchanged_threshold'] is None
    fixtures={f['code']:f for f in read(FROZEN/'eastmoney-browser-histories.json')}
    zero,patch=load_zero_variant()
    OUT.mkdir(exist_ok=True)
    rows,profiles,inputs,checks=[],{},{},[]
    prior_rows={(r['code'],r['date']):r for r in prior['rows']}
    for code in CODES:
        fixture=fixtures[code]
        payload=json.dumps(fixture['data'],ensure_ascii=False,separators=(',',':')).encode()
        bars,audit=production.prepare_eastmoney(payload,expected_code=code,cutoff=DATES[-1])
        assert len(bars)==210 and bars[0].source_date=='2025-12-02' and bars[-1].source_date==DATES[-1]
        assert not audit['invalid_source_rows'] and not audit['excluded_prefix_rows']
        old=production.estimate_snapshots(bars,ALL_DATES,security_id=code)
        assert old==old_profiles[code]['ours_coefficient2']
        new=zero.estimate_snapshots(bars,ALL_DATES,security_id=code)
        em=em_bundle['stocks'][code]['snapshots']
        first=bars[0];a0=min(Decimal(1),2*first.turnover_pct/100)
        assert 0<a0<1
        first_triangle=production.triangle(first)
        _,first_state=zero.estimate_snapshots([first],[first.source_date],security_id=code,export_state=True)
        with localcontext() as context:
            context.prec=production.PRECISION
            first_raw=sum(Decimal(x) for x in first_state['weights'].values())*Decimal(first_state['scale'])
            assert abs(first_raw-a0)<Decimal('1e-44')
            assert first_raw<1
        identity={}
        for date in ALL_DATES:
            o,n,e=old[date],new[date],em[date]
            assert n['coefficient']=='2' and n['unchanged_threshold'] is None and n['unchanged_classification'] is None
            assert n['history_rows_processed']==o['history_rows_processed']==e['rows']
            assert n['normalized_input_prefix_sha256']==o['normalized_input_prefix_sha256']
            assert 'initial_seed_survival_upper_bound' not in n
            assert Decimal(n['zero_start_initial_inventory_survival_fraction'])==0
            assert len(n['peak_prices_cny'])==1 and len(e['peak_prices'])==1
            with localcontext() as context:
                context.prec=production.PRECISION
                masses=[Decimal(p['share_percent']) for p in n['profile']]
                assert all(x>=0 for x in masses) and abs(sum(masses)-100)<Decimal('1e-44')
            identity[date]=profile_error_against_identity(o,n,first_triangle,a0)
            prefix=bars[:n['history_rows_processed']]
            assert zero.estimate_snapshots(prefix,[date],security_id=code)[date]==n
            checks.append({'code':code,'date':date,'old_control_exactly_reproduced':True,
                'same_history_and_input_digest':True,'linear_identity_verified':True,'future_suffix_invariant':True,
                'new_distribution_nonnegative_normalized':True})
        profiles[code]={'old_full_initialization':old,'new_zero_initialization':new,'em2_zero_initialization':em}
        inputs[code]={'name':fixture['data']['data']['name'],'source_audit':audit,
            'first_daily_injection_weight':str(a0),'first_raw_mass_observed':str(first_raw),
            'initialization_identity':identity,
            'zero_model_top_local_peaks_20261016':local_peaks(new[DATES[-1]])}
        for index,date in enumerate(DATES):
            before=ALL_DATES[index];oldrow=prior_rows[(code,date)]
            frozen_bands=[]
            for band in oldrow['common_original_peak_bands']:
                lower,upper=fraction(band['lower_inclusive']),fraction(band['upper_exclusive'])
                b=price_band(old[before],old[date],new[before],new[date],em[before],em[date],lower,upper)
                assert abs(b['old_after_gap_ours_minus_em2_pp']-band['after_share_difference_ours_minus_em2_pp'])<1e-12
                b.update(anchor_price_cny=band['anchor_our_previous_peak'],anchor_origin='FROZEN_PREVIOUS_ROUND_OURS_PEAK')
                frozen_bands.append(b)
            fresh_bands=[]
            for p in new[before]['peak_prices_cny']:
                lower,upper=Fraction(p)-Fraction(55,1000),Fraction(p)+Fraction(55,1000)
                b=price_band(old[before],old[date],new[before],new[date],em[before],em[date],lower,upper)
                b.update(anchor_price_cny=p,anchor_origin='CURRENT_ZERO_MODEL_PREVIOUS_PEAK')
                fresh_bands.append(b)
            eprice=Decimal(str(em[date]['peak_prices'][0]))
            rows.append({'code':code,'name':inputs[code]['name'],'date':date,'previous_date':before,
                'old_ours_peak':old[date]['peak_prices_cny'],'zero_ours_peak':new[date]['peak_prices_cny'],
                'em2_peak':em[date]['peak_prices'],
                'old_peak_gap_cny':str(Decimal(old[date]['peak_prices_cny'][0])-eprice),
                'zero_peak_gap_cny':str(Decimal(new[date]['peak_prices_cny'][0])-eprice),
                'frozen_previous_round_bands':frozen_bands,'current_zero_model_peak_bands':fresh_bands,
                'old_ours_original_peak_cells':production.compare_snapshots(old[before],old[date])['anchors'],
                'zero_ours_original_peak_cells':zero.compare_snapshots(new[before],new[date])['anchors'],
                'em2_original_peak_bands':own_em_anchors(em[before],em[date])})
    stats={}
    for label,subset in [('all_16',rows),*[(c,[r for r in rows if r['code']==c]) for c in CODES]]:
        old_gaps=[r['frozen_previous_round_bands'][0]['old_after_gap_ours_minus_em2_pp'] for r in subset]
        new_gaps=[r['frozen_previous_round_bands'][0]['zero_after_gap_ours_minus_em2_pp'] for r in subset]
        old_mean,new_mean=mean_abs(old_gaps),mean_abs(new_gaps)
        stats[label]={'old_current_share_mae_pp':old_mean,'zero_current_share_mae_pp':new_mean,
            'current_share_mae_reduction_pp':old_mean-new_mean,
            'current_share_mae_reduction_percent':(old_mean-new_mean)/old_mean*100 if old_mean else None,
            'old_daily_change_mae_pp':mean_abs([r['frozen_previous_round_bands'][0]['old_daily_change_error_pp'] for r in subset]),
            'zero_daily_change_mae_pp':mean_abs([r['frozen_previous_round_bands'][0]['zero_daily_change_error_pp'] for r in subset]),
            'old_peak_mae_cny':str(sum(abs(Decimal(r['old_peak_gap_cny'])) for r in subset)/len(subset)),
            'zero_peak_mae_cny':str(sum(abs(Decimal(r['zero_peak_gap_cny'])) for r in subset)/len(subset)),
            'old_max_peak_gap_cny':str(max(abs(Decimal(r['old_peak_gap_cny'])) for r in subset)),
            'zero_max_peak_gap_cny':str(max(abs(Decimal(r['zero_peak_gap_cny'])) for r in subset))}
    for p,expected in EXPECTED.items():assert sha(p)==expected,str(p)
    result={'schema':'chip-zero-initialization-intervention.v1','comparison_date':'2026-10-19',
        'scope':{'codes':CODES,'dates':DATES,'shared_history_start':'2025-12-02','shared_history_end':'2026-10-16',
            'shared_loaded_bars':210,'snapshot_prefix_rows':{d:206+i for i,d in enumerate(ALL_DATES)},
            'adjustment':'UNADJUSTED','both_coefficients':'2','unchanged_threshold':None,
            'intervention':'OURS_RAW_ZERO_START_AND_FIRST_A_WEIGHTED_INJECTION_ONLY',
            'eastmoney_reference':'Previously audited frozen local coefficient2 source variant, unchanged',
            'improvement_measurement':'Same previous-round price bands fixed before intervention; no moving-band claim',
            'fresh_peak_bands_reported_separately':True,'price_band_width':'0.11',
            'price_band_is_measurement_not_selection_threshold':True,
            'snapshot_normalization_only_no_daily_state_normalization':True,
            'not_official_vendor_client_or_historical_point_in_time_archive':True,
            'not_actual_original_holder_retention':True},
        'patch':patch,'inputs':inputs,'rows':rows,'statistics':stats,
        'validation':{'checks':checks,'four_actual_first_day_raw_mass_checks_passed':True,
            'all_20_linear_identity_checks_passed':True,'previous_source_and_results_unchanged':True,
            'production_model_unchanged':True},
        'source_hashes':{'production_model':sha(ROOT/'backend/chips/model.py'),
            'previous_comparison':sha(PRIOR/'comparison.json'),'previous_profiles':sha(PRIOR/'profiles.json'),
            'em2_frozen_result':sha(PRIOR/'eastmoney-local-decay2.json'),
            'raw_history_fixture':sha(FROZEN/'eastmoney-browser-histories.json'),
            'analysis_code':sha(Path(__file__))}}
    dump(OUT/'comparison.json',result);dump(OUT/'profiles.json',profiles);dump(OUT/'initialization-patch.json',patch)
    report(result)
    print(json.dumps({'rows':len(rows),'bank':stats['000001'],'all':stats['all_16'],
        'production_unchanged':True,'report':str(OUT/'零初始化对照.md')},ensure_ascii=False))

def report(result):
    rows=result['rows'];stats=result['statistics'];bank=stats['000001']
    final=next(r for r in rows if r['code']=='000001' and r['date']==DATES[-1])
    b=final['frozen_previous_round_bands'][0]
    lines=['# 系数2、210根共同行情、零初始化对照','',
        '仍为四股2026年10月13—16日，双方使用2025-12-02至2026-10-16共210根同一冻结不复权行情，各日只用当日之前缀。',
        '东财本地系数2副本完全保持上一轮；仅把我方本地副本初始原始筹码质量设为0，首日也按A=min(1,2×换手率)注入。后续递推、VWAP三角分配、固定一分钱档及最终归一化不变。未新增逐日状态归一化。',
        '生产源文件、前两轮结果不变；双方系数2，“没动”阈值未设。本轮是本地受控回放，不是官方客户端配置认证。','',
        '## 平安银行：固定上一轮原峰价格带，量化缩小多少','',
        '始终使用上一轮原峰11.65元对应的[11.595,11.705)元区间。此区间在干预前后冻结，不随本轮新峰挪动；该口径用于前后可比，当前模型各自新原峰的跟踪另列。',
        '差值=我方−东财本地系数2，单位百分点；占比为当日该固定区间的总占比。','',
        '|日期|上轮我方占比%|零初始化我方占比%|东财2占比%|上轮有符号差|本轮有符号差|绝对差缩小|缩小比例|',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in [r for r in rows if r['code']=='000001']:
        x=r['frozen_previous_round_bands'][0]
        lines.append(f"|{r['date'][5:]}|{x['old_ours_after_percent']:.4f}|{x['zero_ours_after_percent']:.4f}|{x['em2_after_percent']:.4f}|{x['old_after_gap_ours_minus_em2_pp']:+.4f}|{x['zero_after_gap_ours_minus_em2_pp']:+.4f}|{x['absolute_current_gap_reduction_pp']:.4f}|{x['absolute_current_gap_reduction_percent']:.2f}%|")
    lines+=['',f"银行四日占比平均绝对差：{bank['old_current_share_mae_pp']:.4f}→{bank['zero_current_share_mae_pp']:.4f}个百分点，缩小{bank['current_share_mae_reduction_percent']:.2f}%。",
        f"16日绝对差：{abs(b['old_after_gap_ours_minus_em2_pp']):.4f}→{abs(b['zero_after_gap_ours_minus_em2_pp']):.4f}个百分点，缩小{b['absolute_current_gap_reduction_percent']:.2f}%。当前我方略低于东财。",
        '', '**峰价并未同步全部改善。银行15、16日我方峰价变为11.34，东财副本仍为11.69，峰价差从原来的0.04扩大为0.35元。**','',
        '## 四股16行峰价','',
        '|股票|日期|上轮我方|零初始化我方|东财2|原差值元|新差值元|',
        '|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"|{r['name']} {r['code']}|{r['date'][5:]}|{'/'.join(r['old_ours_peak'])}|{'/'.join(r['zero_ours_peak'])}|{'/'.join(f'{p:.2f}' for p in r['em2_peak'])}|{Decimal(r['old_peak_gap_cny']):+.2f}|{Decimal(r['zero_peak_gap_cny']):+.2f}|")
    lines+=['','## 固定旧价格带：全部16行占比及日变化','',
        '各行均沿用上一轮干预前选定的同一原峰价格带。单位：占比为%，差为百分点。',
        '|股票|日期|固定价格带|零初始化我方前→后|东财2前→后|上轮当前差|新当前差|上轮日变化差|新日变化差|',
        '|---|---|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        x=r['frozen_previous_round_bands'][0]
        lines.append(f"|{r['name']}|{r['date'][5:]}|[{x['lower_inclusive']:.3f},{x['upper_exclusive']:.3f})|{x['zero_ours_before_percent']:.4f}→{x['zero_ours_after_percent']:.4f}|{x['em2_before_percent']:.4f}→{x['em2_after_percent']:.4f}|{x['old_after_gap_ours_minus_em2_pp']:+.4f}|{x['zero_after_gap_ours_minus_em2_pp']:+.4f}|{x['old_daily_change_error_pp']:+.4f}|{x['zero_daily_change_error_pp']:+.4f}|")
    lines+=['','## 本轮新原峰：新模型自己的昨日主峰价格带','',
        '这里以零初始化模型自己的昨日主峰选带，与上表冻结的旧带是两个不同问题；不得拿不同带的差值直接声称缩小比例。',
        '|股票|日期|本轮昨日主峰|本轮价格带|我方前→后%|东财2前→后%|当日差百分点|',
        '|---|---|---:|---|---:|---:|---:|']
    for r in rows:
        x=r['current_zero_model_peak_bands'][0]
        lines.append(f"|{r['name']}|{r['date'][5:]}|{x['anchor_price_cny']}|[{x['lower_inclusive']:.3f},{x['upper_exclusive']:.3f})|{x['zero_ours_before_percent']:.4f}→{x['zero_ours_after_percent']:.4f}|{x['em2_before_percent']:.4f}→{x['em2_after_percent']:.4f}|{x['zero_after_gap_ours_minus_em2_pp']:+.4f}|")
    lines+=['','## 本轮各自原峰原始档位（档宽不同，不直接相减）','',
        '|股票|日期|我方原峰价：一分钱档占比前→后%|东财2原峰价：原峰档占比前→后%|',
        '|---|---|---|---|']
    for r in rows:
        a=r['zero_ours_original_peak_cells'][0];e=r['em2_original_peak_bands'][0]
        lines.append(f"|{r['name']}|{r['date'][5:]}|{a['anchor_price_cny']}：{float(a['previous_share_percent']):.4f}→{float(a['current_same_price_share_percent']):.4f}|{e['anchor_price_display']:.2f}：{e['previous_share_percent']:.4f}→{e['current_share_percent']:.4f}{'（网格改变）' if e['grid_changed'] else ''}|")
    lines+=['','## 初始化的线性验证和剩余差异','',
        '设首日注入权重为a0，旧模型首日分布截至t的存活权重为s，首日三角分布为T。其余输入与递推相同，则：',
        '`Q_zero(t) = D_full(t) - (1-a0)*s*T`；报告时除以`1-(1-a0)*s`。',
        '20组新分布均独立通过该线性恒等式核验，用以确认只改了初始化，没有混入每日分配、分档、系数或归一化时点的改动。',
        '', '|股票|日期|旧首日分布权重%|新首日注入分布权重%|恒等式最大误差（质量份额）|',
        '|---|---|---:|---:|---:|']
    for c in CODES:
        x=result['inputs'][c]['initialization_identity'][DATES[-1]]
        lines.append(f"|{result['inputs'][c]['name']}|10-16|{float(x['old_first_day_component_percent']):.6f}|{float(x['new_first_day_component_percent']):.6f}|{x['maximum_profile_identity_error_fraction']}|")
    lines+=['','这些权重都是模型分布的数学分解，不是实际老股东持仓或真实留存率。',
        '未统一的部分仍包括VWAP与OHLC四价均值三角顶点、1分钱CDF积分与150动态节点采样、每日注入总质量和整体归一化口径，以及一字日处理。',
        '平安银行16日，我方零初始化模型最高的局部峰如下（同为我方一分钱档）：','']
    for x in result['inputs']['000001']['zero_model_top_local_peaks_20261016'][:3]:
        lines.append(f"- {x['price_cny']}元：单档占比{float(x['cent_bin_share_percent']):.6f}%")
    lines+=['','## 样本总体统计','',
        '|范围|旧当前占比MAE百分点|新当前占比MAE百分点|旧峰价MAE元|新峰价MAE元|',
        '|---|---:|---:|---:|---:|']
    for label in ['all_16',*CODES]:
        x=stats[label]
        lines.append(f"|{label}|{x['old_current_share_mae_pp']:.6f}|{x['zero_current_share_mae_pp']:.6f}|{Decimal(x['old_peak_mae_cny']):.6f}|{Decimal(x['zero_peak_mae_cny']):.6f}|")
    lines+=['','## 复现与边界','',
        '- 运行：`python -m tooling.analyze_eastmoney_zero_init`。',
        '- 原始20个控制快照与上一轮完全复现；4个实际首日原始质量检查、20个线性恒等式检查、20个未来后缀不变性和归一化检查通过。',
        '- 首日原始质量是a0，不是1；后续没有把每天状态重新归一化再递推。',
        '- 补丁仅驻留独立内存模块，正式model.py和历史两轮结果哈希不变。补丁准确文本见initialization-patch.json。',
        '- 完整数值、固定旧带/新原峰带、原生分布见comparison.json和profiles.json。',
        '- 结论仅限这四股、共同210根冻结行情、两个本地副本；不等于官方软件已匹配或实际筹码真实准确。']
    (OUT/'零初始化对照.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__=='__main__':main()
