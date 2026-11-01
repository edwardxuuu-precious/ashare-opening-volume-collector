export const concentrationGroups = [3, 5, 10, 15, 20] as const;
export type ConcentrationGroup = (typeof concentrationGroups)[number];
export type ConcentrationState = 'PASS' | 'FAIL' | 'UNKNOWN';
export const concentrationRule = 'CENTRAL_90_STRICT_DECLINE_INDEPENDENT_210_V1';
export const concentrationModel =
  'chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1';

export type ConcentrationSource = {
  dataset_id: string;
  active_slot: string;
  as_of: string;
  universe_total: number;
  universe_coverage: string;
};
export type ConcentrationRow = {
  security_id: string;
  name: string;
  close_cny: string | null;
  values: (number | null)[];
  point_reasons: Record<string, string>;
  short_history_days: number;
  streak_days: number;
  streak_boundary: 'WINDOW_LIMIT' | 'UNAVAILABLE' | 'NO_TRADE' | 'NON_DECREASE';
  groups: Record<ConcentrationGroup, ConcentrationState>;
};
type Counts = Record<ConcentrationState, number>;
export type ConcentrationScan = {
  dataset_id: string;
  as_of: string;
  sessions: string[];
  universe_total: number;
  source_universe_coverage: string;
  groups: Record<ConcentrationGroup, Counts>;
  rows: ConcentrationRow[];
};

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw Error('集中度数据格式不正确。');
  return value as Record<string, unknown>;
}
function string(value: unknown): string {
  if (typeof value !== 'string' || !value || value.length > 300)
    throw Error('集中度文本字段不正确。');
  return value;
}
function integer(value: unknown, max = 15000): number {
  if (
    typeof value !== 'number' ||
    !Number.isSafeInteger(value) ||
    value < 0 ||
    value > max
  )
    throw Error('集中度数量字段不正确。');
  return value;
}
function day(value: unknown): string {
  const text = string(value);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(text) ||
    !Number.isFinite(Date.parse(text)) ||
    new Date(text).toISOString().slice(0, 10) !== text
  )
    throw Error('集中度日期不正确。');
  return text;
}
function state(value: unknown): ConcentrationState {
  if (value !== 'PASS' && value !== 'FAIL' && value !== 'UNKNOWN')
    throw Error('集中度筛选状态不正确。');
  return value;
}
function states(value: unknown): ConcentrationRow['groups'] {
  const row = object(value);
  return {
    3: state(row['3']),
    5: state(row['5']),
    10: state(row['10']),
    15: state(row['15']),
    20: state(row['20']),
  };
}
function counts(value: unknown): Counts {
  const row = object(value);
  return {
    PASS: integer(row.PASS),
    FAIL: integer(row.FAIL),
    UNKNOWN: integer(row.UNKNOWN),
  };
}

export function parseConcentrationSource(value: unknown): ConcentrationSource {
  const row = object(value);
  const dataset = string(row.dataset_id);
  if (
    row.schema !== 'stock.chips.market.manifest.v1' ||
    !/^[a-f0-9]{16}$/.test(dataset) ||
    row.active_slot !== dataset ||
    row.coefficient !== 2 ||
    row.model_version !== concentrationModel
  )
    throw Error('筹码数据批次或衰减参数不匹配。');
  const total = integer(row.universe_total);
  if (!total || row.scanned_count !== total)
    throw Error('本批股票名单尚不完整。');
  return {
    dataset_id: dataset,
    active_slot: dataset,
    as_of: day(row.as_of),
    universe_total: total,
    universe_coverage: string(row.universe_coverage),
  };
}

