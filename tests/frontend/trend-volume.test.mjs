import test from 'node:test';
import assert from 'node:assert/strict';
import { categoryMatches, panelRows, parseTrendManifest, parseTrendStock, parseTrendSummary, trendDailyCounts, trendPrefix, trendStatistics } from '../../src/lib/trend-volume.ts';

const dataset = '0123456789abcdef';
const row = (patch = {}) => ({ code: '100001', date: '2020-01-06', period: 'TEST', case_id: null, reference: 'POSITIVE', source_status: 'KNOWN', close_return_pct: 5, v2: 'FAIL', v3: 'PASS', diagnostic_group: null, ...patch });
const rows = [row(), row({ code: '100002', reference: 'NEGATIVE', v3: 'FAIL' }), row({ code: '100003', reference: 'UNCERTAIN', v3: 'FAIL' }), row({ code: '100004', reference: 'UNKNOWN', source_status: 'UNKNOWN', v2: 'UNKNOWN', v3: 'UNKNOWN', close_return_pct: null }), row({ code: '601086', period: 'GUOFANG', reference: 'UNLABELED', v2: 'PASS' })];
const fixture = () => ({ schema: 'stock.trend-volume.summary.v1', dataset_id: dataset, created_at: '2020-01-07T00:00:00Z', periods: [{ id: 'TEST', label: '测试时间段', start: '2020-01-06', end: '2020-01-07' }], stocks: rows.map(r => ({ code: r.code, name: `测试${r.code}`, source_status: r.source_status, provider: 'SYNTHETIC', volume_unit: '测试单位' })), rows, rules: { V2: ['旧规则'], V3: ['冻结规则'], persistence: ['待验证'] }, limitations: ['虚构输入'], sources: [{ label: '虚构来源', sha256: '0'.repeat(64) }], diagnosis: { numeric_review: 8, unresolved: 3, mismatch_supported: 0 }, candidate_status: 'NOT_EVALUATED' });
const screen = (status) => ({ status, rule_id: 'SYNTHETIC_RULE', setup_date: null, setup_age: null, baseline_mean: null, volume_multiple: null, close_return_pct: 5, bullish: true, trend_gate: { status: 'UNKNOWN', metrics: {}, reasons: [], dates: [] }, shrink_gate: { status: 'UNKNOWN', metrics: {}, reasons: [], recent_dates: [], reference_dates: [] }, surge_status: 'UNKNOWN', reasons: [], candidates: [] });

test('statistics keep source unknown, uncertain labels and Guofang outside the error denominators', () => {
  const stats = trendStatistics(rows, 'V3');
  assert.equal(stats.tp, 1);
  assert.equal(stats.fn, 0);
  assert.equal(stats.fp, 0);
  assert.equal(stats.tn, 1);
  assert.equal(stats.positiveCount, 1);
  assert.equal(stats.negativeCount, 1);
  assert.equal(stats.uncertain, 1);
  assert.equal(stats.unknown, 1);
  assert.equal(stats.total, 4);
  assert.equal(stats.selected, 1);
  assert.equal(stats.selectedStocks, 1);
  assert.equal(trendStatistics(rows, 'V2').fn, 1);
  assert.equal(trendStatistics([], 'V3').missRate, null);
});
test('list categories and period scope do not silently replace missing evidence with negatives', () => {
  assert.equal(panelRows(rows, 'TEST').length, 4);
  assert.equal(panelRows(rows, 'OTHER').length, 0);
  assert.equal(rows.filter(r => categoryMatches(r, 'FOCUS', 'V3')).length, 2);
  assert.equal(rows.filter(r => categoryMatches(r, 'MISSED', 'V2')).length, 1);
  assert.equal(rows.filter(r => categoryMatches(r, 'UNKNOWN', 'V3')).length, 1);
  assert.equal(rows.filter(r => categoryMatches(r, 'NEGATIVE', 'V3')).length, 1);
  assert.deepEqual(trendDailyCounts(rows, 'V3'), [{ date: '2020-01-06', known_selected: 1, unknown: 1 }]);
});
test('date replay discards all future candles before taking the display window', () => {
  const history = ['2020-01-03', '2020-01-06', '2020-01-07'].map(date => ({ date, open: 10, high: 11, low: 9, close: 10, volume: 100 }));
  assert.deepEqual(trendPrefix(history, '2020-01-06', 1).map(b => b.date), ['2020-01-06']);
  assert.deepEqual(trendPrefix(history, '2020-01-02'), []);
});
test('manifest and summary reject wrong batch, duplicate rows, impossible date and resolved missing data', () => {
  assert.equal(parseTrendManifest({ schema: 'stock.trend-volume.manifest.v1', dataset_id: dataset, published_at: '2020-01-07', summary_sha256: '0'.repeat(64) }).dataset_id, dataset);
  assert.throws(() => parseTrendManifest({ schema: 'stock.trend-volume.manifest.v1', dataset_id: '../outside', published_at: '', summary_sha256: '' }));
  assert.equal(parseTrendSummary(fixture(), dataset).rows.length, 5);
  assert.throws(() => parseTrendSummary(fixture(), 'fedcba9876543210'));
  assert.throws(() => parseTrendSummary({ ...fixture(), rows: [rows[0], rows[0]] }, dataset));
  assert.throws(() => parseTrendSummary({ ...fixture(), rows: [row({ date: '2020-02-31' })] }, dataset));
  assert.throws(() => parseTrendSummary({ ...fixture(), rows: [row({ source_status: 'UNKNOWN' })] }, dataset));
});
test('detail parsing preserves unknown baseline and rejects cross-stock, future-setup and contradictory decisions', () => {
  const summary = parseTrendSummary(fixture(), dataset);
  const value = { schema: 'stock.trend-volume.stock.v1', dataset_id: dataset, code: '100001', name: '虚构样本', source_status: 'KNOWN', provider: 'SYNTHETIC', volume_unit: '测试单位', adjustment: 'unadjusted', historical_known_at_verified: false, history: [{ date: '2020-01-06', open: 10, high: 11, low: 9, close: 10.5, volume: 100 }], observations: [{ ...rows[0], results: { V2: screen('FAIL'), V3: screen('PASS') } }] };
  assert.equal(parseTrendStock(value, summary, '100001').observations[0].results.V3.volume_multiple, null);
  assert.throws(() => parseTrendStock(value, summary, '100002'));
  assert.throws(() => parseTrendStock({ ...value, observations: [{ ...rows[0], results: { V2: screen('FAIL'), V3: screen('FAIL') } }] }, summary, '100001'));
  assert.throws(() => parseTrendStock({ ...value, observations: [{ ...rows[0], results: { V2: screen('FAIL'), V3: { ...screen('PASS'), setup_date: '2020-01-07' } } }] }, summary, '100001'));
});
