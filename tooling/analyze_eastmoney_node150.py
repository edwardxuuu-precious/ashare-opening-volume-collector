"""Render the frozen local dynamic-node comparison; no production I/O."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/chips-eastmoney-node150-20261019'


def main():
    result = json.loads((OUT / 'comparison.json').read_bytes())
    assert result['analysis_code_sha256'] == hashlib.sha256((ROOT / 'tooling/compare_eastmoney_node150.cjs').read_bytes()).hexdigest()
    for file, digest in result['source_hashes'].items():
        assert hashlib.sha256((ROOT / file).read_bytes()).hexdigest() == digest
    assert len(result['rows']) == 16 and len(result['validation']['snapshots']) == 20
    assert result['scope']['coefficient'] == 2 and result['scope']['unchanged_threshold'] is None
    rows, stats = result['rows'], result['statistics']
    maximum_share_gap = max(abs(r['own_original_peak_bands'][0]['node150_current_gap_pp']) for r in rows)
    maximum_change_gap = max(abs(r['own_original_peak_bands'][0]['node150_change_error_pp']) for r in rows)
    control_error = max(c['source_injection_control_max_error_pp'] for c in result['validation']['snapshots'])
    lines = ['# 150动态节点对照：系数2、零初始化、OHLC四价均值', '',
             '四只股票，2026年10月13—16日；共同冻结不复权行情为2025-12-02至2026-10-16共210根，10月12日另算作昨日基线。12—16日各只使用206/207/208/209/210根当日行情前缀。',
             '双方系数均为2，原始筹码质量从0开始，三角顶点为(开+收+高+低)/4。本轮我方由1分钱CDF积分改为东财150个动态节点与相同三角高度采样，保留我方此前“每天新分布先归一化为1，再按A注入”的规则。',
             '每个目标日分别取截至当日的最低价、最高价，步长=max(0.01,(最高-最低)/149)，再从零回放该段历史。不是将昨天数组原下标直接递推到今天，也不使用16日网格计算13日。',
             '**线上算法未修改，“没动”阈值未设置。这里是本地算法对照，不是官方客户端系数2数据或真实持仓认证。**', '',
             '## 东财系数确实已改为2', '',
             '- 冻结网页源码原权重：`A=min(1,hsl/100)`。',
             '- 本地副本唯一源码补丁：`A=min(1,2*hsl/100)`，同一个A同时用于旧筹码乘以(1-A)与当天筹码注入。',
             '- 本轮重新执行原源码系数1和补丁系数2，各20个快照分别逐项复现此前冻结结果；四股实际计算数组均因系数2而改变，不是只改元数据标签。',
             '- 因此下文的东财2指“东方财富冻结源码的本地系数2副本”；没有声称从官方网页直接下载了系数2筹码。', '',
             '## 全部16行：昨日原峰占比与每日变化', '',
             '每行以本轮我方昨日主峰的**未舍入节点坐标**为中心，取昨天一个节点宽度的固定价格区间：[原峰-昨日步长/2,原峰+昨日步长/2)。两边使用同一价格区间；今日网格变化时重新按价格找节点，不按旧下标。表中锚价仅显示两位小数。',
             '“占比差”=我方当天占比-东财2当天占比；“变化差”=我方(今日-昨日)-东财2(今日-昨日)，均为百分点。它表示同价区间总占比，可能包含新注入筹码，不是原股东留存率。', '',
             '|股票|日期|昨日原峰元|我方昨日→今日%|东财2昨日→今日%|我方日变化|东财2日变化|占比差|变化差|',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        b = r['own_original_peak_bands'][0]
        lines.append(f"|{r['name']} {r['code']}|{r['date'][5:]}|{b['bounds']['center_display']:.2f}|{b['node150_before_percent']:.6f}→{b['node150_after_percent']:.6f}|{b['em2_before_percent']:.6f}→{b['em2_after_percent']:.6f}|{b['node150_change_pp']:+.6f}|{b['em2_change_pp']:+.6f}|{b['node150_current_gap_pp']:+.6f}|{b['node150_change_error_pp']:+.6f}|")
    lines += ['', '## 原峰占比残差统计', '',
              '这是本轮相同动态节点价格带上的差异。与上轮“一分钱原档”宽度不同，不直接拿两种原档占比作前后改善比例。', '',
              '|股票|原峰当前占比平均绝对差（百分点）|每日变化平均绝对差（百分点）|当日峰节点匹配数|',
              '|---|---:|---:|---:|']
    for label in ['all_16', *result['scope']['codes']]:
        s = stats[label]
        lines.append(f"|{label}|{s['node150_own_original_peak_current_share_mae_pp']:.9f}|{s['node150_own_original_peak_daily_change_mae_pp']:.9f}|{s['peak_node_matches']}/{s['observations']}|")
    lines += ['', f'16行最大原峰当前占比绝对差为{maximum_share_gap:.9f}个百分点，最大每日变化绝对差为{maximum_change_gap:.9f}个百分点。', '',
              '## 与上一轮公平比较：冻结同一0.11元价格带', '',
              '以下冻结上一轮OHLC4一分钱模型的昨日主峰及[p-0.055,p+0.055)价格带，改动前、改动后和东财都在相同区间计数。0.11元只是测量口径，不是“没动”的阈值。', '',
              '|样本|旧当前占比MAE|本轮当前占比MAE|旧日变化MAE|本轮日变化MAE|',
              '|---|---:|---:|---:|---:|']
    for label in ['all_16', *result['scope']['codes']]:
        s = stats[label]
        lines.append(f"|{label}|{s['frozen_previous_ohlc4_band_old_current_share_mae_pp']:.9f}|{s['frozen_previous_ohlc4_band_new_current_share_mae_pp']:.9f}|{s['frozen_previous_ohlc4_band_old_daily_change_mae_pp']:.9f}|{s['frozen_previous_ohlc4_band_new_daily_change_mae_pp']:.9f}|")
    lines += ['',
              '与更早VWAP零初始化原峰锚带衔接的16行当前占比MAE为0.432349562→0.027304594个百分点；其锚带与上表不同，已在JSON中单独保留，不能混用。', '',
              '## 今日峰价与动态网格', '',
              '|股票|日期|上轮1分钱峰价|本轮150节点峰价|东财2峰价|节点未舍入坐标相等|网格变化|',
              '|---|---|---:|---:|---:|---|---|']
    for r in rows:
        lines.append(f"|{r['name']}|{r['date'][5:]}|{'/'.join(r['old_cent_peak'])}|{'/'.join(f'{v:.2f}' for v in r['node150_peak'])}|{'/'.join(f'{v:.2f}' for v in r['em2_peak'])}|{'是' if r['node150_peak_unrounded']==r['em2_peak_unrounded'] else '否'}|{'是' if r['grid_changed'] else '否'}|")
    lines += ['',
              '平安银行本轮四日主峰的原始节点坐标也与东财2相同，约11.687248322元（显示11.69元）；此前我方11.69元档中心与东财节点约0.002752元的坐标差也消除。',
              '华北制药16日网格步长从约0.016913元变为0.018725元，仍追踪15日原峰的固定价格区间，不把新旧数组下标当作相同价格。', '',
              '## 剩余差异：每日单位质量归一化', '',
              '设当天150节点三角高度向量为H，S=sum(H)，A=min(1,2×换手率/100)。两边都从Q0=0开始，但：', '',
              '- 本轮我方保留原有单位质量规则：`Q[t]=(1-A)*Q[t-1]+A*(H/S)`。',
              '- 东财源码副本：`R[t]=(1-A)*R[t-1]+A*H`。',
              '- 双方都只在生成展示占比时除以各自原始总质量，没有新增逐日状态归一化。',
              '- S会随每日行情形状、采样位置而变化，不能当作一个始终相同、最后能统一抵消的常数。一字日源码注入尺度为149/2，也保留在H中。',
              '- 本样本所有前缀都没有空采样日；零数组起步，首日我方实际原始总质量经核验等于A而不是1。', '',
              '|股票|16日网格回放中每日S最小值|最大值|一字日数量|',
              '|---|---:|---:|---:|']
    for code in result['scope']['codes']:
        x = result['inputs'][code]
        lines.append(f"|{x['name']}|{x['last_snapshot_daily_total_range'][0]:.9f}|{x['last_snapshot_daily_total_range'][1]:.9f}|{len(x['last_snapshot_flat_samples'])}|")
    lines += ['',
              f'本地另做了明确标注的“源注入规则验算”：只关闭当天H/S这一步，将采样高度H直接注入。20个快照的未归一化数组及最终占比均逐项复现东财系数2源码，最大占比误差为{control_error:.12g}个百分点。该验算用于核实剩余差异来源，并未将这一额外规则改动采纳为线上算法。',
              '因此本样本残差可定位到每日注入质量规则；不是系数仍为1，也不是历史范围或主峰坐标没有对齐。结论只针对这四股冻结输入，不外推官方客户端全部版本或真实筹码持仓。', '',
              '## 复现与证据', '',
              '- 计算：`node tooling/compare_eastmoney_node150.cjs`。',
              '- 报表：`python -m tooling.analyze_eastmoney_node150`。',
              '- 20个系数1控制快照、20个东财系数2快照逐项精确复现；20个我方快照及参考均核验行情前缀/未来后缀不变性。',
              '- 我方150节点坐标、显示价和步长与参考逐项一致；非负、归一化、首日A质量及每日原始质量递推检查通过。',
              '- comparison.json保存40个我方新节点/东财参考快照、16行差异、日注入轨迹、系数补丁和源文件哈希；上轮20个我方快照保留在原冻结文件中。',
              '- 原生产model.py及前轮行情、脚本结果哈希保持不变；没有部署或访问线上写入接口。']
    destination = OUT / '150动态节点对照.md'
    destination.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'report': str(destination), 'comparison_sha256': hashlib.sha256((OUT/'comparison.json').read_bytes()).hexdigest(),
                      'rows': len(rows), 'snapshot_checks': len(result['validation']['snapshots'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
