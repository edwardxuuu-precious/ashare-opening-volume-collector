import { authFetch } from './auth-fetch.ts';
import { readResponse } from './read-request.ts';
import type { TriangleDataset, TriangleSecurity } from './triangle-data.ts';
import {
  defaultTriangleParams,
  parameterFields,
  type MaCross,
  type PathStatus,
  type PriceAnchor,
  type PriceBar,
  type PriceZone,
  type TriangleAnalysis,
  type TriangleEvent,
  type TriangleFormation,
  type TriangleParams,
  type TriangleState,
} from './triangles.ts';

const prefix = '/data/triangles/daily/';
const bundleLimit = 4 * 1024 * 1024;
export const triangleDailyRuleSha256 =
  '2f1c595b2bd48f47c6feb4d272a83bb357355949ac43cfc79658c53dd31082b0';
type FileVersion = { file: string; sha256: string; bytes: number };
export type TriangleDailyManifest = {
  schema: 'stock.triangles.daily.manifest.v1';
  dataset_id: string;
  as_of: string;
  generated_at: string;
  params: TriangleParams;
  history_sessions: 140;
  recent_sessions: 5;
  sessions: string[];
  detail_sessions: string[];
  universe_total: number;
  history_only_securities: TriangleSecurity[];
  rule_sha256: string;
  screening: FileVersion;
  bundles: (FileVersion & { codes: string[] })[];
};
export type TriangleDailyRow = {
  security: TriangleSecurity;
  latestClose: number | null;
  availability: TriangleAnalysis['availability'];
  formations: TriangleFormation[];
};
export type TriangleDailyScreen = {
  dataset_id: string;
  as_of: string;
  generated_at: string;
  params: TriangleParams;
  sessions: string[];
  coverage: TriangleDataset['coverage'];
  rows: TriangleDailyRow[];
};
export type TriangleDailyPublication = {
  manifest: TriangleDailyManifest;
  screen: TriangleDailyScreen;
};
export type TriangleDailyStatus = {
  state: 'RUNNING' | 'COMMITTED' | 'SOURCE_PENDING' | 'FAILED';
  as_of: string;
  updated_at: string;
  dataset_id: string | null;
  last_committed_date: string | null;
  message: string;
};
export type TriangleDailySymbol = {
  security: TriangleSecurity;
  bars: PriceBar[];
};
type ReadOptions = {
  dataBase: string;
  signal: AbortSignal;
  fetcher?: typeof fetch;
  timeoutMs?: number;
  retryBaseMs?: number;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function record(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw Error('三角发布数据格式不正确。');
  return value;
}
function text(value: unknown): string {
  if (typeof value !== 'string') throw Error('三角发布文本字段不正确。');
  return value;
}
function day(value: unknown): string {
  const date = text(value);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
    !Number.isFinite(Date.parse(date)) ||
    new Date(date).toISOString().slice(0, 10) !== date
  )
    throw Error('三角发布日期不正确。');
  return date;
}
function timestamp(value: unknown): string {
  const date = text(value);
  if (!/^\d{4}-\d{2}-\d{2}T/.test(date) || !Number.isFinite(Date.parse(date)))
    throw Error('三角发布时间不正确。');
  return date;
}
function count(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0)
    throw Error('三角发布计数不正确。');
  return value;
}
function positive(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0)
    throw Error('三角发布价格不正确。');
  return value;
}
function nullablePrice(value: unknown): number | null {
  return value === null ? null : positive(value);
}
function bool(value: unknown): boolean {
  if (typeof value !== 'boolean') throw Error('三角发布条件字段不正确。');
  return value;
}
function array(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw Error('三角发布列表不正确。');
  return value;
}
function hash(value: unknown): string {
  const result = text(value);
  if (!/^[a-f0-9]{64}$/.test(result)) throw Error('三角发布校验值不正确。');
  return result;
}
function datasetId(value: unknown): string {
  const result = text(value);
  if (!/^[a-f0-9]{16}$/.test(result)) throw Error('三角发布版本不正确。');
  return result;
}
function code(value: unknown): string {
  const result = text(value);
  if (!/^\d{6}$/.test(result)) throw Error('三角发布股票代码不正确。');
  return result;
}
function security(value: unknown): TriangleSecurity {
  const item = record(value);
  const result = {
    code: code(item.code),
    name: text(item.name),
    market: text(item.market),
  };
  if (!result.name.trim()) throw Error('三角发布股票名称为空。');
  return result;
}
function unique(values: string[]) {
  if (new Set(values).size !== values.length)
    throw Error('三角发布包含重复记录。');
  return values;
}
function sessions(value: unknown, asOf: string) {
  const dates = unique(array(value).map(day));
  if (
    !dates.length ||
    dates.at(-1) !== asOf ||
    dates.some(
      (date, index) => date > asOf || (index > 0 && date <= dates[index - 1]),
    )
  )
    throw Error('三角发布交易日范围不正确。');
  return dates;
}
function sameDates(a: string[], b: string[]) {
  return a.length === b.length && a.every((date, index) => date === b[index]);
}
function params(value: unknown): TriangleParams {
  const item = record(value);
  if (
    Object.keys(item).length !== parameterFields.length ||
    parameterFields.some(
      (field) => item[field.key] !== defaultTriangleParams[field.key],
    )
  )
    throw Error('每日三角发布参数与默认规则不一致。');
  return { ...defaultTriangleParams };
}
function fileVersion(value: unknown, file: string, limit: number): FileVersion {
  const item = record(value),
    bytes = count(item.bytes);
  if (item.file !== file || bytes === 0 || bytes > limit)
    throw Error('三角发布文件路径或大小不正确。');
  return { file, bytes, sha256: hash(item.sha256) };
}

