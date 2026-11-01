'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { SiteHeader } from '@/components/site-header';
import { SiteLink } from '@/components/site-router';
import { WorkbenchCheck, WorkbenchPageBar, WorkbenchShell, WorkbenchListHeader, WorkbenchListFooter, WorkbenchState, WorkbenchListSkeleton, StockDetailHeader } from '@/components/workbench';
import { priceChangeText } from '@/lib/chart-readout';
import { InspectorTabs } from '@/components/inspector-tabs';
import { useStockListNavigation } from '@/components/use-stock-list-navigation';
import { authRead } from '@/lib/auth-fetch';
import { startPublicationFollow } from '@/lib/publication-follow';
import { dailyInitialSelection, dailyMatches, dailyReason, dailySignalText, parseDailyChartHistory, parseDailyManifest, parseDailyScreening, parseDailyStatus, parseDailyStock, type DailyCategory, type DailyManifest, type DailyRow, type DailyRunStatus, type DailyScreening, type DailyStock } from '@/lib/trend-daily';
import { trendStateText, type TrendCandle, type TrendScreen, type TrendState } from '@/lib/trend-volume';
import { TrendChart, volumeText } from './trend-chart';
import '../chips/chips.css';
import '../chips/workbench.css';
import './trend.css';
import './daily.css';

export const dynamic = 'force-static';
const api = (process.env.NEXT_PUBLIC_DATA_BASE || '').replace(/\/$/, '');
type Publication = { manifest: DailyManifest; screening: DailyScreening };
type Detail = { kind: 'ready'; key: string; value: DailyStock } | { kind: 'error'; key: string; message: string } | { kind: 'empty' };
type ChartHistory = { kind: 'ready'; stock: DailyStock; history: TrendCandle[] } | { kind: 'unavailable'; stock: DailyStock };
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
  return <WorkbenchCheck title={title} state={state}>{children}</WorkbenchCheck>;
}
function Conditions({ screen }: { screen: TrendScreen }) {
  const pullback = screen.trend_gate.metrics.pullback_pct, ratio = screen.shrink_gate.metrics.recent_to_reference_volume;
  return <><div className="tv-inspector-intro"><b>入选条件</b><p>趋势准备、缩量准备与当日启动。</p></div>
    <Check title="01 趋势准备" state={screen.trend_gate.status}><dl><div><dt>有效准备日</dt><dd>{screen.setup_date ?? '未找到'}</dd></div><div><dt>距20日高点回落</dt><dd>{typeof pullback === 'number' ? percent(pullback) : '待核实'}</dd></div></dl><p>回落≥5%，且20日或5日收盘斜率向下。</p></Check>
    <Check title="02 缩量准备" state={screen.shrink_gate.status}><dl><div><dt>3日均量 / 此前10日均量</dt><dd>{typeof ratio === 'number' ? percent(ratio * 100) : '待核实'}</dd></div><div><dt>固定基准 B</dt><dd>{volumeText(screen.baseline_mean)}{screen.baseline_mean === null ? '' : '股'}</dd></div><div><dt>距筛选日</dt><dd>{screen.setup_age === null ? '待核实' : `${screen.setup_age}个交易日`}</dd></div></dl><p>比例≤70%；在筛选日前1至5个交易日寻找有效准备。</p></Check>
    <Check title="03 当日放量大涨" state={screen.surge_status}><dl><div><dt>当日量 / 固定基准</dt><dd>{multiple(screen.volume_multiple)}</dd></div><div><dt>当日收盘涨幅</dt><dd>{priceChangeText(screen.close_return_pct)}</dd></div><div><dt>收盘高于开盘</dt><dd>{screen.bullish === null ? '待核实' : screen.bullish ? '是' : '否'}</dd></div></dl><p>成交量≥2B，涨幅≥4%，且为阳线。</p></Check>
    {!screen.setup_date && <p className="tv-unknown-note">尚未找到有效准备日。</p>}
    {screen.reasons.length > 0 && <div className="tv-reasons"><h3>未满足或待核实原因</h3><ul>{[...new Set(screen.reasons)].map(reason => <li key={reason}>{dailyReason(reason)}</li>)}</ul></div>}
  </>;
}

