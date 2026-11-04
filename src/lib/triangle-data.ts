import { authFetch } from './auth-fetch.ts';
import { readResponse } from './read-request.ts';
import type { PriceBar } from './triangles.ts';

export type TriangleSecurity = { code: string; name: string; market: string };
export type TriangleCatalog = {
  generatedAt: string;
  sessions: string[];
  saved: Map<string, string>;
  securities: TriangleSecurity[];
  universeTotal: number | null;
  calendarKnown: boolean;
  stockListNotice: string;
};
export type TriangleDataset = {
  key: string;
  asOf: string;
  sessions: string[];
  series: { security: TriangleSecurity; bars: PriceBar[] }[];
  priceBasis: 'unadjusted';
  coverage: {
    loadedDays: number;
    requestedDays: number;
    requestedHistory: number;
    failedDates: { date: string; reason: string }[];
    calendarKnown: boolean;
    universeTotal: number | null;
    completePriceSeries: number;
    validPriceBars: number;
  };
};
export type TriangleSelection =
  | { kind: 'all' }
  | { kind: 'symbols'; symbols: string[] };
type ReadOptions = {
  dataBase: string;
  signal: AbortSignal;
  fetcher?: typeof fetch;
  timeoutMs?: number;
  retryBaseMs?: number;
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function dayKey(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    Number.isFinite(Date.parse(value)) &&
    new Date(value).toISOString().slice(0, 10) === value
  );
}
function positive(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0;
}
function count(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

class AccessError extends Error {}

async function read<T>(
  path: string,
  options: ReadOptions,
  parse: (value: unknown) => T,
) {
  return readResponse(
    options.dataBase.replace(/\/$/, '') + path,
    { method: 'GET', signal: options.signal, cache: 'no-store' },
    async (response) => {
      if (response.status === 401 || response.status === 403)
        throw new AccessError(
          response.status === 401
            ? '登录已失效，请重新登录。'
            : '当前账号无权读取所选行情。',
        );
      if (!response.ok)
        throw Error(`行情读取失败（HTTP ${response.status}）。`);
      return parse(await response.json());
    },
    {
      fetcher: options.fetcher ?? authFetch,
      timeoutMs: options.timeoutMs,
      retryBaseMs: options.retryBaseMs,
    },
  );
}

export function parseTriangleCatalog(value: unknown): TriangleCatalog {
  if (!record(value) || !Array.isArray(value.dates))
    throw Error('日行情目录格式不正确。');
  const saved = new Map<string, string>();
  for (const entry of value.dates) {
    if (
      !record(entry) ||
      !dayKey(entry.date) ||
      entry.file !== `${entry.date}.json`
    )
      throw Error('日行情目录包含无效日期或文件路径。');
    if (saved.has(entry.date)) throw Error('日行情目录包含重复日期。');
    if (entry.status !== 'not_downloaded') saved.set(entry.date, entry.file);
  }
  const calendarKnown =
    Array.isArray(value.tradingDates) && value.tradingDates.length > 0;
  const calendar =
    calendarKnown && Array.isArray(value.tradingDates)
      ? value.tradingDates
      : [...saved.keys()];
  if (!calendar.every(dayKey)) throw Error('交易日历包含无效日期。');
  const sessions = [...new Set([...calendar, ...saved.keys()])].sort();
  if (!sessions.length) throw Error('当前没有可查询的交易日。');
  return {
    generatedAt: typeof value.generatedAt === 'string' ? value.generatedAt : '',
    sessions,
    saved,
    securities: [],
    universeTotal: count(value.universeTotal) ? value.universeTotal : null,
    calendarKnown,
    stockListNotice: '',
  };
}

function parseSecurities(value: unknown): TriangleSecurity[] {
  if (!record(value) || !Array.isArray(value.rows))
    throw Error('股票名单格式不正确。');
  const codes = new Set<string>();
  return value.rows.map((row) => {
    if (
      !record(row) ||
      typeof row.code !== 'string' ||
      !/^\d{6}$/.test(row.code) ||
      typeof row.name !== 'string' ||
      codes.has(row.code)
    )
      throw Error('股票名单包含无效或重复记录。');
    codes.add(row.code);
    return {
      code: row.code,
      name: row.name,
      market: typeof row.market === 'string' ? row.market : '',
    };
  });
}

export async function loadTriangleCatalog(
  options: ReadOptions,
): Promise<TriangleCatalog> {
  const results = await Promise.allSettled([
    read('/data/manifest.json', options, parseTriangleCatalog),
    read('/data/stocks.json', options, parseSecurities),
  ]);
  const [manifest, stocks] = results;
  if (manifest.status === 'rejected') throw manifest.reason;
  if (stocks.status === 'fulfilled') manifest.value.securities = stocks.value;
  else {
    if (stocks.reason instanceof AccessError) throw stocks.reason;
    manifest.value.stockListNotice =
      '股票名单暂时不可用，可输入代码或按已保存行情筛选。';
  }
  return manifest.value;
}

export function triangleDatasetKey(
  asOf: string,
  historySessions: number,
  selection: TriangleSelection,
) {
  return `${asOf}|${historySessions}|${selection.kind === 'all' ? 'all' : [...new Set(selection.symbols)].sort().join(',')}`;
}

type CompactDay = { security: TriangleSecurity; bar: PriceBar }[];
function parseDay(
  value: unknown,
  date: string,
  wanted: Set<string> | null,
): CompactDay {
  if (!record(value) || value.date !== date || !Array.isArray(value.rows))
    throw Error('行情日期或格式不匹配。');
  const codes = new Set<string>();
  const rows: CompactDay = [];
  for (const row of value.rows) {
    if (
      !record(row) ||
      typeof row.code !== 'string' ||
      !/^\d{6}$/.test(row.code) ||
      codes.has(row.code)
    )
      throw Error('日行情包含无效或重复股票。');
    codes.add(row.code);
    if (wanted && !wanted.has(row.code)) continue;
    const security = {
      code: row.code,
      name: typeof row.name === 'string' ? row.name : row.code,
      market: typeof row.market === 'string' ? row.market : '',
    };
    let bar: PriceBar;
    if (row.priceStatus === 'not_traded')
      bar = { kind: 'gap', date, reason: 'not-traded' };
    else if (
      row.priceStatus === 'available' &&
      positive(row.open) &&
      positive(row.high) &&
      positive(row.low) &&
      positive(row.close) &&
      row.low <= row.open &&
      row.open <= row.high &&
      row.low <= row.close &&
      row.close <= row.high
    )
      bar = {
        kind: 'bar',
        date,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
      };
    else bar = { kind: 'gap', date, reason: 'invalid-price' };
    rows.push({ security, bar });
  }
  return rows;
}

export async function loadTriangleData(
  options: ReadOptions & {
    catalog: TriangleCatalog;
    asOf: string;
    historySessions: number;
    selection: TriangleSelection;
    onProgress?: (completed: number, total: number) => void;
  },
): Promise<TriangleDataset> {
  const { catalog, asOf, historySessions, selection } = options;
  if (!dayKey(asOf) || !catalog.sessions.includes(asOf))
    throw Error('请选择目录中的交易日。');
  if (
    !Number.isInteger(historySessions) ||
    historySessions < 20 ||
    historySessions > 186
  )
    throw Error('历史长度须为20至186个交易日。');
  const symbols =
    selection.kind === 'symbols' ? [...new Set(selection.symbols)] : [];
  if (
    selection.kind === 'symbols' &&
    (!symbols.length || symbols.some((code) => !/^\d{6}$/.test(code)))
  )
    throw Error('请选择至少一只股票，代码须为六位数字。');
  const sessions = catalog.sessions
    .filter((date) => date <= asOf)
    .slice(-historySessions);
  const wanted = selection.kind === 'symbols' ? new Set(symbols) : null;
  const failedDates: { date: string; reason: string }[] = [];
  const series = new Map<
    string,
    { security: TriangleSecurity; observations: Map<string, PriceBar> }
  >();
  for (const security of catalog.securities) {
    if (!wanted || wanted.has(security.code))
      series.set(security.code, { security, observations: new Map() });
  }
  for (const code of symbols) {
    if (!series.has(code))
      series.set(code, {
        security: { code, name: code, market: '' },
        observations: new Map(),
      });
  }
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal.addEventListener('abort', abort, { once: true });
  if (options.signal.aborted) abort();
  let cursor = 0;
  let completed = 0;
  let loadedDays = 0;
  let validPriceBars = 0;
  async function worker() {
    while (cursor < sessions.length) {
      if (controller.signal.aborted)
        throw new DOMException('Aborted', 'AbortError');
      const date = sessions[cursor++];
      const file = catalog.saved.get(date);
      if (!file) failedDates.push({ date, reason: '该交易日尚未保存。' });
      else {
        try {
          const rows = await read(
            `/data/${file}`,
            { ...options, signal: controller.signal },
            (value) => parseDay(value, date, wanted),
          );
          loadedDays++;
          for (const { security, bar } of rows) {
            let target = series.get(security.code);
            if (!target) {
              target = { security, observations: new Map() };
              series.set(security.code, target);
            }
            if (!target.security.market && security.market)
              target.security = { ...target.security, market: security.market };
            target.observations.set(date, bar);
            if (bar.kind === 'bar') validPriceBars++;
          }
        } catch (error) {
          if (controller.signal.aborted || error instanceof AccessError)
            throw error;
          failedDates.push({
            date,
            reason: error instanceof Error ? error.message : '行情无法读取。',
          });
        }
      }
      completed++;
      options.onProgress?.(completed, sessions.length);
    }
  }
  const workers = Array.from({ length: Math.min(2, sessions.length) }, () =>
    worker(),
  );
  try {
    await Promise.all(workers);
  } catch (error) {
    controller.abort();
    await Promise.allSettled(workers);
    throw error;
  } finally {
    options.signal.removeEventListener('abort', abort);
  }
  if (options.signal.aborted) throw new DOMException('Aborted', 'AbortError');
  const failed = new Set(failedDates.map((entry) => entry.date));
  const histories = [...series.values()]
    .sort((a, b) => a.security.code.localeCompare(b.security.code))
    .map((entry) => ({
      security: entry.security,
      bars: sessions.map(
        (date): PriceBar =>
          entry.observations.get(date) ?? {
            kind: 'gap',
            date,
            reason: failed.has(date) ? 'missing-day' : 'missing-row',
          },
      ),
    }));
  return {
    key: triangleDatasetKey(asOf, historySessions, selection),
    asOf,
    sessions,
    series: histories,
    priceBasis: 'unadjusted',
    coverage: {
      loadedDays,
      requestedDays: sessions.length,
      requestedHistory: historySessions,
      failedDates: failedDates.sort((a, b) => a.date.localeCompare(b.date)),
      calendarKnown: catalog.calendarKnown,
      universeTotal: catalog.universeTotal,
      completePriceSeries: histories.filter((entry) =>
        entry.bars.every((bar) => bar.kind === 'bar'),
      ).length,
      validPriceBars,
    },
  };
}
