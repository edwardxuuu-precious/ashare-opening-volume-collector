'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { SiteHeader } from '@/components/site-header';
import { SiteLink } from '@/components/site-router';
import {
  WorkbenchListFooter,
  WorkbenchListHeader,
  WorkbenchPageBar,
  WorkbenchShell,
  WorkbenchState,
} from '@/components/workbench';
import { RetryMessage } from '@/components/read-state';
import { InspectorTabs } from '@/components/inspector-tabs';
import { useStockListNavigation } from '@/components/use-stock-list-navigation';
import { stockListMatches } from '@/lib/stock-list';
import { authRead } from '@/lib/auth-fetch';
import { startPublicationFollow } from '@/lib/publication-follow';
import {
  concentrationGroupRegistry,
  compareConcentrationRows,
  type ConcentrationSort,
  concentrationSeries,
  concentrationStartValue,
  concentrationDrop,
  concentrationReason,
  concentrationStateText,
  concentrationStreakText,
  parseConcentrationScan,
  parseConcentrationSource,
  type ConcentrationGroup,
  type ConcentrationRow,
  type ConcentrationScan,
  type ConcentrationSource,
  type ConcentrationState,
} from '@/lib/concentration-screen';
import '../chips/chips.css';
import '../chips/workbench.css';
import './concentration.css';
import { ConcentrationCandles } from './concentration-candles';

export const dynamic = 'force-static';
const api = (process.env.NEXT_PUBLIC_DATA_BASE || '').replace(/\/$/, '');
const pageSize = 60;
const emptyRows: ConcentrationRow[] = [];
type Publication = { source: ConcentrationSource; scan: ConcentrationScan };
type ReadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; publication: Publication }
  | { kind: 'refreshing'; publication: Publication }
  | { kind: 'refresh-error'; publication: Publication; message: string };
const sortColumns = [
  { key: 'code', label: '股票', title: '按六位股票代码排序' },
  { key: 'current', label: '当前集中度' },
  { key: 'streak', label: '实际连续不增' },
  { key: 'start', label: '起点集中度' },
  { key: 'drop', label: '区间降幅（百分点）' },
] satisfies { key: ConcentrationSort['key']; label: string; title?: string }[];
type Tab = 'series' | 'method';
const percent = (value: number | null | undefined) =>
  value === null || value === undefined ? '待核实' : `${value.toFixed(2)}%`;
const drop = concentrationDrop;

async function read<T>(
  path: string,
  signal: AbortSignal,
  parse: (value: unknown) => T,
) {
  return authRead(
    api + path,
    { signal, cache: 'no-store' },
    async (response) => {
      if (!response.ok)
        throw Error(
          response.status === 401
            ? '登录已失效，请重新登录。'
            : response.status === 404
              ? '本批连续集中度结果尚未发布，请稍后重新读取。'
              : `集中度结果暂时无法读取（HTTP ${response.status}）。`,
        );
      return parse(await response.json());
    },
  );
}

function chartRange(values: number[]) {
  const minimum = values.reduce(
    (lowest, value) => (value < lowest ? value : lowest),
    Infinity,
  );
  const maximum = values.reduce(
    (highest, value) => (value > highest ? value : highest),
    -Infinity,
  );
  const low = minimum > 0.1 ? minimum - 0.1 : 0;
  const upper = maximum + 0.1;
  return { low, high: upper > low + 0.2 ? upper : low + 0.2 };
}

