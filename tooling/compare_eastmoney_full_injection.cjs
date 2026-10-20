// Local-only final injection intervention. Never runs the preceding runner's main.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const previous = path.join(root, 'outputs/chips-eastmoney-node150-20261019');
const output = path.join(root, 'outputs/chips-eastmoney-full-injection-20261019');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const textSha = text => crypto.createHash('sha256').update(text).digest('hex');
const plain = value => JSON.parse(JSON.stringify(value));
const expected = {
  'backend/chips/model.py': 'fdcbac40a87bc5ee99e411e78bffb440c8abfdd41dbf07fcdef813a03f1a439a',
  'tooling/compare_eastmoney_node150.cjs': '7d65fccddfb5740e251e016c2021e9e4d502197ce8fd9ae274e414e1645c21cd',
  'outputs/chips-eastmoney-node150-20261019/comparison.json': '6d0025a99d3e4f95a9a20b921b4af76a02f1c61be7751bd33fa626b92102db15',
  'outputs/chips-eastmoney-20261019/quotechart2022.js': '4b18a8d8ecfd660c1eeead6ac617cd5a05538f0a12f05bd607351cd139d07250',
  'outputs/chips-eastmoney-20261019/eastmoney-browser-histories.json': '59a6c78a94712608c9aa5eca2bcc563673d127ba6a2e1de44cbcf5efa4dc9a62',
  'outputs/chips-eastmoney-decay2-20261019/eastmoney-local-decay2.json': '6752ae424d2dad49615bc0e7e2e6dd2c1c7933f85229700783a4bc97da09ae61',
};
function checkFrozen() {
  for (const [file, hash] of Object.entries(expected)) assert.equal(sha(path.join(root, file)), hash, file);
}
checkFrozen();

// Only the declaration default changes. All grid, daily-node, recurrence and output logic stays intact.
const precedingFile = path.join(root, 'tooling/compare_eastmoney_node150.cjs');
const precedingSource = fs.readFileSync(precedingFile, 'utf8');
const marker = "const result = {schema: 'chip-dynamic150-controlled-comparison.v1'";
assert.equal(precedingSource.split(marker).length-1, 1);
const prefix = precedingSource.slice(0, precedingSource.indexOf(marker));
assert(!prefix.includes('writeFile') && !prefix.includes('mkdir'));
const before = 'function estimate(bars, normalizeDaily = true)';
const after = 'function estimate(bars, normalizeDaily = false)';
assert.equal(prefix.split(before).length-1, 1);
const modified = prefix.replace(before, after);
assert.equal(modified.replace(after, before), prefix);
const context = {require, __dirname, __filename: precedingFile};
vm.runInNewContext(modified+'\nglobalThis.localHelpers={estimate,estimateAt,nodeBand,mass,Original,Derived};', context,
  {filename: '<local-only:full-source-injection>', timeout: 5000});
const helpers = context.localHelpers;

const old = read(path.join(previous, 'comparison.json'));
const reference = read(path.join(root, 'outputs/chips-eastmoney-decay2-20261019/eastmoney-local-decay2.json'));
const originalReference = read(path.join(root, 'outputs/chips-eastmoney-20261019/eastmoney-native-calculated.json'));
const fixtures = read(path.join(root, 'outputs/chips-eastmoney-20261019/eastmoney-browser-histories.json'));
const codes = old.scope.codes, dates = ['2026-10-12', ...old.scope.dates];
assert.equal(reference.patch.after, 'A=Math.min(1,2*d.hsl/100||0)');
assert.equal(reference.settings.coefficient, 2);

function compareBand(oldBefore, oldAfter, oursBefore, oursAfter, emBefore, emAfter, bounds) {
  const oldA = helpers.mass(oldBefore, bounds), oldB = helpers.mass(oldAfter, bounds);
  const a = helpers.mass(oursBefore, bounds), b = helpers.mass(oursAfter, bounds);
  const c = helpers.mass(emBefore, bounds), d = helpers.mass(emAfter, bounds);
  const difference = b-d, changeDifference = (b-a)-(d-c);
  assert.equal(a, c); assert.equal(b, d); assert.equal(difference, 0); assert.equal(changeDifference, 0);
  return {bounds: plain(bounds), previous_unit_before_percent: oldA, previous_unit_after_percent: oldB,
    previous_unit_change_pp: oldB-oldA, ours_before_percent: a, ours_after_percent: b, ours_change_pp: b-a,
    em2_before_percent: c, em2_after_percent: d, em2_change_pp: d-c,
    current_share_difference_pp: difference, daily_change_difference_pp: changeDifference,
    previous_unit_current_share_difference_pp: oldB-d,
    previous_unit_daily_change_difference_pp: (oldB-oldA)-(d-c)};
}

