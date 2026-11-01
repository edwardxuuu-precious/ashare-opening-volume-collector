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
import {
  concentrationGroups,
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
type Sort = 'streak' | 'lowest' | 'drop';
type Tab = 'series' | 'method';
const percent = (value: number | null | undefined) =>
  value === null || value === undefined ? '待核实' : `${value.toFixed(2)}%`;
const drop = (row: ConcentrationRow, group: ConcentrationGroup) => {
  const start = row.values[20 - group],
    current = row.values[20];
  return start === null || current === null ? null : start - current;
};

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
  sessions,
  group,
}: {
  row: ConcentrationRow;
  sessions: string[];
  group: ConcentrationGroup;
}) {
  const available = row.values.filter(
    (value): value is number => value !== null,
  );
  if (!available.length)
    return <p className="conc-note">当前股票没有可绘制的集中度读数。</p>;
  const { low, high } = chartRange(available);
  const x = (index: number) => 48 + (index / 20) * 686;
  const y = (value: number) => 180 - ((value - low) / (high - low)) * 164;
  const segments: string[] = [];
  let current: string[] = [];
  for (const [index, value] of row.values.entries()) {
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
      aria-label={`${row.name}近21个交易日90%筹码集中度`}
    >
      <title>{row.name}，数值越小，成本越集中；缺失日期留空。</title>
      <rect
        x={x(20 - group)}
        y="12"
        width={x(20) - x(20 - group)}
        height="168"
        className="conc-chart-window"
      />
      {[0, 1, 2, 3].map((index) => {
        const value = low + ((high - low) * index) / 3;
        return (
          <g key={index}>
            <line
              x1="48"
              x2="734"
              y1={y(value)}
              y2={y(value)}
              className="conc-chart-grid"
            />
            <text x="40" y={y(value) + 4} textAnchor="end">
              {value.toFixed(1)}%
            </text>
          </g>
        );
      })}
      {segments.map((points, index) => (
        <polyline key={index} points={points} className="conc-chart-line" />
      ))}
      {row.values.map((value, index) =>
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
      {[0, 5, 10, 15, 20].map((index) => (
        <text key={index} x={x(index)} y="205" textAnchor="middle">
          {sessions[index].slice(5)}
        </text>
      ))}
    </svg>
  );
}

export default function ConcentrationPage() {
  const [load, setLoad] = useState<ReadState>({ kind: 'loading' });
  const [retry, setRetry] = useState(0),
    [group, setGroup] = useState<ConcentrationGroup>(3);
  const [category, setCategory] = useState<ConcentrationState>('PASS'),
    [search, setSearch] = useState('');
  const [sort, setSort] = useState<Sort>('streak'),
    [page, setPage] = useState(0),
    [code, setCode] = useState('');
  const [tab, setTab] = useState<Tab>('series');
  const [chartPeriod, setChartPeriod] = useState(120);
  const listArea = useRef<HTMLDivElement>(null);
  const publication = 'publication' in load ? load.publication : null;
  const scan = publication?.scan,
    source = publication?.source;
  const busy = load.kind === 'loading' || load.kind === 'refreshing';
  useEffect(() => {
    const controller = new AbortController();
    void read(
      '/data/chips/daily/manifest.json',
      controller.signal,
      parseConcentrationSource,
    )
      .then(async (value) => {
        const result = await read(
          `/data/chips/daily/${value.active_slot}/concentration.json`,
          controller.signal,
          (input) => parseConcentrationScan(input, value),
        );
        if (!controller.signal.aborted)
          setLoad({
            kind: 'ready',
            publication: { source: value, scan: result },
          });
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        const message =
          error instanceof Error ? error.message : '集中度结果无法读取。';
        setLoad((previous) =>
          'publication' in previous
            ? {
                kind: 'refresh-error',
                publication: previous.publication,
                message: `${message} 仍显示上次完整结果。`,
              }
            : { kind: 'error', message },
        );
      });
    return () => controller.abort();
  }, [retry]);
  const rows = scan?.rows ?? emptyRows;
  const matched = useMemo(() => {
    const selected = rows.filter(
      (row) =>
        row.groups[group] === category &&
        stockListMatches(search, row.name, row.security_id),
    );
    return selected.sort((a, b) => {
      const comparison =
        sort === 'lowest'
          ? (a.values[20] ?? Infinity) - (b.values[20] ?? Infinity)
          : sort === 'drop'
            ? (drop(b, group) ?? -Infinity) - (drop(a, group) ?? -Infinity)
            : b.streak_days - a.streak_days;
      return (
        (Number.isNaN(comparison) ? 0 : comparison) ||
        a.security_id.localeCompare(b.security_id)
      );
    });
  }, [rows, group, category, search, sort]);
  const selected =
    matched.find((row) => row.security_id === code) ?? matched[0];
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
    page: effectivePage,
    enabled: !!scan,
  });
  function refresh() {
    setLoad((previous) =>
      'publication' in previous
        ? { kind: 'refreshing', publication: previous.publication }
        : { kind: 'loading' },
    );
    setRetry((value) => value + 1);
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
  function chooseSort(value: string) {
    if (value === 'streak' || value === 'lowest' || value === 'drop') {
      setSort(value);
      setPage(0);
    }
  }
  const selectedDrop = selected ? drop(selected, group) : null;
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
        description="90%筹码成本集中度逐日严格下降 · 历史换手衰减系数2"
        date={scan?.as_of}
        dateLabel="筛选截至"
      />
      <WorkbenchShell
        sidebar={
          <aside className="wb-sidebar conc-sidebar" aria-label="连续下降分组">
            <WorkbenchListHeader
              title="连续下降分组"
              count={scan?.groups[group][category] ?? null}
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
              filters={concentrationGroups.map((value) => ({
                id: String(value),
                label: `连续${value}天`,
                count: scan?.groups[value].PASS ?? null,
                active: value === group,
                onSelect: () => chooseGroup(value),
              }))}
            />
            <div className="conc-sidebar-note">
              <p>每组按“至少N个交易日”筛选，名单可重叠。</p>
              <dl>
                <div>
                  <dt>集中度口径</dt>
                  <dd>90%成本区间</dd>
                </div>
                <div>
                  <dt>衰减系数</dt>
                  <dd>2</dd>
                </div>
                <div>
                  <dt>比较方式</dt>
                  <dd>逐日严格降低</dd>
                </div>
              </dl>
              <p>相等会中断。缺少所需历史时显示无法判断。</p>
            </div>
          </aside>
        }
        contentClassName="conc-workspace"
        footer={
          <>
            <span>
              {scan
                ? `${scan.universe_total.toLocaleString()}只已保存股票 · 数据截至${scan.as_of}`
                : '读取已发布筹码数据'}
            </span>
            <span>系数2 · 90%成本集中度</span>
          </>
        }
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
                当前按已保存的股票池筛选，最新上市名单覆盖仍待核实。
              </p>
            )}
            <div className="conc-toolbar">
              <div>
                <h2>至少连续下降{group}个交易日</h2>
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
                <label>
                  排序
                  <select
                    aria-label="集中度排序"
                    value={sort}
                    onChange={(event) => chooseSort(event.target.value)}
                  >
                    <option value="streak">连续下降天数</option>
                    <option value="lowest">当前集中度从低到高</option>
                    <option value="drop">区间降幅从高到低</option>
                  </select>
                </label>
              </div>
            </div>
            <p className="conc-note">
              比较起点 {scan!.sessions[20 - group]}，截至 {scan!.as_of}
              。连续下降{group}天使用{group + 1}个连续交易日读数。
            </p>
            {matched.length ? (
              <>
                <div className="conc-table-wrap" ref={listArea}>
                  <table className="conc-table">
                    <caption>
                      {concentrationStateText[category]}名单
                      {search.trim() ? `，搜索“${search.trim()}”` : ''}
                    </caption>
                    <thead>
                      <tr>
                        <th>股票</th>
                        <th>当前集中度</th>
                        <th>连续下降</th>
                        <th>起点集中度</th>
                        <th>区间降幅（百分点）</th>
                        <th>状态</th>
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
                                onFocus={(event) =>
                                  event.currentTarget.scrollIntoView({
                                    block: 'nearest',
                                    inline: 'nearest',
                                  })
                                }
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
                            <td>{percent(row.values[20 - group])}</td>
                            <td>
                              {delta === null ? '待核实' : delta.toFixed(2)}
                            </td>
                            <td>{concentrationStateText[row.groups[group]]}</td>
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
                      ? `暂无连续下降${group}天的股票`
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
                    已确认连续下降{' '}
                    <strong>{concentrationStreakText(selected)}</strong>
                  </span>
                  <span>
                    本组净下降{' '}
                    <strong>
                      {selectedDrop === null
                        ? '待核实'
                        : `${selectedDrop.toFixed(2)}个百分点`}
                    </strong>
                  </span>
                </div>
                {selected.short_history_days > 0 && (
                  <p className="conc-note">
                    其中{selected.short_history_days}
                    个日期使用不足210根的已保存可用历史估算。
                  </p>
                )}
                <SeriesChart
                  row={selected}
                  sessions={scan!.sessions}
                  group={group}
                />
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
                      <summary>展开21个交易日的精确读数与缺失说明</summary>
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
                            {scan!.sessions.map((date, index) => (
                              <tr key={date}>
                                <td>{date}</td>
                                <td>
                                  {selected.values[index] === null
                                    ? '无法计算'
                                    : `${selected.values[index]}%`}
                                </td>
                                <td>
                                  {selected.point_reasons[String(index)]
                                    ? concentrationReason(
                                        selected.point_reasons[String(index)],
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
                      筛选比较未四舍五入的数值。连续下降N天需要N+1个连续市场交易日，每一日均严格低于前一日；相等、上升或确认无交易会中断。缺数据不跳过，有已知反例的窗口为未满足，其余所需读数缺失为无法判断。各组按至少N天计入，允许重叠；“≥20天”表示当前窗口至少已确认20次下降。
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
