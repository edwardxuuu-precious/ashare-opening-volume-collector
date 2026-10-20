// Local discretization experiment only. Production and frozen inputs are read-only.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const frozen = path.join(root, 'outputs/chips-eastmoney-20261019');
const prior = path.join(root, 'outputs/chips-eastmoney-decay2-20261019');
const baseline = path.join(root, 'outputs/chips-eastmoney-ohlc4-20261019');
const out = path.join(root, 'outputs/chips-eastmoney-node150-20261019');
const read = p => JSON.parse(fs.readFileSync(p, 'utf8'));
const hash = p => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const textHash = s => crypto.createHash('sha256').update(s).digest('hex');
const expected = {
  'backend/chips/model.py': 'fdcbac40a87bc5ee99e411e78bffb440c8abfdd41dbf07fcdef813a03f1a439a',
  'outputs/chips-eastmoney-20261019/quotechart2022.js': '4b18a8d8ecfd660c1eeead6ac617cd5a05538f0a12f05bd607351cd139d07250',
  'outputs/chips-eastmoney-20261019/eastmoney-browser-histories.json': '59a6c78a94712608c9aa5eca2bcc563673d127ba6a2e1de44cbcf5efa4dc9a62',
  'outputs/chips-eastmoney-decay2-20261019/eastmoney-local-decay2.json': '6752ae424d2dad49615bc0e7e2e6dd2c1c7933f85229700783a4bc97da09ae61',
  'outputs/chips-eastmoney-ohlc4-20261019/comparison.json': 'bb057ecf4419cfe81bda19a98c48b09b5fb50c5cd056232b0c5b3440eea2f614',
  'outputs/chips-eastmoney-ohlc4-20261019/profiles.json': '01f9b04530e7c3a4af2d3965d5b2f33eb06e934caa202af91b47fc160030b174',
};
function checkFrozen() {
  for (const [p, sha] of Object.entries(expected)) assert.equal(hash(path.join(root, p)), sha, p);
}
checkFrozen();
const em2 = read(path.join(prior, 'eastmoney-local-decay2.json'));
const em1 = read(path.join(frozen, 'eastmoney-native-calculated.json'));
const oldProfiles = read(path.join(baseline, 'profiles.json'));
const oldResult = read(path.join(baseline, 'comparison.json'));
const fixtures = read(path.join(frozen, 'eastmoney-browser-histories.json'));
const codes = ['000573', '600812', '000725', '000001'];
const dates = ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16'];

// Re-execute the actual frozen source; validate both coefficient1 and coefficient2.
const source = fs.readFileSync(path.join(frozen, 'quotechart2022.js'), 'utf8');
const marker = source.indexOf('"./src/modules/tools/indicator/cyq.ts":');
assert(marker >= 0);
const start = source.indexOf('function(e,t)', marker);
const end = source.indexOf('},"./', start) + 1;
const originalModule = source.slice(start, end);
assert.equal(textHash(originalModule), em2.original_module_sha256);
const before = 'A=Math.min(1,d.hsl/100||0)';
const after = 'A=Math.min(1,2*d.hsl/100||0)';
assert.equal(originalModule.split(before).length - 1, 1);
const patchedModule = originalModule.replace(before, after);
assert.equal(patchedModule.replace(after, before), originalModule);
assert.equal(textHash(patchedModule), em2.modified_module_sha256);
assert.equal(em2.settings.coefficient, 2);
function load(moduleSource) {
  const exports = {};
  vm.runInNewContext('('+moduleSource+')(null,exports)', {exports}, {timeout: 2000});
  return exports.CYQCalculator;
}
const Original = load(originalModule), Derived = load(patchedModule);

