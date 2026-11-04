'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { SiteHeader } from '@/components/site-header';
import { StockPicker } from '@/components/stock-picker';
import { TriangleChart } from '@/components/triangle-chart';
import { TriangleDaily } from '@/components/triangle-daily';
import {
  loadTriangleCatalog,
  loadTriangleData,
  triangleDatasetKey,
  type TriangleCatalog,
  type TriangleDataset,
  type TriangleSecurity,
  type TriangleSelection,
} from '@/lib/triangle-data';
import {
  analyzeTriangles,
  defaultTriangleParams,
  parameterFields,
  requiredTriangleHistory,
  validateTriangleParams,
  type TriangleAnalysis,
  type TriangleFormation,
  type TriangleParams,
  type PriceZone,
  type PathStatus,
} from '@/lib/triangles';
import './triangles.css';

export const dynamic = 'force-static';
const dataBase = (process.env.NEXT_PUBLIC_DATA_BASE || '').replace(/\/$/, '');
type Summary = {
  security: TriangleSecurity;
  formations: readonly TriangleFormation[];
  availability: TriangleAnalysis['availability'];
};
type CatalogState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; value: TriangleCatalog };
type ResultState =
  | { kind: 'idle' }
  | { kind: 'loading'; phase: string; completed: number; total: number }
  | { kind: 'error'; message: string }
  | {
      kind: 'ready';
      data: TriangleDataset;
      params: TriangleParams;
      summaries: Summary[];
    };
type Stage =
  | 'active'
  | 'all'
  | 'breakout'
  | 'retest'
  | 'observing'
  | 'rejected'
  | 'invalidated'
  | 'expired'
  | 'unknown';
const stageOptions: { value: Stage; label: string }[] = [
  { value: 'active', label: '有效观察' },
  { value: 'all', label: '全部状态' },
  { value: 'breakout', label: '突破压力已确认' },
  { value: 'retest', label: '回踩支撑已确认' },
  { value: 'observing', label: '死三角风险观察' },
  { value: 'rejected', label: '前期背景不满足' },
  { value: 'invalidated', label: '已失效' },
  { value: 'expired', label: '已到期' },
  { value: 'unknown', label: '数据不足 / 中断' },
];

function closedDates(catalog: TriangleCatalog) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date());
  const part = (kind: string) =>
    parts.find((item) => item.type === kind)?.value ?? '';
  const today = `${part('year')}-${part('month')}-${part('day')}`;
  const afterClose =
    Number(part('hour')) * 60 + Number(part('minute')) >= 15 * 60 + 30;
  return catalog.sessions.filter(
    (date) => date < today || (date === today && afterClose),
  );
}
function price(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : value.toFixed(2);
}
function zoneText(zone: PriceZone | null | undefined) {
  return zone ? `${price(zone.lower)} 至 ${price(zone.upper)}` : '未识别';
}
function lastDate(formation: TriangleFormation) {
  return formation.events.at(-1)?.knownAt ?? formation.formedAt;
}
function isActive(formation: TriangleFormation) {
  return (
    formation.qualified &&
    (formation.state.kind === 'active' || formation.state.kind === 'observing')
  );
}
function statusText(formation: TriangleFormation | null) {
  if (!formation) return '数据不足 / 无完整形态';
  const state = formation.state;
  if (state.kind === 'active') {
    if (
      state.breakout.kind === 'confirmed' &&
      state.retest.kind === 'confirmed'
    )
      return '突破与回踩均已确认';
    if (state.breakout.kind === 'confirmed') return '突破压力已确认';
    if (state.retest.kind === 'confirmed') return '回踩支撑已确认';
    return state.breakout.kind === 'testing' || state.retest.kind === 'testing'
      ? '观察中，等待连续确认'
      : '金三角形成，等待价格条件';
  }
  return {
    observing: '死三角风险观察',
    rejected: '前期背景不满足',
    invalidated: '已失效',
    expired: '已到期',
    'coverage-ended': '数据中断，待判定',
  }[state.kind];
}
function pathText(path: PathStatus) {
  if (path.kind === 'waiting') return '等待触发';
  if (path.kind === 'testing')
    return `${path.startedAt} 起观察，连续 ${path.observedSessions}/${path.requiredSessions} 日`;
  return `${path.knownAt} 确认，起点 ${path.occurredAt}`;
}
function analyzeAvailableHistory(
  input: Parameters<typeof analyzeTriangles>[0],
  calendarKnown: boolean,
): TriangleAnalysis {
  if (calendarKnown) return analyzeTriangles(input);
  return {
    asOf: input.asOf,
    params: input.params,
    points: input.bars
      .filter((bar) => bar.date <= input.asOf)
      .map((bar) => ({
        date: bar.date,
        bar,
        fast: null,
        middle: null,
        slow: null,
      })),
    crosses: [],
    formations: [],
    availability: {
      kind: 'insufficient',
      reason: '交易日历未确认，保留价格，暂不计算均线和形态确认',
      consecutiveBars: 0,
    },
  };
}
function matchesStage(formation: TriangleFormation, stage: Stage) {
  if (stage === 'all') return true;
  if (stage === 'active') return isActive(formation);
  if (stage === 'breakout')
    return (
      formation.state.kind === 'active' &&
      formation.state.breakout.kind === 'confirmed'
    );
  if (stage === 'retest')
    return (
      formation.state.kind === 'active' &&
      formation.state.retest.kind === 'confirmed'
    );
  if (stage === 'unknown') return formation.state.kind === 'coverage-ended';
  return formation.state.kind === stage;
}

