import test from 'node:test';
import assert from 'node:assert/strict';
import { dailyCounts, dailyInitialSelection, dailyMatches, parseDailyManifest, parseDailyScreening, parseDailyStatus, parseDailyStock, TREND_DAILY_PARAMETERS_SHA256, TREND_DAILY_RULE } from '../../src/lib/trend-daily.ts';
import { olderPublication } from '../../src/lib/publication-follow.ts';

const slot = '0123456789abcdef', source = 'fedcba9876543210', day = '2020-01-08';
const params = { trend_context_bars: 20, trend_local_bars: 5, trend_close_ols_slope_lt: '0', setup_close_to_context_high_max: '0.95', shrink_recent_bars: 3, shrink_reference_bars: 10, shrink_recent_to_reference_max: '0.70', watch_valid_bars: 5, surge_volume_to_setup_mean_min: '2.00', surge_close_return_pct_min: '4.00', surge_close_gt_open: true, require_signal_prior_trend: false };
const selected = { code: '100001', name: '虚构入选', date: day, source_status: 'KNOWN', status: 'PASS', signal_state: 'SELECTED', close_cny: 10.5, close_return_pct: 5, setup_date: '2020-01-07', shrink_ratio: .5, volume_multiple: 2.5, reasons: [] };
const watching = { ...selected, code: '100002', name: '虚构观察', status: 'FAIL', signal_state: 'WATCHING', close_return_pct: 1, volume_multiple: 1.1 };
const unmatched = { ...selected, code: '100003', name: '虚构未满足', status: 'FAIL', signal_state: 'NOT_MATCHED', setup_date: null, shrink_ratio: null, volume_multiple: null };
const unknown = { ...unmatched, code: '100004', name: '虚构缺失', source_status: 'UNKNOWN', status: 'UNKNOWN', signal_state: 'UNKNOWN', close_cny: null, close_return_pct: null };
const rows = [selected, watching, unmatched, unknown];
const manifestValue = () => ({ schema: 'stock.trend-volume.daily.manifest.v1', dataset_id: slot, source_dataset_id: source, as_of: day, requested_as_of: day, generated_at: '2020-01-08T08:00:00Z', universe_total: 4, counts: { SELECTED: 1, WATCHING: 1, NOT_MATCHED: 1, UNKNOWN: 1 }, source_known_count: 3, rule_id: TREND_DAILY_RULE, parameter_sha256: TREND_DAILY_PARAMETERS_SHA256, source_generation_stale: false, scope: '虚构网站证券清单', rule_parameters: params, limitations: ['仅测试用虚构输入'] });
const screening = (patch = {}) => ({ schema: 'stock.trend-volume.daily.screen.v1', dataset_id: slot, source_dataset_id: source, as_of: day, rule_id: TREND_DAILY_RULE, parameter_sha256: TREND_DAILY_PARAMETERS_SHA256, universe_total: 4, rows, ...patch });
const emptyGate = () => ({ status: 'PASS', metrics: {}, reasons: [], dates: [] });
function stockValue(row = selected) {
  return { schema: 'stock.trend-volume.daily.stock.v1', dataset_id: slot, source_dataset_id: source, as_of: day, code: row.code, name: row.name, source_status: row.source_status, provider: 'SYNTHETIC', volume_unit: 'shares', adjustment: 'unadjusted', row,
    history: [{ date: '2020-01-07', open: 10, close: 10, high: 11, low: 9, volume: 100 }, { date: day, open: 10, close: 10.5, high: 11, low: 9, volume: 250 }],
    screen: { status: row.status, rule_id: TREND_DAILY_RULE, setup_date: row.setup_date, setup_age: 1, baseline_mean: 100, volume_multiple: row.volume_multiple, close_return_pct: row.close_return_pct, bullish: true,
      trend_gate: emptyGate(), shrink_gate: { status: 'PASS', metrics: { recent_to_reference_volume: .5 }, reasons: [], recent_dates: ['2020-01-07'], reference_dates: ['2020-01-06'] }, surge_status: row.status, reasons: [], candidates: [{ date: '2020-01-07', status: 'PASS', trend_status: 'PASS', shrink_status: 'PASS', pullback_pct: 10, context_slope: -1, local_slope: -1, shrink_ratio: .5, reasons: [] }] } };
}