const result = {
  schema: 'chip-source-injection-alignment.v1', comparison_date: '2026-10-19',
  scope: {codes, dates: dates.slice(1), shared_history_start: '2025-12-02', shared_history_end: dates.at(-1),
    shared_loaded_rows: 210, snapshot_prefix_rows: Object.fromEntries(dates.map((d, i) => [d, 206+i])),
    adjustment: 'UNADJUSTED', coefficient: 2, initial_raw_mass: 0,
    apex: '(open+close+high+low)/4', nodes: 150, step: 'max(0.01,(prefix_high-prefix_low)/149)',
    daily_allocation: 'Raw triangle-height node samples H, including source flat-day scale 149/2; no H/sum(H)',
    update: 'Q[t]=(1-A)*Q[t-1]+A*H[t], A=min(1,2*hsl/100), Q[0]=0',
    normalization: 'Only output shares Q/sum(Q); daily raw state and injection are not normalized',
    grid_replay: 'Independently replay each as-of prefix from zero on its own 150-node grid',
    original_peak: 'Fixed interval around yesterday unrounded peak, +/- half yesterday step; reselect by price if grid changes',
    reference: 'Frozen Eastmoney webpage source locally patched to coefficient2, not official client coefficient2 data',
    original_holder_retention_claimed: false, unchanged_threshold: null, production_changed: false},
  intervention: {kind: 'REMOVE_DAILY_ALLOCATION_UNIT_NORMALIZATION_ONLY',
    before, after, replacement_count: 1, reverse_restores_entire_helper_source: true,
    preceding_runner_sha256: sha(precedingFile), original_helper_sha256: textSha(prefix),
    new_helper_sha256: textSha(modified), production_module_written: false},
  coefficient_patch: reference.patch, rows: [], profiles: {}, inputs: {},
  validation: {snapshots: []}, source_hashes: expected, analysis_code_sha256: sha(__filename),
};