function parseRow(value: unknown): ConcentrationRow {
  const row = object(value),
    code = string(row.security_id);
  if (
    !/^\d{6}$/.test(code) ||
    !Array.isArray(row.values) ||
    row.values.length !== 21
  )
    throw Error('集中度股票或交易日序列不完整。');
  const values = row.values.map((value): number | null => {
    if (value === null) return null;
    if (
      typeof value !== 'number' ||
      !Number.isFinite(value) ||
      value < 0 ||
      value > 100
    )
      throw Error('集中度数值无效。');
    return value;
  });
  const reasons: Record<string, string> = {};
  for (const [index, reason] of Object.entries(object(row.point_reasons))) {
    if (!/^(?:[0-9]|1[0-9]|20)$/.test(index) || values[Number(index)] !== null)
      throw Error('集中度缺失说明不匹配。');
    reasons[index] = string(reason);
  }
  if (values.some((value, index) => value === null && !reasons[String(index)]))
    throw Error('集中度缺失原因未提供。');
  const boundary = row.streak_boundary;
  if (
    boundary !== 'WINDOW_LIMIT' &&
    boundary !== 'UNAVAILABLE' &&
    boundary !== 'NO_TRADE' &&
    boundary !== 'NON_DECREASE'
  )
    throw Error('连续下降边界无效。');
  const close = row.close_cny;
  if (
    close !== null &&
    (typeof close !== 'string' ||
      !close.trim() ||
      !Number.isFinite(Number(close)) ||
      Number(close) <= 0)
  )
    throw Error('股票收盘价无效。');
  return {
    security_id: code,
    name: string(row.name),
    close_cny: close,
    values,
    point_reasons: reasons,
    short_history_days: integer(row.short_history_days, 21),
    streak_days: integer(row.streak_days, 20),
    streak_boundary: boundary,
    groups: states(row.groups),
  };
}

export function parseConcentrationScan(
  value: unknown,
  source: ConcentrationSource,
): ConcentrationScan {
  const row = object(value);
  if (
    row.schema !== 'stock.chips.concentration.v1' ||
    row.rule_version !== concentrationRule ||
    row.coefficient !== 2 ||
    row.model_version !== concentrationModel ||
    row.window_policy !== 'EACH_DATE_INDEPENDENT_TRAILING_210_OBSERVED_BARS' ||
    row.dataset_id !== source.dataset_id ||
    row.active_slot !== source.active_slot ||
    row.as_of !== source.as_of ||
    row.universe_total !== source.universe_total ||
    row.processed_count !== source.universe_total ||
    row.source_universe_coverage !== source.universe_coverage ||
    !Array.isArray(row.sessions) ||
    row.sessions.length !== 21 ||
    !Array.isArray(row.rows) ||
    row.rows.length !== source.universe_total
  )
    throw Error('连续集中度结果与当前完整数据批次不匹配。');
  const sessions = row.sessions.map(day);
  if (
    sessions.at(-1) !== source.as_of ||
    sessions.some((value, index) => index > 0 && value <= sessions[index - 1])
  )
    throw Error('集中度交易日轴无效。');
  const rows = row.rows.map(parseRow);
  if (new Set(rows.map((value) => value.security_id)).size !== rows.length)
    throw Error('集中度名单含重复股票。');
  const rawCounts = object(row.groups);
  const groups = {
    3: counts(rawCounts['3']),
    5: counts(rawCounts['5']),
    10: counts(rawCounts['10']),
    15: counts(rawCounts['15']),
    20: counts(rawCounts['20']),
  };
  for (const group of concentrationGroups) {
    for (const status of ['PASS', 'FAIL', 'UNKNOWN'] as const) {
      if (
        groups[group][status] !==
        rows.filter((value) => value.groups[group] === status).length
      )
        throw Error('集中度分组数量与名单不一致。');
    }
  }
  return {
    dataset_id: source.dataset_id,
    as_of: source.as_of,
    sessions,
    universe_total: source.universe_total,
    source_universe_coverage: source.universe_coverage,
    groups,
    rows,
  };
}

export const concentrationStateText: Record<ConcentrationState, string> = {
  PASS: '入选',
  FAIL: '未满足',
  UNKNOWN: '无法判断',
};
export function concentrationReason(reason: string): string {
  const labels: Record<string, string> = {
    NO_TRADE: '该交易日确认无成交，连续下降中断',
    PRE_LISTING: '该日尚未上市',
    MODEL_NOT_READY: '筹码模型数据未齐',
    SOURCE_SESSION_MISSING: '缺少该交易日行情',
    ARCHIVED_WARMUP_UNAVAILABLE: '历史预热数据不足',
    HISTORICAL_SOURCE_UNAVAILABLE: '该日历史输入不可用',
    SEALED_SNAPSHOT_MISMATCH: '最新筹码重算需要核对',
    MODEL_CALCULATION_UNAVAILABLE: '该日筹码集中度无法计算',
  };
  return labels[reason] ?? '该日数据需要核实';
}
export function concentrationStreakText(row: ConcentrationRow): string {
  if (row.streak_days === 0 && row.streak_boundary === 'UNAVAILABLE')
    return '待核实';
  const lowerBound =
    row.streak_boundary === 'WINDOW_LIMIT' ||
    row.streak_boundary === 'UNAVAILABLE';
  return `${lowerBound ? '≥' : ''}${row.streak_days}天`;
}
