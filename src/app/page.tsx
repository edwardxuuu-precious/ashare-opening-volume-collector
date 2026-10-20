'use client';
import { useEffect, useMemo, useState, useRef, lazy, Suspense } from 'react';
import Link from 'next/link';
import { flushSync } from 'react-dom';
import { registerStockTools } from '@/lib/webmcp';
import {
  CalendarDays,
  ArrowUpRight,
  SlidersHorizontal,
  Check,
  X,
  Share2,
  Info,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Search,
} from 'lucide-react';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
} from '@/components/ui/empty';
import {
  Pagination,
  PaginationContent,
  PaginationItem,
} from '@/components/ui/pagination';
const OpeningTrend = lazy(() =>
  import('@/components/opening-trend').then((m) => ({
    default: m.OpeningTrend,
  })),
);
import { AccountControls } from '@/components/auth-gate';
import { BrandIdentity } from '@/components/brand';
import { StockNavigation } from '@/components/stock-navigation';
import { InfoHint } from '@/components/info-hint';
import { RecordDetail } from '@/components/record-detail';
import {
  Popover,
  PopoverContent,
  PopoverTitle,
  PopoverTrigger,
} from '@/components/ui/popover';
import { StockPicker, type StockOption } from '@/components/stock-picker';
import { loadQuery, type LoadProgress } from '@/lib/query-loader';
import { authFetch, authRead } from '@/lib/auth-fetch';
import { Progress } from '@/components/ui/progress';
import { DateCalendar } from '@/components/date-calendar';
import {
  CollectionStatus,
  collectionQueryMessage,
  startCollectionPolling,
  type CollectionState,
} from '@/components/collection-status';
import { sixMonthStart, shanghaiToday } from '@/lib/date-window';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Skeleton } from '@/components/ui/skeleton';
import { TradingStatus } from '@/components/trading-status';
import { DailyQuote, hasDailyQuote } from '@/components/daily-quote';
import {
  filterRows,
  advanceLatestSelection,
  changeFilters,
  formatVolume,
  formatPct,
  formatPrice,
  pctTone,
  parseFilters,
  queryableDates,
  queryParams,
  shouldFollowLatest,
  type Filters,
  type DayData,
  type Manifest,
} from '@/lib/stock';