for (const code of codes) {
  const fixture = fixtures.find(f => f.code === code);
  const bars = fixture.data.data.klines.map(line => {const p = line.split(','); return {
    date: p[0], open: +p[1], close: +p[2], high: +p[3], low: +p[4],
    volume: +p[5], volume_money: +p[6], hsl: +p[10]};});
  assert.deepEqual(bars, reference.stocks[code].bars);
  assert.equal(bars.length, 210); assert.equal(bars[0].date, '2025-12-02');
  const original = new helpers.Original(bars), derived = new helpers.Derived(bars), snapshots = {}, em = {};
  for (const date of dates) {
    const index = bars.findIndex(b => b.date === date), prefixBars = bars.slice(0, index+1);
    const saved = reference.stocks[code].snapshots[date];
    const baseline = plain(helpers.estimate(prefixBars, true));
    assert.deepEqual(baseline, old.profiles[code].node150_unit_daily[date]);
    const ours = plain(helpers.estimateAt(bars, index));
    assert.deepEqual(ours, plain(helpers.estimate(prefixBars)));
    const one = original.calc(index), two = derived.calc(index);
    assert.deepEqual(Array.from(one.x), originalReference.stocks[code].snapshots[date].profile.map(p => p.raw));
    assert.deepEqual(Array.from(two.x), saved.profile.map(p => p.raw));
    assert.deepEqual(Array.from(new helpers.Derived(prefixBars).calc(index).x), Array.from(two.x));
    assert.deepEqual(ours.profile.map(p => p.raw), saved.profile.map(p => p.raw));
    assert.deepEqual(ours.profile.map(p => p.share_percent), saved.profile.map(p => p.share_percent));
    assert.deepEqual(ours.profile.map(p => p.price), saved.profile.map(p => p.price));
    assert.deepEqual(ours.profile.map(p => p.display_price), saved.profile.map(p => p.display_price));
    assert.deepEqual(ours.peak_indices, saved.peak_indices);
    assert.deepEqual(ours.peak_prices, saved.peak_prices);
    assert.deepEqual(ours.peak_unrounded_prices, saved.peak_unrounded_prices);
    assert.equal(ours.raw_total, saved.raw_total);
    const first = ours.daily_trace[0];
    assert(Math.abs(first.actual_raw_mass_after_update-first.A*first.sampled_daily_total) < 1e-10);
    assert(first.sampled_daily_total !== 1 && first.actual_raw_mass_after_update !== first.A);
    assert(ours.daily_trace.every(t => t.injected_raw_total === t.A*t.sampled_daily_total));
    assert(ours.profile.every(p => Number.isFinite(p.raw) && p.raw >= 0));
    assert(Math.abs(ours.profile.reduce((s,p) => s+p.share_percent,0)-100) < 1e-10);
    snapshots[date] = ours; em[date] = saved;
    result.validation.snapshots.push({code, date, rows: prefixBars.length,
      previous_unit_injection_control_exactly_reproduced: true,
      source_coeff1_control_exact_replay: true, source_coeff2_exact_replay: true,
      future_suffix_invariant_ours_and_reference: true, raw_vector_exactly_equal: true,
      normalized_share_vector_exactly_equal: true, peak_raw_and_display_coordinates_exactly_equal: true,
      first_raw_mass_equals_A_times_sampled_total: true, daily_injection_has_no_unit_normalization: true,
      max_raw_difference: 0, max_share_difference_pp: 0});
  }
  result.profiles[code] = {ours_source_injection: snapshots, em2_source: em};
  result.inputs[code] = {name: fixture.data.data.name, rows: bars.length,
    first_date: bars[0].date, last_date: bars.at(-1).date,
    last_snapshot_first_day: snapshots[dates.at(-1)].daily_trace[0]};
  for (let i = 1; i < dates.length; i++) {
    const date = dates[i], prev = dates[i-1], nb = snapshots[prev], na = snapshots[date], eb = em[prev], ea = em[date];
    const oldProfiles = old.profiles[code].node150_unit_daily;
    const args = [oldProfiles[prev], oldProfiles[date], nb, na, eb, ea];
    const previousRow = old.rows.find(r => r.code === code && r.date === date);
    const originalBands = nb.peak_indices.map(j => compareBand(...args, helpers.nodeBand(nb, j)));
    assert.equal(originalBands.length, previousRow.own_original_peak_bands.length);
    for (let j = 0; j < originalBands.length; j++) {
      assert.equal(originalBands[j].previous_unit_current_share_difference_pp,
        previousRow.own_original_peak_bands[j].node150_current_gap_pp);
      assert.equal(originalBands[j].previous_unit_daily_change_difference_pp,
        previousRow.own_original_peak_bands[j].node150_change_error_pp);
    }
    result.rows.push({code, name: fixture.data.data.name, date, previous_date: prev,
      ours_peak: na.peak_prices, em2_peak: ea.peak_prices,
      ours_peak_unrounded: na.peak_unrounded_prices, em2_peak_unrounded: ea.peak_unrounded_prices,
      original_peak_bands: originalBands, grid_changed: nb.low !== na.low || nb.step !== na.step,
      frozen_previous_ohlc4_011_bands: previousRow.frozen_previous_ohlc4_011_bands.map(b => compareBand(...args,b.bounds)),
      frozen_earlier_vwap_011_bands: previousRow.frozen_earlier_vwap_011_bands.map(b => compareBand(...args,b.bounds))});
  }
}
const mae = values => values.reduce((s, x) => s+Math.abs(x),0)/values.length;
result.statistics = {};
for (const [label, rows] of [['all_16', result.rows], ...codes.map(c => [c,result.rows.filter(r => r.code === c)])]) {
  result.statistics[label] = {observations: rows.length,
    peak_raw_coordinates_exact_matches: rows.length,
    previous_unit_original_share_mae_pp: mae(rows.map(r => r.original_peak_bands[0].previous_unit_current_share_difference_pp)),
    original_share_mae_pp: mae(rows.map(r => r.original_peak_bands[0].current_share_difference_pp)),
    previous_unit_daily_change_mae_pp: mae(rows.map(r => r.original_peak_bands[0].previous_unit_daily_change_difference_pp)),
    daily_change_mae_pp: mae(rows.map(r => r.original_peak_bands[0].daily_change_difference_pp))};
}
assert.equal(result.rows.length,16); assert.equal(result.validation.snapshots.length,20);
checkFrozen();
fs.mkdirSync(output,{recursive:true});
fs.writeFileSync(path.join(output,'comparison.json'),JSON.stringify(result,null,2));

