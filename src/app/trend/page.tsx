/* oxlint-disable next/no-html-link-for-pages -- Static CDN routes use document navigation. */
'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import { BrandIdentity } from '@/components/brand';
import { AccountControls } from '@/components/auth-gate';
import { StockNavigation } from '@/components/stock-navigation';
import { authRead } from '@/lib/auth-fetch';
import { startPublicationFollow } from '@/lib/publication-follow';
import { dailyInitialSelection, dailyMatches, dailyReason, dailySignalText, parseDailyManifest, parseDailyScreening, parseDailyStatus, parseDailyStock, type DailyCategory, type DailyManifest, type DailyRow, type DailyRunStatus, type DailyScreening, type DailyStock } from '@/lib/trend-daily';
import { trendStateText, type TrendScreen, type TrendState } from '@/lib/trend-volume';
import { TrendChart, volumeText } from './trend-chart';
import '../chips/chips.css';
import '../chips/workbench.css';
import './trend.css';
import './daily.css';

export const dynamic = 'force-static';
const api = (process.env.NEXT_PUBLIC_DATA_BASE || '').replace(/\/$/, '');
type Publication = { manifest: DailyManifest; screening: DailyScreening };
type Detail = { kind: 'ready'; key: string; value: DailyStock } | { kind: 'error'; key: string; message: string } | { kind: 'empty' };
type InspectorTab = 'conditions' | 'rules';
const categories: { value: DailyCategory; label: string }[] = [{ value: 'SELECTED', label: '当日入选' }, { value: 'WATCHING', label: '缩量观察' }, { value: 'NOT_MATCHED', label: '未满足' }, { value: 'UNKNOWN', label: '数据不足' }, { value: 'ALL', label: '全部' }];
const emptyRows: DailyRow[] = [];
const failed = (error: unknown) => error instanceof Error ? error.message : '当日筛选数据暂时无法读取。';
const percent = (value: number | null) => value === null ? '待核实' : `${value.toFixed(2)}%`;
const multiple = (value: number | null) => value === null ? '待核实' : `${value.toFixed(2)}倍`;
function timeText(value: string) { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)); }
async function read<T>(path: string, signal: AbortSignal, parse: (value: unknown) => T): Promise<T> {
  return authRead(api + path, { signal, cache: 'no-store' }, async response => {
    if (!response.ok) throw Error(response.status === 401 ? '登录已失效，请重新登录。' : response.status === 404 ? '最新交易日的筛选结果尚未发布。' : `当日筛选数据暂时无法读取（HTTP ${response.status}）。`);
    return parse(await response.json());
  });
}
function State({ state }: { state: TrendState }) { return <span className={`tv-state tv-state-${state.toLowerCase()}`}>{trendStateText[state]}</span>; }
function Check({ title, state, children }: { title: string; state: TrendState; children: React.ReactNode }) {
  return <section className="tv-check"><div className="tv-section-heading"><h3>{title}</h3><State state={state} /></div>{children}</section>;
}
function Conditions({ screen }: { screen: TrendScreen }) {
  const pullback = screen.trend_gate.metrics.pullback_pct, ratio = screen.shrink_gate.metrics.recent_to_reference_volume;
  return <><div className="tv-inspector-intro"><b>V3 当日条件核对</b><p>三项同时满足才列入当日入选。</p></div>
    <Check title="01 趋势准备" state={screen.trend_gate.status}><dl><div><dt>有效准备日</dt><dd>{screen.setup_date ?? '未找到'}</dd></div><div><dt>距20日高点回落</dt><dd>{typeof pullback === 'number' ? percent(pullback) : '待核实'}</dd></div></dl><p>回落≥5%，且20日或5日收盘斜率向下。</p></Check>
    <Check title="02 缩量准备" state={screen.shrink_gate.status}><dl><div><dt>3日均量 / 此前10日均量</dt><dd>{typeof ratio === 'number' ? percent(ratio * 100) : '待核实'}</dd></div><div><dt>固定基准 B</dt><dd>{volumeText(screen.baseline_mean)}{screen.baseline_mean === null ? '' : '股'}</dd></div><div><dt>距筛选日</dt><dd>{screen.setup_age === null ? '待核实' : `${screen.setup_age}个交易日`}</dd></div></dl><p>比例≤70%；在筛选日前1至5个交易日寻找有效准备。</p></Check>
    <Check title="03 当日放量大涨" state={screen.surge_status}><dl><div><dt>当日量 / 固定基准</dt><dd>{multiple(screen.volume_multiple)}</dd></div><div><dt>当日收盘涨幅</dt><dd>{percent(screen.close_return_pct)}</dd></div><div><dt>收盘高于开盘</dt><dd>{screen.bullish === null ? '待核实' : screen.bullish ? '是' : '否'}</dd></div></dl><p>成交量≥2B，涨幅≥4%，且为阳线。</p></Check>
    {!screen.setup_date && <p className="tv-unknown-note">没有有效准备基准时，放量倍数保持待核实，不能判断为没有放量。</p>}
    {screen.reasons.length > 0 && <div className="tv-reasons"><h3>未满足或待核实原因</h3><ul>{[...new Set(screen.reasons)].map(reason => <li key={reason}>{dailyReason(reason)}</li>)}</ul></div>}
  </>;
}