// An isolated status release can leave the independently staged quote UI unpublished.
const showQuoteColumns = process.env.NEXT_PUBLIC_QUOTE_COLUMNS !== 'false';
const collectorApi = (process.env.NEXT_PUBLIC_COLLECTOR_API || '').replace(
  /\/$/,
  '',
);
const dataBase = (
  process.env.NEXT_PUBLIC_DATA_BASE ||
  collectorApi ||
  '.'
).replace(/\/$/, '');
const markets = [
  { value: 'all', label: '全部市场' },
  { value: 'SH', label: '沪市' },
  { value: 'SZ', label: '深市' },
  { value: 'BJ', label: '北交所' },
];
type QueryView = {
  key: string;
  data: DayData | null;
  loading: boolean;
  error: string;
  refreshError: string;
  progress: LoadProgress | null;
};
function queryIdentity(filters: Filters) {
  return JSON.stringify([
    filters.date,
    filters.endDate,
    filters.mode,
    filters.dates,
    filters.stockScope,
    filters.symbols,
  ]);
}
function pendingQuery(key: string, data: DayData | null = null): QueryView {
  return {
    key,
    data,
    loading: !data,
    error: '',
    refreshError: '',
    progress: null,
  };
}
function readError(response: Response, label: string) {
  return response.status === 401
    ? '登录已失效，请重新登录。'
    : `${label}暂时无法读取（HTTP ${response.status}），请重新读取。`;
}
function Picker({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  label: string;
}) {
  return (
    <Select
      value={value}
      onValueChange={(v) => v !== null && onChange(String(v))}
      items={options}
    >
      <SelectTrigger id={label} aria-label={label} className="picker">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
function Segmented({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <RadioGroup
      className="segmented"
      aria-label={label}
      value={value}
      onValueChange={(v) => onChange(String(v))}
    >
      {options.map((o) => (
        <label key={o.value} htmlFor={`${label}-${o.value}`}>
          <RadioGroupItem
            id={`${label}-${o.value}`}
            value={o.value}
            className="segment-radio"
          />
          <span>{o.label}</span>
        </label>
      ))}
    </RadioGroup>
  );
}
export default function Home() {
  const queryCache = useRef(new Map<string, DayData>());
  // Keep one verified range alive for the loader's WeakRef cache. Visibility
  // is still constrained by queryKey, including when stocks/dates change.
  const lastResult = useRef<{ key: string; data: DayData } | null>(null);
  const manifestRef = useRef<Manifest | null>(null);
  const followLatestRef = useRef(false);
  const [collectionState, setCollectionState] =
    useState<CollectionState | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [manifestError, setManifestError] = useState('');
  const [queryView, setQueryView] = useState<QueryView | null>(null);
  const [filters, setFilters] = useState<Filters>({
    date: '',
    endDate: '',
    mode: 'single',
    stockScope: 'all',
    symbols: [],
    dates: [],
    query: '',
    market: 'all',
    min: 0,
    includeUnverified: false,
    order: 'desc',
  });
  const [stockSearchOpen, setStockSearchOpen] = useState(false);
  const [dateOpen, setDateOpen] = useState(false);
  const [rangePending, setRangePending] = useState(false);
  const [dateCalendarSession, setDateCalendarSession] = useState(0);
  const [focusedRecord, setFocusedRecord] = useState<string | null>(null);
  const [recentStocks, setRecentStocks] = useState<string[]>([]);
  const [stocks, setStocks] = useState<StockOption[]>([]);
  const [catalogError, setCatalogError] = useState('');
  const [page, setPage] = useState(1),
    [catalogRetry, setCatalogRetry] = useState(0),
    [manifestRetry, setManifestRetry] = useState(0),
    [queryRetry, setQueryRetry] = useState(0),
    [initialized, setInitialized] = useState(false),
    [followLatest, setFollowLatest] = useState(false),
    [shareFeedback, setShareFeedback] = useState('');
  const { date, endDate, mode, dates, stockScope, symbols } = filters;
  const queryKey = queryIdentity(filters);
  // Results and their notices are visible only for the query that produced them.
  // This also covers URL/latest-date and WebMCP changes that bypass update().
  const currentView = queryView?.key === queryKey ? queryView : null;
  const day = currentView?.data ?? null;
  const error = !manifest ? manifestError : (currentView?.error ?? '');
  const loading = !manifest
    ? !manifestError
    : !currentView || currentView.loading;
  const refreshError = currentView?.refreshError ?? '';
  const loadProgress = currentView?.progress ?? null;
  useEffect(() => {
    const c = new AbortController();
    authRead(
      dataBase + '/data/stocks.json',
      { signal: c.signal, cache: 'no-cache' },
      async (r) => {
        if (!r.ok) throw Error(readError(r, '股票名单'));
        const raw = await r.json();
        const data = raw as { rows?: StockOption[] } | null;
        if (!data || !Array.isArray(data.rows))
          throw Error('股票名单格式不正确');
        return data;
      },
    )
      .then((data) => {
        if (c.signal.aborted) return;
        setStocks(data.rows!);
        setCatalogError('');
      })
      .catch((e) => {
        if (!c.signal.aborted)
          setCatalogError(
            e instanceof Error ? e.message : '股票名单无法读取。',
          );
      });
    return () => c.abort();
  }, [catalogRetry]);
  useEffect(() => {
    const c = new AbortController();
    authRead(
      dataBase + '/data/manifest.json',
      { signal: c.signal, cache: 'no-cache' },
      async (r) => {
        if (!r.ok) throw Error(readError(r, '数据目录'));
        const raw = await r.json();
        const m = raw as Manifest | null;
        if (!m || !Array.isArray(m.dates)) throw Error('数据目录格式不正确。');
        return m;
      },
    )
      .then((m) => {
        if (c.signal.aborted) return;
        const previous = manifestRef.current;
        manifestRef.current = m;
        setManifest(m);
        setManifestError('');
        if (previous) {
          setFilters((current) =>
            advanceLatestSelection(
              current,
              previous.dates[0]?.date ?? '',
              m.dates[0]?.date ?? '',
              followLatestRef.current,
            ),
          );
          return;
        }
        const params = new URLSearchParams(window.location.search);
        const followsLatest = shouldFollowLatest(params);
        followLatestRef.current = followsLatest;
        setFollowLatest(followsLatest);
        const f = parseFilters(params);
        setFilters({
          ...f,
          date: f.date || m.dates[0]?.date || '',
          endDate: f.endDate || m.dates[0]?.date || '',
        });
        setInitialized(true);
      })
      .catch((e) => {
        if (!c.signal.aborted)
          setManifestError(
            e instanceof Error ? e.message : '数据目录无法读取。',
          );
      });
    return () => c.abort();
  }, [manifestRetry]);
  useEffect(() => {
    if (!initialized) return;
    return startCollectionPolling({
      dataBase,
      fetcher: authFetch,
      onStatus: setCollectionState,
      onManifestError: setManifestError,
      initialManifestGeneration: manifestRef.current?.generatedAt,
      initialLatestDate: manifestRef.current?.dates[0]?.date,
      isVisible: () => document.visibilityState === 'visible',
      onManifest: (next) => {
        const previous = manifestRef.current;
        manifestRef.current = next;
        setManifest((current) =>
          current?.generatedAt === next.generatedAt ? current : next,
        );
        if (previous)
          setFilters((current) =>
            advanceLatestSelection(
              current,
              previous.dates[0]?.date ?? '',
              next.dates[0]?.date ?? '',
              followLatestRef.current,
            ),
          );
      },
    });
  }, [initialized]);
  useEffect(() => {
    if (!manifest) return;
    const c = new AbortController();
    const previousResult = lastResult.current;
    const backgroundRefresh = previousResult?.key === queryKey;
    void Promise.resolve()
      .then(() => {
        if (c.signal.aborted) throw new DOMException('Aborted', 'AbortError');
        setQueryView(
          pendingQuery(
            queryKey,
            backgroundRefresh ? previousResult.data : null,
          ),
        );
        return loadQuery(
          manifest,
          {
            date,
            endDate,
            mode,
            dates,
            stockScope,
            symbols,
            query: '',
            market: 'all',
            min: 0,
            includeUnverified: false,
            order: 'desc',
          },
          {
            signal: c.signal,
            dataBase,
            // The server owns the full-market run; viewing a query must not enqueue another one.
            apiBase: '',
            fetcher: authFetch,
            cache: queryCache.current,
            progress: (state) => {
              if (!c.signal.aborted)
                setQueryView((current) =>
                  current?.key === queryKey
                    ? { ...current, progress: state }
                    : current,
                );
            },
          },
        );
      })
      .then((data) => {
        if (!c.signal.aborted) {
          lastResult.current = { key: queryKey, data };
          setQueryView((current) =>
            current?.key === queryKey
              ? { ...pendingQuery(queryKey, data), loading: false }
              : current,
          );
        }
      })
      .catch((e) => {
        if (!c.signal.aborted) {
          const message =
            e instanceof Error ? e.message : '数据暂时无法读取，请重试。';
          setQueryView((current) =>
            current?.key === queryKey
              ? {
                  ...current,
                  loading: false,
                  progress: null,
                  error: backgroundRefresh ? '' : message,
                  refreshError: backgroundRefresh ? message : '',
                }
              : current,
          );
        }
      });
    return () => c.abort();
  }, [
    manifest,
    date,
    endDate,
    mode,
    dates,
    stockScope,
    symbols,
    queryKey,
    queryRetry,
  ]);
  useEffect(() => {
    if (!initialized) return;
    const p = queryParams(filters);
    if (followLatest) {
      p.delete('date');
      p.delete('from');
      p.delete('to');
      p.delete('dates');
      p.set('latest', '1');
    } else p.delete('latest');
    window.history.replaceState(
      null,
      '',
      window.location.pathname + (p.size ? '?' + p.toString() : ''),
    );
  }, [filters, followLatest, initialized]);
  useEffect(
    () =>
      registerStockTools(document, manifest, day, filters, (next) =>
        flushSync(() => {
          setFilters(next);
          setPage(1);
        }),
      ),
    [manifest, day, filters],
  );
  const rows = useMemo(
    () => filterRows(day?.rows ?? [], filters),
    [day, filters],
  );
  const scopedRows = useMemo(
    () => filterRows(day?.rows ?? [], { ...filters, includeUnverified: true }),
    [day, filters],
  );
  const eligible = useMemo(
    () => rows.filter((r) => r.ratio !== null && Number.isFinite(r.ratio)),
    [rows],
  );
  const median = useMemo(() => {
    const values = eligible.map((r) => r.ratio as number).sort((a, b) => a - b);
    return values.length
      ? (values[Math.floor((values.length - 1) / 2)] +
          values[Math.floor(values.length / 2)]) /
          2
      : null;
  }, [eligible]);
  const pages = Math.max(1, Math.ceil(rows.length / 50)),
    currentPage = Math.min(page, pages),
    shown = rows.slice((currentPage - 1) * 50, currentPage * 50);
  const total = scopedRows.length;
  const loadedStocks = useMemo(
    () => new Set((day?.rows ?? []).map((r) => r.code)).size,
    [day],
  );
  const missingRows = useMemo(
    () => (day?.rows ?? []).filter((r) => r.status === 'missing').length,
    [day],
  );
  const selectionMissing = useMemo(
    () =>
      !day || filters.stockScope === 'all'
        ? []
        : filters.symbols
            .map((code) => ({
              code,
              count: (day.dates ?? []).filter(
                (date) =>
                  !day.rows.some((r) => r.code === code && r.date === date),
              ).length,
            }))
            .filter((x) => x.count > 0),
    [day, filters.stockScope, filters.symbols],
  );
  const railStocks = [...new Set([...symbols, ...recentStocks])].slice(0, 8);
  const hasExtraFilters =
    filters.market !== 'all' || filters.min > 0 || Boolean(filters.query);
  const singleMarket =
    stockScope === 'single'
      ? day?.rows.find((r) => r.code === symbols[0])?.market
      : null;
  const extremes = useMemo(
    () =>
      eligible.reduce<{ min: number | null; max: number | null }>(
        (result, row) => ({
          min:
            result.min === null ? row.ratio : Math.min(result.min, row.ratio!),
          max:
            result.max === null ? row.ratio : Math.max(result.max, row.ratio!),
        }),
        { min: null, max: null },
      ),
    [eligible],
  );
  const update = (p: Partial<Filters>) => {
    setFocusedRecord(null);
    const next = changeFilters(filters, p, manifest);
    if ('symbols' in p || 'stockScope' in p) {
      setRecentStocks((previous) =>
        [...new Set([...next.symbols, ...filters.symbols, ...previous])].slice(
          0,
          8,
        ),
      );
    }
    if (['date', 'endDate', 'mode', 'dates'].some((key) => key in p)) {
      const followsLatest = Boolean(
        next.mode === 'single' &&
        next.date &&
        next.date === manifest?.dates[0]?.date &&
        next.endDate === next.date,
      );
      followLatestRef.current = followsLatest;
      setFollowLatest(followsLatest);
    }
    if (queryIdentity(next) !== queryKey) {
      setQueryView(pendingQuery(queryIdentity(next)));
    }
    setFilters(next);
    setPage(1);
    setShareFeedback('');
  };
  const refreshQuery = () => {
    queryCache.current.clear();
    setQueryRetry((x) => x + 1);
  };
  const singleObservation =
    stockScope === 'single' && mode === 'single' ? rows[0] : null;
  async function share() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setShareFeedback('链接已复制');
    } catch {
      setShareFeedback('复制失败，请复制地址栏链接');
    }
  }
  const activeStocks = useMemo(
    () => new Set(rows.map((r) => r.code)).size,
    [rows],
  );
  const title =
    filters.stockScope === 'all'
      ? '全市场'
      : filters.stockScope === 'single'
        ? (stocks.find((s) => s.code === filters.symbols[0])?.name ??
          (filters.symbols[0] || '选择股票'))
        : `${filters.symbols.length} 只股票`;
  const dateLabel =
    filters.mode === 'single'
      ? filters.date
      : filters.mode === 'range'
        ? `${filters.date} — ${filters.endDate}`
        : `${filters.dates.length} 个交易日`;
  const resetExtra = () =>
    update({ market: 'all', min: 0, query: '', includeUnverified: false });
  const recent = (count: number) => {
    const entries = (
      manifest?.tradingDates ??
      manifest?.dates.map((d) => d.date) ??
      []
    )
      .filter(
        (d) => d >= sixMonthStart(shanghaiToday()) && d <= shanghaiToday(),
      )
      .sort()
      .reverse()
      .slice(0, count)
      .map((date) => ({ date }));
    if (entries.length) {
      update({
        mode: 'range',
        date: entries.at(-1)!.date,
        endDate: entries[0].date,
        order: 'date_desc',
      });
      setRangePending(false);
      setDateCalendarSession((session) => session + 1);
    }
  };
  return (
    <div className="app-shell">
      <aside className="query-panel" aria-label="股票查询">
        <div className="rail-brand-row">
          <Link href="/" className="brand">
            <BrandIdentity />
          </Link>
          <button
            type="button"
            className="mobile-stock-search"
            aria-label="搜索股票"
            aria-expanded={
              stockSearchOpen || (stockScope !== 'all' && !symbols.length)
            }
            onClick={() => {
              if (stockScope === 'all') {
                update({ stockScope: 'single' });
                setStockSearchOpen(true);
              } else setStockSearchOpen((open) => !open);
            }}
          >
            <Search size={16} aria-hidden="true" />
          </button>
        </div>
        <Segmented
          label="股票范围"
          value={filters.stockScope}
          onChange={(v) => update({ stockScope: v as Filters['stockScope'] })}
          options={[
            { value: 'single', label: '单只' },
            { value: 'multiple', label: '多只' },
            { value: 'all', label: '全市场' },
          ]}
        />
        {filters.stockScope !== 'all' && (
          <div
            className="stock-scope-control"
            data-mobile-open={stockSearchOpen || !symbols.length}
          >
            <StockPicker
              stocks={stocks}
              value={filters.symbols}
              multiple={filters.stockScope === 'multiple'}
              onChange={(symbols) => {
                update({ symbols, query: '' });
                if (stockScope === 'single' && symbols.length)
                  setStockSearchOpen(false);
              }}
            />
            {catalogError && (
              <div className="catalog-error" role="alert">
                <span>{catalogError}</span>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setCatalogError('');
                    setCatalogRetry((x) => x + 1);
                  }}
                >
                  重试
                </Button>
              </div>
            )}
          </div>
        )}
        {railStocks.length > 0 && (
          <div className="rail-history">
            <h2>
              {filters.stockScope === 'multiple'
                ? '已选与最近查看'
                : '最近查看'}
            </h2>
            <nav className="rail-stock-list" aria-label="快速切换股票">
              {railStocks.map((code) => {
                const name =
                  stocks.find((s) => s.code === code)?.name ??
                  day?.rows.find((r) => r.code === code)?.name ??
                  code;
                const selected =
                  filters.stockScope !== 'all' &&
                  filters.symbols.includes(code);
                return (
                  <button
                    key={code}
                    type="button"
                    className={`rail-stock${selected ? ' active' : ''}`}
                    aria-pressed={selected}
                    onClick={() =>
                      update({
                        stockScope: 'single',
                        symbols: [code],
                        query: '',
                      })
                    }
                  >
                    <span className="stock-monogram" aria-hidden="true">
                      {name.slice(0, 2)}
                    </span>
                    <span>
                      <strong>{name}</strong>
                      <small>{code}</small>
                    </span>
                  </button>
                );
              })}
            </nav>
          </div>
        )}
        <a href="/chips" className="rail-stock" aria-label="每日筹码筛选">
          <span>每日筹码筛选</span>
        </a>
        <div className="rail-footer">
          <AccountControls />
        </div>
      </aside>
      <main className="research-main">
        <header className="research-topbar">
          <StockNavigation active="opening" />
          <CollectionStatus
            status={collectionState}
            latestDate={manifest?.dates[0]?.date ?? null}
          />
        </header>
        <div className="results-workspace">
          {shareFeedback && (
            <output className="share-feedback" aria-live="polite">
              {shareFeedback}
            </output>
          )}
          <div className="view-heading">
            <div className="view-identity">
              <h1>{title}</h1>
              <div className="stock-meta">
                {filters.stockScope === 'single' && filters.symbols[0] && (
                  <span>{filters.symbols[0]}</span>
                )}
                {filters.stockScope === 'all' && manifest?.universeTotal && (
                  <span>
                    {manifest.universeTotal.toLocaleString()} 只 · 当前名单
                  </span>
                )}
                {filters.stockScope === 'multiple' && (
                  <span>{filters.symbols.join(' / ') || '未选择股票'}</span>
                )}
                {singleMarket && (
                  <span className="exchange-tag">
                    {markets.find((m) => m.value === singleMarket)?.label}
                  </span>
                )}
              </div>
            </div>
            <div className="query-toolbar">
              <Popover
                open={dateOpen}
                onOpenChange={(open) => {
                  setDateOpen(open);
                  setRangePending(false);
                }}
              >
                <PopoverTrigger
                  className="toolbar-button date-trigger"
                  aria-label="选择日期"
                >
                  <CalendarDays size={15} aria-hidden="true" />
                  <span>{dateLabel || '选择日期'}</span>
                  <ChevronDown size={13} aria-hidden="true" />
                </PopoverTrigger>
                <PopoverContent
                  className="date-popover"
                  align="end"
                  sideOffset={8}
                >
                  <PopoverTitle>交易日期</PopoverTitle>
                  <Segmented
                    label="日期范围"
                    value={filters.mode}
                    onChange={(v) => {
                      setRangePending(false);
                      update({ mode: v as Filters['mode'] });
                    }}
                    options={[
                      { value: 'single', label: '单日' },
                      { value: 'range', label: '区间' },
                      { value: 'multiple', label: '多日' },
                    ]}
                  />
                  {manifest && dateOpen && (
                    <DateCalendar
                      key={`${filters.mode}:${dateCalendarSession}`}
                      filters={filters}
                      tradingDates={queryableDates(manifest)}
                      savedDates={[
                        ...new Set([
                          ...manifest.dates.map((d) => d.date),
                          ...(day?.dates ?? []),
                        ]),
                      ]}
                      start={
                        queryableDates(manifest)[0] ??
                        sixMonthStart(shanghaiToday())
                      }
                      end={shanghaiToday()}
                      onRangePendingChange={setRangePending}
                      onChange={(patch) => {
                        update(patch);
                        if (filters.mode === 'single') setDateOpen(false);
                      }}
                    />
                  )}
                  <div className="date-shortcuts">
                    {[5, 20, 186].map((n) => (
                      <Button variant="ghost" key={n} onClick={() => recent(n)}>
                        {n === 186 ? '近6个月' : `近${n}日`}
                      </Button>
                    ))}
                    <Button
                      onClick={() => setDateOpen(false)}
                      className="date-done"
                      disabled={filters.mode === 'range' && rangePending}
                    >
                      完成
                    </Button>
                  </div>
                </PopoverContent>
              </Popover>
              <Popover>
                <PopoverTrigger
                  className="toolbar-button"
                  aria-label="筛选条件"
                >
                  <SlidersHorizontal size={15} aria-hidden="true" />
                  <span>筛选</span>
                  {hasExtraFilters && <i className="filter-dot" />}
                </PopoverTrigger>
                <PopoverContent
                  className="filter-popover"
                  align="end"
                  sideOffset={8}
                >
                  <PopoverTitle>筛选</PopoverTitle>
                  <label htmlFor="市场">
                    市场
                    <Picker
                      label="市场"
                      value={filters.market}
                      onChange={(v) => update({ market: v })}
                      options={markets}
                    />
                  </label>
                  <label htmlFor="min-volume">
                    最低占比
                    <div className="minimum">
                      <Input
                        id="min-volume"
                        aria-label="最低占比百分数"
                        type="number"
                        min="0"
                        max="100"
                        value={filters.min || ''}
                        placeholder="不限"
                        onChange={(e) =>
                          update({
                            min: Math.max(
                              0,
                              Math.min(100, Number(e.target.value) || 0),
                            ),
                          })
                        }
                      />
                      <span>%</span>
                    </div>
                  </label>
                  <Button
                    variant="ghost"
                    onClick={resetExtra}
                    disabled={!hasExtraFilters}
                  >
                    重置
                  </Button>
                </PopoverContent>
              </Popover>
              <Button
                variant="outline"
                className="toolbar-icon"
                aria-label="重新读取当前查询"
                title="重新读取"
                onClick={() => {
                  if (!manifest) {
                    setManifestError('');
                    setManifestRetry((x) => x + 1);
                  } else refreshQuery();
                }}
              >
                <RefreshCw size={15} aria-hidden="true" />
              </Button>
              <Button
                onClick={share}
                variant="outline"
                className="toolbar-icon"
                aria-label="复制查询链接"
                title="复制查询链接"
              >
                {shareFeedback === '链接已复制' ? (
                  <Check size={15} aria-hidden="true" />
                ) : (
                  <Share2 size={15} aria-hidden="true" />
                )}
              </Button>
            </div>
          </div>
          {hasExtraFilters && (
            <div className="active-filters">
              {filters.market !== 'all' && (
                <Button
                  variant="outline"
                  aria-label="清除市场筛选"
                  onClick={() => update({ market: 'all' })}
                >
                  {markets.find((m) => m.value === filters.market)?.label}
                  <X size={12} aria-hidden="true" />
                </Button>
              )}
              {filters.min > 0 && (
                <Button
                  variant="outline"
                  aria-label="清除最低占比筛选"
                  onClick={() => update({ min: 0 })}
                >
                  占比 ≥ {filters.min}%<X size={12} aria-hidden="true" />
                </Button>
              )}
              {filters.query && (
                <Button
                  variant="outline"
                  aria-label="清除搜索筛选"
                  onClick={() => update({ query: '' })}
                >
                  {filters.query}
                  <X size={12} aria-hidden="true" />
                </Button>
              )}
            </div>
          )}
          {manifest && manifestError && (
            <output className="query-notice warning" role="alert">
              <span>
                目录更新失败，使用上次目录。
                <InfoHint label="目录读取错误">{manifestError}</InfoHint>
              </span>
              <Button
                variant="ghost"
                onClick={() => {
                  setManifestError('');
                  setManifestRetry((x) => x + 1);
                }}
              >
                重试
              </Button>
            </output>
          )}
          {refreshError && (
            <output className="query-notice warning" role="alert">
              <span>
                刷新失败，仍显示上次结果。
                <InfoHint label="刷新错误">{refreshError}</InfoHint>
              </span>
              <Button variant="ghost" onClick={refreshQuery}>
                重试
              </Button>
            </output>
          )}
          {day && day.priceDataComplete !== true && (
            <output className="query-notice">
              <span>
                部分日行情待补
                <InfoHint label="日行情缺失">
                  未补齐的开、高、低、收留空，不代表价格为零。
                </InfoHint>
              </span>
            </output>
          )}
          {selectionMissing.length > 0 && (
            <output className="query-notice warning">
              <span>
                未保存：
                {selectionMissing
                  .map((s) => `${s.code} × ${s.count} 日`)
                  .join('、')}
                <InfoHint label="未保存记录">
                  未保存的股票日未计入结果，不代表零成交。
                </InfoHint>
              </span>
            </output>
          )}
          {error ? (
            <Empty className="result-empty">
              <EmptyHeader>
                <EmptyTitle>
                  {manifest ? '所选日期无法读取' : '数据目录无法读取'}
                </EmptyTitle>
                <EmptyDescription>
                  {collectionQueryMessage(error, collectionState)}
                </EmptyDescription>
              </EmptyHeader>
              <Button
                variant="outline"
                onClick={() => {
                  if (!manifest) {
                    setManifestError('');
                    setManifestRetry((x) => x + 1);
                  } else refreshQuery();
                }}
              >
                <RefreshCw size={15} aria-hidden="true" />
                重试
              </Button>
            </Empty>
          ) : loading ? (
            <div className="loading-state" aria-label="正在读取行情数据">
              <div className="download-progress">
                <p aria-live="polite">
                  读取数据
                  {loadProgress && loadProgress.total > 0
                    ? ` · ${loadProgress.completed} / ${loadProgress.total} 日`
                    : '…'}
                </p>
                <Progress
                  aria-label="数据加载进度"
                  value={
                    loadProgress && loadProgress.total > 0
                      ? Math.round(
                          (loadProgress.completed / loadProgress.total) * 100,
                        )
                      : null
                  }
                />
              </div>
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-56 w-full" />
              <Skeleton className="h-64 w-full" />
            </div>
          ) : rows.length === 0 ? (
            <Empty className="result-empty">
              <EmptyHeader>
                <EmptyTitle>
                  {filters.stockScope !== 'all' && !filters.symbols.length
                    ? '选择股票'
                    : selectionMissing.length
                      ? '暂无已保存记录'
                      : '没有符合条件的记录'}
                </EmptyTitle>
                <EmptyDescription>
                  {filters.stockScope !== 'all' && !filters.symbols.length
                    ? '按名称或代码搜索。'
                    : selectionMissing.length
                      ? '更换股票或日期。'
                      : '更换日期或放宽筛选。'}
                </EmptyDescription>
              </EmptyHeader>
              {hasExtraFilters && (
                <Button variant="outline" onClick={resetExtra}>
                  清除筛选
                </Button>
              )}
            </Empty>
          ) : (
            <>
              <section className="summary" aria-label="查询概况">
                <div className="primary-stat">
                  <span>
                    {filters.stockScope === 'single' &&
                    filters.mode === 'single'
                      ? '开盘占比'
                      : '占比中位数'}
                    <InfoHint label="占比计算">
                      首15分钟成交量 ÷ 同日全天成交量 ×
                      100%。多条记录取不加权中位数；缺失与无成交不参与计算。
                    </InfoHint>
                  </span>
                  <strong>
                    {median === null ? '—' : median.toFixed(2)}
                    {median !== null && <small>%</small>}
                  </strong>
                </div>
                <div>
                  <span>{singleObservation ? '开盘量 / 股' : '最高占比'}</span>
                  <strong>
                    {singleObservation
                      ? formatVolume(singleObservation.first15Volume)
                      : extremes.max === null
                        ? '—'
                        : extremes.max.toFixed(2)}
                    {!singleObservation && extremes.max !== null && (
                      <small>%</small>
                    )}
                  </strong>
                </div>
                <div>
                  <span>{singleObservation ? '全天量 / 股' : '最低占比'}</span>
                  <strong>
                    {singleObservation
                      ? formatVolume(singleObservation.dailyVolume)
                      : extremes.min === null
                        ? '—'
                        : extremes.min.toFixed(2)}
                    {!singleObservation && extremes.min !== null && (
                      <small>%</small>
                    )}
                  </strong>
                </div>
                {singleObservation ? (
                  <div>
                    <span>收盘 / 元</span>
                    <strong>
                      {hasDailyQuote(singleObservation)
                        ? formatPrice(singleObservation.close)
                        : '—'}
                    </strong>
                  </div>
                ) : (
                  <div>
                    <span>
                      可计算记录
                      <InfoHint label="记录覆盖">
                        每条记录对应一只股票的一天。当前结果覆盖{' '}
                        {activeStocks.toLocaleString()} 只股票、
                        {day?.dates?.length ?? 0} 个已保存交易日；
                        {total - eligible.length} 条记录不可计算。
                      </InfoHint>
                    </span>
                    <strong>
                      {eligible.length.toLocaleString()}
                      <small> / {total.toLocaleString()}</small>
                    </strong>
                  </div>
                )}
              </section>
              {(day?.dates?.length ?? 0) > 1 && (
                <Suspense
                  fallback={
                    <Skeleton
                      className="trend-panel h-56"
                      aria-label="正在载入趋势图"
                    />
                  }
                >
                  <OpeningTrend
                    rows={rows}
                    dates={day!.dates!}
                    symbols={
                      filters.stockScope === 'all' ? null : filters.symbols
                    }
                  />
                </Suspense>
              )}
              <section className="results" aria-label="交易明细">
                <div className="results-heading">
                  <div>
                    <h2>交易明细</h2>
                    <span>{rows.length.toLocaleString()} 条</span>
                    <InfoHint label="明细说明">
                      点交易日期查看精确成交量、日行情与来源。成交量按股计算，价格为不复权日行情。仅停牌、特殊交易状态或数据待补的记录显示提示图标，可查看原因与依据。
                    </InfoHint>
                  </div>
                  <Picker
                    label="排序方式"
                    value={filters.order}
                    onChange={(v) => update({ order: v as Filters['order'] })}
                    options={[
                      { value: 'desc', label: '占比降序' },
                      { value: 'asc', label: '占比升序' },
                      { value: 'date_desc', label: '日期降序' },
                      { value: 'date_asc', label: '日期升序' },
                    ]}
                  />
                </div>
                <div className="table-frame">
                  <Table aria-label="逐股票逐日成交明细">
                    <TableHeader>
                      <TableRow>
                        <TableHead
                          className="trade-date-column"
                          aria-sort={
                            filters.order === 'date_desc'
                              ? 'descending'
                              : filters.order === 'date_asc'
                                ? 'ascending'
                                : 'none'
                          }
                        >
                          交易日期
                        </TableHead>
                        {filters.stockScope !== 'single' && (
                          <TableHead className="stock-column">股票</TableHead>
                        )}
                        <TableHead
                          className="ratio-column"
                          aria-sort={
                            filters.order === 'desc'
                              ? 'descending'
                              : filters.order === 'asc'
                                ? 'ascending'
                                : 'none'
                          }
                        >
                          <span className="table-metric">
                            开盘占比
                            <InfoHint label="开盘占比">
                              首15分钟成交量占同日全天成交量的比例。
                            </InfoHint>
                          </span>
                        </TableHead>
                        <TableHead className="volume-column">开盘量</TableHead>
                        <TableHead className="volume-column">全天量</TableHead>
                        <TableHead className="price-column">开 / 收</TableHead>
                        <TableHead className="price-column">高 / 低</TableHead>
                        {showQuoteColumns && (
                          <>
                            <TableHead className="move-column">
                              涨跌幅
                            </TableHead>
                            <TableHead className="move-column">振幅</TableHead>
                          </>
                        )}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {shown.map((r) => (
                        <TableRow key={r.code + '-' + r.date}>
                          <TableCell className="trade-date">
                            <div className="record-identity">
                              <button
                                type="button"
                                className="date-link"
                                aria-label={`查看 ${r.name} ${r.date} 明细`}
                                onClick={() =>
                                  setFocusedRecord(r.code + '-' + r.date)
                                }
                              >
                                {r.date}
                              </button>
                              {filters.stockScope === 'single' && (
                                <TradingStatus row={r} hideNormal />
                              )}
                            </div>
                          </TableCell>
                          {filters.stockScope !== 'single' && (
                            <TableCell className="stock-column">
                              <div className="record-identity">
                                <button
                                  className="stock-link"
                                  title={`查看${r.name}`}
                                  onClick={() =>
                                    update({
                                      stockScope: 'single',
                                      symbols: [r.code],
                                    })
                                  }
                                >
                                  <span>
                                    <strong>{r.name}</strong>
                                    <small>{r.code}</small>
                                  </span>
                                  <ArrowUpRight size={13} aria-hidden="true" />
                                </button>
                                <TradingStatus row={r} hideNormal />
                              </div>
                            </TableCell>
                          )}
                          <TableCell className="ratio-column">
                            {r.ratio === null ? (
                              <span className="unavailable-value">—</span>
                            ) : (
                              <strong className="ratio-value">
                                {r.ratio.toFixed(2)}
                                <small>%</small>
                              </strong>
                            )}
                          </TableCell>
                          <TableCell className="volume-column">
                            <span
                              title={
                                r.first15Volume === null
                                  ? undefined
                                  : `${r.first15Volume.toLocaleString()} 股`
                              }
                            >
                              {formatVolume(r.first15Volume)}
                            </span>
                          </TableCell>
                          <TableCell className="volume-column">
                            <span
                              title={
                                r.dailyVolume === null
                                  ? undefined
                                  : `${r.dailyVolume.toLocaleString()} 股`
                              }
                            >
                              {formatVolume(r.dailyVolume)}
                            </span>
                          </TableCell>
                          <TableCell className="price-column">
                            <DailyQuote row={r} pair="open-close" />
                          </TableCell>
                          <TableCell className="price-column">
                            <DailyQuote row={r} pair="high-low" />
                          </TableCell>
                          {showQuoteColumns && (
                            <>
                              <TableCell
                                className={`move-column quote-value ${pctTone(r.pctChange)}`}
                              >
                                {formatPct(r.pctChange)}
                              </TableCell>
                              <TableCell className="move-column">
                                {formatPct(r.amplitude).replace(/^\+/, '')}
                              </TableCell>
                            </>
                          )}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <div className="table-footer">
                  <span>
                    {(currentPage - 1) * 50 + 1}–
                    {Math.min(currentPage * 50, rows.length)} /{' '}
                    {rows.length.toLocaleString()}
                  </span>
                  {pages > 1 && (
                    <Pagination aria-label="记录分页">
                      <PaginationContent>
                        <PaginationItem>
                          <Button
                            variant="outline"
                            aria-label="上一页"
                            disabled={currentPage <= 1}
                            onClick={() => setPage((p) => p - 1)}
                          >
                            <ChevronLeft size={16} />
                          </Button>
                        </PaginationItem>
                        <PaginationItem className="page-number">
                          {currentPage} / {pages}
                        </PaginationItem>
                        <PaginationItem>
                          <Button
                            variant="outline"
                            aria-label="下一页"
                            disabled={currentPage >= pages}
                            onClick={() => setPage((p) => p + 1)}
                          >
                            <ChevronRight size={16} />
                          </Button>
                        </PaginationItem>
                      </PaginationContent>
                    </Pagination>
                  )}
                </div>
              </section>
            </>
          )}
          <footer className="data-footer">
            <details className="method">
              <summary>
                <Info size={13} aria-hidden="true" />
                口径与来源
                <ChevronDown size={12} aria-hidden="true" />
              </summary>
              <div className="method-content">
                <p>
                  开盘占比 = 09:45 首根15分钟成交量 ÷ 同日日线成交量 ×
                  100%。统一按股计算，竞价沿用来源口径。
                </p>
                {showQuoteColumns && (
                  <p>
                    涨跌幅 =（收盘 − 前收盘）÷ 前收盘；振幅 =（最高 − 最低）÷
                    前收盘。价格取同日日线，不复权。
                  </p>
                )}
                <p>缺失与无成交留空。开盘量为0且全天量为正数时，占比为0%。</p>
                {manifest && (
                  <p>
                    {day?.scope === 'full'
                      ? '已加载全市场数据'
                      : '当前查询为已保存数据'}{' '}
                    · {loadedStocks.toLocaleString()} 只 ·{' '}
                    {manifest.dates.length}{' '}
                    个保存日。当前上市名单不代表历史当日全体成分。
                    {missingRows > 0 && ` 当前有 ${missingRows} 条缺失记录。`}
                  </p>
                )}
              </div>
            </details>
            <span>
              {filters.mode === 'range' && day?.dates?.length ? (
                <>
                  <time>{day.dates[0]}</time> — <time>{day.dates.at(-1)}</time>{' '}
                  · {day.dates.length} 日
                </>
              ) : null}
            </span>
          </footer>
        </div>
      </main>
      <RecordDetail
        row={rows.find((r) => r.code + '-' + r.date === focusedRecord) ?? null}
        onClose={() => setFocusedRecord(null)}
      />
    </div>
  );
}
