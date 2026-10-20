export type SpecialStatus = {
  type:
    | 'pending_delisting'
    | 'merger_pending_delisting'
    | 'merger'
    | 'major_restructuring'
    | 'control_change'
    | 'suspension';
  label: string;
  description: string;
  startedAt: string;
  source: string;
  announcementTitle?: string;
  announcementUrl?: string;
};

export type StockRow = {
  date?: string;
  code: string;
  name: string;
  market: string;
  first15Volume: number | null;
  dailyVolume: number | null;
  ratio: number | null;
  pctChange?: number | null;
  amplitude?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close?: number | null;
  priceStatus?: 'available' | 'not_traded' | 'missing';
  priceSourceProvider?: 'sina' | 'tencent';
  status: string;
  quality?: string;
  qualityNote?: string;
  referenceRatio?: number | null;
  dayVolumeDifference?: number;
  sourceProvider?: 'sina' | 'tencent' | 'eastmoney' | 'baostock';
  verificationVersion?: number;
  verificationState?: string;
  reconciliationAttempts?: {
    source: string;
    stage: string;
    status: string;
    reason?: string;
  }[];
  reason?: string;
  specialStatus?: SpecialStatus;
};
export function providerLabel(provider: string | undefined): string {
  switch (provider) {
    case 'sina':
      return '新浪';
    case 'tencent':
      return '腾讯';
    case 'eastmoney':
      return '东方财富';
    case 'baostock':
      return 'BaoStock';
    default:
      return '未知来源';
  }
}
export type DayData = {
  date: string;
  dates?: string[];
  generatedAt: string;
  source: string;
  scope: string;
  total: number;
  universeTotal?: number;
  rows: StockRow[];
  methodology: string;
  priceSchemaVersion?: number;
  priceFields?: ['open', 'high', 'low', 'close'];
  priceAvailable?: number;
  priceNoTrade?: number;
  priceMissing?: number;
  priceDataComplete?: boolean;
  specialStatusExplained?: number;
  specialStatusUnexplained?: number;
  statusExplanationComplete?: boolean;
};
export type Manifest = {
  generatedAt: string;
  source: string;
  scope?: string;
  universeTotal?: number;
  methodology: string;
  automationStatus?: string;
  tradingDates?: string[];
  retentionMonths?: number;
  retentionStart?: string;
  calendarAsOf?: string;
  dates: {
    date: string;
    file: string;
    status: string;
    total: number;
    valid: number;
    missing: number;
    unverified?: number;
    suspended: number;
    priceSchemaVersion?: number;
    priceAvailable?: number;
    priceNoTrade?: number;
    priceMissing?: number;
    priceDataComplete?: boolean;
    specialStatusExplained?: number;
    specialStatusUnexplained?: number;
    statusExplanationComplete?: boolean;
  }[];
};
export type Filters = {
  date: string;
  endDate: string;
  mode: 'single' | 'multiple' | 'range';
  dates: string[];
  stockScope: 'single' | 'multiple' | 'all';
  symbols: string[];
  query: string;
  market: string;
  min: number;
  includeUnverified: boolean;
  order: 'asc' | 'desc' | 'date_desc' | 'date_asc';
};
export function shouldFollowLatest(params: URLSearchParams): boolean {
  if (params.get('latest') === '1') return true;
  return !['date', 'from', 'to', 'dates'].some((key) => params.has(key));
}
export function advanceLatestSelection(
  filters: Filters,
  previousLatest: string,
  nextLatest: string,
  followsLatest: boolean,
): Filters {
  if (
    !followsLatest ||
    !previousLatest ||
    !nextLatest ||
    previousLatest === nextLatest ||
    filters.mode !== 'single' ||
    filters.date !== previousLatest ||
    filters.endDate !== previousLatest
  )
    return filters;
  return { ...filters, date: nextLatest, endDate: nextLatest };
}
export function parseFilters(p: URLSearchParams): Filters {
  const min = Number(p.get('min'));
  const start = p.get('from') || p.get('date') || '';
  const end = p.get('to') || '';
  const range = !!p.get('from') || !!p.get('to');
  const legacy = (p.get('q') ?? '').slice(0, 80);
  const symbols = [
    ...new Set(
      (p.get('symbols') ?? (/^\d{6}$/.test(legacy) ? legacy : ''))
        .split(',')
        .filter(Boolean),
    ),
  ];
  const scope = p.get('scope');
  const stockScope =
    scope === 'all' || scope === 'single' || scope === 'multiple'
      ? scope
      : symbols.length > 1
        ? 'multiple'
        : symbols.length
          ? 'single'
          : 'all';
  const multipleDates = p.has('dates');
  return {
    dates: [
      ...new Set((p.get('dates') ?? '').split(',').filter(Boolean)),
    ].sort(),
    stockScope,
    includeUnverified: p.get('unverified') === '1',
    symbols: stockScope === 'all' ? [] : symbols,
    date: /^\d{4}-\d{2}-\d{2}$/.test(start) ? start : '',
    endDate: /^\d{4}-\d{2}-\d{2}$/.test(end) ? end : '',
    mode: multipleDates ? 'multiple' : range ? 'range' : 'single',
    query: /^\d{6}$/.test(legacy) ? '' : legacy,
    market: ['SH', 'SZ', 'BJ'].includes(p.get('market') ?? '')
      ? p.get('market')!
      : 'all',
    min: Number.isFinite(min) ? Math.max(0, Math.min(100, min)) : 0,
    order: ['asc', 'desc', 'date_asc', 'date_desc'].includes(
      p.get('order') || '',
    )
      ? (p.get('order') as Filters['order'])
      : range || multipleDates
        ? 'date_desc'
        : 'desc',
  };
}
// Derive the display value from downloaded volumes, including older snapshots.
// Source reconciliation metadata has no role in availability or ranking.
export function normalizeDownloadedRow(row: StockRow): StockRow {
  const first = row.first15Volume ?? null;
  const daily = row.dailyVolume ?? null;
  const calculable =
    /^\d{6}$/.test(row.code) &&
    typeof first === 'number' && Number.isFinite(first) && first >= 0 &&
    typeof daily === 'number' && Number.isFinite(daily) && daily > 0 &&
    first <= daily;
  const ratio = calculable ? (first / daily) * 100 : null;
  const status = calculable ? 'ok' : row.status === 'suspended' ? 'suspended' : 'missing';
  return row.first15Volume === first && row.dailyVolume === daily &&
    row.ratio === ratio && row.status === status
    ? row
    : { ...row, first15Volume: first, dailyVolume: daily, ratio, status };
}
export function filterRows(rows: StockRow[], f: Filters): StockRow[] {
  const q = f.query.trim().toLowerCase();
  const codes = new Set(f.symbols);
  if (f.stockScope === 'single' && codes.size !== 1) return [];
  return rows
    .map(normalizeDownloadedRow)
    .filter(
      (r) =>
        (f.stockScope === 'all' || codes.has(r.code)) &&
        (!q || r.name.toLowerCase().includes(q) || r.code.includes(q)) &&
        (f.market === 'all' || r.market === f.market) &&
        (!f.min || (r.ratio !== null && r.ratio >= f.min)),
    )
    .sort((a, b) => {
      if (f.order === 'date_desc' || f.order === 'date_asc') {
        const cmp = (a.date || '').localeCompare(b.date || '');
        return (
          (f.order === 'date_desc' ? -cmp : cmp) || a.code.localeCompare(b.code)
        );
      }
      if (a.ratio === null && b.ratio === null)
        return a.code.localeCompare(b.code);
      if (a.ratio === null) return 1;
      if (b.ratio === null) return -1;
      return (
        (f.order === 'desc' ? b.ratio - a.ratio : a.ratio - b.ratio) ||
        a.code.localeCompare(b.code) ||
        (b.date || '').localeCompare(a.date || '')
      );
    });
}
export function formatVolume(n: number | null): string {
  if (n === null || !Number.isFinite(n)) return '—';
  if (n >= 1e8) return (n / 1e8).toFixed(2) + ' 亿';
  if (n >= 1e4) return (n / 1e4).toFixed(2) + ' 万';
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

export function formatPct(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  const sign = n > 0 ? '+' : '';
  return sign + n.toFixed(2) + '%';
}

export function formatPrice(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n) || n <= 0) return '—';
  return n.toFixed(2);
}

