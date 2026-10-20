"""Same frozen input/range and coefficient=2 on both sides; local only."""
from __future__ import annotations
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path
from backend.chips.model import MODEL, estimate_snapshots, prepare_eastmoney, compare_snapshots

ROOT=Path(__file__).resolve().parents[1]
FROZEN=ROOT/'outputs/chips-eastmoney-20261019'
OUT=ROOT/'outputs/chips-eastmoney-decay2-20261019'
CODES=['000573','600812','000725','000001']
ALL_DATES=['2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16']
DATES=ALL_DATES[1:]
MODEL_HASH='fdcbac40a87bc5ee99e411e78bffb440c8abfdd41dbf07fcdef813a03f1a439a'

def read(p): return json.loads(p.read_bytes())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,x): p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
def node_position(snapshot,index):
    low,high=Fraction(str(snapshot['low'])),Fraction(str(snapshot['high']))
    return low+max(Fraction(1,100),(high-low)/149)*index
def node_step(snapshot):
    return max(Fraction(1,100),(Fraction(str(snapshot['high']))-Fraction(str(snapshot['low'])))/149)
def em_mass(snapshot,lower,upper):
    # Native discrete cost-node masses; exact source-grid geometry for boundaries.
    return sum(p['share_percent'] for i,p in enumerate(snapshot['profile'])
               if lower<=node_position(snapshot,i)<upper)
def our_mass(snapshot,lower,upper):
    return float(sum(Decimal(p['share_percent']) for p in snapshot['profile']
        if lower<=Fraction(p['price_cny'])<upper))
def mean_abs(values): return sum(abs(v) for v in values)/len(values)

