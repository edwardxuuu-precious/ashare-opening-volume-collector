import { authRead } from './auth-fetch.ts';

export type CyclePoint = {
  date: string;
  equity: number;
  reference: number;
  fitted: number;
  lower: number;
  upper: number;
  gapPct: number;
  rawGapPct: number;
};
export type ForwardOutcome = {
  sessions: number;
  complete: boolean;
  returnPct: number | null;
  downsidePct: number | null;
};
export type CycleEvent = {
  date: string;
  gapPct: number;
  phase: 'training' | 'holdout';
  outcomes: ForwardOutcome[];
};
export type CycleCase = {
  year: number;
  date: string;
  equityClose: number;
  referenceClose: number;
  fittedClose: number;
  gapPct: number;
  rawGapPct: number;
  phase: 'training' | 'holdout';
  outcomes: ForwardOutcome[];
};
export type CycleComparison = {
  id: 'government' | 'nonpure' | 'wind';
  name: string;
  code: string;
  asOf: string;
  baseDate: string;
  points: CyclePoint[];
  fit: {
    method: string;
    trainStart: string;
    trainEnd: string;
    quantile: number;
    alpha: number;
    beta: number;
    samples: number;
    trainingMinGapPct: number;
    holdoutMinGapPct: number | null;
    holdoutBelowTrainingDays: number;
  };
  latest: {
    equityClose: number;
    referenceClose: number;
    fittedClose: number;
    gapPct: number;
    rawGapPct: number;
    gapChange20Pp: number | null;
  };
  cases: CycleCase[];
  events: CycleEvent[];
  rawTouchEvents: CycleEvent[];
  thresholdPct: number;
  cooldownSessions: number;
};
export type MonthlyPoint = {
  date: string;
  month: string;
  open: number;
  high: number;
  low: number;
  close: number;
  lower: number;
  middle: number;
  upper: number;
  incomplete: boolean;
};
export type CycleSource = {
  id: string;
  code: string;
  name: string;
  provider: string;
  firstDate: string;
  lastDate: string;
  rows: number;
  excludedRows: number;
  missingSessions: number;
  url: string;
  note: string;
};
export type CycleSnapshot = {
  schema: 'stock.cycle-bottom.v1';
  version: string;
  generatedAt: string;
  expectedAsOf: string;
  sources: CycleSource[];
  comparisons: CycleComparison[];
  monthly: {
    asOf: string;
    points: MonthlyPoint[];
    fit: {
      method: string;
      trainStart: string;
      trainEnd: string;
      samples: number;
      annualLogSlope: number;
    };
    latestGapPct: number;
  };
  observations: { title: string; detail: string }[];
  unavailable: { code: string; name: string; reason: string }[];
};
export type CycleStatus = {
  state: 'ready' | 'partial' | 'failed' | 'updating';
  checkedAt: string;
  lastSuccessAt: string | null;
  message: string;
  schedule: string;
};
export type CycleBundle = { snapshot: CycleSnapshot; status: CycleStatus };

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function record(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw Error('周期数据格式异常。');
  return value;
}
function list(value: unknown): unknown[] {
  if (!Array.isArray(value) || value.length > 20000)
    throw Error('周期数据序列格式异常或超过读取上限。');
  return value;
}
function str(value: unknown): string {
  if (typeof value !== 'string') throw Error('周期数据文字字段异常。');
  return value;
}
function num(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value))
    throw Error('周期数据包含无效数值。');
  return value;
}
function positive(value: unknown): number {
  const result = num(value);
  if (result <= 0) throw Error('指数点位必须大于零。');
  return result;
}
function nullable(value: unknown): number | null {
  return value === null ? null : num(value);
}
function bool(value: unknown): boolean {
  if (typeof value !== 'boolean') throw Error('周期数据状态异常。');
  return value;
}
function day(value: unknown): string {
  const result = str(value);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(result) ||
    !Number.isFinite(Date.parse(result)) ||
    new Date(result).toISOString().slice(0, 10) !== result
  )
    throw Error('周期数据日期无效。');
  return result;
}
function choice<const T extends string>(
  value: unknown,
  choices: readonly T[],
): T {
  const found = choices.find((candidate) => candidate === value);
  if (found === undefined) throw Error('周期数据枚举字段异常。');
  return found;
}
function outcome(value: unknown): ForwardOutcome {
  const v = record(value);
  const result = {
    sessions: num(v.sessions),
    complete: bool(v.complete),
    returnPct: nullable(v.returnPct),
    downsidePct: nullable(v.downsidePct),
  };
  if (
    !result.complete &&
    (result.returnPct !== null || result.downsidePct !== null)
  )
    throw Error('尚未成熟的观察窗口不能包含完整收益结论。');
  if (
    result.complete &&
    (result.returnPct === null || result.downsidePct === null)
  )
    throw Error('已成熟的观察窗口缺少结果。');
  return result;
}
function point(value: unknown): CyclePoint {
  const v = record(value);
  const result = {
    date: day(v.date),
    equity: positive(v.equity),
    reference: positive(v.reference),
    fitted: positive(v.fitted),
    lower: positive(v.lower),
    upper: positive(v.upper),
    gapPct: num(v.gapPct),
    rawGapPct: num(v.rawGapPct),
  };
  if (result.lower > result.fitted || result.fitted > result.upper)
    throw Error('拟合区间顺序异常。');
  if (
    Math.abs(result.gapPct - (result.equity / result.fitted - 1) * 100) >
      1e-6 ||
    Math.abs(result.rawGapPct - (result.equity / result.reference - 1) * 100) >
      1e-6
  )
    throw Error('周期相对差距与同日曲线不一致。');
  return result;
}
function event(value: unknown): CycleEvent {
  const v = record(value);
  return {
    date: day(v.date),
    gapPct: num(v.gapPct),
    phase: choice(v.phase, ['training', 'holdout']),
    outcomes: list(v.outcomes).map(outcome),
  };
}
function comparison(value: unknown): CycleComparison {
  const v = record(value),
    f = record(v.fit),
    latest = record(v.latest);
  const points = list(v.points).map(point);
  if (
    points.length < 500 ||
    points.some((p, i) => i > 0 && p.date <= points[i - 1].date)
  )
    throw Error('周期序列不足或日期未严格递增。');
  const result: CycleComparison = {
    id: choice(v.id, ['government', 'nonpure', 'wind']),
    name: str(v.name),
    code: str(v.code),
    asOf: day(v.asOf),
    baseDate: day(v.baseDate),
    points,
    fit: {
      method: str(f.method),
      trainStart: day(f.trainStart),
      trainEnd: day(f.trainEnd),
      quantile: num(f.quantile),
      alpha: num(f.alpha),
      beta: num(f.beta),
      samples: num(f.samples),
      trainingMinGapPct: num(f.trainingMinGapPct),
      holdoutMinGapPct: nullable(f.holdoutMinGapPct),
      holdoutBelowTrainingDays: num(f.holdoutBelowTrainingDays),
    },
    latest: {
      equityClose: positive(latest.equityClose),
      referenceClose: positive(latest.referenceClose),
      fittedClose: positive(latest.fittedClose),
      gapPct: num(latest.gapPct),
      rawGapPct: num(latest.rawGapPct),
      gapChange20Pp: nullable(latest.gapChange20Pp),
    },
    cases: list(v.cases).map((item) => {
      const c = record(item);
      return {
        year: num(c.year),
        date: day(c.date),
        equityClose: positive(c.equityClose),
        referenceClose: positive(c.referenceClose),
        fittedClose: positive(c.fittedClose),
        gapPct: num(c.gapPct),
        rawGapPct: num(c.rawGapPct),
        phase: choice(c.phase, ['training', 'holdout']),
        outcomes: list(c.outcomes).map(outcome),
      };
    }),
    events: list(v.events).map(event),
    rawTouchEvents: list(v.rawTouchEvents).map(event),
    thresholdPct: num(v.thresholdPct),
    cooldownSessions: num(v.cooldownSessions),
  };
  if (points[0].date !== result.baseDate || points.at(-1)?.date !== result.asOf)
    throw Error('周期数据日期范围与序列不一致。');
  return result;
}
export function parseCycleSnapshot(value: unknown): CycleSnapshot {
  const v = record(value),
    m = record(v.monthly),
    f = record(m.fit);
  if (
    v.schema !== 'stock.cycle-bottom.v1' ||
    typeof v.version !== 'string' ||
    !/^[a-f0-9]{16}$/.test(v.version)
  )
    throw Error('周期数据版本格式异常。');
  const result: CycleSnapshot = {
    schema: 'stock.cycle-bottom.v1',
    version: v.version,
    generatedAt: str(v.generatedAt),
    expectedAsOf: day(v.expectedAsOf),
    sources: list(v.sources).map((item) => {
      const s = record(item);
      return {
        id: str(s.id),
        code: str(s.code),
        name: str(s.name),
        provider: str(s.provider),
        firstDate: day(s.firstDate),
        lastDate: day(s.lastDate),
        rows: num(s.rows),
        excludedRows: num(s.excludedRows),
        missingSessions: num(s.missingSessions),
        url: str(s.url),
        note: str(s.note),
      };
    }),
    comparisons: list(v.comparisons).map(comparison),
    monthly: {
      asOf: day(m.asOf),
      points: list(m.points).map((item) => {
        const p = record(item);
        const result = {
          date: day(p.date),
          month: str(p.month),
          open: positive(p.open),
          high: positive(p.high),
          low: positive(p.low),
          close: positive(p.close),
          lower: positive(p.lower),
          middle: positive(p.middle),
          upper: positive(p.upper),
          incomplete: bool(p.incomplete),
        };
        if (
          result.low > Math.min(result.open, result.close) ||
          result.high < Math.max(result.open, result.close) ||
          result.lower > result.middle ||
          result.middle > result.upper
        )
          throw Error('月线行情或通道关系异常。');
        return result;
      }),
      fit: {
        method: str(f.method),
        trainStart: day(f.trainStart),
        trainEnd: day(f.trainEnd),
        samples: num(f.samples),
        annualLogSlope: num(f.annualLogSlope),
      },
      latestGapPct: num(m.latestGapPct),
    },
    observations: list(v.observations).map((item) => {
      const o = record(item);
      return { title: str(o.title), detail: str(o.detail) };
    }),
    unavailable: list(v.unavailable).map((item) => {
      const u = record(item);
      return { code: str(u.code), name: str(u.name), reason: str(u.reason) };
    }),
  };
  if (!result.comparisons.length || !result.monthly.points.length)
    throw Error('周期数据尚无完整可用序列。');
  if (
    !result.sources.some(
      (source) => source.id === 'equity' && source.code === 'H00300',
    ) ||
    !result.sources.some(
      (source) => source.id === 'shanghai' && source.code === '000001',
    )
  )
    throw Error('全收益或月线指数身份不一致。');
  const codes = { government: '000012', nonpure: '930897', wind: '885007.WI' };
  if (
    result.comparisons.some(
      (item) =>
        item.code !== codes[item.id] ||
        !result.sources.some(
          (source) => source.id === item.id && source.code === item.code,
        ),
    )
  )
    throw Error('参照指数身份不一致。');
  return result;
}
export function parseCycleStatus(value: unknown): CycleStatus {
  const v = record(value);
  return {
    state: choice(v.state, ['ready', 'partial', 'failed', 'updating']),
    checkedAt: str(v.checkedAt),
    lastSuccessAt: v.lastSuccessAt === null ? null : str(v.lastSuccessAt),
    message: str(v.message),
    schedule: str(v.schedule),
  };
}
async function read(path: string, signal: AbortSignal): Promise<unknown> {
  const base = (process.env.NEXT_PUBLIC_DATA_BASE || '').replace(/\/$/, '');
  return authRead(
    base + path,
    { signal, cache: 'no-store' },
    async (response) => {
      if (!response.ok)
        throw Error(`周期数据读取失败（HTTP ${response.status}）。`);
      return response.json();
    },
  );
}
export async function loadCycleBundle(
  signal: AbortSignal,
): Promise<CycleBundle> {
  const [rawManifest, rawStatus] = await Promise.all([
    read('/data/cycle-bottom/manifest.json', signal),
    read('/data/cycle-bottom/status.json', signal),
  ]);
  const manifest = record(rawManifest);
  const version = str(manifest.version);
  if (!/^[a-f0-9]{16}$/.test(version)) throw Error('周期数据目录版本无效。');
  const snapshot = parseCycleSnapshot(
    await read(`/data/cycle-bottom/${version}/analysis.json`, signal),
  );
  if (snapshot.version !== version)
    throw Error('周期数据版本已变化，请重新读取。');
  return { snapshot, status: parseCycleStatus(rawStatus) };
}
