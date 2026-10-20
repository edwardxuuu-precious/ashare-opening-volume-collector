import { combineDays, selectedDates } from './stock.ts';
import { projectDayForBrowser } from './browser-data.ts';
import { readResponse } from './read-request.ts';
import type { DayData, Filters, Manifest } from './stock.ts';
export type LoadProgress = {
  stage: 'checking' | 'queued' | 'collecting' | 'reading';
  completed: number;
  total: number;
  unit: 'stocks' | 'dates';
  message: string;
};
type Job = {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  completedStocks: number;
  totalStocks: number;
  message?: string;
  resultDates?: string[];
};
const CACHE_DAYS = 12;
type ResultSnapshot = {
  result: WeakRef<DayData>;
  entries: Map<string, { key: string; metadata: Omit<DayData, 'rows'> }>;
};
// Reuse a range still held by the caller without keeping an additional range alive.
const snapshots = new WeakMap<Map<string, DayData>, ResultSnapshot>();
export async function loadQuery(
  manifest: Manifest,
  filters: Filters,
  options: {
    signal: AbortSignal;
    dataBase: string;
    apiBase: string;
    progress: (state: LoadProgress) => void;
    fetcher?: typeof fetch;
    pollMs?: number;
    requestTimeoutMs?: number;
    cache?: Map<string, DayData>;
  },
): Promise<DayData> {
  const fetcher = options.fetcher ?? fetch,
    signal = options.signal,
    entries = selectedDates(manifest, filters);
  const progress = options.progress;
  async function withResponse<T>(
    url: string,
    init: RequestInit,
    consume: (response: Response) => Promise<T>,
  ) {
    return readResponse(
      url,
      { ...init, signal: init.signal ?? signal },
      consume,
      {
        fetcher,
        timeoutMs: options.requestTimeoutMs ?? 25000,
      },
    );
  }

  async function request(path: string, init: RequestInit = {}) {
    return withResponse(path, { ...init, cache: 'no-cache' }, async (res) => {
      if (!res.ok) {
        let message = '数据服务暂时不可用，请重试。';
        try {
          const data = (await res.json()) as { error?: string };
          message = data.error || message;
        } catch {}
        throw Error(`${message}（HTTP ${res.status}）`);
      }
      return res.json();
    });
  }
  if (filters.stockScope !== 'all' && !filters.symbols.length)
    return {
      date: entries[0].date,
      dates: entries.map((e) => e.date),
      generatedAt: manifest.generatedAt,
      source: manifest.source,
      scope: 'sample',
      total: 0,
      rows: [],
      methodology: manifest.methodology,
    };
  progress({
    stage: 'checking',
    completed: 0,
    total: entries.length,
    unit: 'dates',
    message: '正在检查所选日期的已保存数据…',
  });
  const versions = new Map(
    entries.map((entry) => [
      entry.date,
      (entry as typeof entry & { generatedAt?: string }).generatedAt ||
        manifest.generatedAt,
    ]),
  );
  const cacheKey = (date: string) =>
    `${options.dataBase}|${versions.get(date) || manifest.generatedAt}|${date}`;
  const previous = options.cache?.size
    ? snapshots.get(options.cache)
    : undefined;
  const previousResult = previous?.result.deref();
  let previousDays: Map<string, DayData['rows']> | undefined;
  function fromCurrentResult(date: string) {
    const entry = previous?.entries.get(date);
    if (!previousResult || !entry || entry.key !== cacheKey(date))
      return undefined;
    if (!previousDays) {
      previousDays = new Map();
      for (const row of previousResult.rows) {
        if (!row.date) continue;
        const rows = previousDays.get(row.date) ?? [];
        rows.push(row);
        previousDays.set(row.date, rows);
      }
    }
    return { ...entry.metadata, rows: previousDays.get(date) ?? [] };
  }
  function remember(data: DayData) {
    const cache = options.cache;
    if (!cache) return;
    for (const [key, saved] of cache)
      if (saved.date === data.date && key.startsWith(options.dataBase + '|'))
        cache.delete(key);
    cache.set(cacheKey(data.date), data);
    while (cache.size > CACHE_DAYS) cache.delete(cache.keys().next().value!);
  }
  let completed = 0;
  const loaded = new Map<string, DayData>(),
    missing = new Set<string>();
  let cursor = 0;
  const readController = new AbortController();
  const cancelReads = () => readController.abort();
  signal.addEventListener('abort', cancelReads, { once: true });
  if (signal.aborted) cancelReads();
  async function readWorker() {
    while (cursor < entries.length) {
      const entry = entries[cursor++];
      if (readController.signal.aborted)
        throw new DOMException('Aborted', 'AbortError');
      let data =
        options.cache?.get(cacheKey(entry.date)) ??
        fromCurrentResult(entry.date);
      if (data) {
        data = projectDayForBrowser(data);
        remember(data);
      }
      if (!data && entry.status !== 'not_downloaded') {
        data = await withResponse(
          options.dataBase + '/data/' + encodeURIComponent(entry.file),
          { signal: readController.signal, cache: 'no-cache' },
          async (res) => {
            if (res.status === 404) return undefined;
            if (!res.ok)
              throw Error(
                `${entry.date} 读取失败（HTTP ${res.status}），请重试。`,
              );
            return (await res.json()) as DayData;
          },
        );
        if (data) {
          if (data.date !== entry.date || !Array.isArray(data.rows))
            throw Error('行情数据格式不正确。');
          data = projectDayForBrowser(data);
          remember(data);
        }
      }
      if (!data) missing.add(entry.date);
      else {
        loaded.set(entry.date, data);
        const relevant =
          filters.stockScope === 'all'
            ? data.rows
            : data.rows.filter((r) => filters.symbols.includes(r.code));
        if (
          options.apiBase &&
          (filters.stockScope === 'all'
            ? data.scope !== 'full'
            : relevant.length !== filters.symbols.length)
        )
          missing.add(entry.date);
        if (
          options.apiBase &&
          relevant.some(
            (r) => r.reason === '尚未采集' || r.reason?.startsWith('采集失败'),
          )
        )
          missing.add(entry.date);
      }
      completed++;
      progress({
        stage: 'checking',
        completed,
        total: entries.length,
        unit: 'dates',
        message: `已检查 ${completed} / ${entries.length} 个交易日的缓存`,
      });
    }
  }
  const readers = Array.from({ length: Math.min(2, entries.length) }, () =>
    readWorker(),
  );
  try {
    await Promise.all(readers);
  } catch (error) {
    cancelReads();
    await Promise.allSettled(readers);
    throw error;
  } finally {
    signal.removeEventListener('abort', cancelReads);
  }
  if (missing.size) {
    if (!options.apiBase)
      throw Error(
        `${[...missing].sort().join('、')} 尚未下载。自动下载服务尚未接通，请先选择有圆点的日期。`,
      );
    progress({
      stage: 'queued',
      completed: 0,
      total: 0,
      unit: 'stocks',
      message: '正在提交缺失日期的下载任务…',
    });
    let job = (await request(options.apiBase + '/api/query-jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        dates: [...missing].sort(),
        scope: filters.stockScope,
        symbols: filters.stockScope === 'all' ? [] : filters.symbols,
      }),
    })) as Job;
    const started = Date.now();
    while (job.status === 'queued' || job.status === 'running') {
      progress({
        stage: job.status === 'queued' ? 'queued' : 'collecting',
        completed: job.completedStocks,
        total: job.totalStocks,
        unit: 'stocks',
        message:
          job.message ||
          (job.status === 'queued'
            ? '任务已排队，等待获取行情…'
            : `已处理 ${job.completedStocks} / ${job.totalStocks} 只股票`),
      });
      await new Promise<void>((resolve, reject) => {
        const abort = () => {
          clearTimeout(timer);
          reject(new DOMException('Aborted', 'AbortError'));
        };
        const timer = setTimeout(() => {
          signal.removeEventListener('abort', abort);
          resolve();
        }, options.pollMs ?? 2000);
        signal.addEventListener('abort', abort, { once: true });
        if (signal.aborted) abort();
      });
      if (Date.now() - started > 6 * 60 * 60 * 1000)
        throw Error('下载任务仍在运行，请稍后重新查询。');
      job = (await request(
        options.apiBase + '/api/query-jobs/' + encodeURIComponent(job.id),
      )) as Job;
    }
    if (job.status !== 'completed')
      throw Error(job.message || '行情下载失败，请重试。');
    let received = 0;
    progress({
      stage: 'reading',
      completed: 0,
      total: missing.size,
      unit: 'dates',
      message: '下载完成，正在读取结果…',
    });
    for (const date of missing) {
      let data = (await request(
        options.apiBase + '/data/' + date + '.json',
      )) as DayData;
      if (data.date !== date || !Array.isArray(data.rows))
        throw Error('下载结果未准备好，请重试。');
      data = projectDayForBrowser(data);
      loaded.set(date, data);
      remember(data);
      received++;
      progress({
        stage: 'reading',
        completed: received,
        total: missing.size,
        unit: 'dates',
        message: `已读取 ${received} / ${missing.size} 个下载结果`,
      });
    }
  }
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
  const result = combineDays(entries.map((e) => loaded.get(e.date)!));
  if (options.cache)
    snapshots.set(options.cache, {
      result: new WeakRef(result),
      entries: new Map(
        [...loaded].map(([date, data]) => {
          const { rows: _rows, ...metadata } = data;
          return [date, { key: cacheKey(date), metadata }];
        }),
      ),
    });
  return result;
}