def main():
    assert sha(ROOT/'backend/chips/model.py')==MODEL_HASH
    assert MODEL['historical_turnover_decay_coefficient']=='2' and MODEL['unchanged_threshold'] is None
    assert sha(FROZEN/'comparison.json')=='11ff64d6d7e617ec76c017e49ea82c386a4f25ea0a54212ce72086d00d9ccba8'
    derived=read(OUT/'eastmoney-local-decay2.json')
    original=read(FROZEN/'eastmoney-native-calculated.json')
    old_comparison=read(FROZEN/'comparison.json')
    fixtures={r['code']:r for r in read(FROZEN/'eastmoney-browser-histories.json')}
    rows,profiles,inputs=[],{},{}
    for code in CODES:
        fixture=fixtures[code]
        payload=json.dumps(fixture['data'],ensure_ascii=False,separators=(',',':')).encode()
        bars,audit=prepare_eastmoney(payload,expected_code=code,cutoff=DATES[-1])
        assert len(bars)==210 and not audit['invalid_source_rows'] and not audit['excluded_prefix_rows']
        assert bars[0].source_date=='2025-12-02' and bars[-1].source_date=='2026-10-16'
        ours=estimate_snapshots(bars,ALL_DATES,security_id=code)
        em=derived['stocks'][code]['snapshots']
        em1=original['stocks'][code]['snapshots']
        inputs[code]={'name':fixture['data']['data']['name'],'source_audit':audit,
            'shared_first_date':bars[0].source_date,'shared_last_date':bars[-1].source_date,
            'shared_loaded_rows':len(bars),'source_max_effective_replacement':str(max(min(Decimal(1),2*b.turnover_pct/100) for b in bars)),
            'our_final_initial_seed_survival_fraction':ours[DATES[-1]]['initial_seed_survival_upper_bound'],
            'initial_seed_is_model_initialization_not_observed_holders':True,
            'coefficient2_clipped_dates':[b.source_date for b in bars if 2*b.turnover_pct/100>1]}
        profiles[code]={'ours_coefficient2':ours,'eastmoney_local_coefficient2':em}
        for date in ALL_DATES:
            assert ours[date]['history_rows_processed']==em[date]['rows']
            assert abs(sum(Decimal(p['share_percent']) for p in ours[date]['profile'])-100)<Decimal('1e-24')
            assert len(em[date]['profile'])==150 and em[date]['step']==em1[date]['step']
        for i,date in enumerate(DATES):
            prev=ALL_DATES[i];o,ob=ours[date],ours[prev];e,eb=em[date],em[prev]
            assert len(o['peak_prices_cny'])==1 and len(e['peak_prices'])==1
            # Reuse-check against the previous SAME-INPUT control, not its long-history baseline.
            previous_control=old_comparison['same_input_controls'][code][date]
            assert o['peak_prices_cny']==previous_control['ours_same_eastmoney_210_input_peak']
            assert o['peak_bin_share_percent']==previous_control['ours_same_eastmoney_210_input_peak_share']
            own=compare_snapshots(ob,o)
            em_own=[]
            for idx in eb['peak_indices']:
                center=node_position(eb,idx);half=node_step(eb)/2
                before,after=em_mass(eb,center-half,center+half),em_mass(e,center-half,center+half)
                em_own.append({'anchor_price_display':eb['profile'][idx]['display_price'],
                    'lower_inclusive':str(center-half),'upper_exclusive':str(center+half),
                    'width_cny':float(2*half),'before_percent':before,'after_percent':after,
                    'change_percentage_points':after-before,
                    'grid_changed':node_step(e)!=node_step(eb) or e['low']!=eb['low']})
            common=[]
            for p in ob['peak_prices_cny']:
                lower,upper=Fraction(p)-Fraction(55,1000),Fraction(p)+Fraction(55,1000)
                a,b=our_mass(ob,lower,upper),our_mass(o,lower,upper)
                c,d=em_mass(eb,lower,upper),em_mass(e,lower,upper)
                old_c,old_d=em_mass(em1[prev],lower,upper),em_mass(em1[date],lower,upper)
                common.append({'anchor_our_previous_peak':p,'lower_inclusive':float(lower),
                    'upper_exclusive':float(upper),'width_cny':'0.11','whole_our_cent_cells':11,
                    'purpose':'DISPLAY_ONLY_SAME_PRICE_RANGE_NOT_A_STABILITY_THRESHOLD',
                    'ours_before_percent':a,'ours_after_percent':b,'ours_change_pp':b-a,
                    'em2_before_percent':c,'em2_after_percent':d,'em2_change_pp':d-c,
                    'after_share_difference_ours_minus_em2_pp':b-d,
                    'change_difference_ours_minus_em2_pp':(b-a)-(d-c),
                    'em1_before_percent':old_c,'em1_after_percent':old_d,'em1_change_pp':old_d-old_c,
                    'after_share_difference_ours_minus_em1_pp':b-old_d,
                    'change_difference_ours_minus_em1_pp':(b-a)-(old_d-old_c)})
            ours_price=Decimal(o['peak_prices_cny'][0]);em2_price=Decimal(str(e['peak_prices'][0]));em1_price=Decimal(str(em1[date]['peak_prices'][0]))
            rows.append({'code':code,'name':inputs[code]['name'],'date':date,'previous_date':prev,
                'input_rows':o['history_rows_processed'],'ours_peak':o['peak_prices_cny'],
                'em2_peak':e['peak_prices'],'em1_peak':em1[date]['peak_prices'],
                'peak_difference_ours_minus_em2_cny':str(ours_price-em2_price),
                'peak_difference_ours_minus_em1_cny':str(ours_price-em1_price),
                'ours_original_peaks':own['anchors'],'em2_original_peak_bands':em_own,
                'common_original_peak_bands':common})
    assert len(rows)==16
    stats={
        'peak_exact_matches_em1':sum(Decimal(r['peak_difference_ours_minus_em1_cny'])==0 for r in rows),
        'peak_exact_matches_em2':sum(Decimal(r['peak_difference_ours_minus_em2_cny'])==0 for r in rows),
        'peak_mean_absolute_error_em1_cny':str(sum(abs(Decimal(r['peak_difference_ours_minus_em1_cny'])) for r in rows)/16),
        'peak_mean_absolute_error_em2_cny':str(sum(abs(Decimal(r['peak_difference_ours_minus_em2_cny'])) for r in rows)/16),
        'peak_max_absolute_error_em1_cny':str(max(abs(Decimal(r['peak_difference_ours_minus_em1_cny'])) for r in rows)),
        'peak_max_absolute_error_em2_cny':str(max(abs(Decimal(r['peak_difference_ours_minus_em2_cny'])) for r in rows)),
        'common_band_after_share_mean_absolute_error_em1_pp':mean_abs([r['common_original_peak_bands'][0]['after_share_difference_ours_minus_em1_pp'] for r in rows]),
        'common_band_after_share_mean_absolute_error_em2_pp':mean_abs([r['common_original_peak_bands'][0]['after_share_difference_ours_minus_em2_pp'] for r in rows]),
        'common_band_change_mean_absolute_error_em1_pp':mean_abs([r['common_original_peak_bands'][0]['change_difference_ours_minus_em1_pp'] for r in rows]),
        'common_band_change_mean_absolute_error_em2_pp':mean_abs([r['common_original_peak_bands'][0]['change_difference_ours_minus_em2_pp'] for r in rows])}
    result={'schema':'chip-controlled-decay2-comparison.v1','comparison_date':'2026-10-19',
        'scope':{'stocks':CODES,'dates':DATES,'ours_coefficient':'2','eastmoney_local_variant_coefficient':'2',
            'first_date':'2025-12-02','last_date':'2026-10-16','shared_loaded_rows':210,
            'snapshot_prefix_rows':{d:206+i for i,d in enumerate(ALL_DATES)},
            'adjustment':'UNADJUSTED','unchanged_threshold':None,
            'native_code_claim':'Locally patched frozen Eastmoney source, not official client configurable output',
            'patch_semantics':derived['patch'],
            'ours_recomputed_with_exact_same_source_and_start_date':True,
            'oct12_is_direct_computation_this_round':True,
            'common_band_definition':'11 original cent cells around OUR previous peak, [p-.055,p+.055); display only',
            'grid_membership':'Exact rational geometry from source low/high; sums original native node masses without interpolation',
            'own_native_peak_bins_have_different_widths_and_are_not_direct_error_metrics':True,
            'same_price_occupancy_is_not_original_holder_retention':True,
            'not_historical_point_in_time_archive':True},
        'inputs':inputs,'rows':rows,'statistics':stats,
        'validation':{'frozen_unmodified_original_replay_checks':20,'native_no_future_suffix_checks':20,
            'both_models_normalized_profile_checks':40,'shared_history_prefix_count_checks':20,
            'our_previous_same_input_control_reproduction_checks':16,
            'production_model_hash_before_and_after':MODEL_HASH,'production_model_unchanged':True,
            'previous_comparison_unchanged':True},
        'source_hashes':{'frozen_web_source':derived['source_sha256'],
            'frozen_native_module':derived['original_module_sha256'],
            'local_derived_native_module':derived['modified_module_sha256'],
            'source_fixture':sha(FROZEN/'eastmoney-browser-histories.json'),
            'previous_comparison':sha(FROZEN/'comparison.json'),
            'node_runner':sha(ROOT/'tooling/compare_eastmoney_decay2.cjs'),
            'python_analysis':sha(Path(__file__))}}
    assert sha(ROOT/'backend/chips/model.py')==MODEL_HASH
    dump(OUT/'comparison.json',result);dump(OUT/'profiles.json',profiles)
    lines=['# 相同历史范围、双方系数2的筹码对照','',
        '四股：粤宏远A000573、华北制药600812、京东方A000725、平安银行000001。日期：2026年10月13—16日。',
        '双方使用完全相同的上一轮冻结东财不复权行情：2025-12-02至2026-10-16共210根。12日直接计算206根前缀，13—16日分别207/208/209/210根；不反解12日、不使用未来数据。',
        '我方仍用原模型且系数2；东财侧是本地源码副本，将共享换手权重A从min(1,hsl/100)改为min(1,2*hsl/100)，A同时用于旧筹码衰减与当日注入。保留150节点、OHLC四价均值三角顶点、初始化、一字日及归一化逻辑。',
        '**该结果是本地修改版的回放，不是东方财富官方客户端系数设置的认证。线上代码和上轮结果未修改，未设“没动”阈值。**','',
        '## 峰价差','', '|股票|日期|我们系数2|东财本地系数2|我方减东财（元）|东财原系数1（同范围）|',
        '|---|---|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"|{r['name']} {r['code']}|{r['date'][5:]}|{'/'.join(r['ours_peak'])}|{'/'.join(f'{v:.2f}' for v in r['em2_peak'])}|{Decimal(r['peak_difference_ours_minus_em2_cny']):+.2f}|{'/'.join(f'{v:.2f}' for v in r['em1_peak'])}|")
    lines+=['','## 原峰占比：相同价格带的可比差异','',
        '每行以昨日我方主峰为中心，使用[p-0.055,p+0.055)元价格带，含11个我方一分钱档；只用于对齐测量范围，不是筛选阈值。两边都累加原始档/节点，不插值制造东财一分钱筹码。',
        '“当前差”=我方当日占比−东财2当日占比；“变动差”=我方昨日到今日变化−东财2昨日到今日变化。差值单位是百分点。','',
        '|股票|日期|固定价格带|我们占比前→后（%）|东财2占比前→后（%）|当前差（百分点）|变动差（百分点）|',
        '|---|---|---|---:|---:|---:|---:|']
    for r in rows:
        b=r['common_original_peak_bands'][0]
        lines.append(f"|{r['name']}|{r['date'][5:]}|[{b['lower_inclusive']:.3f},{b['upper_exclusive']:.3f})|{b['ours_before_percent']:.4f}→{b['ours_after_percent']:.4f}|{b['em2_before_percent']:.4f}→{b['em2_after_percent']:.4f}|{b['after_share_difference_ours_minus_em2_pp']:+.4f}|{b['change_difference_ours_minus_em2_pp']:+.4f}|")
    lines+=['','## 各自原始主峰档（档宽不同，不能直接相减）','',
        '|股票|日期|我方原峰价：占比前→后（%）|东财2原峰价：占比前→后（%）|东财档宽（元）|',
        '|---|---|---|---|---:|']
    for r in rows:
        a=r['ours_original_peaks'][0];b=r['em2_original_peak_bands'][0]
        lines.append(f"|{r['name']}|{r['date'][5:]}|{a['anchor_price_cny']}：{float(a['previous_share_percent']):.4f}→{float(a['current_same_price_share_percent']):.4f}|{b['anchor_price_display']:.2f}：{b['before_percent']:.4f}→{b['after_percent']:.4f}{'（网格改变）' if b['grid_changed'] else ''}|{b['width_cny']:.6f}|")
    lines+=['','## 剩余差异与结论','',
        f"- 在相同210日输入、我方系数2固定的控制下，东财从系数1改为2后，16组平均绝对峰价差从{Decimal(stats['peak_mean_absolute_error_em1_cny']).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)}元降至{Decimal(stats['peak_mean_absolute_error_em2_cny']).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)}元；最大差从{float(stats['peak_max_absolute_error_em1_cny']):.2f}元降至{float(stats['peak_max_absolute_error_em2_cny']):.2f}元。",
        f"- 同一原峰价格带的当日占比平均绝对差：{stats['common_band_after_share_mean_absolute_error_em1_pp']:.4f}→{stats['common_band_after_share_mean_absolute_error_em2_pp']:.4f}个百分点；昨日到今日变动平均绝对差：{stats['common_band_change_mean_absolute_error_em1_pp']:.4f}→{stats['common_band_change_mean_absolute_error_em2_pp']:.4f}个百分点。",
        '- 差异缩小，但没有完全一致。本轮已控制行情、换手率精度、历史起点和乘数，剩余差异来自其余算法选择；没有把各项贡献拆成可相加百分比。',
        '- 当日三角顶点：我方为成交额÷股数，东财副本为(open+close+high+low)/4。',
        '- 价格分档与注入：我方固定0.01元档，三角CDF积分且每日分布总质量为1；东财副本用150个动态节点按三角高度采样、累积后整体归一化。故A相同不保证归一化后的实际替换比例相同。',
        '- 初始化：我方首个有效成交日为完整估算三角分布；东财副本从零数组注入，影响有限历史的分布。',
        f"- 平安银行截至16日，首日初始化假设仍有{Decimal(inputs['000001']['our_final_initial_seed_survival_fraction'])*100:.4f}%的模型权重存活，统一210日没有消除初始状态影响；这不是实际老股东持仓比例，也未将全部占比差归因于该项。",
        '- 一字日：东财副本使用特殊注入尺度，保留原样；粤宏远A16日为4.47元一字成交，原峰3.85不在当日成交区间。',
        '- 动态网格：华北制药16日新高6.90使网格重设，仍以固定昨日价格带追踪，没有沿用旧数组下标。',
        '- 与上一轮展示的长期校准基线区别：本轮我方也从2025-12-02重算，因此平安银行峰价为11.65，上一轮长期基线为11.68/11.67；不是线上模型改变。',
        '', '## 验证与来源','',
        '- 未修改的东财原算法20点重放与上一轮冻结结果逐节点一致；系数2副本20点未来后缀不变性检查通过。',
        '- 双方40个分布归一化检查、20个历史前缀根数检查、16个我方相同输入旧控制结果复现检查通过。',
        '- 本轮12日基准直接正向计算，不再使用上一轮数学反解。',
        '- 本地计算不证明真实股东持仓，也不代表同花顺/通达信或东财官方软件已经匹配。',
        '- 冻结公开代码原地址：https://quote.eastmoney.com/newstatic/libs/quotechart2022.js',
        '- 补丁前后准确文本及哈希：source-patch.json；完整输入、每档分布和差值：comparison.json / profiles.json。',
        '- 复现：`node tooling/compare_eastmoney_decay2.cjs outputs/chips-eastmoney-20261019 outputs/chips-eastmoney-decay2-20261019`',
        '- 分析：`python -m tooling.analyze_eastmoney_decay2`']
    (OUT/'系数2同历史对照.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'rows':len(rows),'statistics':stats,'production_model_unchanged':True,'unchanged_threshold':None,
        'report':str(OUT/'系数2同历史对照.md')},ensure_ascii=False))

if __name__=='__main__':main()