function grid(bars) {
  const low = Math.min(...bars.map(b => b.low)), high = Math.max(...bars.map(b => b.high));
  const step = Math.max(.01, (high-low)/149);
  const lowCents = Math.round(low*100), highCents = Math.round(high*100);
  return {low, high, step, lowCents, stepNumerator: Math.max(149, highCents-lowCents)};
}
function mode(bar) { return (bar.open+bar.close+bar.high+bar.low)/4; }
function dailyNodes(bar, g) {
  const result = Array(150).fill(0), apex = mode(bar);
  if (bar.high === bar.low) {
    const index = Math.floor((apex-g.low)/g.step);
    assert(index >= 0 && index < 150);
    result[index] = 149/2; // source flat-day scale; unit normalization below removes its amplitude only.
  } else {
    const height = 2/(bar.high-bar.low);
    const first = Math.ceil((bar.low-g.low)/g.step), last = Math.floor((bar.high-g.low)/g.step);
    for (let j = first; j <= last; j++) {
      assert(j >= 0 && j < 150);
      const x = g.low+g.step*j;
      result[j] = x <= apex
        ? (Math.abs(apex-bar.low) < 1e-8 ? height : (x-bar.low)/(apex-bar.low)*height)
        : (Math.abs(bar.high-apex) < 1e-8 ? height : (bar.high-x)/(bar.high-apex)*height);
    }
  }
  assert(result.every(x => Number.isFinite(x) && x >= 0));
  return result;
}
function estimate(bars, normalizeDaily = true) {
  const g = grid(bars), raw = Array(150).fill(0), trace = [];
  let expectedRawMass = 0;
  for (const b of bars) {
    const a = Math.min(1, 2*b.hsl/100), allocation = dailyNodes(b, g);
    assert(Number.isFinite(a) && a >= 0 && a <= 1);
    const total = allocation.reduce((s, x) => s+x, 0);
    // A positive-turnover narrow bar may contain no sampled node; do not invent an injection.
    const divisor = normalizeDaily && total > 0 ? total : 1;
    for (let i = 0; i < 150; i++) raw[i] *= 1-a;
    for (let i = 0; i < 150; i++) raw[i] += (allocation[i]/divisor)*a;
    const injectionMass = normalizeDaily && total > 0 ? a : total*a;
    expectedRawMass = expectedRawMass*(1-a)+injectionMass;
    const actualRawMass = raw.reduce((s, x) => s+x, 0);
    assert(Math.abs(actualRawMass-expectedRawMass) < 1e-10*Math.max(1, expectedRawMass));
    trace.push({date: b.date, mode: mode(b), A: a, sampled_daily_total: total,
      injected_raw_total: injectionMass, actual_raw_mass_after_update: actualRawMass,
      flat: b.low === b.high, empty_sample: total === 0});
  }
  const total = raw.reduce((s, x) => s+x, 0);
  assert(Number.isFinite(total) && total > 0);
  const maximum = Math.max(...raw), peaks = raw.map((x, i) => x === maximum ? i : -1).filter(i => i >= 0);
  return {date: bars.at(-1).date, rows: bars.length, ...g, raw_total: total,
    peak_indices: peaks, peak_prices: peaks.map(i => +(g.low+g.step*i).toFixed(2)),
    peak_unrounded_prices: peaks.map(i => g.low+g.step*i), peak_share_percent: maximum/total*100,
    profile: raw.map((x, i) => ({price: g.low+g.step*i, display_price: +(g.low+g.step*i).toFixed(2), raw: x, share_percent: x/total*100})),
    daily_trace: trace};
}
function estimateAt(bars, index) { return estimate(bars.slice(0, index+1)); }