export function pctTone(n: number | null | undefined): 'up' | 'down' | 'flat' {
  if (n === null || n === undefined || !Number.isFinite(n) || n === 0) return 'flat';
  return n > 0 ? 'up' : 'down';
}

export function queryableDates(manifest: Manifest): string[] {
  return [
    ...new Set([
      ...(manifest.tradingDates ?? []),
      ...manifest.dates.map((d) => d.date),
    ]),
  ].sort();
}

export function selectedDates(
  manifest: Manifest,
  filters: Pick<Filters, 'date' | 'endDate' | 'mode' | 'dates'>,
) {
  const known = queryableDates(manifest);
  let wanted: string[];
  if (filters.mode === 'multiple') wanted = [...new Set(filters.dates)].sort();
  else {
    const end = filters.mode === 'range' ? filters.endDate : filters.date;
    if (!filters.date || !end) throw Error('请选择日期。');
    if (filters.date > end) throw Error('开始日期不能晚于结束日期。');
    if (
      ![filters.date, end].every(
        (d) =>
          /^\d{4}-\d{2}-\d{2}$/.test(d) &&
          !Number.isNaN(Date.parse(d)) &&
          new Date(d).toISOString().slice(0, 10) === d,
      )
    )
      throw Error('请输入有效日期。');
    wanted =
      filters.mode === 'single'
        ? [filters.date]
        : known.filter((d) => d >= filters.date && d <= end);
  }
  if (!wanted.length) throw Error('请选择至少一个已收盘交易日。');
  if (wanted.length > 186)
    throw Error('一次最多查询 186 个交易日，请分段查询。');
  const invalid = wanted.filter((date) => !known.includes(date));
  if (invalid.length)
    throw Error('以下日期不在可查询的交易日内：' + invalid.join('、'));
  return wanted.map(
    (date) =>
      manifest.dates.find((d) => d.date === date) ?? {
        date,
        file: date + '.json',
        status: 'not_downloaded',
        total: 0,
        valid: 0,
        missing: 0,
        suspended: 0,
      },
  );
}
export function queryParams(filters: Filters) {
  const p = new URLSearchParams();
  if (filters.mode === 'multiple') {
    p.set('dates', [...new Set(filters.dates)].sort().join(','));
  } else if (filters.mode === 'range') {
    if (filters.date) p.set('from', filters.date);
    if (filters.endDate) p.set('to', filters.endDate);
  } else if (filters.date) p.set('date', filters.date);
  p.set('scope', filters.stockScope);
  if (filters.stockScope !== 'all' && filters.symbols.length)
    p.set('symbols', [...new Set(filters.symbols)].join(','));
  if (filters.query) p.set('q', filters.query);
  if (filters.includeUnverified) p.set('unverified', '1');
  if (filters.market !== 'all') p.set('market', filters.market);
  if (filters.min) p.set('min', String(filters.min));
  const defaultOrder = filters.mode !== 'single' ? 'date_desc' : 'desc';
  if (filters.order !== defaultOrder) p.set('order', filters.order);
  return p;
}
export function combineDays(days: DayData[]): DayData {
  if (!days.length) throw Error('所选区间没有已保存的交易日。');
  const ordered = [...days].sort((a, b) => a.date.localeCompare(b.date));
  const rows = ordered.flatMap((d) =>
    d.rows.map((r) => {
      const row = normalizeDownloadedRow(r);
      return row.date === d.date ? row : { ...row, date: d.date };
    }),
  );
  return {
    ...ordered[0],
    date:
      ordered.length === 1
        ? ordered[0].date
        : ordered[0].date + ' 至 ' + ordered.at(-1)!.date,
    dates: ordered.map((d) => d.date),
    rows,
    total: rows.length,
    scope: ordered.every((d) => d.scope === 'full')
      ? 'full'
      : ordered.some((d) => d.scope === 'partial')
        ? 'partial'
        : 'sample',
    generatedAt: ordered.map((d) => d.generatedAt).sort()[0],
    universeTotal: Math.max(...ordered.map((d) => d.universeTotal ?? d.total)),
    priceSchemaVersion: ordered.every((d) => d.priceSchemaVersion === 1)
      ? 1
      : undefined,
    priceFields: ordered.every(
      (d) => JSON.stringify(d.priceFields) === JSON.stringify(['open', 'high', 'low', 'close']),
    )
      ? ['open', 'high', 'low', 'close']
      : undefined,
    priceAvailable: ordered.reduce((sum, d) => sum + (d.priceAvailable ?? 0), 0),
    priceNoTrade: ordered.reduce((sum, d) => sum + (d.priceNoTrade ?? 0), 0),
    priceMissing: ordered.reduce(
      (sum, d) => sum + (d.priceMissing ?? d.rows.length),
      0,
    ),
    priceDataComplete: ordered.every((d) => d.priceDataComplete === true),
  };
}
export async function loadDateRange(
  manifest: Manifest,
  filters: Pick<Filters, 'date' | 'endDate' | 'mode' | 'dates'>,
  signal: AbortSignal,
  load: (entry: Manifest['dates'][number]) => Promise<DayData>,
): Promise<DayData> {
  const entries = selectedDates(manifest, filters),
    results: DayData[] = [];
  let cursor = 0;
  async function worker() {
    while (cursor < entries.length) {
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      const i = cursor++,
        entry = entries[i];
      const d = await load(entry);
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      if (d.date !== entry.date || !Array.isArray(d.rows))
        throw Error(entry.date + ' 的数据日期或格式不匹配。');
      results[i] = d;
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(4, entries.length) }, () => worker()),
  );
  return combineDays(results);
}