export default function TrendDailyPage() {
  const [publication, setPublication] = useState<Publication | null>(null), [status, setStatus] = useState<DailyRunStatus | null>(null), [settled, setSettled] = useState(false);
  const [error, setError] = useState(''), [statusError, setStatusError] = useState(''), [detail, setDetail] = useState<Detail>({ kind: 'empty' }), [detailReload, setDetailReload] = useState(0);
  const [code, setCode] = useState(''), [category, setCategory] = useState<DailyCategory>('SELECTED'), [search, setSearch] = useState(''), [page, setPage] = useState(0), [tab, setTab] = useState<InspectorTab>('conditions');
  const follow = useRef<{ refresh: () => void; stop: () => void } | null>(null), currentCode = useRef(code), currentFilter = useRef({ category, search });
  useEffect(() => {
    const controller = startPublicationFollow({
      readStatus: signal => read('/data/trend-volume/daily/status.json', signal, parseDailyStatus),
      readManifest: signal => read('/data/trend-volume/daily/manifest.json', signal, parseDailyManifest),
      readPayload: (manifest, signal) => read(`/data/trend-volume/daily/${manifest.dataset_id}/screening.json`, signal, value => parseDailyScreening(value, manifest)),
      onStatus: setStatus, onStatusError: value => setStatusError(value ? '更新状态暂不可读，保留已发布的完整结果。' : ''),
      onPublication: (manifest, screening) => {
        const filter = currentFilter.current, selected = dailyInitialSelection(screening.rows, currentCode.current, filter.category, filter.search);
        currentCode.current = selected; setCode(selected); setPage(0); setPublication({ manifest, screening });
      },
      onPublicationError: value => setError(value?.message ?? ''), onSettled: () => setSettled(true),
      isRunning: value => value.state === 'RUNNING' || value.state === 'SOURCE_PENDING', publishedDataset: value => value.state === 'COMMITTED' ? value.dataset_id : null,
      activeManifestMs: 30000, idleManifestMs: 60000, activeStatusMs: 20000, idleStatusMs: 30000,
    });
    follow.current = controller;
    return () => { controller.stop(); follow.current = null; };
  }, []);
  const manifest = publication?.manifest, rows = publication?.screening.rows ?? emptyRows;
  const matched = useMemo(() => rows.filter(row => dailyMatches(row, category, search)), [rows, category, search]);
  const visible = matched.slice(page * 60, (page + 1) * 60), selectedRow = rows.find(row => row.code === code);
  const key = manifest && code ? `${manifest.dataset_id}:${code}` : '';
  const stock = detail.kind === 'ready' && detail.key === key ? detail.value : null, detailError = detail.kind === 'error' && detail.key === key ? detail.message : '';
  useEffect(() => {
    if (!manifest || !selectedRow) return;
    const controller = new AbortController(), detailKey = `${manifest.dataset_id}:${selectedRow.code}`;
    read(`/data/trend-volume/daily/${manifest.dataset_id}/${selectedRow.code}.json`, controller.signal, value => parseDailyStock(value, manifest, selectedRow))
      .then(value => { if (!controller.signal.aborted) setDetail({ kind: 'ready', key: detailKey, value }); })
      .catch(value => { if (!controller.signal.aborted) setDetail({ kind: 'error', key: detailKey, message: failed(value) }); });
    return () => controller.abort();
  }, [manifest, selectedRow, detailReload]);
  function select(row: DailyRow) { currentCode.current = row.code; setCode(row.code); }
  function filter(nextCategory: DailyCategory, nextSearch: string) {
    const next = dailyInitialSelection(rows, code, nextCategory, nextSearch);
    currentCode.current = next; currentFilter.current = { category: nextCategory, search: nextSearch };
    setCategory(nextCategory); setSearch(nextSearch); setPage(0); setCode(next);
  }
  const running = status?.state === 'RUNNING', newerTarget = manifest && ((status?.as_of ?? '') > manifest.as_of || manifest.requested_as_of > manifest.as_of);
  return <div className="chip-site chip-workbench tv-site td-site">
    <header className="chip-site-header cw-header"><a href="/" className="brand" aria-label="量序首页"><BrandIdentity /></a><StockNavigation active="trend" /><div className="cw-header-actions"><button className="tv-refresh" aria-label="刷新最新筛选" onClick={() => follow.current?.refresh()}><RefreshCw size={14} /><span>刷新</span></button><AccountControls /></div></header>
    <div className="tv-page-heading td-heading"><div><h1>趋势量价筛选 <span>冻结 V3</span></h1><p>趋势 → 缩量 → 当日放量大涨 <a href="/trend/research">历史样本验证</a></p></div><div className="td-date"><span>已发布筛选日期</span><strong>{manifest?.as_of ?? '等待发布'}</strong>{manifest && <small>北京时间 {timeText(manifest.generated_at)} 更新</small>}</div></div>
    {!publication && <main className="td-initial" aria-live="polite"><h2>{!settled ? '正在获取最新交易日筛选…' : running ? '最新一批正在筛选' : '最新筛选结果尚未可用'}</h2>{status && <p>目标日期 {status.as_of} · 已处理 {status.scanned_count.toLocaleString()} / {status.universe_total.toLocaleString()} 只</p>}{settled && error && <p>{error}</p>}<p>完整结果发布后自动显示，不用历史样本替代当日名单。</p><button className="tv-button" onClick={() => follow.current?.refresh()}>重新读取</button></main>}
    {publication && <>
      <section className="td-overview" aria-label="当前日期筛选统计">{categories.filter(item => item.value !== 'ALL').map(item => <button key={item.value} aria-pressed={category === item.value} onClick={() => filter(item.value, search)} className={`td-count td-count-${item.value.toLowerCase()}`}><span>{item.label}</span><strong>{item.value === 'ALL' ? rows.length.toLocaleString() : manifest?.counts[item.value].toLocaleString()}</strong><small>{item.value === 'SELECTED' ? '三项同时满足' : item.value === 'WATCHING' ? '已有准备，启动未满足' : item.value === 'UNKNOWN' ? '不当作排除或入选' : '当日条件未满足'}</small></button>)}<div className="td-coverage"><span>当前网站证券清单</span><strong>{manifest?.universe_total.toLocaleString()}只</strong><small>行情可核 {manifest?.source_known_count.toLocaleString()}只 · 日期 {manifest?.as_of}</small></div></section>
      <div className={`td-update${newerTarget || error || status?.state === 'FAILED' ? ' td-update-pending' : ''}`} aria-live="polite">
        {running ? <span>正在筛选 {status.as_of} · {status.scanned_count.toLocaleString()} / {status.universe_total.toLocaleString()}只；当前显示 {manifest?.as_of} 已发布结果。</span>
          : newerTarget ? <span>最新目标 {status?.as_of && status.as_of > (manifest?.requested_as_of ?? '') ? status.as_of : manifest?.requested_as_of} 尚未发布，当前筛选日期为 {manifest?.as_of}。</span>
          : status?.state === 'FAILED' || status?.state === 'SOURCE_PENDING' ? <span>{status.message} 当前保留 {manifest?.as_of} 完整结果。</span>
          : <span>{manifest?.as_of} 收盘筛选已发布。{manifest?.source_generation_stale ? '行情有更新批次，等待后续筛选更新。' : '自动跟随已发布的最新完整批次。'}</span>}
        {(error || statusError) && <span>{error || statusError}</span>}
      </div>
      <div className="tv-shell td-shell">
        <aside className="tv-sidebar" aria-label="当日股票名单"><div className="tv-list-heading"><h2>{manifest?.as_of} 名单</h2><span>{matched.length.toLocaleString()}只</span></div><div className="tv-search"><input aria-label="搜索当日股票" placeholder="股票名称或代码" value={search} onChange={event => filter(category, event.target.value)} /></div><div className="tv-filters td-filters" aria-label="当日名单分类">{categories.map(item => <button key={item.value} aria-pressed={category === item.value} onClick={() => filter(item.value, search)}>{item.label}<span>{item.value === 'ALL' ? rows.length : manifest?.counts[item.value]}</span></button>)}</div>
          <ul className="tv-list" aria-label="当前筛选名单">{!visible.length && <li className="tv-list-empty">{category === 'SELECTED' && !search ? <>{manifest?.as_of} 已核对数据中，没有同时满足三项条件的股票。{manifest && manifest.counts.UNKNOWN > 0 && <p>另有 {manifest.counts.UNKNOWN} 只数据不足，仍待核实。</p>}<button className="tv-button" onClick={() => filter('WATCHING', '')}>查看缩量观察</button></> : '当前条件没有股票，请调整分类或搜索。'}</li>}{visible.map(row => <li key={row.code}><button className="tv-row" aria-pressed={row.code === code} onClick={() => select(row)}><span><strong>{row.name}</strong><b className={row.close_return_pct !== null && row.close_return_pct >= 0 ? 'tv-up' : 'tv-down'}>{row.close_return_pct === null ? '涨幅待核' : `${row.close_return_pct >= 0 ? '+' : ''}${row.close_return_pct.toFixed(2)}%`}</b></span><span><small>{row.code}</small><small>{row.close_cny === null ? '收盘待核' : row.close_cny.toFixed(2)}</small></span><em>{dailySignalText[row.signal_state]} · 筛选 {row.date}</em></button></li>)}</ul>
          <footer className="tv-list-footer"><span>列表 {matched.length.toLocaleString()}只</span><div><button aria-label="上一页股票" disabled={page === 0} onClick={() => setPage(value => value - 1)}><ChevronLeft size={14} /></button><span>{page + 1} / {Math.max(1, Math.ceil(matched.length / 60))}</span><button aria-label="下一页股票" disabled={(page + 1) * 60 >= matched.length} onClick={() => setPage(value => value + 1)}><ChevronRight size={14} /></button></div><small>分类和搜索只改变列表，不改变统计。</small></footer>
        </aside>
        <main className="tv-workspace" aria-label="当日量价详情">{!selectedRow ? <div className="td-no-selection"><h2>{category === 'SELECTED' ? '当前没有当日入选股票' : '选择股票查看当日条件'}</h2><p>筛选日期 {manifest?.as_of}。可切换分类查看缩量观察、未满足或数据不足。</p></div> : <>
          <div className="tv-stock-heading"><div><h2>{selectedRow.name} <small>{selectedRow.code}</small></h2><p>{manifest?.as_of} 收盘筛选 · 不复权</p></div><span className={`td-signal td-signal-${selectedRow.signal_state.toLowerCase()}`}>{dailySignalText[selectedRow.signal_state]}</span></div>
          <div className="td-selected-summary"><span>收盘 <b>{selectedRow.close_cny?.toFixed(2) ?? '待核实'}</b></span><span>当日涨幅 <b className={selectedRow.close_return_pct !== null && selectedRow.close_return_pct >= 0 ? 'tv-up' : 'tv-down'}>{percent(selectedRow.close_return_pct)}</b></span><strong>筛选日 {selectedRow.date}</strong></div>
          {!stock && !detailError && <output className="tv-chart-empty">正在读取 {manifest?.as_of} 的行情与条件…</output>}
          {detailError && <div className="tv-chart-empty" role="alert">{detailError}<button className="tv-button" onClick={() => setDetailReload(value => value + 1)}>重试详情</button></div>}
          {stock && <>{stock.source_status === 'KNOWN' ? <TrendChart stock={stock} date={stock.as_of} screen={stock.screen} lengths={[35, 60]} /> : <div className="tv-chart-empty">筛选日行情不足，保留未知状态，不用较早日期的K线代替。</div>}
            <div className="tv-result-strip"><div className="tv-metric"><span>准备日</span><strong>{stock.screen.setup_date ?? '未找到有效准备'}</strong></div><div className="tv-metric"><span>缩量比例</span><strong>{selectedRow.shrink_ratio === null ? '待核实' : percent(selectedRow.shrink_ratio * 100)}</strong></div><div className="tv-metric"><span>当日放量</span><strong>{multiple(selectedRow.volume_multiple)}</strong></div><div className="tv-metric"><span>当日涨幅</span><strong>{percent(selectedRow.close_return_pct)}</strong></div></div>
            {selectedRow.signal_state === 'WATCHING' && <p className="td-watch-note">已找到近期缩量准备；{stock.as_of} 尚未满足完整的当日启动条件，不列入当日入选。</p>}
            <details className="tv-candidates" open={!stock.screen.setup_date}><summary>筛选前5个交易日的准备检查 <span>{stock.screen.candidates.length}条</span></summary><p>趋势和缩量需在同一个准备日满足；准备日不等于本次筛选日期。</p><div className="tv-table-scroll"><table><thead><tr><th>准备候选日</th><th>趋势</th><th>缩量</th><th>回落</th><th>3 / 10日量</th></tr></thead><tbody>{stock.screen.candidates.map(candidate => <tr key={candidate.date} data-chosen={candidate.date === stock.screen.setup_date}><td>{candidate.date}{candidate.date === stock.screen.setup_date ? ' ✓' : ''}</td><td><State state={candidate.trend_status} /></td><td><State state={candidate.shrink_status} /></td><td>{percent(candidate.pullback_pct)}</td><td>{candidate.shrink_ratio === null ? '待核实' : percent(candidate.shrink_ratio * 100)}</td></tr>)}</tbody></table></div></details>
            <p className="tv-source-note">行情来源 {stock.provider} · 成交量单位为股 · 仅使用截至 {stock.as_of} 的收盘行情。</p>
          </>}
        </>}</main>
        <aside className="tv-inspector" aria-label="当日筛选条件"><div className="tv-inspector-tabs" role="tablist" aria-label="筛选核对面板">{([{ value: 'conditions', label: '条件核对' }, { value: 'rules', label: '规则与数据' }] satisfies { value: InspectorTab; label: string }[]).map(item => <button key={item.value} role="tab" aria-selected={tab === item.value} aria-controls={`td-panel-${item.value}`} id={`td-tab-${item.value}`} onClick={() => setTab(item.value)}>{item.label}</button>)}</div><div className="tv-inspector-body" role="tabpanel" id={`td-panel-${tab}`} aria-labelledby={`td-tab-${tab}`}>
          {tab === 'conditions' && (stock ? <Conditions screen={stock.screen} /> : <p className="tv-muted">{selectedRow ? '条件记录加载后显示在这里。' : '从当日名单选择股票，核对趋势、缩量和量价启动。'}</p>)}
          {tab === 'rules' && <><h2>当前采用冻结 V3</h2><ol className="tv-rule-list"><li>准备日较20日最高价回落≥5%，20日或5日收盘斜率向下。</li><li>3日均量≤此前不重叠10日均量的70%；从前5个交易日选最近有效准备，固定基准 B。</li><li>筛选当日成交量≥2B、涨幅≥4%、收盘价高于开盘价，三项同时满足才入选。</li></ol><h3>缩量观察的含义</h3><p className="tv-muted">准备有效而当日启动条件未满足，单独归入观察。观察名单不等于当日入选。</p><h3>当前数据范围</h3><p className="tv-muted">{manifest?.scope}</p><ul className="tv-limitations">{manifest?.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul><details className="td-pending-definition"><summary>新下跌持续定义仍待验证</summary><p>当前筛选继续使用冻结 V3。高低点逐级降低、下跌状态持续到大量且大涨的定义尚未替换进来，不能把这里的缩量观察当作已实现的新状态模型。</p></details><p className="tv-muted">黄金分割未加入。这里统计当日条件，不评价收益或漏选、误选率。</p><a href="/trend/research" className="td-history-link">查看历史 V2 / V3 样本验证</a><details className="tv-source-hash"><summary>数据批次</summary><p>筛选 {manifest?.dataset_id}<br />行情 {manifest?.source_dataset_id}<br />更新 {manifest && timeText(manifest.generated_at)} 北京时间</p></details></>}
        </div></aside>
      </div>
    </>}
  </div>;
}