// Rational membership in denominator 29800 = 2*149*100. No rounded display-price anchors.
function coordinateNumerator(snapshot, index) {
  return Math.round(snapshot.low*100)*149 + Math.max(149, Math.round(snapshot.high*100)-Math.round(snapshot.low*100))*index;
}
function nodeBand(snapshot, index) {
  const stepNumerator = Math.max(149, Math.round(snapshot.high*100)-Math.round(snapshot.low*100));
  const center = 2*coordinateNumerator(snapshot, index);
  return {lower: center-stepNumerator, upper: center+stepNumerator, denominator: 29800,
    center_unrounded: center/29800, center_display: snapshot.profile[index].display_price,
    width_cny: stepNumerator/14900};
}
function centBand(anchor) {
  const center = Math.round(Number(anchor)*100)*298;
  return {lower: center-1639, upper: center+1639, denominator: 29800,
    center_unrounded: Number(anchor), center_display: Number(anchor), width_cny: .11};
}
function mass(snapshot, bounds, cents = false) {
  return snapshot.profile.reduce((s, p, i) => {
    const position = cents ? Math.round(Number(p.price_cny)*100)*298 : 2*coordinateNumerator(snapshot, i);
    return s + (bounds.lower <= position && position < bounds.upper ? Number(p.share_percent) : 0);
  }, 0);
}
function changes(ob, oa, nb, na, eb, ea, bounds) {
  const oldBefore = mass(ob, bounds, true), oldAfter = mass(oa, bounds, true);
  const newBefore = mass(nb, bounds), newAfter = mass(na, bounds);
  const emBefore = mass(eb, bounds), emAfter = mass(ea, bounds);
  return {bounds, old_before_percent: oldBefore, old_after_percent: oldAfter, old_change_pp: oldAfter-oldBefore,
    node150_before_percent: newBefore, node150_after_percent: newAfter, node150_change_pp: newAfter-newBefore,
    em2_before_percent: emBefore, em2_after_percent: emAfter, em2_change_pp: emAfter-emBefore,
    old_current_gap_pp: oldAfter-emAfter, node150_current_gap_pp: newAfter-emAfter,
    old_change_error_pp: (oldAfter-oldBefore)-(emAfter-emBefore),
    node150_change_error_pp: (newAfter-newBefore)-(emAfter-emBefore)};
}

const result = {schema: 'chip-dynamic150-controlled-comparison.v1',
  scope: {codes, dates: dates.slice(1), history_start: '2025-12-02', history_end: dates.at(-1), loaded_rows: 210,
    prefix_rows: Object.fromEntries(dates.map((d, i) => [d, 206+i])), coefficient: 2, initial_raw_mass: 0,
    mode: '(open+close+high+low)/4', nodes: 150, step: 'max(0.01,(prefix_high-prefix_low)/149)',
    replay_grid: 'Each as-of prefix determines its own grid; replay that prefix from zero on that grid',
    intervention: 'Replace cent CDF integration with identical source dynamic nodes and triangle-height samples',
    retained_ours_rule: 'Normalize each nonempty daily allocation to one before A-weighted injection; raw state normalized only for reporting',
    remaining_reference_difference: 'Source injects sampled triangle heights without daily unit-mass normalization; flat scale retained in source',
    numerical_arithmetic: 'JavaScript double, same node-boundary and OHLC summation order as reference; independently audited replay',
    unchanged_threshold: null, production_changed: false,
    reference: 'locally patched frozen Eastmoney source at coefficient2; not official client coefficient2 data',
    original_peak_definition: 'Yesterday peak raw coordinate +/- half yesterday step; fixed price interval retained on grid changes',
    same_price_occupancy_is_original_holder_retention: false},
  coefficient_patch: {before, after, unique_replacement: 1, reverse_restores_source: true,
    applies_to: 'Historical (1-A) decay AND same-day A injection',
    original_module_sha256: textHash(originalModule), modified_module_sha256: textHash(patchedModule)},
  inputs: {}, rows: [], profiles: {}, validation: {snapshots: []}, source_hashes: expected,
  analysis_code_sha256: hash(__filename)};