test('current-day categories partition the universe without treating watches as selections or unknowns as failures', () => {
  const manifest = parseDailyManifest(manifestValue()), parsed = parseDailyScreening(screening(), manifest);
  assert.equal(parsed.as_of, day);
  assert.deepEqual(dailyCounts(parsed.rows), { SELECTED: 1, WATCHING: 1, NOT_MATCHED: 1, UNKNOWN: 1 });
  assert.equal(parsed.rows.filter(row => dailyMatches(row, 'SELECTED')).length, 1);
  assert.equal(parsed.rows.filter(row => dailyMatches(row, 'ALL', '缺失')).length, 1);
});
test('default selection stays on the latest matching selection and never falls back to a historical or watch stock', () => {
  assert.equal(dailyInitialSelection(rows), selected.code);
  assert.equal(dailyInitialSelection([watching, unknown]), '');
  assert.equal(dailyInitialSelection(rows, watching.code), selected.code);
  assert.equal(dailyInitialSelection(rows, watching.code, 'WATCHING'), watching.code);
  assert.equal(dailyInitialSelection(rows, selected.code, 'SELECTED', '没有'), '');
  const next = { ...manifestValue(), as_of: '2020-01-09', requested_as_of: '2020-01-09', generated_at: '2020-01-09T08:00:00Z' };
  assert.equal(olderPublication(next, manifestValue()), false);
  assert.equal(olderPublication(manifestValue(), next), true);
});
test('manifest rejects changed frozen parameters, impossible coverage and an incomplete count partition', () => {
  assert.throws(() => parseDailyManifest({ ...manifestValue(), rule_parameters: { ...params, watch_valid_bars: 6 } }));
  assert.throws(() => parseDailyManifest({ ...manifestValue(), source_known_count: 5 }));
  assert.throws(() => parseDailyManifest({ ...manifestValue(), counts: { ...manifestValue().counts, UNKNOWN: 0 } }));
  assert.throws(() => parseDailyManifest({ ...manifestValue(), requested_as_of: '2020-01-07' }));
});
test('a committed list cannot mix dates, source generations, duplicate stocks or false PASS states', () => {
  const manifest = parseDailyManifest(manifestValue());
  assert.throws(() => parseDailyScreening(screening({ as_of: '2020-01-07' }), manifest));
  assert.throws(() => parseDailyScreening(screening({ source_dataset_id: '0000000000000000' }), manifest));
  assert.throws(() => parseDailyScreening(screening({ rows: [selected, selected, unmatched, unknown] }), manifest));
  assert.throws(() => parseDailyScreening(screening({ rows: [{ ...selected, source_status: 'UNKNOWN' }, watching, unmatched, unknown] }), manifest));
  assert.throws(() => parseDailyScreening(screening({ rows: [{ ...selected, volume_multiple: null }, watching, unmatched, unknown] }), manifest));
});
test('current details bind to their list, reject future bars and cannot use an earlier close as the current signal day', () => {
  const manifest = parseDailyManifest(manifestValue()), value = stockValue();
  assert.equal(parseDailyStock(value, manifest, selected).history.at(-1).date, day);
  assert.throws(() => parseDailyStock({ ...value, as_of: '2020-01-07' }, manifest, selected));
  assert.throws(() => parseDailyStock({ ...value, history: [value.history[0]] }, manifest, selected));
  assert.throws(() => parseDailyStock({ ...value, history: [...value.history, { ...value.history[1], date: '2020-01-09' }] }, manifest, selected));
  assert.throws(() => parseDailyStock({ ...value, row: { ...selected, volume_multiple: 3 } }, manifest, selected));
  assert.throws(() => parseDailyStock({ ...value, screen: { ...value.screen, bullish: false } }, manifest, selected));
  assert.throws(() => parseDailyStock({ ...value, screen: { ...value.screen, candidates: [{ ...value.screen.candidates[0], date: day }] } }, manifest, selected));
});
test('status can announce a new target while the last completed publication stays at its actual older date', () => {
  const status = parseDailyStatus({ schema: 'stock.trend-volume.daily.status.v1', state: 'RUNNING', as_of: '2020-01-09', source_dataset_id: source, dataset_id: null, universe_total: 4, scanned_count: 2, updated_at: '2020-01-09T08:00:00Z', message: '虚构进度', last_committed_date: day });
  assert.equal(status.as_of, '2020-01-09');
  assert.equal(parseDailyManifest(manifestValue()).as_of, day);
  assert.throws(() => parseDailyStatus({ ...status, scanned_count: 5 }));
});
