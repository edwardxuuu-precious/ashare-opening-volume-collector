'use client';
import { ChevronDown, RefreshCw } from 'lucide-react';
import type { Manifest } from '../lib/stock';
import { readResponse } from '../lib/read-request.ts';

export type CollectionState = {
  id: string;
  state: 'running' | 'paused' | 'completed' | 'failed';
  completedStocks: number;
  totalStocks: number;
  latestDate: string;
  latestPublishedDate?: string | null;
  latestPublishedAt?: string | null;
  updatedAt: string;
  message: string;
  phase?: string;
  outcome?: string;
  exitReason?: string;
  calculationPolicy?: string;
  // Stock-date repair tasks, not a count of distinct stocks or verified records.
  pendingCount?: number | null;
  lastSuccessfulUpdate?: string | null;
  targetDate?: string;
  unprocessedCount?: number | null;
  calculableCount?: number | null;
  retryableCount?: number | null;
  noTradeCount?: number | null;
  historicalPendingCount?: number | null;
  openingCachedCount?: number | null;
  openingNoTradeCount?: number | null;
  openingMissingCount?: number | null;
  openingUnprocessedCount?: number | null;
  openingRetryableCount?: number | null;
  openingFirstPassComplete?: boolean;
  nextRetryAt?: string | null;
  firstPassCompletedAt?: string | null;
  fullyPublishedAt?: string | null;
  firstPassComplete?: boolean;
  dataComplete?: boolean;
  priceDataComplete?: boolean;
  openingComplete?: boolean;
  speedDegraded?: boolean;
  publicationCommitted?: boolean;
};

const targetCountFields = [
  'unprocessedCount',
  'calculableCount',
  'retryableCount',
  'noTradeCount',
  'openingCachedCount',
  'openingNoTradeCount',
  'openingMissingCount',
  'openingUnprocessedCount',
  'openingRetryableCount',
] as const;
const timestampFields = [
  'lastSuccessfulUpdate',
  'latestPublishedAt',
  'nextRetryAt',
  'firstPassCompletedAt',
  'fullyPublishedAt',
] as const;
const completionFields = [
  'firstPassComplete',
  'dataComplete',
  'priceDataComplete',
  'openingComplete',
  'openingFirstPassComplete',
  'speedDegraded',
  'publicationCommitted',
] as const;

export function parseCollectionState(raw: unknown): CollectionState | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  if (
    !['running', 'paused', 'completed', 'failed'].includes(
      String(value.state),
    ) ||
    typeof value.id !== 'string' ||
    !value.id ||
    !Number.isSafeInteger(value.completedStocks) ||
    !Number.isSafeInteger(value.totalStocks) ||
    Number(value.completedStocks) < 0 ||
    Number(value.totalStocks) <= 0 ||
    Number(value.completedStocks) > Number(value.totalStocks) ||
    typeof value.latestDate !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}$/.test(value.latestDate) ||
    (value.latestPublishedDate != null &&
      (typeof value.latestPublishedDate !== 'string' ||
        !/^\d{4}-\d{2}-\d{2}$/.test(value.latestPublishedDate))) ||
    typeof value.updatedAt !== 'string' ||
    !Number.isFinite(Date.parse(value.updatedAt)) ||
    typeof value.message !== 'string' ||
    (value.phase !== undefined && typeof value.phase !== 'string') ||
    (value.outcome !== undefined &&
      (typeof value.outcome !== 'string' ||
        ![
          'running',
          'completed',
          'incomplete_checkpointed',
          'failed',
          'no_work',
          'validated',
        ].includes(value.outcome))) ||
    (value.exitReason !== undefined && typeof value.exitReason !== 'string') ||
    (value.calculationPolicy !== undefined &&
      typeof value.calculationPolicy !== 'string') ||
    (value.pendingCount != null &&
      (!Number.isSafeInteger(value.pendingCount) ||
        Number(value.pendingCount) < 0)) ||
    (value.targetDate !== undefined &&
      (typeof value.targetDate !== 'string' ||
        !/^\d{4}-\d{2}-\d{2}$/.test(value.targetDate))) ||
    targetCountFields.some(
      (field) =>
        value[field] != null &&
        (!Number.isSafeInteger(value[field]) ||
          Number(value[field]) < 0 ||
          Number(value[field]) > Number(value.totalStocks)),
    ) ||
    (value.historicalPendingCount != null &&
      (!Number.isSafeInteger(value.historicalPendingCount) ||
        Number(value.historicalPendingCount) < 0)) ||
    timestampFields.some(
      (field) =>
        value[field] != null &&
        (typeof value[field] !== 'string' ||
          !Number.isFinite(Date.parse(value[field] as string))),
    ) ||
    completionFields.some(
      (field) =>
        value[field] !== undefined && typeof value[field] !== 'boolean',
    )
  )
    return null;
  const openingCounts = [
    'openingCachedCount',
    'openingNoTradeCount',
    'openingMissingCount',
  ] as const;
  if (
    openingCounts.every((field) => value[field] != null) &&
    openingCounts.reduce((total, field) => total + Number(value[field]), 0) !==
      value.totalStocks
  )
    return null;
  if (
    [
      'openingMissingCount',
      'openingUnprocessedCount',
      'openingRetryableCount',
    ].every((field) => value[field] != null) &&
    Number(value.openingUnprocessedCount) +
      Number(value.openingRetryableCount) !==
      value.openingMissingCount
  )
    return null;
  if (
    value.openingComplete === true &&
    Number(value.openingMissingCount ?? 0) > 0
  )
    return null;
  return value as CollectionState;
}