const lines = ['# 每日注入统一后的完整对照', '',
  '结论：四股2026年10月13—16日共16行的峰价、原峰占比和每日变化均与东财本地系数2源码副本完全一致。包含10月12日基线的20个快照，每个150节点的原始数组、占比数组及未舍入价格坐标也逐项完全一致，最大差为0。', '',
  '双方使用相同210根冻结不复权行情（2025-12-02至2026-10-16）；12—16日各仅使用206/207/208/209/210根当日行情前缀，独立确定网格并从零回放。系数2、零初始化、OHLC四价均值顶点、150动态节点及三角高度采样保持不变。',
  '本轮唯一数值改动：取消每日新筹码H/sum(H)，直接按Q[t]=(1-A)*Q[t-1]+A*H[t]注入，A=min(1,2×换手率/100)。原始状态不逐日归一化，只有生成占比时除以原始总质量。',
  '线上算法未修改，“没动”阈值未设置。东财参考是冻结网页源码在本地将A乘数改成2后的回放，并非官方客户端系数2数据，也不是实际股东持仓认证。', '',
  '## 四股全部16行', '',
  '原峰按昨日未舍入峰坐标±昨日半步长形成的固定价格区间追踪；网格改变时按价格重新选择节点，不沿用旧下标。表中峰价为两位显示值；相等结论还包括未舍入坐标。占比为%，变化及差异为百分点。', '',
  '|股票|日期|当日峰价（双方）|昨日原峰价|原峰占比昨日→今日（双方）|每日变化（双方）|峰价差元|占比差|变化差|',
  '|---|---|---:|---:|---:|---:|---:|---:|---:|'];
for (const r of result.rows) {
  const b = r.original_peak_bands[0];
  lines.push(`|${r.name} ${r.code}|${r.date.slice(5)}|${r.ours_peak.map(p=>p.toFixed(2)).join('/')}|${b.bounds.center_display.toFixed(2)}|${b.ours_before_percent.toFixed(9)}→${b.ours_after_percent.toFixed(9)}|${b.ours_change_pp.toFixed(9)}|0|0|0|`);
}
lines.push('', '## 同一原峰区间：上一轮到本轮', '',
  '各股票四日平均绝对差，均为百分点。没有通过移动原峰价格区间取得改善。', '',
  '|股票|上一轮原峰占比MAE|本轮占比MAE|上一轮每日变化MAE|本轮变化MAE|',
  '|---|---:|---:|---:|---:|');
for (const label of ['all_16',...codes]) {
  const s = result.statistics[label];
  lines.push(`|${label}|${s.previous_unit_original_share_mae_pp.toFixed(12)}|${s.original_share_mae_pp}|${s.previous_unit_daily_change_mae_pp.toFixed(12)}|${s.daily_change_mae_pp}|`);
}
lines.push('', '## 核验与可复现边界', '',
  '- 运行：`node tooling/compare_eastmoney_full_injection.cjs`。',
  '- 上轮辅助计算源码仅在内存中将normalizeDaily默认值true改为false；一项补丁可逆恢复全部原辅助源码，原文件未修改。',
  '- 20个上轮单位注入控制快照逐字段完全复现；20个原源码系数1及20个东财系数2快照重新执行并逐节点复现。',
  '- 20个本轮快照共3000个原始节点质量和3000个占比数值精确相等；未将小差异四舍五入成0。',
  '- 首日原始质量核验为A×sum(H)，不是A，也不是完整初始三角质量1；每日注入及原始总质量递推检查保留。',
  '- 华北制药16日网格变化，原峰区间仍固定在15日原峰价格；每个目标日都通过截断历史/完整未来后缀不变性核验。',
  '- comparison.json保存双方40份快照、16行峰价/占比/日变化、同价旧锚带、日注入轨迹、精确补丁及输入哈希。',
  '- production model.py、上轮代码/结果及冻结行情哈希不变；没有部署或调用线上写入接口。',
  '- 本结论仅证实这四股、这210根冻结输入下的数值实现一致，不等于官方客户端所有设置或真实筹码分布已验证。');
fs.writeFileSync(path.join(output,'每日注入统一对照.md'),lines.join('\n')+'\n');
console.log(JSON.stringify({rows:result.rows.length,snapshot_checks:result.validation.snapshots.length,
  exact_raw_nodes:3000,exact_share_nodes:3000,statistics:result.statistics,
  comparison_sha256:sha(path.join(output,'comparison.json')),report:path.join(output,'每日注入统一对照.md')}));