for (const code of codes) {
  const fixture = fixtures.find(f => f.code === code), lines = fixture.data.data.klines;
  const bars = lines.map(line => { const p = line.split(','); return {date: p[0], open: +p[1], close: +p[2],
    high: +p[3], low: +p[4], volume: +p[5], volume_money: +p[6], hsl: +p[10]}; });
  assert.equal(bars.length, 210); assert.equal(bars[0].date, '2025-12-02');
  assert.deepEqual(bars, em2.stocks[code].bars);
  const original = new Original(bars), derived = new Derived(bars), snapshots = {}, native = {};
  let maxControlError = 0, maxControlRawError = 0, changedFromCoefficient1 = false;
  for (const date of dates) {
    const index = bars.findIndex(b => b.date === date), prefix = bars.slice(0, index+1);
    const e = em2.stocks[code].snapshots[date], one = original.calc(index), two = derived.calc(index);
    assert.deepEqual(Array.from(one.x), em1.stocks[code].snapshots[date].profile.map(p => p.raw));
    assert.deepEqual(Array.from(two.x), e.profile.map(p => p.raw));
    assert.deepEqual(Array.from(two.y), e.profile.map(p => p.display_price));
    assert.deepEqual(Array.from(new Derived(prefix).calc(index).x), Array.from(two.x));
    changedFromCoefficient1 ||= Array.from(two.x).some((x, i) => x !== one.x[i]);
    const ours = estimateAt(bars, index), sourceInjectionControl = estimate(prefix, false);
    assert.deepEqual(ours, estimate(prefix));
    assert(ours.daily_trace.every(t => !t.empty_sample));
    assert(Math.abs(ours.daily_trace[0].actual_raw_mass_after_update-ours.daily_trace[0].A) < 1e-12);
    assert.equal(ours.step, e.step); assert.equal(ours.low, e.low); assert.equal(ours.high, e.high);
    assert.deepEqual(ours.profile.map(p => p.price), e.profile.map(p => p.price));
    assert.deepEqual(ours.profile.map(p => p.display_price), e.profile.map(p => p.display_price));
    assert(Math.abs(ours.profile.reduce((s, p) => s+p.share_percent, 0)-100) < 1e-10);
    assert(ours.profile.every(p => p.raw >= 0 && Number.isFinite(p.share_percent)));
    const error = Math.max(...sourceInjectionControl.profile.map((p, i) => Math.abs(p.share_percent-e.profile[i].share_percent)));
    const rawError = Math.max(...sourceInjectionControl.profile.map((p, i) => Math.abs(p.raw-e.profile[i].raw)));
    assert(error < 1e-10); assert(rawError < 1e-9);
    maxControlError = Math.max(maxControlError, error); maxControlRawError = Math.max(maxControlRawError, rawError);
    snapshots[date] = ours; native[date] = e;
    result.validation.snapshots.push({code, date, source_coeff1_exact_replay: true, source_coeff2_exact_replay: true,
      reference_future_suffix_invariant: true, ours_future_suffix_invariant: true,
      first_raw_mass_equals_A: true, direct_raw_mass_recurrence_verified: true,
      node_geometry_exact_match: true, normalized_nonnegative: true,
      source_injection_control_max_error_pp: error, source_injection_control_max_raw_error: rawError});
  }
  assert(changedFromCoefficient1);
  result.inputs[code] = {name: fixture.data.data.name, rows: bars.length,
    coefficient2_changes_actual_calculated_values: changedFromCoefficient1,
    source_injection_control_max_error_pp: maxControlError, source_injection_control_max_raw_error: maxControlRawError,
    last_snapshot_daily_total_range: [Math.min(...snapshots[dates.at(-1)].daily_trace.map(t => t.sampled_daily_total)),
      Math.max(...snapshots[dates.at(-1)].daily_trace.map(t => t.sampled_daily_total))],
    last_snapshot_empty_samples: snapshots[dates.at(-1)].daily_trace.filter(t => t.empty_sample).map(t => t.date),
    last_snapshot_flat_samples: snapshots[dates.at(-1)].daily_trace.filter(t => t.flat)};
  result.profiles[code] = {node150_unit_daily: snapshots, em2_source: native};
  for (let i = 1; i < dates.length; i++) {
    const date = dates[i], prev = dates[i-1], old = oldProfiles[code].ohlc4_zero_start;
    const ob = old[prev], oa = old[date], nb = snapshots[prev], na = snapshots[date], eb = native[prev], ea = native[date];
    const args = [ob, oa, nb, na, eb, ea];
    const oldRow = oldResult.rows.find(r => r.code === code && r.date === date);
    const frozenOldBands = oldRow.frozen_previous_vwap_peak_bands.map(b => {
      const x = changes(...args, centBand(b.anchor_price_cny));
      assert(Math.abs(x.old_current_gap_pp-b.ohlc4_current_gap_pp) < 1e-10);
      return x;
    });
    result.rows.push({code, name: fixture.data.data.name, date, previous_date: prev,
      old_cent_peak: oa.peak_prices_cny, node150_peak: na.peak_prices, em2_peak: ea.peak_prices,
      node150_peak_unrounded: na.peak_unrounded_prices, em2_peak_unrounded: ea.peak_unrounded_prices,
      own_original_peak_bands: nb.peak_indices.map(j => changes(...args, nodeBand(nb, j))),
      reference_original_peak_bands: eb.peak_indices.map(j => changes(...args, nodeBand(eb, j))),
      frozen_earlier_vwap_011_bands: frozenOldBands,
      frozen_previous_ohlc4_011_bands: ob.peak_prices_cny.map(p => changes(...args, centBand(p))),
      grid_changed: nb.low !== na.low || nb.step !== na.step,
      previous_step: nb.step, current_step: na.step,
      own_previous_peak_indices: nb.peak_indices, reference_previous_peak_indices: eb.peak_indices});
  }
}

