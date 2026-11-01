export const concentrationGroupRegistry = [
  {
    key: 20,
    comparisons: 20,
    label: '连续20天',
    title: '至少连续不增20个交易日',
  },
  {
    key: 'LONG',
    comparisons: 21,
    label: '大于20天',
    title: '连续不增大于20个交易日',
  },
] as const;
export const concentrationGroups = concentrationGroupRegistry.map(
  (group) => group.key,
);
export type ConcentrationGroup =
  (typeof concentrationGroupRegistry)[number]['key'];
export type ConcentrationState = 'PASS' | 'FAIL' | 'UNKNOWN';
export const concentrationRule = 'CENTRAL_90_NONINCREASING_INDEPENDENT_210_TWO_V1';
export const concentrationModel =
  'chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1';
export type ConcentrationSource = {
  dataset_id: string;
  active_slot: string;
  as_of: string;
  universe_total: number;
  universe_coverage: string;
};
type Boundary =
  | {
      kind: 'INCREASE';
      previous_session: string;
      previous_value: number;
      anchor_session: string;
      anchor_value: number;
    }
  | {
      kind: 'UNAVAILABLE' | 'NO_TRADE' | 'WARMUP' | 'CALENDAR_LIMIT';
      session: string;
      reason: string;
    };
export type ConcentrationRow = {
  security_id: string;
  name: string;
  close_cny: string | null;
  values: (number | null)[];
  point_reasons: Record<string, string>;
  short_history_days: number;
  streak_days: number;
  streak_boundary: Boundary;
  streak_start_session: string | null;
  streak_values?: number[];
  groups: Record<ConcentrationGroup, ConcentrationState>;
};
type Counts = Record<ConcentrationState, number>;
export type ConcentrationScan = {
  dataset_id: string;
  as_of: string;
  sessions: string[];
  streak_sessions: string[];
  universe_total: number;
  source_universe_coverage: string;
  groups: Record<ConcentrationGroup, Counts>;
  rows: ConcentrationRow[];
};

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function object(value: unknown): Record<string, unknown> {
  if (!isObject(value)) throw Error('集中度数据格式不正确。');
  return value;
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
function concentration(value: unknown): number {
  if (
    typeof value !== 'number' ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > 100
  )
    throw Error('集中度数值无效。');
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
function axis(value: unknown): string[] {
  if (!Array.isArray(value)) throw Error('集中度交易日轴无效。');
  const days = value.map(day);
  if (days.some((value, index) => index > 0 && value <= days[index - 1]))
    throw Error('集中度交易日轴无效。');
  return days;
}
function state(value: unknown): ConcentrationState {
  if (value !== 'PASS' && value !== 'FAIL' && value !== 'UNKNOWN')
    throw Error('集中度筛选状态不正确。');
  return value;
}
function groupRecord<T>(
  value: unknown,
  parse: (value: unknown) => T,
): Record<ConcentrationGroup, T> {
  const row = object(value);
  if (Object.keys(row).length !== 2 || !('20' in row) || !('LONG' in row))
    throw Error('集中度分组必须恰好为20天和大于20天。');
  return {
    20: parse(row['20']),
    LONG: parse(row.LONG),
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
function boundary(value: unknown): Boundary {
  const row = object(value);
  if (row.kind === 'INCREASE') {
    const result: Boundary = {
      kind: 'INCREASE',
      previous_session: day(row.previous_session),
      previous_value: concentration(row.previous_value),
      anchor_session: day(row.anchor_session),
      anchor_value: concentration(row.anchor_value),
    };
    if (result.previous_value >= result.anchor_value)
      throw Error('集中度增加边界无效。');
    return result;
  }
  if (
    row.kind !== 'UNAVAILABLE' &&
    row.kind !== 'NO_TRADE' &&
    row.kind !== 'WARMUP' &&
    row.kind !== 'CALENDAR_LIMIT'
  )
    throw Error('连续不增边界无效。');
  return {
    kind: row.kind,
    session: day(row.session),
    reason: string(row.reason),
  };
}
function classify(
  values: (number | null)[],
  reasons: Record<string, string>,
  count: number,
): ConcentrationState {
  let unknown = values.length <= count;
  for (
    let index = Math.max(1, values.length - count);
    index < values.length;
    index++
  ) {
    const before = values[index - 1],
      after = values[index];
    if (
      reasons[String(index)] === 'NO_TRADE' ||
      (before !== null && after !== null && after > before)
    )
      return 'FAIL';
    if (before === null || after === null) unknown = true;
  }
  return unknown ? 'UNKNOWN' : 'PASS';
}

export function parseConcentrationSource(value: unknown): ConcentrationSource {
  const row = object(value),
    dataset = string(row.dataset_id);
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

function parseRow(value: unknown, streakSessions: string[]): ConcentrationRow {
  const row = object(value),
    code = string(row.security_id);
  if (
    !/^\d{6}$/.test(code) ||
    !Array.isArray(row.values) ||
    row.values.length !== 21
  )
    throw Error('集中度股票或交易日序列不完整。');
  const values = row.values.map((value) =>
    value === null ? null : concentration(value),
  );
  const reasons: Record<string, string> = {};
  for (const [index, reason] of Object.entries(object(row.point_reasons))) {
    if (!/^(?:[0-9]|1[0-9]|20)$/.test(index) || values[Number(index)] !== null)
      throw Error('集中度缺失说明不匹配。');
    reasons[index] = string(reason);
  }
  if (values.some((value, index) => value === null && !reasons[String(index)]))
    throw Error('集中度缺失原因未提供。');
  const close = row.close_cny;
  if (
    close !== null &&
    (typeof close !== 'string' ||
      !close.trim() ||
      !Number.isFinite(Number(close)) ||
      Number(close) <= 0)
  )
    throw Error('股票收盘价无效。');
  const groups = groupRecord(row.groups, state),
    streak = integer(row.streak_days, streakSessions.length - 1);
  const end = boundary(row.streak_boundary),
    start =
      row.streak_start_session === null ? null : day(row.streak_start_session);
  if (start !== (values[20] === null ? null : streakSessions.at(-streak - 1)))
    throw Error('连续不增起点无效。');
  const result: ConcentrationRow = {
    security_id: code,
    name: string(row.name),
    close_cny: close,
    values,
    point_reasons: reasons,
    short_history_days: integer(row.short_history_days, 21),
    streak_days: streak,
    streak_boundary: end,
    streak_start_session: start,
    groups,
  };
  let evidence = values,
    evidenceReasons = reasons;
  if (groups[20] === 'PASS') {
    if (
      !Array.isArray(row.streak_values) ||
      row.streak_values.length !== streak + 1 ||
      streak < 20
    )
      throw Error('完整连续不增曲线缺失。');
    const curve = row.streak_values.map(concentration);
    if (
      curve.some((v, i) => i > 0 && v > curve[i - 1]) ||
      curve.slice(-21).some((v, i) => v !== values[i])
    )
      throw Error('连续不增曲线与最近读数不匹配。');
    if (end.kind === 'INCREASE' && end.anchor_value !== curve[0])
      throw Error('集中度增加边界与起点不匹配。');
    result.streak_values = curve;
    evidence = end.kind === 'INCREASE' ? [end.previous_value, ...curve] : curve;
    evidenceReasons = {};
  } else {
    if (row.streak_values !== undefined)
      throw Error('非候选股票不应含长期曲线。');
    let confirmed = 0;
    for (let i = 20; i > 0; i--) {
      const before = values[i - 1],
        after = values[i];
      if (
        reasons[String(i)] === 'NO_TRADE' ||
        before === null ||
        after === null ||
        after > before
      )
        break;
      confirmed++;
    }
    if (streak !== confirmed) throw Error('实际连续不增天数与读数不匹配。');
    const afterIndex = 20 - confirmed;
    const before = values[afterIndex - 1],
      after = values[afterIndex];
    if (reasons[String(afterIndex)] === 'NO_TRADE') {
      if (
        end.kind !== 'NO_TRADE' ||
        end.session !== streakSessions.at(-confirmed - 1) ||
        end.reason !== 'NO_TRADE'
      )
        throw Error('无交易边界与读数不匹配。');
    } else if (before === null || after === null) {
      const missing = after === null ? afterIndex : afterIndex - 1;
      const reason = reasons[String(missing)];
      const kind =
        reason === 'ARCHIVED_WARMUP_UNAVAILABLE' ? 'WARMUP' : 'UNAVAILABLE';
      if (
        end.kind !== kind ||
        end.session !== streakSessions.at(missing - 21) ||
        end.reason !== reason
      )
        throw Error('缺失边界与读数不匹配。');
    } else if (
      end.kind !== 'INCREASE' ||
      end.previous_value !== before ||
      end.anchor_value !== after
    ) {
      throw Error('增加边界与读数不匹配。');
    }
  }
  if (end.kind === 'INCREASE') {
    if (
      end.anchor_session !== start ||
      streakSessions.indexOf(end.previous_session) + 1 !==
        streakSessions.indexOf(end.anchor_session)
    )
      throw Error('集中度增加边界日期无效。');
  } else if (!streakSessions.includes(end.session))
    throw Error('集中度边界日期不在交易日轴内。');
  for (const group of concentrationGroupRegistry) {
    if (
      classify(evidence, evidenceReasons, group.comparisons) !==
      groups[group.key]
    )
      throw Error('集中度分组与原精度读数不匹配。');
  }
  return result;
}

export function parseConcentrationScan(
  value: unknown,
  source: ConcentrationSource,
): ConcentrationScan {
  const row = object(value);
  if (
    row.schema !== 'stock.chips.concentration.two.v1' ||
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
    !Array.isArray(row.rows) ||
    row.rows.length !== source.universe_total
  )
    throw Error('连续集中度结果与当前完整数据批次不匹配。');
  const sessions = axis(row.sessions),
    streakSessions = axis(row.streak_sessions);
  if (
    sessions.length !== 21 ||
    streakSessions.length < 21 ||
    streakSessions.length > 10000 ||
    sessions.at(-1) !== source.as_of ||
    streakSessions.slice(-21).some((value, index) => value !== sessions[index])
  )
    throw Error('集中度交易日轴无效。');
  const rows = row.rows.map((value) => parseRow(value, streakSessions));
  if (new Set(rows.map((value) => value.security_id)).size !== rows.length)
    throw Error('集中度名单含重复股票。');
  const groups = groupRecord(row.groups, counts);
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
    streak_sessions: streakSessions,
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
    NO_TRADE: '该交易日确认无成交，连续不增中断',
    PRE_LISTING: '该日尚未上市',
    MODEL_NOT_READY: '筹码模型数据未齐',
    SOURCE_SESSION_MISSING: '缺少该交易日行情',
    ARCHIVED_WARMUP_UNAVAILABLE: '历史预热数据不足',
    HISTORICAL_SOURCE_UNAVAILABLE: '该日历史输入不可用',
    SEALED_SNAPSHOT_MISMATCH: '最新筹码重算需要核对',
    MODEL_CALCULATION_UNAVAILABLE: '该日筹码集中度无法计算',
    SUPPORTED_CALENDAR_START: '已到支持的交易日历起点',
  };
  return labels[reason] ?? '该日数据需要核实';
}
export function concentrationStreakText(row: ConcentrationRow): string {
  if (row.values[20] === null) return '待核实';
  return `${row.streak_boundary.kind === 'INCREASE' ? '' : '≥'}${row.streak_days}天`;
}
export function concentrationSeries(
  scan: ConcentrationScan,
  row: ConcentrationRow,
  group: ConcentrationGroup,
) {
  if (group === 'LONG' && row.streak_values)
    return {
      sessions: scan.streak_sessions.slice(-row.streak_values.length),
      values: row.streak_values,
      reasons: {},
      highlightFrom: 0,
    };
  const definition = concentrationGroupRegistry.find(
    (entry) => entry.key === group,
  );
  return {
    sessions: scan.sessions,
    values: row.values,
    reasons: row.point_reasons,
    highlightFrom: Math.max(0, 20 - (definition?.comparisons ?? 20)),
  };
}
export function concentrationStartValue(
  row: ConcentrationRow,
  group: ConcentrationGroup,
): number | null {
  if (group === 'LONG') return row.streak_values?.[0] ?? null;
  return row.values[20 - group];
}
export function concentrationDrop(
  row: ConcentrationRow,
  group: ConcentrationGroup,
): number | null {
  const start = concentrationStartValue(row, group),
    latest = row.values[20];
  return start === null || latest === null ? null : start - latest;
}

export type ConcentrationSort = {
  key: 'code' | 'current' | 'streak' | 'start' | 'drop';
  direction: 'asc' | 'desc';
};

export function compareConcentrationRows(
  a: ConcentrationRow,
  b: ConcentrationRow,
  group: ConcentrationGroup,
  sort: ConcentrationSort,
): number {
  const codeOrder = a.security_id.localeCompare(b.security_id);
  if (sort.key === 'code')
    return sort.direction === 'asc' ? codeOrder : -codeOrder;
  function value(row: ConcentrationRow): number | null {
    switch (sort.key) {
      case 'current': return row.values[20];
      case 'streak': return row.values[20] === null ? null : row.streak_days;
      case 'start': return concentrationStartValue(row, group);
      case 'drop': return concentrationDrop(row, group);
      case 'code': return null;
    }
  }
  const left = value(a), right = value(b);
  if (left === null) return right === null ? codeOrder : 1;
  if (right === null) return -1;
  const comparison = sort.direction === 'asc' ? left - right : right - left;
  return comparison || codeOrder;
}