/** A single polling loop, with abort covering both response headers and JSON body. */
export function startCollectionPolling(options: {
  dataBase: string;
  fetcher: typeof fetch;
  onStatus: (status: CollectionState | null) => void;
  onManifest: (manifest: Manifest) => void;
  onManifestError?: (message: string) => void;
  pollMs?: number;
  idlePollMs?: number;
  manifestPollMs?: number;
  activeManifestPollMs?: number;
  initialManifestGeneration?: string;
  initialLatestDate?: string;
  isVisible?: () => boolean;
  timeoutMs?: number;
}) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let active: AbortController | undefined;
  let lastGeneration = options.initialManifestGeneration ?? '';
  let knownLatestDate = options.initialLatestDate ?? '';
  let lastManifestReadAt = options.initialManifestGeneration ? Date.now() : 0;
  async function read(path: string) {
    const controller = new AbortController();
    active = controller;
    try {
      return await readResponse(
        options.dataBase + '/data/' + path,
        {
          signal: controller.signal,
          cache: 'no-cache',
        },
        async (response) => {
          if (response.status === 401) {
            stopped = true; // authFetch owns the session-expired event.
            return null;
          }
          if (!response.ok) {
            if (path === 'manifest.json')
              throw Error(
                `数据目录暂时无法读取，请重试。（HTTP ${response.status}）`,
              );
            return null;
          }
          return (await response.json()) as unknown;
        },
        { fetcher: options.fetcher, timeoutMs: options.timeoutMs ?? 12000 },
      );
    } finally {
      active = undefined;
    }
  }
  async function tick() {
    if (options.isVisible && !options.isVisible()) {
      timer = setTimeout(
        () => void tick(),
        options.idlePollMs ?? options.pollMs ?? 60000,
      );
      return;
    }
    let status: CollectionState | null = null;
    try {
      const raw = await read('collection-status.json');
      if (stopped) return;
      status = parseCollectionState(raw);
      options.onStatus(status);
    } catch {
      if (!stopped) options.onStatus(null);
    }
    if (stopped) return;
    const targetDate = status?.targetDate ?? status?.latestDate ?? '';
    const newlyPublishedTarget = Boolean(
      status?.publicationCommitted &&
      targetDate &&
      targetDate > knownLatestDate,
    );
    const activelyPublishing = Boolean(
      status?.state === 'running' &&
      ['daily', 'close', 'catchup'].includes(status.phase ?? ''),
    );
    const manifestInterval = activelyPublishing
      ? (options.activeManifestPollMs ?? 120000)
      : (options.manifestPollMs ?? 300000);
    if (
      newlyPublishedTarget ||
      Date.now() - lastManifestReadAt >= manifestInterval
    ) {
      lastManifestReadAt = Date.now();
      try {
        const raw = await read('manifest.json');
        if (stopped) return;
        if (
          raw &&
          typeof raw === 'object' &&
          Array.isArray((raw as Manifest).dates) &&
          typeof (raw as Manifest).generatedAt === 'string' &&
          (raw as Manifest).generatedAt
        ) {
          const next = raw as Manifest;
          options.onManifestError?.('');
          knownLatestDate = next.dates[0]?.date ?? knownLatestDate;
          if (next.generatedAt !== lastGeneration) {
            lastGeneration = next.generatedAt;
            options.onManifest(next);
          }
        } else {
          options.onManifestError?.('数据目录格式不正确，请重新读取目录。');
        }
      } catch (error) {
        if (!stopped)
          options.onManifestError?.(
            error instanceof Error
              ? error.message
              : '数据目录暂时无法读取，请重试。',
          );
      }
    }
    if (!stopped) {
      const nextPollMs =
        status?.state === 'running'
          ? (options.pollMs ?? 20000)
          : (options.idlePollMs ?? options.pollMs ?? 60000);
      timer = setTimeout(() => void tick(), nextPollMs);
    }
  }
  void tick();
  return () => {
    stopped = true;
    clearTimeout(timer);
    active?.abort();
  };
}

