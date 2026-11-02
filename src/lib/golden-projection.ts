import type { DailyCandle } from './chip-market';
import { stockListMatches } from './stock-list.ts';

export const PROJECTION_RULE = 'GOLDEN_V21_HYBRID_DAILY_V1';
export const projectionCategories = ['ABOVE', 'BELOW', 'NEAR_M', 'NEAR_L', 'NEAR_H', 'FAR', 'AMBIGUOUS', 'HISTORY', 'NO_DRAW', 'UNKNOWN'] as const;
export type ProjectionCategory = typeof projectionCategories[number];
export type ProjectionFilter = ProjectionCategory | 'FEATURED' | 'ALL';
export const drawingStates = ['CURRENT', 'NEW_REFERENCE', 'HISTORY', 'NO_DRAW', 'UNKNOWN'] as const;
export type DrawingState = typeof drawingStates[number];
export type ProjectionCounts = Record<ProjectionCategory, number>;
export type ProjectionStateCounts = Record<DrawingState, number>;
export const featuredCategories: ProjectionCategory[] = ['ABOVE', 'BELOW', 'NEAR_M', 'NEAR_L', 'NEAR_H'];
export const categoryText: Record<ProjectionCategory, string> = { ABOVE: '突破高点', BELOW: '跌破低点', NEAR_M: '中轴附近', NEAR_L: '低点附近', NEAR_H: '高点附近', FAR: '远离三线', AMBIGUOUS: '多线同距', HISTORY: '历史结构', NO_DRAW: '无合理画法', UNKNOWN: '数据不足' };
export const stateText: Record<DrawingState, string> = { CURRENT: '已生效画法', NEW_REFERENCE: '当前收盘位置参考', HISTORY: '历史结构', NO_DRAW: '不画线', UNKNOWN: '行情待核' };
export const eventText: Record<string, string> = { UPPER_REJECTED: '盘中突破高点后回落', LOWER_RECOVERED: '盘中跌破低点后收回', TOUCH_H: '盘中触及高点', TOUCH_M: '盘中触及中轴', TOUCH_L: '盘中触及低点' };
type Direction = 'UP' | 'DOWN';
type PriceField = 'open' | 'high' | 'low' | 'close';
type Route = 'ROLE' | 'TURN' | 'BOTH';
export type ProjectionRow = {
  code: string; name: string; as_of: string; source_status: 'KNOWN' | 'UNKNOWN'; drawing_state: DrawingState;
  category: ProjectionCategory; close: string | null; H: string | null; M: string | null; L: string | null;
  direction: Direction | null; basis: 'PREVIOUS_LOCK' | 'CURRENT_REFERENCE' | 'NONE'; near_tags: string[];
  intraday_tags: string[]; new_crossing: 'UP' | 'DOWN' | 'NONE' | 'UNKNOWN';
  distance_percent: { H: string | null; M: string | null; L: string | null }; reasons: string[]; structure_id: string | null;
};
export type ProjectionManifest = {
  schema: string; dataset_id: string; source_dataset_id: string; as_of: string; generated_at: string; universe_total: number;
  counts: ProjectionCounts; state_counts: ProjectionStateCounts; rule_id: string; policy_sha256: string;
  scope: string; limitations: string[]; source_generation_stale: boolean; source_universe_coverage: string;
};
export type ProjectionScreening = { schema: string; dataset_id: string; source_dataset_id: string; as_of: string;
  rule_id: string; policy_sha256: string; universe_total: number; rows: ProjectionRow[] };
export type ProjectionStatus = { schema: string; state: 'RUNNING' | 'COMMITTED' | 'SOURCE_PENDING' | 'FAILED'; as_of: string;
  source_dataset_id: string | null; dataset_id: string | null; universe_total: number; scanned_count: number;
  updated_at: string; message: string; last_committed_date: string | null };
type SourcePoint = { date: string; field: PriceField; price: string };
export type ProjectionStructure = {
  id: string; direction: Direction; levels: { H: string; M: string; L: string }; origin: SourcePoint & { kind: 'H' | 'L' };
  axis: SourcePoint; route: Route; evidence_through: string; recorded_at: string; effective_from: string;
  life_state: 'OPEN' | 'REACHED' | 'INVALID'; guides_end_on: string | null; reasons: string[];
  evidence: { date: string; field: PriceField; value: string; role: string }[];
  role_zone: { lower: string; upper: string; start_date: string; end_date: string } | null;
};
export type RejectedProjection = { direction: Direction; origin_date: string; origin_price: string; axis_date: string;
  axis_price: string; route: Route; reason_code: string; reason: string };
