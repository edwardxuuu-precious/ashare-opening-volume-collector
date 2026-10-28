import { parseTrendScreen, trendReason, type TrendCandle, type TrendScreen, type TrendState } from './trend-volume.ts';

export const TREND_DAILY_RULE = 'TREND_LOCAL_OR_CONTEXT_SHRINK70_FIXED_BASELINE_SURGE_V3_CANDIDATE';
export const TREND_DAILY_PARAMETERS_SHA256 = '96ca6b8ae2a85b054037b960e89fabf9722986ab1a63616d26a0ce5dc075bfff';
export const dailySignalStates = ['SELECTED', 'WATCHING', 'NOT_MATCHED', 'UNKNOWN'] as const;
export type DailySignalState = typeof dailySignalStates[number];
export type DailyCategory = DailySignalState | 'ALL';
export type DailyRow = {
  code: string; name: string; date: string; source_status: 'KNOWN' | 'UNKNOWN'; status: TrendState;
  signal_state: DailySignalState; close_cny: number | null; close_return_pct: number | null;
  setup_date: string | null; shrink_ratio: number | null; volume_multiple: number | null; reasons: string[];
};
export type DailyCounts = Record<DailySignalState, number>;
export type DailyManifest = {
  schema: string; dataset_id: string; source_dataset_id: string; as_of: string; requested_as_of: string;
  generated_at: string; universe_total: number; counts: DailyCounts; source_known_count: number;
  rule_id: string; parameter_sha256: string; source_generation_stale: boolean; scope: string;
  rule_parameters: Record<string, string | number | boolean>; limitations: string[];
};
export type DailyScreening = { schema: string; dataset_id: string; source_dataset_id: string; as_of: string;
  rule_id: string; parameter_sha256: string; universe_total: number; rows: DailyRow[] };
export type DailyStock = { schema: string; dataset_id: string; source_dataset_id: string; as_of: string;
  code: string; name: string; source_status: 'KNOWN' | 'UNKNOWN'; provider: string; volume_unit: 'shares';
  adjustment: 'unadjusted'; history: TrendCandle[]; row: DailyRow; screen: TrendScreen };
export type DailyRunStatus = { schema: string; state: 'RUNNING' | 'COMMITTED' | 'FAILED' | 'SOURCE_PENDING';
  as_of: string; source_dataset_id: string | null; dataset_id: string | null; universe_total: number;
  scanned_count: number; updated_at: string; message: string; last_committed_date: string | null };

const parameters = {
  trend_context_bars: 20, trend_local_bars: 5, trend_close_ols_slope_lt: '0', setup_close_to_context_high_max: '0.95',
  shrink_recent_bars: 3, shrink_reference_bars: 10, shrink_recent_to_reference_max: '0.70', watch_valid_bars: 5,
  surge_volume_to_setup_mean_min: '2.00', surge_close_return_pct_min: '4.00', surge_close_gt_open: true, require_signal_prior_trend: false,
};
const invalid = () => Error('当日筛选数据的日期、规则或批次不一致，请刷新后重试。');
function object(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid(); return Object.fromEntries(Object.entries(value)); }
function text(value: unknown): string { if (typeof value !== 'string') throw invalid(); return value; }
function finite(value: unknown): number { if (typeof value !== 'number' || !Number.isFinite(value)) throw invalid(); return value; }
function count(value: unknown): number { const n = finite(value); if (!Number.isSafeInteger(n) || n < 0 || n > 20000) throw invalid(); return n; }
function optionalNumber(value: unknown): number | null { return value === null ? null : finite(value); }
function boolean(value: unknown): boolean { if (typeof value !== 'boolean') throw invalid(); return value; }
function array<T>(value: unknown, parse: (item: unknown) => T): T[] { if (!Array.isArray(value)) throw invalid(); return value.map(parse); }
function choice<T extends string>(value: unknown, options: readonly T[]): T { const found = options.find(option => option === value); if (!found) throw invalid(); return found; }
function matching(value: unknown, pattern: RegExp): string { const s = text(value); if (!pattern.test(s)) throw invalid(); return s; }
function date(value: unknown): string { const s = matching(value, /^\d{4}-\d{2}-\d{2}$/); if (!Number.isFinite(Date.parse(s)) || new Date(s).toISOString().slice(0, 10) !== s) throw invalid(); return s; }
function timestamp(value: unknown): string { const s = text(value); if (!Number.isFinite(Date.parse(s))) throw invalid(); return s; }
const dataset = (value: unknown) => matching(value, /^[a-f0-9]{16}$/);
const optionalDataset = (value: unknown) => value === null ? null : dataset(value);
const optionalDate = (value: unknown) => value === null ? null : date(value);
function rule(value: Record<string, unknown>) { if (value.rule_id !== TREND_DAILY_RULE || value.parameter_sha256 !== TREND_DAILY_PARAMETERS_SHA256) throw invalid(); }