export function collectionQueryMessage(
  error: string,
  status: CollectionState | null,
) {
  if (!status || !error.includes('尚未下载')) return error;
  const dates = error.split(' 尚未下载')[0];
  if (status.targetDate && !dates.includes(status.targetDate))
    return `${dates} 未发布。可查看已保存日期。`;
  if (status.state === 'failed' || status.outcome === 'failed')
    return `${dates} 未发布，采集失败。可查看已保存日期。`;
  if (status.state === 'paused' && status.exitReason === 'source_retry_not_due')
    return `${dates} 未发布，等待下一轮补采。可查看已保存日期。`;
  if (status.state === 'paused' && status.exitReason === 'source_throttled')
    return `${dates} 未发布，等待数据源恢复。可查看已保存日期。`;
  if (status.state === 'paused')
    return `${dates} 未发布，采集已暂停。可查看已保存日期。`;
  if (
    status.phase === 'opening' &&
    dates.includes(status.targetDate ?? status.latestDate)
  )
    return `${dates} 全天数据未发布。上午仅预采并缓存 09:45 开盘量，15:30 后更新全天指标。`;
  if (status.state === 'running')
    return `${dates} 未发布，后台正在采集。可先查看已保存日期。`;
  return `${dates} 未发布，可能不在本轮范围内。请查看已保存日期。`;
}

export function CollectionStatus({
  status,
  latestDate,
}: {
  status: CollectionState | null;
  latestDate?: string | null;
}) {
  let label = '状态暂不可用';
  let tone = 'bg-slate-100 text-slate-600';
  if (status?.state === 'failed' || status?.outcome === 'failed') {
    label = '同步失败';
    tone = 'bg-red-50 text-red-700';
  } else if (status?.state === 'running') {
    label = '同步中';
    tone = 'bg-indigo-50 text-indigo-700';
  } else if (status?.state === 'paused') {
    label =
      status.exitReason === 'source_retry_not_due'
        ? '等待补采'
        : status.exitReason === 'source_throttled'
          ? '等待数据源恢复'
          : status.exitReason === 'source_evidence_required'
            ? '部分数据待补齐'
            : '同步已暂停';
    tone = 'bg-amber-50 text-amber-700';
  } else if (status?.state === 'completed') {
    if (status.phase === 'opening') {
      label =
        status.openingComplete === false ? '开盘数据待补齐' : '等待收盘数据';
      if (status.openingComplete === false) tone = 'bg-amber-50 text-amber-700';
    } else if (
      status.dataComplete === false ||
      status.priceDataComplete === false
    ) {
      label = '部分数据待补齐';
      tone = 'bg-amber-50 text-amber-700';
    } else if (status.publicationCommitted === false) {
      label = '等待发布';
    } else if (status.dataComplete === true) {
      label = '已同步';
      tone = 'bg-emerald-50 text-emerald-700';
    } else {
      label = '本轮同步结束';
    }
  }
  // A polling heartbeat or a processed target date is not a successful sync.
  const lastSyncedAt = status?.lastSuccessfulUpdate ?? status?.fullyPublishedAt;
  const syncTime = lastSyncedAt
    ? new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
      }).format(new Date(lastSyncedAt))
    : null;

  const stateBadge = (
    <span className={`sync-state ${tone}`}>
      {status?.state === 'running' ? (
        <RefreshCw
          size={12}
          className="animate-spin motion-reduce:animate-none"
          aria-hidden="true"
        />
      ) : (
        <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      )}
      {label}
    </span>
  );

  if (latestDate !== undefined)
    return (
      <div aria-label="数据更新信息" aria-live="polite" className="sync-status">
        <details className="sync-details">
          <summary>
            <span className="saved-date">
              行情截至{' '}
              <time dateTime={latestDate ?? undefined}>
                {latestDate || '—'}
              </time>
            </span>
            <ChevronDown size={12} aria-hidden="true" />
          </summary>
          <div className="sync-detail-panel">
            <strong>数据更新</strong>
            <span>行情日期：{latestDate || '暂无已保存行情'}</span>
            <span>
              最后成功同步：
              {syncTime ? (
                <time dateTime={lastSyncedAt ?? undefined}>{syncTime}</time>
              ) : (
                '暂无记录'
              )}
            </span>
            {stateBadge}
          </div>
        </details>
        {label !== '已同步' && stateBadge}
      </div>
    );

  return (
    <output
      aria-label="数据同步状态"
      aria-live="polite"
      className="sync-status"
    >
      <span className="sync-timestamp">
        <span className="sync-label">最后同步：</span>
        {syncTime ? (
          <time
            dateTime={lastSyncedAt ?? undefined}
            className="whitespace-nowrap tabular-nums text-slate-700"
          >
            {syncTime}
          </time>
        ) : (
          <span className="text-slate-700">暂无记录</span>
        )}
      </span>
      {stateBadge}
    </output>
  );
}
