export type TrendVersion = 'V2' | 'V3';
export type TrendState = 'PASS' | 'FAIL' | 'UNKNOWN';
export type TrendReference = 'POSITIVE' | 'NEGATIVE' | 'UNCERTAIN' | 'UNKNOWN' | 'UNLABELED';
export type TrendCategory = 'FOCUS' | 'SELECTED' | 'MISSED' | 'NEGATIVE' | 'UNKNOWN' | 'ALL';
export type TrendIndexRow = {
  code: string; date: string; period: string; case_id: string | null;
  reference: TrendReference; source_status: 'KNOWN' | 'UNKNOWN'; close_return_pct: number | null;
  v2: TrendState; v3: TrendState; diagnostic_group: string | null;
};
type Metrics = Record<string, number | boolean | null>;
export type TrendScreen = {
  status: TrendState; rule_id: string; setup_date: string | null; setup_age: number | null;
  baseline_mean: number | null; volume_multiple: number | null; close_return_pct: number | null;
  bullish: boolean | null;
  trend_gate: { status: TrendState; metrics: Metrics; reasons: string[]; dates: string[] };
  shrink_gate: { status: TrendState; metrics: Metrics; reasons: string[]; recent_dates: string[]; reference_dates: string[] };
  surge_status: TrendState; reasons: string[];
  candidates: { date: string; status: TrendState; trend_status: TrendState; shrink_status: TrendState;
    pullback_pct: number | null; context_slope: number | null; local_slope: number | null;
    shrink_ratio: number | null; reasons: string[] }[];
};
export type TrendObservation = TrendIndexRow & { results: Record<TrendVersion, TrendScreen> };
export type TrendCandle = { date: string; open: number; high: number; low: number; close: number; volume: number };
export type TrendStock = {
  schema: string; dataset_id: string; code: string; name: string; source_status: 'KNOWN' | 'UNKNOWN';
  provider: string; volume_unit: string; adjustment: 'unadjusted'; historical_known_at_verified: false;
  history: TrendCandle[]; observations: TrendObservation[];
};
export type TrendSummary = {
  schema: string; dataset_id: string; created_at: string;
  periods: { id: string; label: string; start: string; end: string }[];
  stocks: { code: string; name: string; source_status: 'KNOWN' | 'UNKNOWN'; provider: string; volume_unit: string }[];
  rows: TrendIndexRow[]; rules: Record<TrendVersion | 'persistence', string[]>;
  limitations: string[]; sources: { label: string; sha256: string }[];
  diagnosis: { numeric_review: number; unresolved: number; mismatch_supported: number };
  candidate_status: 'NOT_EVALUATED';
};
const states: TrendState[] = ['PASS', 'FAIL', 'UNKNOWN'];
const references: TrendReference[] = ['POSITIVE', 'NEGATIVE', 'UNCERTAIN', 'UNKNOWN', 'UNLABELED'];
const sourceStates: ('KNOWN' | 'UNKNOWN')[] = ['KNOWN', 'UNKNOWN'];
const invalid = () => new Error('研究快照格式或批次不一致，请刷新后重试。');
function object(v: unknown): Record<string, unknown> {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw invalid();
  return Object.fromEntries(Object.entries(v));
}
function string(v: unknown): string { if (typeof v !== 'string') throw invalid(); return v; }
function number(v: unknown): number { if (typeof v !== 'number' || !Number.isFinite(v)) throw invalid(); return v; }
function nullableNumber(v: unknown): number | null { return v === null ? null : number(v); }
function nullableString(v: unknown): string | null { return v === null ? null : string(v); }
function array<T>(v: unknown, parse: (x: unknown) => T): T[] { if (!Array.isArray(v)) throw invalid(); return v.map(parse); }
function choice<T extends string>(v: unknown, choices: T[]): T { const found = choices.find(x => x === v); if (!found) throw invalid(); return found; }
function matching(v: unknown, re: RegExp): string { const s = string(v); if (!re.test(s)) throw invalid(); return s; }
function date(v: unknown): string {
  const s = matching(v, /^\d{4}-\d{2}-\d{2}$/);
  if (!Number.isFinite(Date.parse(s)) || new Date(s).toISOString().slice(0, 10) !== s) throw invalid();
  return s;
}
function metricValues(v: unknown): Metrics {
  return Object.fromEntries(Object.entries(object(v)).map(([key, value]) => [key, value === null || typeof value === 'boolean' ? value : number(value)]));
}
const id = (v: unknown) => matching(v, /^[a-f0-9]{16}$/);
const code = (v: unknown) => matching(v, /^\d{6}$/);
const hash = (v: unknown) => matching(v, /^[a-f0-9]{64}$/);
export function parseTrendManifest(value: unknown) {
  const v = object(value);
  if (v.schema !== 'stock.trend-volume.manifest.v1') throw invalid();
  return { dataset_id: id(v.dataset_id), published_at: string(v.published_at), summary_sha256: hash(v.summary_sha256) };
}
function parseIndex(value: unknown): TrendIndexRow {
  const v = object(value);
  return { code: code(v.code), date: date(v.date), period: string(v.period), case_id: nullableString(v.case_id),
    reference: choice(v.reference, references), source_status: choice(v.source_status, sourceStates),
    close_return_pct: nullableNumber(v.close_return_pct), v2: choice(v.v2, states), v3: choice(v.v3, states),
    diagnostic_group: nullableString(v.diagnostic_group) };
}
export function parseTrendSummary(value: unknown, datasetId: string): TrendSummary {
  const v = object(value), rules = object(v.rules), diagnosis = object(v.diagnosis);
  if (v.schema !== 'stock.trend-volume.summary.v1' || v.dataset_id !== datasetId || v.candidate_status !== 'NOT_EVALUATED') throw invalid();
  const periods = array(v.periods, value => { const p = object(value); return { id: string(p.id), label: string(p.label), start: date(p.start), end: date(p.end) }; });
  const stocks = array(v.stocks, value => { const s = object(value); return { code: code(s.code), name: string(s.name), source_status: choice(s.source_status, sourceStates), provider: string(s.provider), volume_unit: string(s.volume_unit) }; });
  const rows = array(v.rows, parseIndex), codes = new Set(stocks.map(s => s.code));
  if (codes.size !== stocks.length || new Set(rows.map(rowKey)).size !== rows.length || new Set(periods.map(p => p.id)).size !== periods.length) throw invalid();
  for (const row of rows) {
    const period = periods.find(p => p.id === row.period);
    if (!codes.has(row.code) || (row.period !== 'GUOFANG' && (!period || row.date < period.start || row.date > period.end))) throw invalid();
    if (row.source_status === 'UNKNOWN' && (row.v2 !== 'UNKNOWN' || row.v3 !== 'UNKNOWN')) throw invalid();
  }
  return { schema: string(v.schema), dataset_id: datasetId, created_at: string(v.created_at), periods, stocks, rows,
    rules: { V2: array(rules.V2, string), V3: array(rules.V3, string), persistence: array(rules.persistence, string) },
    limitations: array(v.limitations, string), sources: array(v.sources, value => { const s = object(value); return { label: string(s.label), sha256: hash(s.sha256) }; }),
    diagnosis: { numeric_review: number(diagnosis.numeric_review), unresolved: number(diagnosis.unresolved), mismatch_supported: number(diagnosis.mismatch_supported) }, candidate_status: 'NOT_EVALUATED' };
}
function parseScreen(value: unknown): TrendScreen {
  const v = object(value), trend = object(v.trend_gate), shrink = object(v.shrink_gate);
  if (v.bullish !== null && typeof v.bullish !== 'boolean') throw invalid();
  return { status: choice(v.status, states), rule_id: string(v.rule_id), setup_date: v.setup_date === null ? null : date(v.setup_date),
    setup_age: nullableNumber(v.setup_age), baseline_mean: nullableNumber(v.baseline_mean), volume_multiple: nullableNumber(v.volume_multiple),
    close_return_pct: nullableNumber(v.close_return_pct), bullish: v.bullish,
    trend_gate: { status: choice(trend.status, states), metrics: metricValues(trend.metrics), reasons: array(trend.reasons, string), dates: array(trend.dates, date) },
    shrink_gate: { status: choice(shrink.status, states), metrics: metricValues(shrink.metrics), reasons: array(shrink.reasons, string), recent_dates: array(shrink.recent_dates, date), reference_dates: array(shrink.reference_dates, date) },
    surge_status: choice(v.surge_status, states), reasons: array(v.reasons, string),
    candidates: array(v.candidates, value => { const c = object(value); return { date: date(c.date), status: choice(c.status, states), trend_status: choice(c.trend_status, states), shrink_status: choice(c.shrink_status, states),
      pullback_pct: nullableNumber(c.pullback_pct), context_slope: nullableNumber(c.context_slope), local_slope: nullableNumber(c.local_slope), shrink_ratio: nullableNumber(c.shrink_ratio), reasons: array(c.reasons, string) }; }) };
}
export function parseTrendStock(value: unknown, summary: TrendSummary, requestedCode: string): TrendStock {
  const v = object(value);
  if (v.schema !== 'stock.trend-volume.stock.v1' || v.dataset_id !== summary.dataset_id || v.code !== requestedCode || v.adjustment !== 'unadjusted' || v.historical_known_at_verified !== false) throw invalid();
  const history = array(v.history, value => { const b = object(value); return { date: date(b.date), open: number(b.open), high: number(b.high), low: number(b.low), close: number(b.close), volume: number(b.volume) }; });
  const observations = array(v.observations, value => {
    const row = parseIndex(value), r = object(object(value).results), V2 = parseScreen(r.V2), V3 = parseScreen(r.V3);
    const original = summary.rows.find(x => rowKey(x) === rowKey(row));
    if (!original || row.code !== requestedCode || original.v2 !== V2.status || original.v3 !== V3.status || row.v2 !== V2.status || row.v3 !== V3.status) throw invalid();
    if ([V2, V3].some(s => (s.setup_date !== null && s.setup_date >= row.date) || s.candidates.some(c => c.date >= row.date))) throw invalid();
    return { ...row, results: { V2, V3 } };
  });
  if (history.some((b, i) => b.volume < 0 || b.low <= 0 || b.high < Math.max(b.open, b.close, b.low) || b.low > Math.min(b.open, b.close) || (i > 0 && b.date <= history[i - 1].date))) throw invalid();
  return { schema: string(v.schema), dataset_id: summary.dataset_id, code: requestedCode, name: string(v.name), source_status: choice(v.source_status, sourceStates), provider: string(v.provider), volume_unit: string(v.volume_unit), adjustment: 'unadjusted', historical_known_at_verified: false, history, observations };
}
export const rowKey = (row: Pick<TrendIndexRow, 'code' | 'date'>) => `${row.code}:${row.date}`;
export const prediction = (row: TrendIndexRow, version: TrendVersion) => version === 'V2' ? row.v2 : row.v3;
export const panelRows = (rows: TrendIndexRow[], period: string) => rows.filter(r => r.period !== 'GUOFANG' && (period === 'ALL' || r.period === period));
export function categoryMatches(row: TrendIndexRow, category: TrendCategory, version: TrendVersion): boolean {
  switch (category) {
    case 'FOCUS': return row.reference === 'POSITIVE' || row.reference === 'UNCERTAIN';
    case 'SELECTED': return prediction(row, version) === 'PASS';
    case 'MISSED': return row.reference === 'POSITIVE' && prediction(row, version) === 'FAIL';
    case 'NEGATIVE': return row.reference === 'NEGATIVE';
    case 'UNKNOWN': return row.source_status === 'UNKNOWN' || row.reference === 'UNKNOWN' || prediction(row, version) === 'UNKNOWN';
    case 'ALL': return true;
  }
}
export function trendStatistics(rows: TrendIndexRow[], version: TrendVersion) {
  const panel = rows.filter(r => r.period !== 'GUOFANG');
  const positives = panel.filter(r => r.source_status === 'KNOWN' && r.reference === 'POSITIVE' && prediction(r, version) !== 'UNKNOWN');
  const negatives = panel.filter(r => r.source_status === 'KNOWN' && r.reference === 'NEGATIVE' && prediction(r, version) !== 'UNKNOWN');
  const selected = panel.filter(r => prediction(r, version) === 'PASS');
  const tp = positives.filter(r => prediction(r, version) === 'PASS').length, fn = positives.length - tp;
  const fp = negatives.filter(r => prediction(r, version) === 'PASS').length;
  return { tp, fn, fp, tn: negatives.length - fp, positiveCount: positives.length, negativeCount: negatives.length,
    recall: positives.length ? tp / positives.length : null, missRate: positives.length ? fn / positives.length : null,
    selected: selected.length, selectedStocks: new Set(selected.map(r => r.code)).size,
    selectedDays: new Set(selected.map(r => r.date)).size, total: panel.length,
    known: panel.filter(r => r.source_status === 'KNOWN').length, unknown: panel.filter(r => r.source_status === 'UNKNOWN').length,
    uncertain: panel.filter(r => r.reference === 'UNCERTAIN').length,
    stocks: new Set(panel.map(r => r.code)).size, knownStocks: new Set(panel.filter(r => r.source_status === 'KNOWN').map(r => r.code)).size };
}
export function trendDailyCounts(rows: TrendIndexRow[], version: TrendVersion) {
  const dates = [...new Set(rows.filter(r => r.period !== 'GUOFANG').map(r => r.date))].sort();
  return dates.map(date => {
    const daily = rows.filter(r => r.period !== 'GUOFANG' && r.date === date);
    return { date, known_selected: daily.filter(r => prediction(r, version) === 'PASS').length,
      unknown: daily.filter(r => r.source_status === 'UNKNOWN' || prediction(r, version) === 'UNKNOWN').length };
  });
}
export const trendPrefix = (history: TrendCandle[], asOf: string, length = 60) => history.filter(b => b.date <= asOf).slice(-length);
export const trendStateText: Record<TrendState, string> = { PASS: '满足', FAIL: '未满足', UNKNOWN: '待核实' };
export const trendReferenceText: Record<TrendReference, string> = { POSITIVE: '代理正例', NEGATIVE: '自动反例', UNCERTAIN: '形态待定', UNKNOWN: '参考未知', UNLABELED: '单例核对' };
const reasonLabels: Record<string, string> = {
  NO_VALID_SETUP_IN_WATCH_WINDOW: '前5日未找到有效缩量准备', CLOSE_ABOVE_RETRACEMENT_THRESHOLD: '回落幅度未达5%',
  SHRINK_VOLUME_RATIO_ABOVE_THRESHOLD: '3日均量/此前10日均量超过本版本冻结阈值', TREND_SLOPE_NOT_NEGATIVE: '趋势斜率未向下',
  SURGE_CLOSE_RETURN_BELOW_THRESHOLD: '当日涨幅未达4%', SURGE_NOT_BULLISH_CANDLE: '收盘未高于开盘',
  SETUP_BASELINE_UNAVAILABLE: '无有效准备基准，放量倍数待核', SURGE_VOLUME_RATIO_BELOW_THRESHOLD: '当日量/冻结基准未达2倍',
  CONTEXT_AND_LOCAL_SLOPES_NOT_NEGATIVE: '20日和5日斜率均未向下', INCOMPLETE_BAR: 'K线未完成',
};
export const trendReason = (reason: string) => reasonLabels[reason] ?? reason.replaceAll('_', ' ');

export { parseScreen as parseTrendScreen };
