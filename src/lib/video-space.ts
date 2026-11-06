import { authRead } from './auth-fetch.ts';

export type VideoCode = '300502' | '300308' | '300394';
type Endpoint = { readonly date: string; readonly field: 'high' | 'low' };
export type VideoScenario = {
  readonly id: string;
  readonly code: VideoCode;
  readonly label: string;
  readonly high: Endpoint;
  readonly low: Endpoint;
  readonly reason: string;
  readonly primary: boolean;
};
export const VIDEO_RULE_VERSION = 'video-space.fixed-candidates.v1';
export const FROZEN_DATASET = '31c3b953170dd239';
const FROZEN_SOURCE = 'b6b7196f317c4f9f';
const PREFIX = '/data/golden-projection/daily/';
export const videoStocks = [
  {
    code: '300502',
    name: '新易盛',
    sourceSha:
      'dd6c48621feaf3b5a2c5c9d59167c070ad9236fa41256d111767ea198c42d48c',
    historySha:
      'aeda0950b1036f20504d971c7261c2b5d9b46a6ed5ec2fc40ae51244cd57c0d9',
  },
  {
    code: '300308',
    name: '中际旭创',
    sourceSha:
      '157dbb29d02e352d76d679c39c043da5f9199d1f563d85faa35f779a374f7e16',
    historySha:
      '93055d42af059ebbf8203f889ad8167c124bd823bb3c20602a61326ce6b1f984',
  },
  {
    code: '300394',
    name: '天孚通信',
    sourceSha:
      '25ce1df8b90a1fbfac2f6e0599ec19b2152f39554ffecce60aa16853ab4dc59e',
    historySha:
      '5f5b4938e188db880e9a4a1ecbc51b41443c631b64025d99473ff492df29c94f',
  },
] satisfies readonly {
  code: VideoCode;
  name: string;
  sourceSha: string;
  historySha: string;
}[];
const high = (date: string): Endpoint => ({ date, field: 'high' });
const low = (date: string): Endpoint => ({ date, field: 'low' });
export const videoScenarios: readonly VideoScenario[] = [
  {
    id: 'XY_SUPP_PRIMARY',
    code: '300502',
    label: '补片较早波段 · 主候选',
    high: high('2026-08-18'),
    low: low('2026-07-30'),
    primary: true,
    reason: '按补片较早波段人工固定7/30低点与8/18高点，保留相邻高点差异。',
  },
  {
    id: 'XY_SUPP_H0817',
    code: '300502',
    label: '补片较早波段 · 8/17高点替代',
    high: high('2026-08-17'),
    low: low('2026-07-30'),
    primary: false,
    reason: '相邻8/17高点作为独立候选，不按后续贴线程度替换主候选。',
  },
  {
    id: 'XY_FIRST_L0902',
    code: '300502',
    label: '第一片近期空间 · 9/2低点',
    high: high('2026-09-22'),
    low: low('2026-09-02'),
    primary: false,
    reason: '第一片9月平台候选，保留9/2至9/4低点的取点差异。',
  },
  {
    id: 'XY_FIRST_L0903',
    code: '300502',
    label: '第一片近期空间 · 9/3低点',
    high: high('2026-09-22'),
    low: low('2026-09-03'),
    primary: false,
    reason: '第一片9月平台的9/3低点替代，独立展示同尺变化。',
  },
  {
    id: 'XY_FIRST_L0904',
    code: '300502',
    label: '第一片近期空间 · 9/4低点',
    high: high('2026-09-22'),
    low: low('2026-09-04'),
    primary: false,
    reason: '第一片9月平台的9/4低点替代，独立展示同尺变化。',
  },
  {
    id: 'ZJ_SUPP_PRIMARY',
    code: '300308',
    label: '补片较早标尺 · 主候选',
    high: high('2026-08-04'),
    low: low('2026-07-30'),
    primary: true,
    reason: '按补片另一把较早标尺人工固定7/30低点与8/4高点。',
  },
  {
    id: 'ZJ_FIRST_RECENT',
    code: '300308',
    label: '第一片近期空间 · 替代候选',
    high: high('2026-09-22'),
    low: low('2026-09-03'),
    primary: false,
    reason:
      '第一片9月平台另场景；9/3原片属性价与保存源存在差异，沿用同一不复权源，不混用或平移价格。',
  },
  {
    id: 'TF_PLATFORM_PRIMARY',
    code: '300394',
    label: '平台主候选 · 晚峰与早谷',
    high: high('2026-09-18'),
    low: low('2026-08-24'),
    primary: true,
    reason:
      '人工固定平台较新上沿与较早回撤，保留另外三组端点组合，半格读数可能对端点敏感。',
  },
  {
    id: 'TF_PLATFORM_H0818',
    code: '300394',
    label: '平台替代 · 早峰与早谷',
    high: high('2026-08-18'),
    low: low('2026-08-24'),
    primary: false,
    reason: '平台早峰与早谷候选，不因后续价格更贴线提升为主候选。',
  },
  {
    id: 'TF_PLATFORM_L0902',
    code: '300394',
    label: '平台替代 · 晚峰与刺破低点',
    high: high('2026-09-18'),
    low: low('2026-09-02'),
    primary: false,
    reason: '保留9/2刺破低点作为独立替代，检查半格判读敏感性。',
  },
  {
    id: 'TF_PLATFORM_H0818_L0902',
    code: '300394',
    label: '平台替代 · 早峰与刺破低点',
    high: high('2026-08-18'),
    low: low('2026-09-02'),
    primary: false,
    reason: '早峰与刺破低点组合，保留端点差异，不按结果筛选。',
  },
];
export type VideoBar = {
  readonly date: string;
  readonly open: string;
  readonly high: string;
  readonly low: string;
  readonly close: string;
  readonly volume_shares: string;
};
export type VideoManifest = {
  readonly dataset: string;
  readonly sourceDataset: string;
  readonly asOf: string;
};
type Frame = VideoManifest & {
  readonly code: VideoCode;
  readonly history: readonly VideoBar[];
  readonly sourceSha: string;
  readonly provider: 'THS_UNADJUSTED';
};
export type VerifiedVideoStock = {
  readonly code: VideoCode;
  readonly asOf: string;
  readonly dataset: string;
  readonly sourceDataset: string;
  readonly frozen: Frame;
  readonly history: readonly VideoBar[];
};
export type SpaceRuler = { readonly H: number; readonly L: number };
export type SpaceLine = { readonly k: number; readonly price: number };
export type PriceRelation = {
  readonly side: 'ABOVE' | 'ON' | 'BELOW';
  readonly inBand: boolean;
  readonly distancePercent: number;
};
export type ReferenceSelection =
  | { readonly kind: 'AUTO_PREVIOUS_CLOSE' }
  | { readonly kind: 'FIXED'; readonly k: number };