function ZoneEvidence({
  zone,
  reason,
  label,
}: {
  zone: PriceZone | null;
  reason: string;
  label: string;
}) {
  return (
    <article className="triangle-zone-card">
      <h3>{label}</h3>
      <strong>{zone ? `${zoneText(zone)} 元` : '尚无合格价区'}</strong>
      {zone ? (
        <>
          <p>
            参考价 {price(zone.center)} 元 · {zone.touches.length}次有效测试
          </p>
          <p>
            冻结于 {zone.frozenAt}，依据在 {zone.knownAt} 已可知。
          </p>
          <div className="triangle-table-scroll">
            <table>
              <caption>{label}的历史触点</caption>
              <thead>
                <tr>
                  <th>触点发生日</th>
                  <th>条件确认可知日</th>
                  <th>价格</th>
                </tr>
              </thead>
              <tbody>
                {zone.touches.map((touch) => (
                  <tr key={touch.occurredAt}>
                    <td>{touch.occurredAt}</td>
                    <td>{touch.knownAt}</td>
                    <td>{price(touch.price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p>{reason}</p>
      )}
    </article>
  );
}

export default function TrianglesPage() {
  const [mode, setMode] = useState<'daily' | 'research'>('daily');
  return mode === 'daily' ? (
    <TriangleDaily onResearch={() => setMode('research')} />
  ) : (
    <>
      <div className="triangle-research-return">
        <button onClick={() => setMode('daily')}>返回每日自动结果</button>
        <span>自定义参数研究 · 需应用参数后重新计算</span>
      </div>
      <TriangleResearch />
    </>
  );
}

function TriangleResearch() {
  const [catalogState, setCatalogState] = useState<CatalogState>({
    kind: 'loading',
  });
  const [catalogRetry, setCatalogRetry] = useState(0);
  const [draft, setDraft] = useState<TriangleParams>({
    ...defaultTriangleParams,
  });
  const [asOf, setAsOf] = useState('');
  const [historySessions, setHistorySessions] = useState(140);
  const [recentSessions, setRecentSessions] = useState(5);
  const [scope, setScope] = useState<'all' | 'single' | 'multiple'>('all');
  const [symbols, setSymbols] = useState<string[]>([]);
  const [state, setState] = useState<ResultState>({ kind: 'idle' });
  const [formError, setFormError] = useState('');
  const [stage, setStage] = useState<Stage>('active');
  const [direction, setDirection] = useState<'all' | 'golden' | 'death'>('all');
  const [market, setMarket] = useState('all');
  const [search, setSearch] = useState('');
  const [selectedCode, setSelectedCode] = useState('');
  const [formationId, setFormationId] = useState('');
  const [page, setPage] = useState(1);
  const active = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const cached = useRef<TriangleDataset | null>(null);
  const restored = useRef(false);
  const catalog = catalogState.kind === 'ready' ? catalogState.value : null;
  const result = state.kind === 'ready' ? state : null;
  const busy = state.kind === 'loading';
  const dates = useMemo(() => (catalog ? closedDates(catalog) : []), [catalog]);
  const selection: TriangleSelection =
    scope === 'all' ? { kind: 'all' } : { kind: 'symbols', symbols };
  const changed =
    result &&
    (triangleDatasetKey(asOf, historySessions, selection) !== result.data.key ||
      JSON.stringify(draft) !== JSON.stringify(result.params));

  useEffect(() => {
    const controller = new AbortController();
    loadTriangleCatalog({ dataBase, signal: controller.signal })
      .then((value) => {
        if (controller.signal.aborted) return;
        const available = closedDates(value);
        const query = new URLSearchParams(window.location.search);
        if (!restored.current) {
          const params = { ...defaultTriangleParams };
          for (const field of parameterFields)
            if (query.has(field.key))
              params[field.key] = Number(query.get(field.key));
          setDraft(params);
          if (query.has('history'))
            setHistorySessions(Number(query.get('history')));
          const requestedScope = query.get('scope');
          if (requestedScope === 'single' || requestedScope === 'multiple')
            setScope(requestedScope);
          setSymbols(
            (query.get('symbols') ?? '')
              .split(',')
              .filter((code) => /^\d{6}$/.test(code))
              .slice(0, requestedScope === 'single' ? 1 : 100),
          );
          restored.current = true;
        }
        setAsOf((previous) =>
          available.includes(previous)
            ? previous
            : available.includes(query.get('asOf') ?? '')
              ? (query.get('asOf') ?? '')
              : (available.at(-1) ?? ''),
        );
        setCatalogState({ kind: 'ready', value });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setCatalogState({
            kind: 'error',
            message:
              error instanceof Error ? error.message : '日行情目录无法读取。',
          });
      });
    return () => controller.abort();
  }, [catalogRetry]);
  useEffect(
    () => () => {
      generation.current++;
      active.current?.abort();
    },
    [],
  );

  async function run(forceRead = false) {
    if (!catalog) return;
    let params: TriangleParams;
    try {
      params = validateTriangleParams(draft);
      if (!dates.includes(asOf)) throw Error('请选择已收盘的交易日。');
      if (
        !Number.isInteger(historySessions) ||
        historySessions < 20 ||
        historySessions > 186
      )
        throw Error('历史长度须为20至186个交易日。');
      if (
        selection.kind === 'symbols' &&
        (!selection.symbols.length ||
          (scope === 'single' && selection.symbols.length !== 1) ||
          selection.symbols.some((code) => !/^\d{6}$/.test(code)))
      )
        throw Error('请选择有效股票，单只模式须只选择一只。');
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '参数无效。');
      return;
    }
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const token = ++generation.current;
    setFormError('');
    setState({
      kind: 'loading',
      phase: '正在读取已保存日行情',
      completed: 0,
      total: historySessions,
    });
    try {
      const key = triangleDatasetKey(asOf, historySessions, selection);
      const data =
        !forceRead && cached.current?.key === key
          ? cached.current
          : await loadTriangleData({
              catalog,
              dataBase,
              signal: controller.signal,
              asOf,
              historySessions,
              selection,
              onProgress: (completed, total) => {
                if (generation.current === token)
                  setState({
                    kind: 'loading',
                    phase: '正在读取已保存日行情',
                    completed,
                    total,
                  });
              },
            });
      cached.current = data;
      const summaries: Summary[] = [];
      for (let index = 0; index < data.series.length; index++) {
        if (controller.signal.aborted || generation.current !== token)
          throw new DOMException('Aborted', 'AbortError');
        const item = data.series[index];
        const analysis = analyzeAvailableHistory(
          { bars: item.bars, params, asOf: data.asOf },
          data.coverage.calendarKnown,
        );
        summaries.push({
          security: item.security,
          formations: analysis.formations,
          availability: analysis.availability,
        });
        if (index % 32 === 0) {
          setState({
            kind: 'loading',
            phase: '正在逐日计算三角和关键价位',
            completed: index + 1,
            total: data.series.length,
          });
          await new Promise<void>((resolve) => setTimeout(resolve, 0));
        }
      }
      if (controller.signal.aborted || generation.current !== token) return;
      const first =
        summaries.find((item) => item.formations.some(isActive)) ??
        summaries.find((item) => item.availability.kind === 'ready') ??
        summaries[0];
      setSelectedCode((previous) =>
        data.series.some((item) => item.security.code === previous)
          ? previous
          : (first?.security.code ?? ''),
      );
      setFormationId('');
      setPage(1);
      setState({ kind: 'ready', data, params, summaries });
      const query = new URLSearchParams({
        asOf,
        history: String(historySessions),
        scope,
      });
      if (selection.kind === 'symbols')
        query.set('symbols', [...new Set(selection.symbols)].join(','));
      for (const field of parameterFields)
        if (params[field.key] !== defaultTriangleParams[field.key])
          query.set(field.key, String(params[field.key]));
      window.history.replaceState(
        null,
        '',
        `${window.location.pathname}?${query}`,
      );
    } catch (error) {
      if (generation.current === token && !controller.signal.aborted)
        setState({
          kind: 'error',
          message: error instanceof Error ? error.message : '筛选无法完成。',
        });
    } finally {
      if (generation.current === token) active.current = null;
    }
  }
  function cancel() {
    generation.current++;
    active.current?.abort();
    active.current = null;
    setState({ kind: 'idle' });
  }
  function reset() {
    setDraft({ ...defaultTriangleParams });
    setHistorySessions(140);
    setRecentSessions(5);
    setFormError('');
  }
  function filterChanged() {
    setPage(1);
  }

  const stats = useMemo(() => {
    const summaries = result?.summaries ?? [];
    return {
      active: result?.data.coverage.calendarKnown
        ? summaries.filter((item) => item.formations.some(isActive)).length
        : 0,
      unknown: summaries.filter(
        (item) =>
          !result?.data.coverage.calendarKnown ||
          item.availability.kind === 'insufficient',
      ).length,
      raw: summaries.filter((item) =>
        item.formations.some((formation) => !formation.qualified),
      ).length,
    };
  }, [result]);
  const rows = useMemo(() => {
    if (!result) return [];
    const cutoff =
      recentSessions > 0
        ? (result.data.sessions.at(-recentSessions) ?? result.data.sessions[0])
        : '';
    return result.summaries
      .flatMap((summary) => {
        const security = summary.security;
        if (
          (market !== 'all' && security.market !== market) ||
          (search &&
            !`${security.code} ${security.name}`
              .toLowerCase()
              .includes(search.toLowerCase()))
        )
          return [];
        if (!result.data.coverage.calendarKnown)
          return stage === 'unknown' || stage === 'all'
            ? [{ summary, formation: null }]
            : [];
        const unavailable = summary.availability.kind === 'insufficient';
        if (
          unavailable &&
          ['active', 'breakout', 'retest', 'observing'].includes(stage)
        )
          return [];
        const matches = summary.formations.filter(
          (formation) =>
            (direction === 'all' || formation.direction === direction) &&
            matchesStage(formation, stage) &&
            (!cutoff || lastDate(formation) >= cutoff),
        );
        const formation = matches.at(-1) ?? null;
        if (stage === 'unknown' && unavailable && !formation)
          return [{ summary, formation: null }];
        return formation || (stage === 'all' && direction === 'all')
          ? [{ summary, formation }]
          : [];
      })
      .sort(
        (a, b) =>
          (b.formation ? lastDate(b.formation) : '').localeCompare(
            a.formation ? lastDate(a.formation) : '',
          ) || a.summary.security.code.localeCompare(b.summary.security.code),
      );
  }, [result, stage, direction, market, search, recentSessions]);
  const selected = result?.data.series.find(
    (item) => item.security.code === selectedCode,
  );
  const selectedAnalysis = useMemo(
    () =>
      selected && result
        ? analyzeAvailableHistory(
            {
              bars: selected.bars,
              params: result.params,
              asOf: result.data.asOf,
            },
            result.data.coverage.calendarKnown,
          )
        : null,
    [selected, result],
  );
  const formation =
    selectedAnalysis?.formations.find((item) => item.id === formationId) ??
    selectedAnalysis?.formations.at(-1) ??
    null;
  const pageCount = Math.max(1, Math.ceil(rows.length / 50));
  const shownPage = Math.min(page, pageCount);
  const minimumHistory =
    requiredTriangleHistory(draft) + draft.formationSessions - 1;
  function parameterInput(field: (typeof parameterFields)[number]) {
    return (
      <label key={field.key} className="triangle-field">
        <span>{field.label}</span>
        <input
          type="number"
          name={field.key}
          aria-label={field.label}
          min={field.min}
          max={field.max}
          step={field.step}
          value={Number.isFinite(draft[field.key]) ? draft[field.key] : ''}
          onChange={(event) =>
            setDraft((previous) => ({
              ...previous,
              [field.key]: event.target.valueAsNumber,
            }))
          }
        />
      </label>
    );
  }

  return (
    <div className="triangle-site">
      <SiteHeader active="triangles" />
      <main className="triangle-main">
        <div className="triangle-title">
          <div>
            <p>价格趋势观察</p>
            <h1>趋势三角与关键点位</h1>
          </div>
          <span>先识别背景，再等待价格条件</span>
        </div>
        <p className="triangle-scope">
          日线均线交叉 · 前期涨跌背景 ·
          支撑压力依据。当前使用不复权价格，除权除息等公司行为尚未核对，结果作为观察候选。默认参数尚未经过收益验证。
        </p>
        {catalogState.kind === 'loading' && (
          <output className="triangle-empty">正在读取私有行情目录…</output>
        )}
        {catalogState.kind === 'error' && (
          <div className="triangle-error" role="alert">
            {catalogState.message}
            <button
              onClick={() => {
                setCatalogState({ kind: 'loading' });
                setCatalogRetry((value) => value + 1);
              }}
            >
              重新读取目录
            </button>
          </div>
        )}
        {catalog && (
          <>
            {catalog.stockListNotice && (
              <p className="triangle-notice">{catalog.stockListNotice}</p>
            )}
            <form
              className="triangle-form"
              onSubmit={(event) => {
                event.preventDefault();
                void run();
              }}
            >
              <fieldset disabled={busy}>
                <legend>筛选范围与规则</legend>
                <div className="triangle-query-grid">
                  <label className="triangle-field">
                    <span>股票范围</span>
                    <select
                      aria-label="股票范围"
                      value={scope}
                      onChange={(event) => {
                        const value = event.target.value;
                        if (
                          value === 'all' ||
                          value === 'single' ||
                          value === 'multiple'
                        ) {
                          setScope(value);
                          if (value === 'single')
                            setSymbols((previous) => previous.slice(0, 1));
                        }
                      }}
                    >
                      <option value="all">全市场已保存行情</option>
                      <option value="single">单只股票</option>
                      <option value="multiple">指定多只股票</option>
                    </select>
                  </label>
                  <label className="triangle-field">
                    <span>历史截止日</span>
                    <select
                      aria-label="历史截止日"
                      value={asOf}
                      onChange={(event) => setAsOf(event.target.value)}
                    >
                      <option value="" disabled>
                        选择交易日
                      </option>
                      {[...dates].reverse().map((date) => (
                        <option key={date} value={date}>
                          {date}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="triangle-field">
                    <span>读取历史交易日</span>
                    <input
                      type="number"
                      aria-label="读取历史交易日"
                      min="20"
                      max="186"
                      step="1"
                      value={
                        Number.isFinite(historySessions) ? historySessions : ''
                      }
                      onChange={(event) =>
                        setHistorySessions(event.target.valueAsNumber)
                      }
                    />
                  </label>
                  {scope !== 'all' && (
                    <div className="triangle-stock-picker">
                      <span>选择股票</span>
                      {catalog.securities.length ? (
                        <StockPicker
                          stocks={catalog.securities}
                          value={symbols}
                          multiple={scope === 'multiple'}
                          onChange={setSymbols}
                        />
                      ) : (
                        <input
                          aria-label="输入股票代码"
                          placeholder="六位代码，多只用逗号分隔"
                          value={symbols.join(',')}
                          onChange={(event) =>
                            setSymbols(
                              event.target.value
                                .split(/[,，\s]+/)
                                .filter(Boolean),
                            )
                          }
                        />
                      )}
                    </div>
                  )}
                </div>
                <div className="triangle-parameters">
                  {parameterFields
                    .filter((field) => !field.advanced)
                    .map(parameterInput)}
                </div>
                <details className="triangle-advanced">
                  <summary>调整价位识别、确认和有效期参数</summary>
                  <div className="triangle-parameters">
                    {parameterFields
                      .filter((field) => field.advanced)
                      .map(parameterInput)}
                  </div>
                </details>
              </fieldset>
              <p className="triangle-form-note">
                当前参数至少需要约{' '}
                {Number.isFinite(minimumHistory) ? minimumHistory : '—'}{' '}
                个连续有效日，才能完整覆盖一次形成窗口。历史不足保留为待判定。调整规则后可复用本次已读取行情。
              </p>
              <div className="triangle-actions">
                <button
                  className="triangle-primary"
                  type="submit"
                  disabled={busy || !asOf}
                >
                  {busy ? '正在筛选…' : result ? '应用参数并筛选' : '开始筛选'}
                </button>
                {busy && (
                  <button type="button" onClick={cancel}>
                    取消
                  </button>
                )}
                <button type="button" disabled={busy} onClick={reset}>
                  恢复默认参数
                </button>
                {result && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void run(true)}
                  >
                    重新读取行情
                  </button>
                )}
              </div>
              {formError && (
                <p className="triangle-error" role="alert">
                  {formError}
                </p>
              )}
            </form>
          </>
        )}
        {state.kind === 'loading' && (
          <output className="triangle-progress" aria-live="polite">
            <span>
              {state.phase} · {state.completed}/{state.total}
            </span>
            <progress max={Math.max(1, state.total)} value={state.completed} />
          </output>
        )}
        {state.kind === 'error' && (
          <p className="triangle-error" role="alert">
            {state.message}
          </p>
        )}
        {state.kind === 'idle' && catalog && (
          <p className="triangle-empty">
            选择截止日与规则后开始筛选。仅查询已保存行情，读取过程可以取消。
          </p>
        )}
        {result && (
          <>
            {changed && (
              <output className="triangle-notice" aria-live="polite">
                参数或查询范围已修改。下方仍是上一次应用的结果，点击“应用参数并筛选”后更新。
              </output>
            )}
            <section className="triangle-results" aria-label="三角筛选结果">
              <div className="triangle-section-heading">
                <div>
                  <h2>筛选结果</h2>
                  <p>
                    {result.data.sessions[0]} 至 {result.data.asOf} · 已应用 MA
                    {result.params.fastPeriod}/{result.params.middlePeriod}/
                    {result.params.slowPeriod}
                  </p>
                </div>
                <span>{result.data.series.length}只已检查</span>
              </div>
              <output className="triangle-coverage">
                成功读取 {result.data.coverage.loadedDays}/
                {result.data.coverage.requestedDays} 个交易日 · 全段日行情完整{' '}
                {result.data.coverage.completePriceSeries} 只 · 当前有效观察{' '}
                {stats.active} 只 · 数据不足 {stats.unknown} 只 ·
                背景不满足记录涉及 {stats.raw} 只。
              </output>
              <p className="triangle-form-note">
                全市场范围使用当前名单与所读历史记录的并集，不代表历史当日完整上市名单。
                {result.data.coverage.universeTotal !== null &&
                  `目录声明当前范围 ${result.data.coverage.universeTotal} 只。`}
                {result.data.coverage.requestedDays <
                  result.data.coverage.requestedHistory &&
                  `目录仅提供本次 ${result.data.coverage.requestedDays} 日，少于请求的 ${result.data.coverage.requestedHistory} 日。`}
              </p>
              {!result.data.coverage.calendarKnown && (
                <p className="triangle-notice">
                  目录没有完整交易日历，无法确认交易日连续性。本次结果保留为待判定。
                </p>
              )}
              {!!result.data.coverage.failedDates.length && (
                <details className="triangle-missing">
                  <summary>
                    {result.data.coverage.failedDates.length}{' '}
                    个交易日未读到有效文件，展开查看缺口
                  </summary>
                  <ul>
                    {result.data.coverage.failedDates.map((item) => (
                      <li key={item.date}>
                        {item.date} · {item.reason}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <div className="triangle-filter-grid">
                <label className="triangle-field">
                  <span>观察类型</span>
                  <select
                    aria-label="观察类型"
                    value={direction}
                    onChange={(event) => {
                      if (
                        event.target.value === 'all' ||
                        event.target.value === 'golden' ||
                        event.target.value === 'death'
                      )
                        setDirection(event.target.value);
                      filterChanged();
                    }}
                  >
                    <option value="all">金三角与死三角</option>
                    <option value="golden">金三角</option>
                    <option value="death">死三角</option>
                  </select>
                </label>
                <label className="triangle-field">
                  <span>结果状态</span>
                  <select
                    aria-label="结果状态"
                    value={stage}
                    onChange={(event) => {
                      const next = stageOptions.find(
                        (item) => item.value === event.target.value,
                      );
                      if (next) setStage(next.value);
                      filterChanged();
                    }}
                  >
                    {stageOptions.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="triangle-field">
                  <span>信号发生或更新范围</span>
                  <select
                    aria-label="信号更新范围"
                    value={recentSessions}
                    onChange={(event) => {
                      setRecentSessions(Number(event.target.value));
                      filterChanged();
                    }}
                  >
                    <option value="5">最近5个交易日</option>
                    <option value="10">最近10个交易日</option>
                    <option value="20">最近20个交易日</option>
                    <option value="0">全部已读取历史</option>
                  </select>
                </label>
                <label className="triangle-field">
                  <span>市场</span>
                  <select
                    aria-label="筛选市场"
                    value={market}
                    onChange={(event) => {
                      setMarket(event.target.value);
                      filterChanged();
                    }}
                  >
                    <option value="all">全部市场</option>
                    <option value="SH">沪市</option>
                    <option value="SZ">深市</option>
                    <option value="BJ">北交所</option>
                  </select>
                </label>
                <label className="triangle-field">
                  <span>查找股票</span>
                  <input
                    aria-label="查找筛选股票"
                    placeholder="名称或代码"
                    value={search}
                    onChange={(event) => {
                      setSearch(event.target.value);
                      filterChanged();
                    }}
                  />
                </label>
              </div>
              <div className="triangle-table-scroll">
                <table>
                  <caption>
                    当前条件 {rows.length}{' '}
                    只，每只显示最近一条匹配记录；个股详情可切换历史形态。
                  </caption>
                  <thead>
                    <tr>
                      <th>股票</th>
                      <th>形态 / 状态</th>
                      <th>形成日</th>
                      <th>前期幅度</th>
                      <th>支撑区 S</th>
                      <th>压力区 R</th>
                      <th>详情</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows
                      .slice((shownPage - 1) * 50, shownPage * 50)
                      .map(({ summary, formation: item }) => (
                        <tr
                          key={summary.security.code}
                          data-selected={summary.security.code === selectedCode}
                        >
                          <td>
                            {summary.security.name}
                            <small>{summary.security.code}</small>
                          </td>
                          <td>
                            {item && (
                              <span
                                className={`triangle-tag ${item.direction}`}
                              >
                                {item.direction === 'golden'
                                  ? '金三角'
                                  : '死三角'}
                              </span>
                            )}
                            <span className="triangle-state">
                              {statusText(item)}
                            </span>
                            {!item && (
                              <small>
                                {!result.data.coverage.calendarKnown
                                  ? '交易日连续性未确认'
                                  : summary.availability.reason}
                              </small>
                            )}
                          </td>
                          <td>{item?.formedAt ?? '—'}</td>
                          <td>
                            {item?.pretrend.movePct === null ||
                            item?.pretrend.movePct === undefined
                              ? '—'
                              : `${item.pretrend.kind === 'decline' ? '下跌' : '上涨'} ${item.pretrend.movePct.toFixed(2)}%`}
                          </td>
                          <td>{zoneText(item?.support)}</td>
                          <td>{zoneText(item?.resistance)}</td>
                          <td>
                            <button
                              className="triangle-text-button"
                              onClick={() => {
                                setSelectedCode(summary.security.code);
                                setFormationId(item?.id ?? '');
                                document
                                  .getElementById('triangle-detail')
                                  ?.scrollIntoView({
                                    behavior: 'smooth',
                                    block: 'start',
                                  });
                              }}
                            >
                              查看标记
                            </button>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              {!rows.length && (
                <div className="triangle-empty">
                  当前条件没有匹配结果。
                  <button
                    className="triangle-text-button"
                    onClick={() => {
                      setStage('all');
                      setRecentSessions(0);
                      setDirection('all');
                      setMarket('all');
                      setSearch('');
                      setPage(1);
                    }}
                  >
                    查看全部状态与历史
                  </button>
                </div>
              )}
              {rows.length > 50 && (
                <div className="triangle-pagination">
                  <button
                    disabled={shownPage <= 1}
                    onClick={() => setPage(shownPage - 1)}
                  >
                    上一页
                  </button>
                  <span>
                    {shownPage}/{pageCount}
                  </span>
                  <button
                    disabled={shownPage >= pageCount}
                    onClick={() => setPage(shownPage + 1)}
                  >
                    下一页
                  </button>
                </div>
              )}
            </section>
            {selected && selectedAnalysis && (
              <section
                className="triangle-detail"
                id="triangle-detail"
                aria-label="个股三角与关键点位"
              >
                <div className="triangle-section-heading">
                  <div>
                    <h2>
                      {selected.security.name} <span>{selectedCode}</span>
                    </h2>
                    <p>截至 {result.data.asOf} 的价格、均线与可知事件</p>
                  </div>
                  <label className="triangle-field">
                    <span>切换已读取股票</span>
                    <select
                      aria-label="切换已读取股票"
                      value={selectedCode}
                      onChange={(event) => {
                        setSelectedCode(event.target.value);
                        setFormationId('');
                      }}
                    >
                      {result.data.series.map((item) => (
                        <option
                          key={item.security.code}
                          value={item.security.code}
                        >
                          {item.security.name} · {item.security.code}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                {selectedAnalysis.availability.kind === 'insufficient' && (
                  <p className="triangle-notice">
                    {selectedAnalysis.availability.reason}。连续有效日{' '}
                    {selectedAnalysis.availability.consecutiveBars} 个。
                  </p>
                )}
                <div className="triangle-detail-controls">
                  <label className="triangle-field">
                    <span>查看形态及其冻结依据</span>
                    <select
                      aria-label="查看历史形态"
                      value={formation?.id ?? ''}
                      onChange={(event) => setFormationId(event.target.value)}
                    >
                      {!selectedAnalysis.formations.length && (
                        <option value="">
                          {result.data.coverage.calendarKnown
                            ? '当前历史没有完整三交叉'
                            : '交易日连续性未确认，形态待判定'}
                        </option>
                      )}
                      {[...selectedAnalysis.formations]
                        .reverse()
                        .map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.formedAt} ·{' '}
                            {item.direction === 'golden' ? '金三角' : '死三角'}{' '}
                            · {statusText(item)}
                          </option>
                        ))}
                    </select>
                  </label>
                  {formation && (
                    <p className="triangle-state">
                      {statusText(formation)} · 形成日收盘{' '}
                      {price(formation.price)} 元
                    </p>
                  )}
                </div>
                <TriangleChart
                  analysis={selectedAnalysis}
                  formation={formation}
                  name={selected.security.name}
                />
                {!formation && (
                  <p className="triangle-form-note">
                    当前显示日K线、已可计算均线和交叉观察。历史不足或条件未完成时，不补造三角和关键价位。
                  </p>
                )}
                {formation && (
                  <>
                    <div className="triangle-evidence-head">
                      <h3>形成依据</h3>
                      <p>
                        首个交叉 {formation.startedAt} · 完成三次交叉{' '}
                        {formation.formedAt} ·{' '}
                        {formation.qualified
                          ? '前期背景满足参数'
                          : '前期背景未满足'}
                      </p>
                      {formation.reasons.map((reason) => (
                        <p className="triangle-notice" key={reason}>
                          {reason}
                        </p>
                      ))}
                    </div>
                    <p className="triangle-form-note">
                      {formation.pretrend.from && formation.pretrend.to
                        ? `${formation.pretrend.from.date} 的 ${price(formation.pretrend.from.price)} 元，到 ${formation.pretrend.to.date} 的 ${price(formation.pretrend.to.price)} 元，${formation.pretrend.kind === 'decline' ? '下跌' : '上涨'} ${formation.pretrend.movePct?.toFixed(2)}%。`
                        : formation.pretrend.reason}{' '}
                      前期依据在首个交叉前冻结，后续行情不改写。
                    </p>
                    <div className="triangle-table-scroll">
                      <table>
                        <caption>
                          三个实际交叉日期，观察值为当日两条均线的均值
                        </caption>
                        <thead>
                          <tr>
                            <th>均线组合</th>
                            <th>方向</th>
                            <th>发生 / 可知日期</th>
                            <th>交叉观察值</th>
                          </tr>
                        </thead>
                        <tbody>
                          {formation.crosses.map((cross) => (
                            <tr key={cross.id}>
                              <td>
                                {cross.pair === 'fast-middle'
                                  ? `${result.params.fastPeriod}/${result.params.middlePeriod}`
                                  : cross.pair === 'fast-slow'
                                    ? `${result.params.fastPeriod}/${result.params.slowPeriod}`
                                    : `${result.params.middlePeriod}/${result.params.slowPeriod}`}
                                日线
                              </td>
                              <td>
                                {cross.direction === 'up' ? '上穿' : '下穿'}
                              </td>
                              <td>{cross.date}</td>
                              <td>{price(cross.value)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="triangle-zone-grid">
                      <ZoneEvidence
                        zone={formation.support}
                        label="支撑区 S"
                        reason={formation.zoneReasons.support}
                      />
                      <ZoneEvidence
                        zone={formation.resistance}
                        label="压力区 R"
                        reason={formation.zoneReasons.resistance}
                      />
                    </div>
                    <div
                      className="triangle-thresholds"
                      aria-label="关键触发阈值"
                    >
                      {formation.direction === 'golden' && (
                        <>
                          <p>
                            <span>突破压力参考阈值</span>
                            <strong>
                              {formation.thresholds.breakout?.toFixed(4) ??
                                '待价区依据'}
                            </strong>
                            <small>
                              收盘高于阈值，连续 {result.params.confirmSessions}{' '}
                              日确认
                            </small>
                          </p>
                          <p>
                            <span>回踩后守住上沿</span>
                            <strong>
                              {formation.thresholds.retestRecovery?.toFixed(
                                4,
                              ) ?? '待价区依据'}
                            </strong>
                            <small>
                              实际触及后，收盘不低于上沿连续{' '}
                              {result.params.confirmSessions} 日
                            </small>
                          </p>
                        </>
                      )}
                      <p>
                        <span>
                          {formation.direction === 'golden'
                            ? '支撑区失效参考阈值'
                            : '压力区失效参考阈值'}
                        </span>
                        <strong>
                          {formation.thresholds.zoneInvalidation?.toFixed(4) ??
                            '待价区依据'}
                        </strong>
                        <small>
                          连续 {result.params.confirmSessions} 日收盘
                          {formation.direction === 'golden' ? '低于' : '高于'}
                          阈值判为失效
                        </small>
                      </p>
                      <p>
                        <span>前期趋势锚点失效参考阈值</span>
                        <strong>
                          {formation.thresholds.anchorInvalidation?.toFixed(
                            4,
                          ) ?? '待完整历史'}
                        </strong>
                        <small>
                          连续 {result.params.confirmSessions} 日收盘
                          {formation.direction === 'golden' ? '低于' : '高于'}
                          阈值判为失效
                        </small>
                      </p>
                    </div>
                    <p className="triangle-form-note">
                      参考阈值单位为元，显示4位小数。图中蓝色点线为突破阈值，浅红点线为失效参考阈值。反向均线交叉也会终止有效观察。
                    </p>
                    {formation.state.kind === 'active' && (
                      <div className="triangle-paths">
                        <p>
                          <strong>突破压力路径</strong>
                          {pathText(formation.state.breakout)}
                        </p>
                        <p>
                          <strong>回踩原支撑路径</strong>
                          {pathText(formation.state.retest)}
                        </p>
                        <small>
                          两条路径独立观察。价区缺失时，对应路径继续等待依据。
                        </small>
                      </div>
                    )}
                    {formation.state.kind === 'observing' && (
                      <p className="triangle-notice">
                        死三角为趋势转弱观察。当前处于有效期内，关键价位与后续失效事件在此保留。
                      </p>
                    )}
                    {'reason' in formation.state && (
                      <p className="triangle-notice">
                        {formation.state.reason}
                      </p>
                    )}
                    <div className="triangle-table-scroll">
                      <table>
                        <caption>
                          发生日与确认可知日分别保留；历史确认不代表截至当前仍有效
                        </caption>
                        <thead>
                          <tr>
                            <th>事件与依据</th>
                            <th>发生日</th>
                            <th>确认可知日</th>
                            <th>收盘参考价</th>
                          </tr>
                        </thead>
                        <tbody>
                          {formation.events.map((event) => (
                            <tr key={event.id}>
                              <td className="triangle-event-reason">
                                {event.reason}
                              </td>
                              <td>{event.occurredAt}</td>
                              <td>{event.knownAt}</td>
                              <td>{price(event.price)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </section>
            )}
          </>
        )}
        <details className="triangle-method">
          <summary>规则口径与适用范围</summary>
          <p>
            均线使用收盘价的简单平均。上穿要求上一交易日短线不高于长线、当日高于长线；下穿相反。同日三交叉只按日线记录，不推断盘中先后。前期背景要求完整回看窗口、反向均线排列和长均线方向，原始三交叉未满足背景时仍保留。
          </p>
          <p>
            前期跌幅按先高后低、涨幅按先低后高计算。支撑与压力来自已经确认的局部高低点，触点间隔与转折幅度须同时满足参数。局部点的确认日期包含右侧K线与转折条件所需等待，不提前使用未来价格。价区在首个交叉时冻结，已被有效突破的旧区不会自动继续有效。
          </p>
          <p>
            金三角完成后，突破压力与回踩原支撑分别观察，均需真实触发和连续收盘确认。有效破位或反向交叉优先处理，形成后的有效期按交易日计。缺失、停牌与无成交中断连续计算，保留历史事件，重新积累足够历史后再判断。
          </p>
          <p>
            每次最多读取186个交易日。结果只覆盖成功读取的现有行情，不包含完整历史退市样本，且没有复权因子或公司行为核验。这里识别的是明确定义的价格形态；方法胜率、收益和交易可执行性尚未验证。
          </p>
        </details>
      </main>
    </div>
  );
}