export function dailyCounts(rows: DailyRow[]): DailyCounts {
  const result: DailyCounts = { SELECTED: 0, WATCHING: 0, NOT_MATCHED: 0, UNKNOWN: 0 };
  for (const row of rows) result[row.signal_state]++;
  return result;
}
export function dailyMatches(row: DailyRow, category: DailyCategory, search = ''): boolean {
  return (category === 'ALL' || row.signal_state === category) && `${row.code} ${row.name}`.toLowerCase().includes(search.trim().toLowerCase());
}
export function dailyInitialSelection(rows: DailyRow[], requested = '', category: DailyCategory = 'SELECTED', search = ''): string {
  const visible = rows.filter(row => dailyMatches(row, category, search));
  return visible.find(row => row.code === requested)?.code ?? visible[0]?.code ?? '';
}
export function parseDailyManifest(value: unknown): DailyManifest {
  const v = object(value); rule(v);
  if (v.schema !== 'stock.trend-volume.daily.manifest.v1') throw invalid();
  const rawCounts = object(v.counts), rawParameters = object(v.rule_parameters);
  const counts: DailyCounts = { SELECTED: count(rawCounts.SELECTED), WATCHING: count(rawCounts.WATCHING), NOT_MATCHED: count(rawCounts.NOT_MATCHED), UNKNOWN: count(rawCounts.UNKNOWN) };
  const total = count(v.universe_total), known = count(v.source_known_count), asOf = date(v.as_of), requested = date(v.requested_as_of);
  if (!total || dailySignalStates.reduce((sum, state) => sum + counts[state], 0) !== total || known > total || requested < asOf || Object.keys(rawParameters).length !== Object.keys(parameters).length) throw invalid();
  for (const [key, expected] of Object.entries(parameters)) if (rawParameters[key] !== expected) throw invalid();
  return { schema: text(v.schema), dataset_id: dataset(v.dataset_id), source_dataset_id: dataset(v.source_dataset_id), as_of: asOf, requested_as_of: requested,
    generated_at: timestamp(v.generated_at), universe_total: total, counts, source_known_count: known, rule_id: TREND_DAILY_RULE, parameter_sha256: TREND_DAILY_PARAMETERS_SHA256,
    source_generation_stale: boolean(v.source_generation_stale), scope: text(v.scope), rule_parameters: parameters, limitations: array(v.limitations, text) };
}
function parseRow(value: unknown, asOf: string): DailyRow {
  const v = object(value), status = choice(v.status, ['PASS', 'FAIL', 'UNKNOWN']), signal = choice(v.signal_state, dailySignalStates);
  const source = choice(v.source_status, ['KNOWN', 'UNKNOWN']), setup = optionalDate(v.setup_date);
  const close = optionalNumber(v.close_cny), shrink = optionalNumber(v.shrink_ratio), multiple = optionalNumber(v.volume_multiple), change = optionalNumber(v.close_return_pct);
  if (date(v.date) !== asOf || (setup !== null && setup >= asOf) || (close !== null && close <= 0) || (shrink !== null && shrink < 0) || (multiple !== null && multiple < 0)) throw invalid();
  if ((signal === 'SELECTED') !== (status === 'PASS') || (signal === 'UNKNOWN') !== (status === 'UNKNOWN') || (source === 'UNKNOWN' && status !== 'UNKNOWN')) throw invalid();
  if (signal === 'WATCHING' && (status !== 'FAIL' || setup === null)) throw invalid();
  if (signal === 'NOT_MATCHED' && status !== 'FAIL') throw invalid();
  if (signal === 'SELECTED' && (source !== 'KNOWN' || setup === null || multiple === null || multiple < 2 || change === null || change < 4 || shrink === null || shrink > .7)) throw invalid();
  return { code: matching(v.code, /^\d{6}$/), name: text(v.name), date: asOf, source_status: source, status, signal_state: signal,
    close_cny: close, close_return_pct: change, setup_date: setup, shrink_ratio: shrink, volume_multiple: multiple, reasons: array(v.reasons, text) };
}
export function parseDailyScreening(value: unknown, manifest: DailyManifest): DailyScreening {
  const v = object(value); rule(v);
  if (v.schema !== 'stock.trend-volume.daily.screen.v1' || v.dataset_id !== manifest.dataset_id || v.source_dataset_id !== manifest.source_dataset_id || v.as_of !== manifest.as_of || v.universe_total !== manifest.universe_total) throw invalid();
  const rows = array(v.rows, row => parseRow(row, manifest.as_of));
  if (rows.length !== manifest.universe_total || new Set(rows.map(row => row.code)).size !== rows.length || rows.filter(row => row.source_status === 'KNOWN').length !== manifest.source_known_count) throw invalid();
  const actual = dailyCounts(rows);
  if (dailySignalStates.some(state => actual[state] !== manifest.counts[state])) throw invalid();
  return { schema: text(v.schema), dataset_id: manifest.dataset_id, source_dataset_id: manifest.source_dataset_id, as_of: manifest.as_of,
    rule_id: TREND_DAILY_RULE, parameter_sha256: TREND_DAILY_PARAMETERS_SHA256, universe_total: manifest.universe_total, rows };
}
export function parseDailyStatus(value: unknown): DailyRunStatus {
  const v = object(value);
  if (v.schema !== 'stock.trend-volume.daily.status.v1') throw invalid();
  const total = count(v.universe_total), scanned = count(v.scanned_count);
  if (scanned > total) throw invalid();
  return { schema: text(v.schema), state: choice(v.state, ['RUNNING', 'COMMITTED', 'FAILED', 'SOURCE_PENDING']), as_of: date(v.as_of), source_dataset_id: optionalDataset(v.source_dataset_id),
    dataset_id: optionalDataset(v.dataset_id), universe_total: total, scanned_count: scanned, updated_at: timestamp(v.updated_at), message: text(v.message), last_committed_date: optionalDate(v.last_committed_date) };
}
export function parseDailyStock(value: unknown, manifest: DailyManifest, expected: DailyRow): DailyStock {
  const v = object(value);
  if (v.schema !== 'stock.trend-volume.daily.stock.v1' || v.dataset_id !== manifest.dataset_id || v.source_dataset_id !== manifest.source_dataset_id || v.as_of !== manifest.as_of || v.code !== expected.code || v.volume_unit !== 'shares' || v.adjustment !== 'unadjusted') throw invalid();
  const row = parseRow(v.row, manifest.as_of), history = array(v.history, value => {
    const b = object(value); return { date: date(b.date), open: finite(b.open), high: finite(b.high), low: finite(b.low), close: finite(b.close), volume: finite(b.volume) };
  });
  if (JSON.stringify(row) !== JSON.stringify(expected) || v.source_status !== row.source_status || v.name !== row.name || history.length > 500) throw invalid();
  if (history.some((bar, index) => bar.date > manifest.as_of || bar.low <= 0 || bar.high < Math.max(bar.open, bar.close, bar.low) || bar.low > Math.min(bar.open, bar.close) || bar.volume < 0 || (index > 0 && bar.date <= history[index - 1].date))) throw invalid();
  if (row.source_status === 'KNOWN' && history.at(-1)?.date !== manifest.as_of) throw invalid();
  let screen: TrendScreen;
  try { screen = parseTrendScreen(v.screen); } catch { throw invalid(); }
  const days = [...screen.trend_gate.dates, ...screen.shrink_gate.recent_dates, ...screen.shrink_gate.reference_dates];
  if (screen.rule_id !== TREND_DAILY_RULE || screen.status !== row.status || screen.setup_date !== row.setup_date || screen.volume_multiple !== row.volume_multiple || screen.close_return_pct !== row.close_return_pct
    || days.some(day => day > manifest.as_of) || screen.candidates.some(candidate => candidate.date >= manifest.as_of)) throw invalid();
  if (row.status === 'PASS' && (screen.trend_gate.status !== 'PASS' || screen.shrink_gate.status !== 'PASS' || screen.surge_status !== 'PASS' || screen.bullish !== true)) throw invalid();
  if (row.signal_state === 'WATCHING' && (screen.trend_gate.status !== 'PASS' || screen.shrink_gate.status !== 'PASS' || screen.surge_status !== 'FAIL')) throw invalid();
  return { schema: text(v.schema), dataset_id: manifest.dataset_id, source_dataset_id: manifest.source_dataset_id, as_of: manifest.as_of, code: row.code, name: row.name,
    source_status: row.source_status, provider: text(v.provider), volume_unit: 'shares', adjustment: 'unadjusted', history, row, screen };
}
export const dailySignalText: Record<DailySignalState, string> = { SELECTED: '当日入选', WATCHING: '缩量观察', NOT_MATCHED: '未满足', UNKNOWN: '数据不足' };
const reasonNames: Record<string, string> = {
  SOURCE_FRAME_IDENTITY_MISMATCH: '行情日期或批次与本次筛选不一致', SOURCE_HISTORY_INVALID: '保存的历史行情不完整', SOURCE_BAR_INVALID: 'K线字段无法核实',
  SOURCE_HISTORY_ORDER_OR_DUPLICATE: '历史行情日期顺序或重复记录待核', SOURCE_OHLC_INCONSISTENT: '开高低收价格不一致', INVALID_SOURCE_DATE: '行情日期无法核实',
  SOURCE_FRAME_MISSING: '该股票行情尚未保存', SOURCE_OBJECT_READ_FAILED: '保存的行情暂时无法读取', SOURCE_OBJECT_INVALID: '保存的行情格式或大小无法核实',
  SIGNAL_DAY_MISSING: '缺少筛选日行情', SIGNAL_DAY_INCOMPLETE: '筛选日K线尚未完成', INCOMPLETE_BAR_IN_WINDOW: '计算窗口内有未完成K线',
  INSUFFICIENT_HISTORY: '历史K线不足以核对条件', NEWEST_UNRESOLVED_SETUP: '最近准备日仍待核实，不能改用更早准备',
  BOTH_CONTEXT_AND_LOCAL_SLOPES_NOT_NEGATIVE: '20日和5日收盘斜率均未向下', SURGE_VOLUME_TO_SETUP_BASELINE_BELOW_THRESHOLD: '当日成交量未达固定基准的2倍',
  ZERO_REFERENCE_VOLUME: '此前10日成交量基准为零，比例待核', ZERO_SETUP_BASELINE: '准备期成交量基准为零，放量倍数待核',
  NEGATIVE_VOLUME: '成交量字段异常', NONPOSITIVE_SIGNAL_PRICE: '筛选日价格无法核实', NONPOSITIVE_TREND_PRICE: '趋势窗口价格无法核实',
};
export const dailyReason = (reason: string) => reasonNames[reason.split(':')[0]] ?? trendReason(reason);