export default function TrendDailyPage() {
  const [publication, setPublication] = useState<Publication | null>(null), [status, setStatus] = useState<DailyRunStatus | null>(null), [settled, setSettled] = useState(false);
  const [error, setError] = useState(''), [statusError, setStatusError] = useState(''), [detail, setDetail] = useState<Detail>({ kind: 'empty' }), [detailReload, setDetailReload] = useState(0);
  const [chartHistory, setChartHistory] = useState<ChartHistory | null>(null), [chartPeriod, setChartPeriod] = useState(120);
  const listArea = useRef<HTMLUListElement>(null);
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
  useEffect(() => {
    if (!stock || stock.source_status !== 'KNOWN') return;
    const controller = new AbortController();
    authRead(api + `/data/chips/daily/${stock.source_dataset_id}/${stock.code}.json`, { signal: controller.signal, cache: 'no-store' }, async response => {
      if (!response.ok) throw Error('历史行情暂不可读取。');
      return parseDailyChartHistory(await response.json(), stock);
    }, { timeoutMs: 8000, maxAttempts: 2 })
      .then(history => { if (!controller.signal.aborted) setChartHistory({ kind: 'ready', stock, history }); })
      .catch(() => { if (!controller.signal.aborted) setChartHistory({ kind: 'unavailable', stock }); });
    return () => controller.abort();
  }, [stock]);
  const extended = chartHistory?.stock === stock ? chartHistory : null;
  const history = extended?.kind === 'ready' ? extended.history : stock?.history;
  const periods = [60, 120, 250, 500];
  function select(row: DailyRow) { currentCode.current = row.code; setCode(row.code); }
  const navigationKeys = useMemo(() => matched.map(row => row.code), [matched]);
  useStockListNavigation({ keys: navigationKeys, selected: code, listRef: listArea, page, onSelect: (value, index) => { currentCode.current = value; setCode(value); setPage(Math.floor(index / 60)); } });
  const refreshData = () => { follow.current?.refresh(); if (detailError) setDetailReload(value => value + 1); };
  function filter(nextCategory: DailyCategory, nextSearch: string) {
    const next = dailyInitialSelection(rows, code, nextCategory, nextSearch);
    currentCode.current = next; currentFilter.current = { category: nextCategory, search: nextSearch };
    setCategory(nextCategory); setSearch(nextSearch); setPage(0); setCode(next);
  }
  const running = status?.state === 'RUNNING', newerTarget = manifest && ((status?.as_of ?? '') > manifest.as_of || manifest.requested_as_of > manifest.as_of);
  const updateMessage = running ? `正在更新 ${status.as_of} · ${status.scanned_count.toLocaleString()} / ${status.universe_total.toLocaleString()}只` : newerTarget ? `新结果待发布 · 当前查看 ${manifest?.as_of}` : status?.state === 'FAILED' || status?.state === 'SOURCE_PENDING' ? status.message : manifest?.source_generation_stale ? '行情已更新，筛选结果更新中。' : '';
  return <div className="chip-site chip-workbench tv-site td-site wb-site">
    <SiteHeader active="trend"><button className="stock-header-refresh" aria-label="刷新最新筛选" onClick={refreshData}><RefreshCw size={14}/><span>刷新</span></button></SiteHeader>
    <WorkbenchPageBar title="趋势量价筛选" date={manifest?.as_of} explanation={{label:'筛选条件',content:<ol><li><b>趋势准备</b><p>先确认有效准备日，再观察后续缩量。</p></li><li><b>缩量观察</b><p>准备期均量不高于此前参考均量的70%。</p></li><li><b>放量启动</b><p>当日成交量达到冻结基准2倍，涨幅≥4%，且收盘高于开盘。</p></li></ol>}}><SiteLink href="/trend/research">历史研究</SiteLink></WorkbenchPageBar>
    <WorkbenchShell contentClassName="tv-workspace" sidebar={
      <aside className="tv-sidebar wb-sidebar" aria-label="当日股票名单">
        <WorkbenchListHeader count={manifest?matched.length:null} ready={!!manifest} search={{label:'搜索当日股票',value:search,onChange:value=>filter(category,value)}} filters={categories.map(item=>({id:item.value,label:item.label,count:manifest?(item.value==='ALL'?rows.length:manifest.counts[item.value]):null,active:category===item.value,onSelect:()=>filter(item.value,search)}))}/>
        <ul ref={listArea} className="tv-list wb-stock-list" aria-label="当前筛选名单">
          {!publication&&!error&&<li><WorkbenchListSkeleton/></li>}
          {publication&&!visible.length&&<li className="tv-list-empty">当前范围没有结果。{category==='SELECTED'&&!search&&<button className="wb-button" onClick={()=>filter('WATCHING','')}>查看缩量观察</button>}</li>}
          {visible.map(row=><li key={row.code}><button className="tv-row wb-stock-row" data-stock-key={row.code} aria-label={`${row.name} ${row.code}`} aria-pressed={row.code===code} onClick={()=>select(row)}><span><strong>{row.name}</strong><b className="wb-row-price">{row.close_cny?.toFixed(2)??'待核实'}</b></span><span><small>{row.code}</small><em className="wb-row-status">{dailySignalText[row.signal_state]}</em></span></button></li>)}
        </ul>
        <WorkbenchListFooter count={matched.length} page={page} onPageChange={setPage}/>
      </aside>}>
      {publication&&(updateMessage||error||statusError)&&<div className="wb-update-note" role={error||statusError?'alert':'status'}>{[updateMessage,error||statusError].filter(Boolean).join(' · ')}</div>}
      {!publication&&<WorkbenchState state={error?{kind:'error',message:error,onRetry:()=>follow.current?.refresh()}:settled?{kind:'empty',title:running?'正在生成今日结果':'今日结果待发布',message:status?`${status.as_of} · 已处理 ${status.scanned_count.toLocaleString()} / ${status.universe_total.toLocaleString()}只`:undefined}:{kind:'loading',label:'正在读取每日结果…'}}/>}
      {publication&&!selectedRow&&<WorkbenchState state={{kind:'empty',title:category==='SELECTED'?'今日暂无入选股票':'选择股票查看详情',message:'可切换左侧分类查看其他股票。'}}/>}
      {selectedRow&&!stock&&<WorkbenchState state={detailError?{kind:'error',message:detailError,onRetry:()=>setDetailReload(value=>value+1)}:{kind:'loading',label:`正在读取${selectedRow.name}的行情…`}}/>}
      {stock&&selectedRow&&<>
        <StockDetailHeader className="tv-stock-heading" name={selectedRow.name} code={selectedRow.code} date={selectedRow.date} close={selectedRow.close_cny} percent={selectedRow.close_return_pct} badge={<span className={`cw-screen-badge td-signal td-signal-${selectedRow.signal_state.toLowerCase()}`}>{dailySignalText[selectedRow.signal_state]}</span>}/>
        {stock.source_status==='KNOWN'&&history?<><TrendChart key={`${stock.dataset_id}:${stock.code}:${extended?.kind??'loading'}`} stock={{...stock,history}} date={stock.as_of} screen={stock.screen} lengths={periods} periodValue={chartPeriod} onPeriodChange={setChartPeriod}/>{extended?.kind==='unavailable'&&<output className="wb-update-note">已显示 {history.length} 根行情，较早历史暂不可读。</output>}</>:<WorkbenchState state={{kind:'empty',title:'当日行情待补齐'}}/>}
        <div className="tv-result-strip wb-metrics"><div className="tv-metric"><span>准备日</span><strong>{stock.screen.setup_date??'未找到'}</strong></div><div className="tv-metric"><span>缩量比例</span><strong>{selectedRow.shrink_ratio===null?'待核实':percent(selectedRow.shrink_ratio*100)}</strong></div><div className="tv-metric"><span>当日放量</span><strong>{multiple(selectedRow.volume_multiple)}</strong></div><div className="tv-metric"><span>当日涨幅</span><strong>{priceChangeText(selectedRow.close_return_pct)}</strong></div></div>
        <section className="wb-inspector" aria-label="当日筛选条件"><InspectorTabs className="tv-inspector-tabs" label="筛选核对面板" idPrefix="td" tabs={[{id:'conditions',label:'条件核对'},{id:'rules',label:'规则与数据'}]} value={tab} onChange={setTab}/><div className="tv-inspector-body" role="tabpanel" id={`td-${tab}`} aria-labelledby={`td-${tab}-tab`}>
          {tab==='conditions'&&<Conditions screen={stock.screen}/>}
          {tab==='rules'&&<><h2>筛选规则 · V3</h2><ol className="tv-rule-list"><li>准备日较20日最高价回落≥5%，20日或5日收盘斜率向下。</li><li>3日均量≤此前不重叠10日均量的70%；从前5个交易日选最近有效准备，固定基准 B。</li><li>当日成交量≥2B、涨幅≥4%、收盘价高于开盘价。</li></ol><p className="tv-muted">缩量观察表示准备有效，当日启动条件尚未满足。</p><details className="tv-candidates"><summary>准备日检查 · {stock.screen.candidates.length}条</summary><div className="tv-table-scroll"><table><thead><tr><th>候选日</th><th>趋势</th><th>缩量</th><th>回落</th><th>3 / 10日量</th></tr></thead><tbody>{stock.screen.candidates.map(candidate=><tr key={candidate.date} data-chosen={candidate.date===stock.screen.setup_date}><td>{candidate.date}{candidate.date===stock.screen.setup_date?' ✓':''}</td><td><State state={candidate.trend_status}/></td><td><State state={candidate.shrink_status}/></td><td>{percent(candidate.pullback_pct)}</td><td>{candidate.shrink_ratio===null?'待核实':percent(candidate.shrink_ratio*100)}</td></tr>)}</tbody></table></div></details><details className="tv-source-hash"><summary>数据口径与范围</summary><p>{manifest?.scope}</p><ul className="tv-limitations">{manifest?.limitations.map((item,index)=><li key={index}>{item}</li>)}</ul><p>来源 {stock.provider} · 成交量为股 · 截至 {stock.as_of}。</p><p>筛选批次 {manifest?.dataset_id}<br/>行情批次 {manifest?.source_dataset_id}<br/>更新 {manifest&&timeText(manifest.generated_at)}</p></details></>}
        </div></section>
      </>}
    </WorkbenchShell>
  </div>;
}