export function parseTriangleDailyManifest(
  value: unknown,
): TriangleDailyManifest {
  const item = record(value);
  if (
    item.schema !== 'stock.triangles.daily.manifest.v1' ||
    item.history_sessions !== 140 ||
    item.recent_sessions !== 5
  )
    throw Error('每日三角发布版本或观察范围不正确。');
  const asOf = day(item.as_of),
    history = sessions(item.sessions, asOf),
    detail = sessions(item.detail_sessions, asOf);
  if (history.length > 140 || !sameDates(history, detail.slice(-140)))
    throw Error('每日筛选与个股历史交易日不一致。');
  record(item.source);
  const bundles = array(item.bundles).map((value, index) => {
    const entry = record(value),
      codes = unique(array(entry.codes).map(code));
    if (!codes.length || codes.length > 100)
      throw Error('三角发布分包股票数量不正确。');
    return {
      ...fileVersion(
        value,
        `bundle-${String(index).padStart(3, '0')}.json`,
        bundleLimit,
      ),
      codes,
    };
  });
  const historical =
    item.history_only_securities === undefined
      ? []
      : array(item.history_only_securities).map(security);
  unique(historical.map((stock) => stock.code));
  const codes = unique(bundles.flatMap((entry) => entry.codes));
  const total = count(item.universe_total);
  if (
    !total ||
    codes.length !== total + historical.length ||
    bundles.length > 1000 ||
    historical.some((stock) => !codes.includes(stock.code))
  )
    throw Error('三角发布股票总数与分包不一致。');
  if (hash(item.rule_sha256) !== triangleDailyRuleSha256)
    throw Error('每日发布规则与当前页面版本不一致，请更新页面。');
  return {
    schema: item.schema,
    dataset_id: datasetId(item.dataset_id),
    as_of: asOf,
    generated_at: timestamp(item.generated_at),
    params: params(item.params),
    history_sessions: 140,
    recent_sessions: 5,
    sessions: history,
    detail_sessions: detail,
    universe_total: total,
    history_only_securities: historical,
    rule_sha256: triangleDailyRuleSha256,
    screening: fileVersion(item.screening, 'screening.json', 32 * 1024 * 1024),
    bundles,
  };
}
function knownDay(value: unknown, dates: Set<string>) {
  const date = day(value);
  if (!dates.has(date)) throw Error('三角依据日期超出发布历史。');
  return date;
}
function anchor(value: unknown, dates: Set<string>): PriceAnchor | null {
  if (value === null) return null;
  const item = record(value);
  if (item.source !== 'high' && item.source !== 'low')
    throw Error('三角前期价格依据不正确。');
  return {
    date: knownDay(item.date, dates),
    price: positive(item.price),
    source: item.source,
  };
}
function cross(value: unknown, dates: Set<string>): MaCross {
  const item = record(value);
  if (
    (item.pair !== 'fast-middle' &&
      item.pair !== 'fast-slow' &&
      item.pair !== 'middle-slow') ||
    (item.direction !== 'up' && item.direction !== 'down')
  )
    throw Error('三角均线交叉不正确。');
  return {
    id: text(item.id),
    pair: item.pair,
    direction: item.direction,
    date: knownDay(item.date, dates),
    value: positive(item.value),
  };
}
function zone(
  value: unknown,
  dates: Set<string>,
  kind: PriceZone['kind'],
): PriceZone | null {
  if (value === null) return null;
  const item = record(value),
    lower = positive(item.lower),
    center = positive(item.center),
    upper = positive(item.upper);
  if (item.kind !== kind || lower > center || center > upper)
    throw Error('三角价格区域不正确。');
  const frozenAt = knownDay(item.frozenAt, dates),
    knownAt = knownDay(item.knownAt, dates);
  const touches = array(item.touches).map((value) => {
    const touch = record(value),
      occurredAt = knownDay(touch.occurredAt, dates),
      knownAt = knownDay(touch.knownAt, dates);
    if (occurredAt > knownAt || knownAt > frozenAt)
      throw Error('三角触点提前使用未来依据。');
    return { occurredAt, knownAt, price: positive(touch.price) };
  });
  if (knownAt > frozenAt) throw Error('三角价格区域提前使用未来依据。');
  return { kind, lower, center, upper, frozenAt, knownAt, touches };
}
function pathStatus(value: unknown, dates: Set<string>): PathStatus {
  const item = record(value);
  if (item.kind === 'waiting') return { kind: item.kind };
  if (item.kind === 'testing')
    return {
      kind: item.kind,
      startedAt: knownDay(item.startedAt, dates),
      observedSessions: count(item.observedSessions),
      requiredSessions: count(item.requiredSessions),
    };
  if (item.kind === 'confirmed') {
    const occurredAt = knownDay(item.occurredAt, dates),
      knownAt = knownDay(item.knownAt, dates);
    if (occurredAt > knownAt) throw Error('三角路径确认日期不正确。');
    return {
      kind: item.kind,
      occurredAt,
      knownAt,
      price: positive(item.price),
    };
  }
  throw Error('三角路径状态不正确。');
}
function formationState(value: unknown, dates: Set<string>): TriangleState {
  const item = record(value);
  if (item.kind === 'active')
    return {
      kind: item.kind,
      ageSessions: count(item.ageSessions),
      breakout: pathStatus(item.breakout, dates),
      retest: pathStatus(item.retest, dates),
    };
  if (item.kind === 'observing')
    return { kind: item.kind, ageSessions: count(item.ageSessions) };
  if (item.kind === 'rejected')
    return { kind: item.kind, reason: text(item.reason) };
  if (
    item.kind === 'invalidated' ||
    item.kind === 'expired' ||
    item.kind === 'coverage-ended'
  )
    return {
      kind: item.kind,
      date: knownDay(item.date, dates),
      reason: text(item.reason),
    };
  throw Error('三角形态状态不正确。');
}
function event(value: unknown, dates: Set<string>): TriangleEvent {
  const item = record(value),
    occurredAt = knownDay(item.occurredAt, dates),
    knownAt = knownDay(item.knownAt, dates);
  const kind = item.kind;
  if (
    kind !== 'formed' &&
    kind !== 'resistance-breakout-observed' &&
    kind !== 'support-touch' &&
    kind !== 'resistance-breakout' &&
    kind !== 'support-retest' &&
    kind !== 'invalidated' &&
    kind !== 'expired' &&
    kind !== 'coverage-ended'
  )
    throw Error('三角事件类型不正确。');
  if (occurredAt > knownAt) throw Error('三角事件确认日期不正确。');
  return {
    id: text(item.id),
    kind,
    occurredAt,
    knownAt,
    price: nullablePrice(item.price),
    reason: text(item.reason),
  };
}
function formation(value: unknown, dates: Set<string>): TriangleFormation {
  const item = record(value),
    pretrend = record(item.pretrend),
    thresholds = record(item.thresholds),
    reasons = record(item.zoneReasons);
  if (
    (item.direction !== 'golden' && item.direction !== 'death') ||
    (pretrend.kind !== 'decline' && pretrend.kind !== 'advance')
  )
    throw Error('三角方向不正确。');
  const startedAt = knownDay(item.startedAt, dates),
    formedAt = knownDay(item.formedAt, dates);
  const crosses = array(item.crosses).map((value) => cross(value, dates));
  const state = formationState(item.state, dates);
  if (
    !bool(item.qualified) ||
    (state.kind !== 'active' && state.kind !== 'observing') ||
    startedAt > formedAt ||
    crosses.length !== 3 ||
    new Set(crosses.map((entry) => entry.pair)).size !== 3 ||
    crosses.some(
      (entry) =>
        entry.date < startedAt ||
        entry.date > formedAt ||
        entry.direction !== (item.direction === 'golden' ? 'up' : 'down'),
    )
  )
    throw Error('每日三角列表包含非有效观察形态。');
  const events = array(item.events).map((value) => event(value, dates));
  unique(events.map((entry) => entry.id));
  if (
    !events.length ||
    events.some(
      (entry, index) =>
        entry.occurredAt < formedAt ||
        (index > 0 && entry.knownAt < events[index - 1].knownAt),
    )
  )
    throw Error('三角事件顺序不正确。');
  const movePct =
    pretrend.movePct === null ? null : countOrPercent(pretrend.movePct);
  return {
    id: text(item.id),
    direction: item.direction,
    startedAt,
    formedAt,
    price: positive(item.price),
    crosses,
    qualified: true,
    reasons: array(item.reasons).map(text),
    state,
    events,
    pretrend: {
      kind: pretrend.kind,
      requiredSessions: count(pretrend.requiredSessions),
      observedSessions: count(pretrend.observedSessions),
      complete: bool(pretrend.complete),
      movePct,
      from: anchor(pretrend.from, dates),
      to: anchor(pretrend.to, dates),
      qualified: bool(pretrend.qualified),
      reason: text(pretrend.reason),
    },
    support: zone(item.support, dates, 'support'),
    resistance: zone(item.resistance, dates, 'resistance'),
    thresholds: {
      breakout: nullablePrice(thresholds.breakout),
      retestRecovery: nullablePrice(thresholds.retestRecovery),
      zoneInvalidation: nullablePrice(thresholds.zoneInvalidation),
      anchorInvalidation: nullablePrice(thresholds.anchorInvalidation),
    },
    zoneReasons: {
      support: text(reasons.support),
      resistance: text(reasons.resistance),
    },
  };
}
function countOrPercent(value: unknown) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0)
    throw Error('三角前期幅度不正确。');
  return value;
}
function coverage(
  value: unknown,
  manifest: TriangleDailyManifest,
): TriangleDataset['coverage'] {
  const item = record(value),
    failedDates = array(item.failedDates).map((value) => {
      const entry = record(value);
      return {
        date: knownDay(entry.date, new Set(manifest.sessions)),
        reason: text(entry.reason),
      };
    });
  unique(failedDates.map((entry) => entry.date));
  const result = {
    loadedDays: count(item.loadedDays),
    requestedDays: count(item.requestedDays),
    requestedHistory: count(item.requestedHistory),
    failedDates,
    calendarKnown: bool(item.calendarKnown),
    universeTotal:
      item.universeTotal === null ? null : count(item.universeTotal),
    completePriceSeries: count(item.completePriceSeries),
    validPriceBars: count(item.validPriceBars),
  };
  if (
    result.requestedHistory !== 140 ||
    result.requestedDays !== manifest.sessions.length ||
    result.loadedDays + failedDates.length !== result.requestedDays ||
    (result.universeTotal !== null &&
      result.universeTotal !== manifest.universe_total) ||
    result.completePriceSeries > manifest.universe_total ||
    result.validPriceBars > manifest.universe_total * manifest.sessions.length
  )
    throw Error('三角发布行情覆盖不一致。');
  return result;
}
export function parseTriangleDailyScreen(
  value: unknown,
  manifest: TriangleDailyManifest,
): TriangleDailyScreen {
  const item = record(value);
  if (
    item.schema !== 'stock.triangles.daily.screen.v1' ||
    item.dataset_id !== manifest.dataset_id ||
    item.as_of !== manifest.as_of ||
    item.generated_at !== manifest.generated_at ||
    !sameDates(sessions(item.sessions, manifest.as_of), manifest.sessions)
  )
    throw Error('三角筛选与发布版本不一致。');
  const historical = new Set(
    manifest.history_only_securities.map((stock) => stock.code),
  );
  const dates = new Set(manifest.sessions),
    expected = new Set(
      manifest.bundles
        .flatMap((entry) => entry.codes)
        .filter((code) => !historical.has(code)),
    );
  const rows = array(item.rows).map((value): TriangleDailyRow => {
    const row = record(value),
      available = record(row.availability),
      stock = security(row.security);
    if (
      !expected.delete(stock.code) ||
      (available.kind !== 'ready' && available.kind !== 'insufficient')
    )
      throw Error('三角筛选股票记录重复或不一致。');
    const formations = array(row.formations).map((value) =>
      formation(value, dates),
    );
    unique(formations.map((entry) => entry.id));
    return {
      security: stock,
      latestClose: nullablePrice(row.latestClose),
      availability: {
        kind: available.kind,
        reason: text(available.reason),
        consecutiveBars: count(available.consecutiveBars),
      },
      formations,
    };
  });
  if (expected.size || rows.length !== manifest.universe_total)
    throw Error('三角筛选缺少股票记录。');
  return {
    dataset_id: manifest.dataset_id,
    as_of: manifest.as_of,
    generated_at: manifest.generated_at,
    params: params(item.params),
    sessions: [...manifest.sessions],
    coverage: coverage(item.coverage, manifest),
    rows,
  };
}
function bar(value: unknown, date: string): PriceBar {
  const item = record(value);
  if (item.date !== date) throw Error('个股历史交易日不连续或顺序错误。');
  if (
    item.kind === 'gap' &&
    (item.reason === 'not-traded' ||
      item.reason === 'missing-row' ||
      item.reason === 'missing-day' ||
      item.reason === 'invalid-price')
  )
    return { kind: item.kind, date, reason: item.reason };
  if (item.kind !== 'bar') throw Error('个股历史行情类型不正确。');
  const open = positive(item.open),
    high = positive(item.high),
    low = positive(item.low),
    close = positive(item.close);
  if (low > open || open > high || low > close || close > high)
    throw Error('个股历史OHLC价格不正确。');
  return { kind: item.kind, date, open, high, low, close };
}
export function parseTriangleDailyBundle(
  value: unknown,
  manifest: TriangleDailyManifest,
  file: string,
): TriangleDailySymbol[] {
  const expectedBundle = manifest.bundles.find((entry) => entry.file === file);
  if (!expectedBundle) throw Error('个股分包不在发布目录内。');
  const item = record(value);
  if (
    item.schema !== 'stock.triangles.daily.bundle.v1' ||
    item.dataset_id !== manifest.dataset_id ||
    item.as_of !== manifest.as_of ||
    !sameDates(
      sessions(item.sessions, manifest.as_of),
      manifest.detail_sessions,
    )
  )
    throw Error('个股历史与发布版本不一致。');
  const expected = new Set(expectedBundle.codes);
  const rows = array(item.rows).map((value) => {
    const row = record(value),
      stock = security(row.security),
      values = array(row.bars);
    if (
      !expected.delete(stock.code) ||
      values.length !== manifest.detail_sessions.length
    )
      throw Error('个股历史股票记录重复或行情长度不正确。');
    return {
      security: stock,
      bars: values.map((value, index) =>
        bar(value, manifest.detail_sessions[index]),
      ),
    };
  });
  if (expected.size) throw Error('个股历史分包缺少股票。');
  return rows;
}
export function parseTriangleDailyStatus(value: unknown): TriangleDailyStatus {
  const item = record(value);
  if (
    item.schema !== 'stock.triangles.daily.status.v1' ||
    (item.state !== 'RUNNING' &&
      item.state !== 'COMMITTED' &&
      item.state !== 'SOURCE_PENDING' &&
      item.state !== 'FAILED')
  )
    throw Error('三角更新状态格式不正确。');
  return {
    state: item.state,
    as_of: day(item.as_of),
    updated_at: timestamp(item.updated_at),
    dataset_id: item.dataset_id === null ? null : datasetId(item.dataset_id),
    last_committed_date:
      item.last_committed_date === null ? null : day(item.last_committed_date),
    message: text(item.message),
  };
}
async function readJson(
  path: string,
  options: ReadOptions,
  version?: FileVersion,
): Promise<unknown> {
  return readResponse(
    options.dataBase.replace(/\/$/, '') + prefix + path,
    { method: 'GET', cache: 'no-store', signal: options.signal },
    async (response) => {
      if (response.status === 401 || response.status === 403)
        throw Error(
          response.status === 401
            ? '登录已失效，请重新登录。'
            : '当前账号无权读取三角行情。',
        );
      if (!response.ok)
        throw Error(`三角发布读取失败（HTTP ${response.status}）。`);
      const limit = version?.bytes ?? 2 * 1024 * 1024;
      const chunks: Uint8Array[] = [];
      let size = 0;
      if (response.body) {
        const reader = response.body.getReader();
        try {
          for (;;) {
            const next = await reader.read();
            if (next.done) break;
            size += next.value.byteLength;
            if (size > limit) throw Error('三角发布文件超过声明大小。');
            chunks.push(next.value);
          }
        } finally {
          await reader.cancel().catch(() => {});
        }
      }
      if (version && size !== version.bytes)
        throw Error('三角发布文件长度与目录不一致。');
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.byteLength;
      }
      if (version) {
        const digest = await crypto.subtle.digest('SHA-256', bytes);
        const actual = Array.from(new Uint8Array(digest), (value) =>
          value.toString(16).padStart(2, '0'),
        ).join('');
        if (actual !== version.sha256)
          throw Error('三角发布文件校验失败，保留上次结果。');
      }
      return JSON.parse(
        new TextDecoder('utf-8', { fatal: true }).decode(bytes),
      );
    },
    {
      fetcher: options.fetcher ?? authFetch,
      timeoutMs: options.timeoutMs,
      retryBaseMs: options.retryBaseMs,
    },
  );
}
export async function loadTriangleDailyManifest(options: ReadOptions) {
  return parseTriangleDailyManifest(await readJson('manifest.json', options));
}
export async function loadTriangleDailyStatus(options: ReadOptions) {
  return parseTriangleDailyStatus(await readJson('status.json', options));
}
export async function loadTriangleDailyScreen(
  manifest: TriangleDailyManifest,
  options: ReadOptions,
) {
  return parseTriangleDailyScreen(
    await readJson(
      `${manifest.dataset_id}/${manifest.screening.file}`,
      options,
      manifest.screening,
    ),
    manifest,
  );
}
export function createTriangleDailyDetailReader(
  publication: TriangleDailyPublication,
) {
  const { manifest, screen } = publication;
  const cache = new Map<string, TriangleDailySymbol[]>();
  return async (symbol: string, options: ReadOptions) => {
    const bundle = manifest.bundles.find((entry) =>
      entry.codes.includes(symbol),
    );
    if (!bundle) throw Error('所选股票不在当前发布名单。');
    let rows = cache.get(bundle.file);
    if (!rows) {
      rows = parseTriangleDailyBundle(
        await readJson(
          `${manifest.dataset_id}/${bundle.file}`,
          options,
          bundle,
        ),
        manifest,
        bundle.file,
      );
      for (const row of rows) {
        const stock =
          screen.rows.find((entry) => entry.security.code === row.security.code)
            ?.security ??
          manifest.history_only_securities.find(
            (entry) => entry.code === row.security.code,
          );
        if (
          !stock ||
          stock.name !== row.security.name ||
          stock.market !== row.security.market
        )
          throw Error('个股历史与筛选股票身份不一致。');
      }
      if (options.signal.aborted)
        throw new DOMException('Aborted', 'AbortError');
      cache.set(bundle.file, rows);
    }
    const row = rows.find((entry) => entry.security.code === symbol);
    if (!row) throw Error('个股历史分包缺少所选股票。');
    return row;
  };
}
export function triangleLastKnownAt(formation: TriangleFormation) {
  return formation.events.at(-1)?.knownAt ?? formation.formedAt;
}
export function filterTriangleDailyRows(
  screen: TriangleDailyScreen,
  direction: 'all' | 'golden' | 'death',
  recentSessions: number,
) {
  const cutoff =
    recentSessions > 0 ? screen.sessions.slice(-recentSessions)[0] : null;
  return screen.rows
    .flatMap((row) => {
      const matching = row.formations.filter(
        (formation) =>
          (direction === 'all' || formation.direction === direction) &&
          (!cutoff || triangleLastKnownAt(formation) >= cutoff),
      );
      const latest = matching
        .toSorted(
          (a, b) =>
            triangleLastKnownAt(a).localeCompare(triangleLastKnownAt(b)) ||
            a.formedAt.localeCompare(b.formedAt),
        )
        .at(-1);
      return latest ? [{ row, formation: latest }] : [];
    })
    .sort(
      (a, b) =>
        triangleLastKnownAt(b.formation).localeCompare(
          triangleLastKnownAt(a.formation),
        ) || a.row.security.code.localeCompare(b.row.security.code),
    );
}