export type SpaceLocation = {
  readonly upper: SpaceLine;
  readonly middle: SpaceLine;
  readonly lower: SpaceLine;
  readonly onBoundary: boolean;
  readonly adjacent: {
    readonly upper: SpaceLine;
    readonly middle: SpaceLine;
    readonly lower: SpaceLine;
  } | null;
};
export type LineObservation = SpaceLine & {
  readonly open: PriceRelation;
  readonly close: PriceRelation;
  readonly rangeIncludes: boolean;
};
export type VideoObservation =
  | { readonly kind: 'pending'; readonly reason: string }
  | {
      readonly kind: 'ready';
      readonly date: string;
      readonly bar: VideoBar;
      readonly scenario: VideoScenario;
      readonly ruler: SpaceRuler;
      readonly startDate: string;
      readonly high: Endpoint & { readonly price: string };
      readonly low: Endpoint & { readonly price: string };
      readonly location: SpaceLocation;
      readonly reference: LineObservation;
      readonly referenceSelection: ReferenceSelection;
      readonly previous: {
        readonly date: string;
        readonly close: string;
      } | null;
      readonly spaceLines: readonly LineObservation[];
    };

function object(value: unknown): Record<string, unknown> {
  if (!isObject(value)) throw Error('数据对象缺失，待核。');
  return value;
}
function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function text(value: unknown): string {
  if (typeof value !== 'string') throw Error('必要字段缺失，待核。');
  return value;
}
function date(value: unknown): string {
  const result = text(value);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(result) ||
    !Number.isFinite(Date.parse(result)) ||
    new Date(result).toISOString().slice(0, 10) !== result
  )
    throw Error('日期无效，待核。');
  return result;
}
function batch(value: unknown): string {
  const result = text(value);
  if (!/^[a-f0-9]{16}$/.test(result)) throw Error('批次无效，待核。');
  return result;
}
function sha(value: unknown): string {
  const result = text(value);
  if (!/^[a-f0-9]{64}$/.test(result)) throw Error('来源指纹无效，待核。');
  return result;
}
function decimal(value: unknown, zero = false): string {
  const result = text(value),
    n = Number(result);
  if (
    !/^\d+(\.\d+)?$/.test(result) ||
    !Number.isFinite(n) ||
    (zero ? n < 0 : n <= 0)
  )
    throw Error('价格或成交量无效，待核。');
  return result;
}
export function parseVideoManifest(value: unknown): VideoManifest {
  const v = object(value);
  if (v.schema !== 'stock.chips.market.manifest.v1')
    throw Error('最新行情数据格式不匹配，待核。');
  const dataset = batch(v.dataset_id);
  return { dataset, sourceDataset: dataset, asOf: date(v.as_of) };
}
export function parseVideoFrame(
  value: unknown,
  code: VideoCode,
  expected: VideoManifest,
  frozen = false,
): Frame {
  const v = object(value),
    source = object(v.source);
  const identityValid = frozen
    ? v.schema === 'stock.golden-projection.daily.stock.v1' &&
      v.code === code &&
      v.source_dataset_id === expected.sourceDataset &&
      source.key ===
        `data/chips/daily/${expected.sourceDataset}/${code}.json` &&
      source.last_bar_date === expected.asOf
    : v.schema === 'stock.chips.market.stock.v1' &&
      v.security_id === code &&
      source.adjustment === '00_UNADJUSTED' &&
      source.url ===
        `https://d.10jqka.com.cn/v6/line/hs_${code}/00/last10000.js`;
  if (
    !identityValid ||
    v.dataset_id !== expected.dataset ||
    v.as_of !== expected.asOf ||
    source.provider !== 'THS_UNADJUSTED'
  )
    throw Error('股票、数据格式、批次、日期或不复权来源不一致，待核。');
  if (!Array.isArray(v.history) || !v.history.length)
    throw Error('日线缺失，待核。');
  const history = v.history.map((raw: unknown): VideoBar => {
    const b = object(raw);
    const bar = {
      date: date(b.date),
      open: decimal(b.open),
      high: decimal(b.high),
      low: decimal(b.low),
      close: decimal(b.close),
      volume_shares: decimal(b.volume_shares, true),
    };
    if (
      Number(bar.low) > Math.min(Number(bar.open), Number(bar.close)) ||
      Number(bar.high) < Math.max(Number(bar.open), Number(bar.close)) ||
      Number(bar.low) > Number(bar.high)
    )
      throw Error('OHLC范围不一致，待核。');
    return bar;
  });
  if (
    history.some((b, i) => i > 0 && b.date <= history[i - 1].date) ||
    history.at(-1)?.date !== expected.asOf
  )
    throw Error('日线日期重复、倒序或末日不一致，待核。');
  return {
    ...expected,
    code,
    history,
    provider: source.provider,
    sourceSha: sha(source.sha256),
  };
}
export async function verifyVideoStock({
  code,
  frozenInput,
  latestInput,
  manifest,
}: {
  code: VideoCode;
  frozenInput: unknown;
  latestInput: unknown;
  manifest: VideoManifest;
}): Promise<VerifiedVideoStock> {
  const registry = videoStocks.find((s) => s.code === code);
  if (!registry) throw Error('股票未登记，待核。');
  const frozen = parseVideoFrame(
    frozenInput,
    code,
    {
      dataset: FROZEN_DATASET,
      sourceDataset: FROZEN_SOURCE,
      asOf: '2026-11-04',
    },
    true,
  );
  const tuples = frozen.history.map((b) => [
    b.date,
    b.open,
    b.high,
    b.low,
    b.close,
    b.volume_shares,
  ]);
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(JSON.stringify(tuples)),
  );
  const historySha = Array.from(new Uint8Array(digest), (b) =>
    b.toString(16).padStart(2, '0'),
  ).join('');
  if (
    frozen.sourceSha !== registry.sourceSha ||
    historySha !== registry.historySha ||
    frozen.history.length !== 120
  )
    throw Error('冻结原尺来源或历史指纹不匹配，待核。');
  const latest = parseVideoFrame(latestInput, code, manifest);
  if (latest.asOf < frozen.asOf) throw Error('最新数据早于冻结原尺，待核。');
  let overlap = 0;
  for (const b of latest.history) {
    const old = frozen.history.find((item) => item.date === b.date);
    if (!old) continue;
    overlap++;
    if (
      Number(b.open) !== Number(old.open) ||
      Number(b.high) !== Number(old.high) ||
      Number(b.low) !== Number(old.low) ||
      Number(b.close) !== Number(old.close)
    )
      throw Error('重叠历史OHLC发生修订，待核；不自动换尺。');
  }
  if (!overlap) throw Error('新旧行情无重叠，无法核对口径，待核。');
  for (const scenario of videoScenarios.filter((s) => s.code === code)) {
    for (const endpoint of [scenario.high, scenario.low]) {
      const original = frozen.history.find((b) => b.date === endpoint.date),
        current = latest.history.find((b) => b.date === endpoint.date);
      if (
        !original ||
        (current &&
          Number(current[endpoint.field]) !== Number(original[endpoint.field]))
      )
        throw Error('固定端点缺失或修订，待核。');
    }
    const h = frozen.history.find((b) => b.date === scenario.high.date),
      l = frozen.history.find((b) => b.date === scenario.low.date);
    if (!h || !l || Number(h.high) <= Number(l.low))
      throw Error('原尺高低关系无效，待核。');
  }
  return {
    code,
    asOf: latest.asOf,
    dataset: latest.dataset,
    sourceDataset: latest.sourceDataset,
    frozen,
    history: latest.history,
  };
}
export async function loadVideoSpace(
  code: VideoCode,
  { signal }: { signal: AbortSignal },
): Promise<VerifiedVideoStock> {
  const base = (process.env.NEXT_PUBLIC_DATA_BASE || '').replace(/\/$/, '');
  const read = (path: string) =>
    authRead(base + path, { signal, cache: 'no-store' }, async (response) => {
      if (!response.ok)
        throw Error(
          response.status === 401
            ? '登录已失效，请重新登录。'
            : response.status === 404
              ? '必要行情端点不存在，待核。'
              : `行情读取失败（HTTP ${response.status}），待核。`,
        );
      const input: unknown = await response.json();
      return input;
    });
  const manifest = parseVideoManifest(
    await read('/data/chips/daily/manifest.json'),
  );
  const [frozenInput, latestInput] = await Promise.all([
    read(`${PREFIX}${FROZEN_DATASET}/${code}.json`),
    read(`/data/chips/daily/${manifest.dataset}/${code}.json`),
  ]);
  return verifyVideoStock({ code, manifest, frozenInput, latestInput });
}
export function boundaryAt(ruler: SpaceRuler, k: number): number {
  return ruler.H * Math.pow(ruler.L / ruler.H, k);
}
function coordinate(ruler: SpaceRuler, price: number): number {
  return Math.log(price / ruler.H) / Math.log(ruler.L / ruler.H);
}
const EPS = 1e-10;
function line(ruler: SpaceRuler, k: number): SpaceLine {
  return { k, price: boundaryAt(ruler, k) };
}
function cell(ruler: SpaceRuler, k: number) {
  return {
    upper: line(ruler, k),
    middle: line(ruler, k + 0.5),
    lower: line(ruler, k + 1),
  };
}
export function locateSpace(ruler: SpaceRuler, price: number): SpaceLocation {
  const u = coordinate(ruler, price),
    rounded = Math.round(u),
    onBoundary = Math.abs(u - rounded) <= EPS,
    k = onBoundary ? rounded : Math.floor(u);
  return {
    ...cell(ruler, k),
    onBoundary,
    adjacent: onBoundary ? cell(ruler, k - 1) : null,
  };
}
export function relate(
  price: number,
  reference: number,
  bandPercent = 0.5,
): PriceRelation {
  const distancePercent = (price / reference - 1) * 100;
  return {
    side:
      Math.abs(distancePercent) <= EPS
        ? 'ON'
        : distancePercent > 0
          ? 'ABOVE'
          : 'BELOW',
    inBand: Math.abs(distancePercent) <= bandPercent + EPS,
    distancePercent,
  };
}
export function nearestReference(
  ruler: SpaceRuler,
  previousClose: number,
): SpaceLine {
  const u = coordinate(ruler, previousClose) * 2,
    lower = Math.floor(u),
    upper = Math.ceil(u);
  const k = (u - lower <= upper - u + EPS ? lower : upper) / 2;
  return line(ruler, k);
}
export function observeVideoDay(
  stock: VerifiedVideoStock,
  scenarioId: string,
  selectedDate: string,
  selection: ReferenceSelection,
): VideoObservation {
  const scenario = videoScenarios.find(
    (s) => s.code === stock.code && s.id === scenarioId,
  );
  if (!scenario) return { kind: 'pending', reason: '场景未登记，待核。' };
  const h = stock.frozen.history.find((b) => b.date === scenario.high.date),
    l = stock.frozen.history.find((b) => b.date === scenario.low.date);
  if (!h || !l) return { kind: 'pending', reason: '冻结端点缺失，待核。' };
  const later =
    scenario.high.date > scenario.low.date
      ? scenario.high.date
      : scenario.low.date;
  const startDate = stock.frozen.history.find((b) => b.date > later)?.date;
  if (!startDate || selectedDate < startDate)
    return {
      kind: 'pending',
      reason: '原尺尚未形成；晚端点后的下一实际交易日起才判读。',
    };
  const index = stock.history.findIndex((b) => b.date === selectedDate),
    bar = stock.history[index],
    previous = stock.history[index - 1] ?? null;
  if (!bar)
    return { kind: 'pending', reason: '所选交易日没有已保存行情，待核。' };
  if (selection.kind === 'AUTO_PREVIOUS_CLOSE' && !previous)
    return {
      kind: 'pending',
      reason: '缺少前一根交易日收盘，自动参考线待核。',
    };
  if (
    selection.kind === 'FIXED' &&
    (!Number.isFinite(selection.k) || !Number.isInteger(selection.k * 2))
  )
    return { kind: 'pending', reason: '固定k须为有限整数或半整数。' };
  const ruler = { H: Number(h.high), L: Number(l.low) },
    location = locateSpace(ruler, Number(bar.close));
  const reference =
    selection.kind === 'FIXED'
      ? line(ruler, selection.k)
      : nearestReference(ruler, Number(previous?.close));
  if (!(reference.price > 0 && Number.isFinite(reference.price)))
    return { kind: 'pending', reason: '参考线超出可计算价格范围，待核。' };
  const observe = (item: SpaceLine): LineObservation => ({
    ...item,
    open: relate(Number(bar.open), item.price),
    close: relate(Number(bar.close), item.price),
    rangeIncludes:
      Number(bar.low) <= item.price * (1 + Number.EPSILON * 8) &&
      Number(bar.high) >= item.price * (1 - Number.EPSILON * 8),
  });
  return {
    kind: 'ready',
    date: selectedDate,
    bar,
    scenario,
    ruler,
    startDate,
    high: { ...scenario.high, price: h.high },
    low: { ...scenario.low, price: l.low },
    location,
    reference: observe(reference),
    referenceSelection: selection,
    previous: previous ? { date: previous.date, close: previous.close } : null,
    spaceLines: [location.upper, location.middle, location.lower].map(observe),
  };
}