function SeriesChart({
  row,
  scan,
  group,
}: {
  row: ConcentrationRow;
  scan: ConcentrationScan;
  group: ConcentrationGroup;
}) {
  const { sessions, values, highlightFrom } = concentrationSeries(
    scan,
    row,
    group,
  );
  const available = values.filter((value): value is number => value !== null);
  if (!available.length)
    return <p className="conc-note">当前股票没有可绘制的集中度读数。</p>;
  const { low, high } = chartRange(available);
  const x = (index: number) => 90 + (index / (values.length - 1)) * 644;
  const y = (value: number) => 180 - ((value - low) / (high - low)) * 164;
  const segments: string[] = [];
  let current: string[] = [];
  for (const [index, value] of values.entries()) {
    if (value === null) {
      if (current.length) segments.push(current.join(' '));
      current = [];
    } else current.push(`${x(index)},${y(value)}`);
  }
  if (current.length) segments.push(current.join(' '));
  return (
    <svg
      className="conc-chart"
      viewBox="0 0 760 220"
      aria-label={`${row.name}${values.length}个交易日90%筹码集中度`}
    >
      <title>{row.name}，数值越小，成本越集中；缺失日期留空。</title>
      <rect
        x={x(highlightFrom)}
        y="12"
        width={x(values.length - 1) - x(highlightFrom)}
        height="168"
        className="conc-chart-window"
      />
      {[0, 1, 2, 3].map((index) => {
        const value = low + ((high - low) * index) / 3;
        return (
          <g key={index}>
            <line
              x1="90"
              x2="734"
              y1={y(value)}
              y2={y(value)}
              className="conc-chart-grid"
            />
            <text x="82" y={y(value)} textAnchor="end" dominantBaseline="middle">
              {value.toFixed(1)}%
            </text>
          </g>
        );
      })}
      {segments.map((points, index) => (
        <polyline key={index} points={points} className="conc-chart-line" />
      ))}
      {values.map((value, index) =>
        value === null ? null : (
          <circle
            key={sessions[index]}
            cx={x(index)}
            cy={y(value)}
            r="2.5"
            className="conc-chart-point"
          >
            <title>
              {sessions[index]} · {value}%
            </title>
          </circle>
        ),
      )}
      {[0, 1, 2, 3, 4].map((tick) => {
        const index = Math.round((tick * (values.length - 1)) / 4);
        return (
          <text
            key={index}
            x={x(index)}
            y="205"
            textAnchor={tick === 0 ? 'start' : tick === 4 ? 'end' : 'middle'}
          >
            {sessions[index].slice(5)}
          </text>
        );
      })}
    </svg>
  );
}