const mae = values => values.reduce((s, x) => s+Math.abs(x), 0)/values.length;
result.statistics = {};
for (const [label, rows] of [['all_16', result.rows], ...codes.map(code => [code, result.rows.filter(r => r.code === code)])]) {
  result.statistics[label] = {
    node150_own_original_peak_current_share_mae_pp: mae(rows.map(r => r.own_original_peak_bands[0].node150_current_gap_pp)),
    node150_own_original_peak_daily_change_mae_pp: mae(rows.map(r => r.own_original_peak_bands[0].node150_change_error_pp)),
    frozen_previous_ohlc4_band_old_current_share_mae_pp: mae(rows.map(r => r.frozen_previous_ohlc4_011_bands[0].old_current_gap_pp)),
    frozen_previous_ohlc4_band_new_current_share_mae_pp: mae(rows.map(r => r.frozen_previous_ohlc4_011_bands[0].node150_current_gap_pp)),
    frozen_previous_ohlc4_band_old_daily_change_mae_pp: mae(rows.map(r => r.frozen_previous_ohlc4_011_bands[0].old_change_error_pp)),
    frozen_previous_ohlc4_band_new_daily_change_mae_pp: mae(rows.map(r => r.frozen_previous_ohlc4_011_bands[0].node150_change_error_pp)),
    frozen_earlier_vwap_band_old_current_share_mae_pp: mae(rows.map(r => r.frozen_earlier_vwap_011_bands[0].old_current_gap_pp)),
    frozen_earlier_vwap_band_new_current_share_mae_pp: mae(rows.map(r => r.frozen_earlier_vwap_011_bands[0].node150_current_gap_pp)),
    peak_node_matches: rows.filter(r => JSON.stringify(r.node150_peak_unrounded) === JSON.stringify(r.em2_peak_unrounded)).length,
    observations: rows.length,
  };
}
checkFrozen();
fs.mkdirSync(out, {recursive: true});
fs.writeFileSync(path.join(out, 'comparison.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify({rows: result.rows.length, validation_snapshots: result.validation.snapshots.length,
  statistics: result.statistics, path: path.join(out, 'comparison.json')}));