export function changeFilters(
  f: Filters,
  p: Partial<Filters>,
  manifest: Manifest | null,
): Filters {
  if (p.stockScope !== undefined)
    return {
      ...f,
      ...p,
      symbols:
        p.stockScope === 'all'
          ? []
          : (p.symbols ??
            (p.stockScope === 'single' ? f.symbols.slice(0, 1) : f.symbols)),
      query: '',
      market: 'all',
    };
  if (p.mode === 'multiple' && f.mode !== 'multiple')
    return {
      ...f,
      ...p,
      dates: [f.mode === 'single' ? f.date : f.endDate || f.date].filter(
        Boolean,
      ),
      order: 'date_desc',
    };
  if (p.mode === 'range' && f.mode !== 'range' && p.date === undefined) {
    const index = Math.max(
      0,
      manifest?.dates.findIndex((d) => d.date === f.date) ?? 0,
    );
    return {
      ...f,
      ...p,
      date:
        manifest?.dates[Math.min(index + 19, (manifest?.dates.length ?? 1) - 1)]
          ?.date ?? f.date,
      endDate: f.date,
      order: 'date_desc',
    };
  }
  if (p.mode === 'single' && f.mode !== 'single')
    return {
      ...f,
      ...p,
      date:
        f.mode === 'multiple'
          ? [...f.dates].sort().at(-1) || f.date
          : f.endDate || f.date,
      order: 'desc',
    };
  return { ...f, ...p };
}
export function dateQueryKey(f: Filters) {
  return JSON.stringify([f.mode, f.date, f.endDate, [...f.dates].sort()]);
}