export default function ConcentrationPage() {
  const [load, setLoad] = useState<ReadState>({ kind: 'loading' });
  const [group, setGroup] = useState<ConcentrationGroup>(20);
  const [category, setCategory] = useState<ConcentrationState>('PASS'),
    [search, setSearch] = useState('');
  const [sort, setSort] = useState<ConcentrationSort>({ key: 'streak', direction: 'desc' }),
    [page, setPage] = useState(0),
    [code, setCode] = useState('');
  const [tab, setTab] = useState<Tab>('series');
  const [chartPeriod, setChartPeriod] = useState(120);
  const listArea = useRef<HTMLDivElement>(null);
  const listHeader = useRef<HTMLTableSectionElement>(null);
  const publicationFollow = useRef<ReturnType<typeof startPublicationFollow> | null>(null);
  const publication = 'publication' in load ? load.publication : null;
  const scan = publication?.scan,
    source = publication?.source;
  const busy = load.kind === 'loading' || load.kind === 'refreshing';
  useEffect(() => {
    function reportError(error: Error | null) {
      if (!error) return;
      setLoad((previous) =>
        'publication' in previous
          ? { kind: 'refresh-error', publication: previous.publication,
              message: `${error.message} 仍显示上次完整结果。` }
          : { kind: 'error', message: error.message },
      );
    }
    const sourceRead = (signal: AbortSignal) => read(
      '/data/chips/daily/manifest.json', signal, parseConcentrationSource,
    );
    const follow = startPublicationFollow({
      readStatus: sourceRead,
      readManifest: sourceRead,
      readPayload: async (source, signal) => {
        const raw = await read(
          `/data/chips/daily/${source.active_slot}/concentration-two-v1.json`,
          signal,
          (value) => value,
        );
        const current = await read(
          '/data/chips/daily/manifest.json',
          signal,
          parseConcentrationSource,
        );
        if (JSON.stringify(current) !== JSON.stringify(source))
          throw Error('筹码批次正在切换，等待完整结果。');
        return parseConcentrationScan(raw, source);
      },
      onStatus: () => undefined,
      onPublication: (source, scan) => setLoad({ kind: 'ready', publication: { source, scan } }),
      onStatusError: reportError,
      onPublicationError: (error) => {
        if (error) reportError(error);
        else setLoad((previous) => 'publication' in previous
          ? { kind: 'ready', publication: previous.publication } : previous);
      },
      isRunning: () => false,
      publishedDataset: (source) => source.dataset_id,
    });
    publicationFollow.current = follow;
    return () => { follow.stop(); publicationFollow.current = null; };
  }, []);
  const rows = scan?.rows ?? emptyRows;
  const matched = useMemo(() => {
    const selected = rows.filter(
      (row) =>
        row.groups[group] === category &&
        stockListMatches(search, row.name, row.security_id),
    );
    return selected.sort((a, b) => compareConcentrationRows(a, b, group, sort));
  }, [rows, group, category, search, sort]);
  const selected =
    matched.find((row) => row.security_id === code) ?? matched[0];
  const selectedCode = selected?.security_id;
  const [pageContext, setPageContext] = useState({ matched, selectedCode });
  if (pageContext.matched !== matched || pageContext.selectedCode !== selectedCode) {
    const index = matched.findIndex((row) => row.security_id === selectedCode);
    setPageContext({ matched, selectedCode });
    setPage(index < 0 ? 0 : Math.floor(index / pageSize));
  }
  const effectivePage = Math.min(
    page,
    Math.max(0, Math.ceil(matched.length / pageSize) - 1),
  );
  const visible = matched.slice(
    effectivePage * pageSize,
    (effectivePage + 1) * pageSize,
  );
  const keys = useMemo(() => matched.map((row) => row.security_id), [matched]);
  const navigate = useCallback((key: string, index: number) => {
    setCode(key);
    setPage(Math.floor(index / pageSize));
  }, []);
  useStockListNavigation({
    keys,
    selected: selected?.security_id ?? '',
    onSelect: navigate,
    listRef: listArea,
    headerRef: listHeader,
    page: effectivePage,
    enabled: !!scan,
  });
  function refresh() {
    setLoad((previous) =>
      'publication' in previous
        ? { kind: 'refreshing', publication: previous.publication }
        : { kind: 'loading' },
    );
    publicationFollow.current?.refresh();
  }
  function chooseGroup(value: ConcentrationGroup) {
    setGroup(value);
    setPage(0);
    setCode('');
  }
  function chooseCategory(value: string) {
    if (value === 'PASS' || value === 'FAIL' || value === 'UNKNOWN') {
      setCategory(value);
      setPage(0);
      setCode('');
    }
  }
  function sortBy(key: ConcentrationSort['key']) {
    setSort((previous) => ({
      key,
      direction: previous.key === key && previous.direction === 'asc' ? 'desc' : 'asc',
    }));
  }
  const selectedDrop = selected ? drop(selected, group) : null;
  const groupDefinition = concentrationGroupRegistry.find(
    (entry) => entry.key === group,
  );
  const selectedSeries =
    scan && selected ? concentrationSeries(scan, selected, group) : null;
  return (
    <div className="chip-site chip-workbench wb-site concentration-page">
      <SiteHeader active="concentration">
        <button
          className="stock-header-refresh"
          disabled={busy}
          onClick={refresh}
          aria-label="刷新集中度筛选"
        >
          <RefreshCw size={14} aria-hidden="true" />
          <span>{busy ? '读取中' : '刷新'}</span>
        </button>
      </SiteHeader>
      <WorkbenchPageBar
        title="连续集中度筛选"
        explanation={{
          label: '筛选条件',
          content: <>
            <p>90%筹码成本集中度逐日小于等于前一日，下降或持平均有效，增加会中断。</p>
            <p>每组按至少N个交易日筛选，名单可重叠。大于20天需要至少21次比较，并显示实际已确认天数。历史不足时保留下界与原因。模型衰减系数2。</p>
            {scan && <p>本批包含{scan.universe_total.toLocaleString()}只已保存股票。</p>}
          </>,
        }}
        date={scan?.as_of}
        dateLabel="筛选截至"
      />
      <WorkbenchShell
        sidebar={
          <aside className="wb-sidebar conc-sidebar" aria-label="连续不增分组">
            <WorkbenchListHeader
              title="连续不增分组"
              count={scan ? matched.length : null}
              ready={!!scan}
              search={{
                value: search,
                label: '搜索集中度股票',
                onChange: (value) => {
                  setSearch(value);
                  setPage(0);
                  setCode('');
                },
              }}
              filters={concentrationGroupRegistry.map(
                ({ key: value, label }) => ({
                  id: String(value),
                  label,
                  count: scan?.groups[value].PASS ?? null,
                  active: value === group,
                  onSelect: () => chooseGroup(value),
                }),
              )}
            />
          </aside>
        }
        contentClassName="conc-workspace"
      >
        {!publication && (
          <WorkbenchState
            state={
              load.kind === 'error'
                ? { kind: 'error', message: load.message, onRetry: refresh }
                : { kind: 'loading', label: '正在读取连续集中度筛选…' }
            }
          />
        )}
        {publication && (
          <>
            {load.kind === 'refresh-error' && (
              <RetryMessage message={load.message} onRetry={refresh} />
            )}
            {source?.universe_coverage !==
              'OFFICIAL_CURRENT_LIST_RECONCILED' && (
              <p className="conc-coverage" role="note">
                股票池覆盖范围待核实。
              </p>
            )}
            <div className="conc-toolbar">
              <div>
                <h2>{groupDefinition?.title}</h2>
                <p>
                  本组入选 {scan!.groups[group].PASS.toLocaleString()}只 ·
                  无法判断 {scan!.groups[group].UNKNOWN.toLocaleString()}只
                </p>
              </div>
              <div className="conc-selects">
                <label>
                  查看
                  <select
                    aria-label="集中度名单状态"
                    value={category}
                    onChange={(event) => chooseCategory(event.target.value)}
                  >
                    {(['PASS', 'UNKNOWN', 'FAIL'] as const).map((value) => (
                      <option key={value} value={value}>
                        {concentrationStateText[value]} ·{' '}
                        {scan!.groups[group][value].toLocaleString()}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
            <p className="conc-note">
              {group === 'LONG'
                ? '大于20天需要至少21次比较、22个连续交易日读数；每股展示实际已确认长度与起点。'
                : `比较起点 ${scan!.sessions[20 - group]}，截至 ${scan!.as_of}。连续不增${group}天使用${group + 1}个连续交易日读数。`}
            </p>
            {matched.length ? (
              <>
                <div className="conc-table-wrap" ref={listArea}>
                  <table className="conc-table conc-sort-table">
                    <caption>
                      {concentrationStateText[category]}名单
                      {search.trim() ? `，搜索“${search.trim()}”` : ''}
                    </caption>
                    <thead ref={listHeader}>
                      <tr>
                        {sortColumns.map((column) => (
                          <th key={column.key} scope="col" aria-sort={sort.key === column.key ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                            <button type="button" title={column.title} onClick={() => sortBy(column.key)} aria-label={`${column.label}，点击${sort.key === column.key && sort.direction === 'asc' ? '降序' : '升序'}排序`}>
                              {column.label} <span aria-hidden="true">{sort.key === column.key ? (sort.direction === 'asc' ? '↑' : '↓') : '↕'}</span>
                            </button>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {visible.map((row) => {
                        const delta = drop(row, group);
                        return (
                          <tr
                            key={row.security_id}
                            aria-selected={
                              row.security_id === selected?.security_id
                            }
                          >
                            <td>
                              <button
                                type="button"
                                data-stock-key={row.security_id}
                                aria-pressed={
                                  row.security_id === selected?.security_id
                                }
                                onClick={() => setCode(row.security_id)}
                                onFocus={(event) => {
                                  event.currentTarget.scrollIntoView({
                                    block: 'nearest',
                                    inline: 'nearest',
                                  });
                                  const list = listArea.current;
                                  if (!list) return;
                                  const top = list.getBoundingClientRect().top +
                                    (listHeader.current?.getBoundingClientRect().height ?? 0);
                                  const rowTop = event.currentTarget.getBoundingClientRect().top;
                                  if (rowTop < top) list.scrollTop = Math.floor(list.scrollTop + rowTop - top);
                                }}
                              >
                                {row.name}
                                <small>
                                  {row.security_id}
                                  {row.short_history_days > 0
                                    ? ' · 短历史估算'
                                    : ''}
                                </small>
                              </button>
                            </td>
                            <td>{percent(row.values[20])}</td>
                            <td>{concentrationStreakText(row)}</td>
                            <td>
                              {percent(concentrationStartValue(row, group))}
                            </td>
                            <td>
                              {delta === null ? '待核实' : delta.toFixed(2)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <WorkbenchListFooter
                  count={matched.length}
                  page={effectivePage}
                  pageSize={pageSize}
                  onPageChange={setPage}
                />
              </>
            ) : (
              <WorkbenchState
                state={{
                  kind: 'empty',
                  title: search.trim()
                    ? '没有匹配的股票'
                    : category === 'PASS'
                      ? group === 'LONG'
                        ? '暂无连续不增大于20天的股票'
                        : `暂无连续不增${group}天的股票`
                      : '当前分类没有股票',
                  message: search.trim()
                    ? '可以调整名称、代码或分组。'
                    : '空名单表示当前已保存股票池在本组没有对应结果。',
                }}
              />
            )}
            {selected && (
              <section className="conc-detail" aria-label="所选股票集中度详情">
                <div className="conc-detail-heading">
                  <h2>
                    {selected.name}
                    <small>{selected.security_id}</small>
                  </h2>
                  <span>{percent(selected.values[20])}</span>
                </div>
                <div className="conc-metrics">
                  <span>
                    已确认连续不增{' '}
                    <strong>{concentrationStreakText(selected)}</strong>
                  </span>
                  <span>
                    本组净变化{' '}
                    <strong>
                      {selectedDrop === null
                        ? '待核实'
                        : `${selectedDrop.toFixed(2)}个百分点`}
                    </strong>
                  </span>
                  <span>
                    连续段起点{' '}
                    <strong>{selected.streak_start_session ?? '待核实'}</strong>
                  </span>
                </div>
                {selected.streak_boundary.kind !== 'INCREASE' && (
                  <p className="conc-note" aria-label="连续天数追溯边界">
                    追溯边界 {selected.streak_boundary.session}，
                    {selected.streak_boundary.kind === 'UNAVAILABLE' &&
                    selected.streak_boundary.reason === 'NO_TRADE'
                      ? '较早交易日确认无成交，没有可比较读数'
                      : concentrationReason(selected.streak_boundary.reason)}。
                    {selected.values[20] === null
                      ? '最新集中度尚无有效读数。'
                      : '连续天数按已确认下界显示，更早情况无法确认。'}
                  </p>
                )}
                {selected.short_history_days > 0 && (
                  <p className="conc-note">
                    其中{selected.short_history_days}
                    个日期使用不足210根的已保存可用历史估算。
                  </p>
                )}
                <SeriesChart row={selected} scan={scan!} group={group} />
                <p className="conc-note">
                  浅色区间为当前分组的比较窗口。缺失读数留空，图表缩放不影响筛选。
                </p>
                <ConcentrationCandles
                  source={publication.source}
                  security={selected}
                  period={chartPeriod}
                  onPeriodChange={setChartPeriod}
                />
                <InspectorTabs<Tab>
                  idPrefix="concentration-inspector"
                  label="集中度核对资料"
                  className="conc-tabs"
                  value={tab}
                  onChange={setTab}
                  tabs={[
                    { id: 'series', label: '逐日读数' },
                    { id: 'method', label: '口径与范围' },
                  ]}
                />
                {tab === 'series' ? (
                  <div
                    id="concentration-inspector-series"
                    role="tabpanel"
                    aria-labelledby="concentration-inspector-series-tab"
                  >
                    <details className="conc-series-table">
                      <summary>
                        展开{selectedSeries!.values.length}
                        个交易日的精确读数与缺失说明
                      </summary>
                      <div className="conc-table-wrap">
                        <table className="conc-table">
                          <thead>
                            <tr>
                              <th>交易日</th>
                              <th>90%集中度</th>
                              <th>说明</th>
                            </tr>
                          </thead>
                          <tbody>
                            {selectedSeries!.sessions.map((date, index) => (
                              <tr key={date}>
                                <td>{date}</td>
                                <td>
                                  {selectedSeries!.values[index] === null
                                    ? '无法计算'
                                    : `${selectedSeries!.values[index]}%`}
                                </td>
                                <td>
                                  {selectedSeries!.reasons[String(index)]
                                    ? concentrationReason(
                                        selectedSeries!.reasons[String(index)],
                                      )
                                    : '有效读数'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  </div>
                ) : (
                  <div
                    id="concentration-inspector-method"
                    role="tabpanel"
                    aria-labelledby="concentration-inspector-method-tab"
                    className="conc-method"
                  >
                    <p>
                      90%集中度 =（Q95 − Q5）÷（Q95 +
                      Q5）×100%。Q5、Q95为累计筹码首次达到5%、95%的价格节点。数值越小，成本价格越集中。
                    </p>
                    <p>
                      每个日期独立使用截至当日最近210根有效不复权行情；历史不足时按已核验的可用输入估算并保留说明。模型为系数2、150个动态价格节点、OHLC4三角成交分配与零初始化，未认证为某款看盘软件的完全等价实现。
                    </p>
                    <p>
                      筛选比较未四舍五入的数值。连续不增N天需要N+1个连续市场交易日，每一日均小于等于前一日；相等有效，增加或确认无交易会中断。缺数据不跳过，有已知反例的窗口为未满足，其余所需读数缺失为无法判断。两组按至少对应天数计入，允许重叠；大于20天需要至少21次比较。仅增加边界给出精确长度，其余边界显示“≥N天”。长期组显示完整N+1点，20天组显示最近21点。
                    </p>
                    <p>
                      当前股票池范围沿用已发布目录，不套用其他页面的价格、涨停或主峰条件。短历史读数{' '}
                      {selected.short_history_days}
                      日；它不证明已重建全部上市历史。该指标描述成本价格分布，不确定股东集中程度或后续涨跌。
                    </p>
                    <SiteLink href="/chips">查看每日筹码工作台</SiteLink>
                  </div>
                )}
              </section>
            )}
          </>
        )}
      </WorkbenchShell>
    </div>
  );
}