export type ProjectionStock = { schema: string; dataset_id: string; source_dataset_id: string; as_of: string; code: string;
  name: string; rule_id: string; policy_sha256: string; row: ProjectionRow; history: DailyCandle[];
  structure: ProjectionStructure | null; candidate_next_session: ProjectionStructure | null; rejected_candidates: RejectedProjection[] };

const invalid = () => Error('画线数据的日期、价格或批次不一致，请刷新后重试。');
function object(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid(); return Object.fromEntries(Object.entries(value)); }
function text(value: unknown): string { if (typeof value !== 'string') throw invalid(); return value; }
function nonempty(value: unknown): string { const s = text(value); if (!s.length || s.length > 5000) throw invalid(); return s; }
function match(value: unknown, pattern: RegExp): string { const s = text(value); if (!pattern.test(s)) throw invalid(); return s; }
function day(value: unknown): string { const s = match(value, /^\d{4}-\d{2}-\d{2}$/); if (!Number.isFinite(Date.parse(s)) || new Date(s).toISOString().slice(0, 10) !== s) throw invalid(); return s; }
function timestamp(value: unknown): string { const s = text(value); if (!Number.isFinite(Date.parse(s))) throw invalid(); return s; }
function optionalDay(value: unknown): string | null { return value === null ? null : day(value); }
function dataset(value: unknown): string { return match(value, /^[a-f0-9]{16}$/); }
function optionalDataset(value: unknown): string | null { return value === null ? null : dataset(value); }
function boolean(value: unknown): boolean { if (typeof value !== 'boolean') throw invalid(); return value; }
function count(value: unknown): number { if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0 || value > 20000) throw invalid(); return value; }
function array<T>(value: unknown, parse: (item: unknown) => T): T[] { if (!Array.isArray(value) || value.length > 20000) throw invalid(); return value.map(parse); }
function choice<T extends string>(value: unknown, values: readonly T[]): T { const found = values.find(item => value === item); if (!found) throw invalid(); return found; }
function numberText(value: unknown, positive = false): string { const s = text(value); if (!/^-?\d+(?:\.\d+)?$/.test(s) || !Number.isFinite(Number(s)) || (positive && Number(s) <= 0)) throw invalid(); return s; }
function price(value: unknown): string { return numberText(value, true); }
function optionalPrice(value: unknown): string | null { return value === null ? null : price(value); }
function optionalNumberText(value: unknown): string | null { return value === null ? null : numberText(value); }
function rule(v: Record<string, unknown>, expected?: ProjectionManifest) {
  if (v.rule_id !== PROJECTION_RULE || (expected && (v.rule_id !== expected.rule_id || v.policy_sha256 !== expected.policy_sha256))) throw invalid();
  match(v.policy_sha256, /^[a-f0-9]{64}$/);
}
function categoryCounts(value: unknown): ProjectionCounts {
  const v = object(value);
  return { ABOVE: count(v.ABOVE), BELOW: count(v.BELOW), NEAR_M: count(v.NEAR_M), NEAR_L: count(v.NEAR_L), NEAR_H: count(v.NEAR_H), FAR: count(v.FAR), AMBIGUOUS: count(v.AMBIGUOUS), HISTORY: count(v.HISTORY), NO_DRAW: count(v.NO_DRAW), UNKNOWN: count(v.UNKNOWN) };
}
function stateCounts(value: unknown): ProjectionStateCounts {
  const v = object(value); return { CURRENT: count(v.CURRENT), NEW_REFERENCE: count(v.NEW_REFERENCE), HISTORY: count(v.HISTORY), NO_DRAW: count(v.NO_DRAW), UNKNOWN: count(v.UNKNOWN) };
}
export function projectionCounts(rows: ProjectionRow[]): ProjectionCounts {
  const result: ProjectionCounts = { ABOVE: 0, BELOW: 0, NEAR_M: 0, NEAR_L: 0, NEAR_H: 0, FAR: 0, AMBIGUOUS: 0, HISTORY: 0, NO_DRAW: 0, UNKNOWN: 0 };
  for (const row of rows) result[row.category]++;
  return result;
}
export function projectionStrictMatch(row: ProjectionRow, strict: boolean): boolean {
  return !strict || !((row.category === 'NEAR_H' && row.intraday_tags.includes('UPPER_REJECTED')) || (row.category === 'NEAR_L' && row.intraday_tags.includes('LOWER_RECOVERED')));
}
export function projectionMatches(row: ProjectionRow, category: ProjectionFilter = 'FEATURED', search = '', strict = false): boolean {
  return projectionStrictMatch(row, strict) && (category === 'ALL' || category === 'FEATURED' && featuredCategories.includes(row.category) || row.category === category) && stockListMatches(search, row.code, row.name);
}
export function projectionDistance(row: ProjectionRow): number {
  const key = row.category === 'ABOVE' || row.category === 'NEAR_H' ? 'H' : row.category === 'BELOW' || row.category === 'NEAR_L' ? 'L' : row.category === 'NEAR_M' ? 'M' : null;
  if (key) return row.distance_percent[key] === null ? Infinity : Math.abs(Number(row.distance_percent[key]));
  const values = Object.values(row.distance_percent).filter(value => value !== null).map(value => Math.abs(Number(value)));
  return values.length ? Math.min(...values) : Infinity;
}
export function projectionList(rows: ProjectionRow[], category: ProjectionFilter = 'FEATURED', search = '', strict = false): ProjectionRow[] {
  return rows.filter(row => projectionMatches(row, category, search, strict)).sort((a, b) => projectionDistance(a) - projectionDistance(b) || a.code.localeCompare(b.code));
}
export function projectionInitialSelection(rows: ProjectionRow[], requested = '', category: ProjectionFilter = 'FEATURED', search = '', strict = false): string {
  const shown = projectionList(rows, category, search, strict); return shown.find(row => row.code === requested)?.code ?? shown[0]?.code ?? '';
}
export function parseProjectionManifest(value: unknown): ProjectionManifest {
  const v = object(value); rule(v);
  if (v.schema !== 'stock.golden-projection.daily.manifest.v1') throw invalid();
  const total = count(v.universe_total), counts = categoryCounts(v.counts), states = stateCounts(v.state_counts);
  if (!total || Object.values(counts).reduce((sum, n) => sum + n, 0) !== total || Object.values(states).reduce((sum, n) => sum + n, 0) !== total) throw invalid();
  return { schema: text(v.schema), dataset_id: dataset(v.dataset_id), source_dataset_id: dataset(v.source_dataset_id), as_of: day(v.as_of), generated_at: timestamp(v.generated_at), universe_total: total, counts, state_counts: states,
    rule_id: PROJECTION_RULE, policy_sha256: text(v.policy_sha256), scope: nonempty(v.scope), limitations: array(v.limitations, text), source_generation_stale: boolean(v.source_generation_stale), source_universe_coverage: nonempty(v.source_universe_coverage) };
}
function parseRow(value: unknown, asOf: string): ProjectionRow {
  const v = object(value), distance = object(v.distance_percent);
  const category = choice(v.category, projectionCategories), state = choice(v.drawing_state, drawingStates), source = choice(v.source_status, ['KNOWN', 'UNKNOWN']);
  const H = optionalPrice(v.H), M = optionalPrice(v.M), L = optionalPrice(v.L), close = optionalPrice(v.close), basis = choice(v.basis, ['PREVIOUS_LOCK', 'CURRENT_REFERENCE', 'NONE']);
  const direction = v.direction === null ? null : choice(v.direction, ['UP', 'DOWN']);
  const crossing = choice(v.new_crossing, ['UP', 'DOWN', 'NONE', 'UNKNOWN']);
  const structureId = v.structure_id === null ? null : nonempty(v.structure_id);
  if (day(v.as_of) !== asOf || (source === 'UNKNOWN' && state !== 'UNKNOWN') || (state === 'UNKNOWN' && category !== 'UNKNOWN') || (state === 'NO_DRAW' && category !== 'NO_DRAW') || (state === 'HISTORY' && category !== 'HISTORY')) throw invalid();
  if ((category === 'UNKNOWN') !== (state === 'UNKNOWN') || (category === 'NO_DRAW') !== (state === 'NO_DRAW') || (category === 'HISTORY') !== (state === 'HISTORY')) throw invalid();
  if ((state === 'CURRENT' || state === 'NEW_REFERENCE') && (source !== 'KNOWN' || close === null || H === null || M === null || L === null || !(Number(H) > Number(M) && Number(M) > Number(L)) || !direction || !structureId || basis === 'NONE')) throw invalid();
  if (state === 'NEW_REFERENCE' && (basis !== 'CURRENT_REFERENCE' || crossing === 'UP' || crossing === 'DOWN')) throw invalid();
  if ((crossing === 'UP' || crossing === 'DOWN') && basis !== 'PREVIOUS_LOCK') throw invalid();
  return { code: match(v.code, /^\d{6}$/), name: nonempty(v.name), as_of: asOf, source_status: source, drawing_state: state, category, close, H, M, L, direction, basis,
    near_tags: array(v.near_tags, item => choice(item, ['NEAR_H', 'NEAR_M', 'NEAR_L'])), intraday_tags: array(v.intraday_tags, item => choice(item, ['UPPER_REJECTED', 'LOWER_RECOVERED', 'TOUCH_H', 'TOUCH_M', 'TOUCH_L'])),
    new_crossing: crossing, distance_percent: { H: optionalNumberText(distance.H), M: optionalNumberText(distance.M), L: optionalNumberText(distance.L) }, reasons: array(v.reasons, text), structure_id: structureId };
}
export function parseProjectionScreening(value: unknown, manifest: ProjectionManifest): ProjectionScreening {
  const v = object(value); rule(v, manifest);
  if (v.schema !== 'stock.golden-projection.daily.screen.v1' || v.dataset_id !== manifest.dataset_id || v.source_dataset_id !== manifest.source_dataset_id || v.as_of !== manifest.as_of || v.universe_total !== manifest.universe_total) throw invalid();
  const rows = array(v.rows, row => parseRow(row, manifest.as_of));
  if (rows.length !== manifest.universe_total || new Set(rows.map(row => row.code)).size !== rows.length) throw invalid();
  const counts = projectionCounts(rows);
  if (projectionCategories.some(key => counts[key] !== manifest.counts[key]) || drawingStates.some(state => rows.filter(row => row.drawing_state === state).length !== manifest.state_counts[state])) throw invalid();
  return { schema: text(v.schema), dataset_id: manifest.dataset_id, source_dataset_id: manifest.source_dataset_id, as_of: manifest.as_of, rule_id: manifest.rule_id, policy_sha256: manifest.policy_sha256, universe_total: manifest.universe_total, rows };
}
export function parseProjectionStatus(value: unknown): ProjectionStatus {
  const v = object(value); if (v.schema !== 'stock.golden-projection.daily.status.v1') throw invalid();
  const total = count(v.universe_total), scanned = count(v.scanned_count); if (scanned > total) throw invalid();
  return { schema: text(v.schema), state: choice(v.state, ['RUNNING', 'COMMITTED', 'SOURCE_PENDING', 'FAILED']), as_of: day(v.as_of), source_dataset_id: optionalDataset(v.source_dataset_id), dataset_id: optionalDataset(v.dataset_id), universe_total: total,
    scanned_count: scanned, updated_at: timestamp(v.updated_at), message: text(v.message), last_committed_date: optionalDay(v.last_committed_date) };
}
function sourcePoint(value: unknown, asOf: string): SourcePoint {
  const v = object(value), date = day(v.date); if (date > asOf) throw invalid();
  return { date, field: choice(v.field, ['open', 'high', 'low', 'close']), price: price(v.price) };
}
function parseStructure(value: unknown, asOf: string): ProjectionStructure | null {
  if (value === null) return null;
  const v = object(value), rawLevels = object(v.levels), rawOrigin = object(v.origin), origin = sourcePoint(v.origin, asOf), axis = sourcePoint(v.axis, asOf);
  const levels = { H: price(rawLevels.H), M: price(rawLevels.M), L: price(rawLevels.L) }, through = day(v.evidence_through);
  if (!(Number(levels.H) > Number(levels.M) && Number(levels.M) > Number(levels.L)) || through > asOf || through < origin.date || through < axis.date || Number(levels.M) !== Number(axis.price)) throw invalid();
  const kind = choice(rawOrigin.kind, ['H', 'L']), direction = choice(v.direction, ['UP', 'DOWN']);
  if ((direction === 'DOWN' && (kind !== 'H' || Number(origin.price) !== Number(levels.H))) || (direction === 'UP' && (kind !== 'L' || Number(origin.price) !== Number(levels.L)))) throw invalid();
  const end = optionalDay(v.guides_end_on); if (end && (end > asOf || end < through)) throw invalid();
  const evidence = array(v.evidence, item => { const e = object(item), date = day(e.date); if (date > through) throw invalid(); return { date, field: choice(e.field, ['open', 'high', 'low', 'close']), value: price(e.value), role: text(e.role) }; });
  let zone: ProjectionStructure['role_zone'] = null;
  if (v.role_zone !== undefined && v.role_zone !== null) { const z = object(v.role_zone); zone = { lower: price(z.lower), upper: price(z.upper), start_date: day(z.start_date), end_date: day(z.end_date) }; if (Number(zone.lower) > Number(zone.upper) || zone.start_date > zone.end_date || zone.end_date > through) throw invalid(); }
  return { id: nonempty(v.id), direction, levels, origin: { ...origin, kind }, axis, route: choice(v.route, ['ROLE', 'TURN', 'BOTH']), evidence_through: through, recorded_at: timestamp(v.recorded_at), effective_from: day(v.effective_from), life_state: choice(v.life_state, ['OPEN', 'REACHED', 'INVALID']), guides_end_on: end, reasons: array(v.reasons, text), evidence, role_zone: zone };
}
function candleNumber(value: unknown, positive: boolean): string {
  const s = typeof value === 'number' && Number.isFinite(value) ? String(value) : text(value);
  if (!Number.isFinite(Number(s)) || !s.length || (positive ? Number(s) <= 0 : Number(s) < 0)) throw invalid(); return s;
}
function parseCandle(value: unknown): DailyCandle {
  const v = object(value), open = candleNumber(v.open, true), high = candleNumber(v.high, true), low = candleNumber(v.low, true), close = candleNumber(v.close, true);
  if (Number(low) > Math.min(Number(open), Number(close)) || Number(high) < Math.max(Number(open), Number(close))) throw invalid();
  return { date: day(v.date), open, high, low, close, volume_shares: candleNumber(v.volume_shares, false), amount_cny: v.amount_cny === undefined || v.amount_cny === null ? '' : candleNumber(v.amount_cny, false), turnover_percent: v.turnover_percent === undefined || v.turnover_percent === null ? '' : candleNumber(v.turnover_percent, false) };
}
export function parseProjectionStock(value: unknown, manifest: ProjectionManifest, expected: ProjectionRow): ProjectionStock {
  const v = object(value); rule(v, manifest);
  if (v.schema !== 'stock.golden-projection.daily.stock.v1' || v.dataset_id !== manifest.dataset_id || v.source_dataset_id !== manifest.source_dataset_id || v.as_of !== manifest.as_of || v.code !== expected.code || v.name !== expected.name) throw invalid();
  const row = parseRow(v.row, manifest.as_of); if (JSON.stringify(row) !== JSON.stringify(expected)) throw invalid();
  const history = array(v.history, parseCandle); let last = '';
  if (history.length > 500) throw invalid();
  for (const bar of history) { if (bar.date <= last || bar.date > manifest.as_of) throw invalid(); last = bar.date; }
  if (row.source_status === 'KNOWN' && last !== manifest.as_of) throw invalid();
  if (row.source_status === 'KNOWN' && row.close !== null && Number(history[history.length - 1]?.close) !== Number(row.close)) throw invalid();
  const structure = parseStructure(v.structure, manifest.as_of), next = parseStructure(v.candidate_next_session, manifest.as_of);
  if ((row.drawing_state === 'CURRENT' || row.drawing_state === 'NEW_REFERENCE') && (!structure || structure.id !== row.structure_id || structure.levels.H !== row.H || structure.levels.M !== row.M || structure.levels.L !== row.L)) throw invalid();
  for (const selected of [structure, next]) if (selected) {
    for (const point of [selected.origin, selected.axis]) { const bar = history.find(item => item.date === point.date); if (bar && Number(bar[point.field]) !== Number(point.price)) throw invalid(); }
    for (const point of selected.evidence) { const bar = history.find(item => item.date === point.date); if (bar && Number(bar[point.field]) !== Number(point.value)) throw invalid(); }
  }
  const rejections = array(v.rejected_candidates, item => { const r = object(item), originDate = day(r.origin_date), axisDate = day(r.axis_date); if (originDate > manifest.as_of || axisDate > manifest.as_of) throw invalid(); return { direction: choice(r.direction, ['UP', 'DOWN']), origin_date: originDate, origin_price: price(r.origin_price), axis_date: axisDate, axis_price: price(r.axis_price), route: choice(r.route, ['ROLE', 'TURN', 'BOTH']), reason_code: nonempty(r.reason_code), reason: nonempty(r.reason) }; });
  return { schema: text(v.schema), dataset_id: manifest.dataset_id, source_dataset_id: manifest.source_dataset_id, as_of: manifest.as_of, code: row.code, name: row.name, rule_id: manifest.rule_id, policy_sha256: manifest.policy_sha256, row, history, structure, candidate_next_session: next, rejected_candidates: rejections };
}
