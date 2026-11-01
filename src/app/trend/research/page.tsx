'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import { SiteHeader } from '@/components/site-header';
import { WorkbenchCheck, WorkbenchPageBar, WorkbenchShell, WorkbenchListHeader, WorkbenchListFooter, WorkbenchListSkeleton, WorkbenchState, StockDetailHeader } from '@/components/workbench';
import { SiteLink } from '@/components/site-router';
import { candleReturn } from '@/lib/candle-quote';
import { priceChangeClass, priceChangeText } from '@/lib/chart-readout';
import { InspectorTabs } from '@/components/inspector-tabs';
import { RetryMessage, StockListNote } from '@/components/read-state';
import { useStockListNavigation } from '@/components/use-stock-list-navigation';
import { stockListMatches } from '@/lib/stock-list';
import { authRead } from '@/lib/auth-fetch';
import { categoryMatches, panelRows, parseTrendManifest, parseTrendStock, parseTrendSummary, prediction, rowKey, trendDailyCounts, trendReason, trendReferenceText, trendStateText, trendStatistics, type TrendCategory, type TrendIndexRow, type TrendScreen, type TrendState, type TrendStock, type TrendSummary, type TrendVersion } from '@/lib/trend-volume';
import { TrendChart, volumeText, volumeUnitLabel } from '../trend-chart';
import '../../chips/chips.css';
import '../../chips/workbench.css';
import '../trend.css';

export const dynamic = 'force-static';
const api = (process.env.NEXT_PUBLIC_DATA_BASE || '').replace(/\/$/, '');
type Load<T> = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; value: T };
type InspectorTab = 'conditions' | 'statistics' | 'definition' | 'evidence';
const categories: { id: TrendCategory; label: string }[] = [{ id: 'FOCUS', label: '重点样本' }, { id: 'SELECTED', label: '入选' }, { id: 'MISSED', label: '漏选' }, { id: 'NEGATIVE', label: '反例' }, { id: 'UNKNOWN', label: '数据不足' }, { id: 'ALL', label: '全部' }];
const errorMessage = (error: unknown) => error instanceof Error ? error.message : '研究数据暂时无法读取，请重试。';
async function read<T>(path: string, signal: AbortSignal, parse: (value: unknown) => T): Promise<T> {
  return authRead(api + path, { signal, cache: 'no-store' }, async response => {
    if (!response.ok) throw Error(response.status === 401 ? '登录已失效，请重新登录。' : `研究数据暂时无法读取（HTTP ${response.status}）。`);
    return parse(await response.json());
  });
}
const percent = (value: number | null) => value === null ? '待核实' : `${value.toFixed(2)}%`;
const ratio = (value: number | null) => value === null ? '待核实' : `${value.toFixed(2)}倍`;
function Metric({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return <div className="tv-metric"><span>{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</div>;
}
function State({ value, label }: { value: TrendState; label?: string }) { return <span className={`tv-state tv-state-${value.toLowerCase()}`}>{label ?? trendStateText[value]}</span>; }
function Check({ title, state, children }: { title: string; state: TrendState; children: React.ReactNode }) {
  return <WorkbenchCheck title={title} state={state}>{children}</WorkbenchCheck>;
}
function Conditions({ screen, version, unit }: { screen: TrendScreen | null; version: TrendVersion; unit: string }) {
  if (!screen) return <p className="tv-muted">此观察日没有可核对的筛选记录。</p>;
  const pullback = screen.trend_gate.metrics.pullback_pct, shrinkRatio = screen.shrink_gate.metrics.recent_to_reference_volume;
  return <>
    <div className="tv-inspector-intro"><b>{version} 冻结条件</b><p>保留原计算结果，当前页面不调整参数。</p></div>
    <Check title="01 趋势准备" state={screen.trend_gate.status}><dl><div><dt>准备日</dt><dd>{screen.setup_date ?? '未找到有效准备'}</dd></div><div><dt>距20日高点回落</dt><dd>{typeof pullback === 'number' ? percent(pullback) : '待核实'}</dd></div></dl><p>{version === 'V3' ? '回落≥5%，且20日或5日收盘斜率向下。' : '回落≥5%，且20日收盘斜率向下；信号前一日也检查趋势。'}</p></Check>
    <Check title="02 缩量准备" state={screen.shrink_gate.status}><dl><div><dt>3日均量 / 此前10日均量</dt><dd>{typeof shrinkRatio === 'number' ? percent(shrinkRatio * 100) : '待核实'}</dd></div><div><dt>冻结基准 B</dt><dd>{volumeText(screen.baseline_mean)}{screen.baseline_mean !== null ? ` ${unit}` : ''}</dd></div><div><dt>准备距信号日</dt><dd>{screen.setup_age === null ? '待核实' : `${screen.setup_age}个交易日`}</dd></div></dl><p>比例≤{version === 'V2' ? '50%' : '70%'}；准备发生在信号前1至5个交易日。</p></Check>
    <Check title="03 放量大涨" state={screen.surge_status}><dl><div><dt>当日量 / 冻结基准</dt><dd>{ratio(screen.volume_multiple)}</dd></div><div><dt>较前收盘涨幅</dt><dd>{priceChangeText(screen.close_return_pct)}</dd></div><div><dt>收盘高于开盘</dt><dd>{screen.bullish === null ? '待核实' : screen.bullish ? '是' : '否'}</dd></div></dl><p>成交量≥2B，涨幅≥4%，且为阳线。</p></Check>
    {!screen.setup_date && <p className="tv-unknown-note">尚未找到有效准备日。</p>}
    {screen.reasons.length > 0 && <div className="tv-reasons"><h3>本日未满足 / 待核实原因</h3><ul>{[...new Set(screen.reasons)].map(r => <li key={r}>{trendReason(r)}</li>)}</ul></div>}
  </>;
}
function DailyCounts({ rows, version }: { rows: TrendIndexRow[]; version: TrendVersion }) {
  const counts = trendDailyCounts(rows, version), max = Math.max(1, ...counts.map(d => d.known_selected));
  return <section className="tv-daily"><div className="tv-section-heading"><h3>每日名单数量</h3><span>已知样本的入选下限 · {version}</span></div><ul className="tv-daily-bars" aria-label="每日已知入选数量">{counts.map((d, i) => <li key={d.date} className="tv-daily-column" aria-label={`${d.date}：已知入选${d.known_selected}条，缺失${d.unknown}条；完整数量${d.unknown ? "未知" : d.known_selected}`} title={`${d.date}：已知入选${d.known_selected}条，另${d.unknown}条未知；完整名单${d.unknown ? '未知' : d.known_selected}`}><b>{d.known_selected || ''}</b><i style={{ height: `${Math.max(2, d.known_selected / max * 34)}px` }} /><small>{i === 0 || i === counts.length - 1 || counts[i - 1]?.date.slice(0, 7) !== d.date.slice(0, 7) ? d.date.slice(5) : ''}</small></li>)}</ul><p>柱高表示已保存样本中的入选数量。</p></section>;
}

export default function TrendPage() {
  const [study, setStudy] = useState<Load<TrendSummary>>({ kind: 'loading' }), [refresh, setRefresh] = useState(0);
  const [stock, setStock] = useState<Load<TrendStock>>({ kind: 'loading' });
  const [refreshing, setRefreshing] = useState(true), [refreshError, setRefreshError] = useState(''), [detailError, setDetailError] = useState(''), [detailReload, setDetailReload] = useState(0), [chartPeriod, setChartPeriod] = useState(60);
  const listArea = useRef<HTMLUListElement>(null);
  const [version, setVersion] = useState<TrendVersion>('V3'), [period, setPeriod] = useState('ALL'), [category, setCategory] = useState<TrendCategory>('FOCUS'), [search, setSearch] = useState('');
  const [selection, setSelection] = useState({ code: '601086', date: '2026-10-27' });
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('conditions'), [page, setPage] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    read('/data/trend-volume/manifest.json', controller.signal, parseTrendManifest)
      .then(manifest => read(`/data/trend-volume/${manifest.dataset_id}/summary.json`, controller.signal, value => parseTrendSummary(value, manifest.dataset_id)))
      .then(value => { if (!controller.signal.aborted) { setStudy({ kind: 'ready', value }); setDetailError(''); } })
      .catch(error => { if (!controller.signal.aborted) { const message = errorMessage(error); setRefreshError(message); setStudy(previous => previous.kind === 'ready' ? previous : { kind: 'error', message }); } })
      .finally(() => { if (!controller.signal.aborted) setRefreshing(false); });
    return () => controller.abort();
  }, [refresh]);
  const summary = study.kind === 'ready' ? study.value : null;
  useEffect(() => {
    if (!summary) return;
    const controller = new AbortController();
    read(`/data/trend-volume/${summary.dataset_id}/${selection.code}.json`, controller.signal, value => parseTrendStock(value, summary, selection.code))
      .then(value => { if (!controller.signal.aborted) setStock({ kind: 'ready', value }); })
      .catch(error => { if (!controller.signal.aborted) { const message = errorMessage(error); setDetailError(message); setStock(previous => previous.kind === 'ready' && previous.value.dataset_id === summary.dataset_id && previous.value.code === selection.code ? previous : { kind: 'error', message }); } });
    return () => controller.abort();
  }, [summary, selection.code, detailReload]);
  const scope = useMemo(() => panelRows(summary?.rows ?? [], period), [summary, period]);
  const stats = useMemo(() => ({ V2: trendStatistics(scope, 'V2'), V3: trendStatistics(scope, 'V3') }), [scope]);
  const names = useMemo(() => new Map(summary?.stocks.map(s => [s.code, s.name])), [summary]);
  const filtered = useMemo(() => scope.filter(r => categoryMatches(r, category, version) && stockListMatches(search, r.code, names.get(r.code) ?? '', r.date)).sort((a, b) => a.date.localeCompare(b.date) || a.code.localeCompare(b.code)), [scope, category, version, search, names]);
  const visible = filtered.slice(page * 60, (page + 1) * 60), activeStats = stats[version];
  const availableStock = stock.kind === 'ready' && stock.value.code === selection.code && stock.value.dataset_id === summary?.dataset_id ? stock.value : null;
  const observation = availableStock?.observations.find(o => o.date === selection.date) ?? null, screen = observation?.results[version] ?? null;
  const selectedIndex = summary?.rows.find(r => rowKey(r) === rowKey(selection)), dates = availableStock?.observations.map(o => o.date).sort() ?? [], dateIndex = dates.indexOf(selection.date);
  const select = (row: Pick<TrendIndexRow, 'code' | 'date'>) => { if (row.code !== selection.code) { setStock({ kind: 'loading' }); setDetailError(''); } setSelection({ code: row.code, date: row.date }); };
  const navigationKeys = useMemo(() => filtered.map(rowKey), [filtered]);
  useStockListNavigation({ keys: navigationKeys, selected: rowKey(selection), listRef: listArea, page, onSelect: (key, index) => { const row = filtered.find(value => rowKey(value) === key); if (row) { select(row); setPage(Math.floor(index / 60)); } } });
  const reload = () => { setRefreshing(true); setRefreshError(''); if (study.kind !== 'ready') setStudy({ kind: 'loading' }); setRefresh(value => value + 1); };
  const retryDetail = () => { setDetailError(''); if (!availableStock) setStock({ kind: 'loading' }); setDetailReload(value => value + 1); };
  const step = (direction: -1 | 1) => { const next = dates[dateIndex + direction]; if (next) setSelection({ ...selection, date: next }); };
  const setFilter = (id: TrendCategory) => { setCategory(id); setPage(0); };
  const quoteBar=availableStock?.history.find(bar=>bar.date===selection.date);
  return <div className="chip-site chip-workbench tv-site wb-site">
    <SiteHeader active="trend"><button className="stock-header-refresh" aria-label="刷新研究快照" onClick={reload}><RefreshCw size={14}/><span>刷新</span></button></SiteHeader>
    <WorkbenchPageBar title="趋势历史研究" description="观察日复盘 · V2 / V3 条件对照" dateLabel="观察日期" date={summary?selection.date:null}><SiteLink href="/trend">每日筛选</SiteLink></WorkbenchPageBar>
    <WorkbenchShell contentClassName="tv-workspace" footer={<><span>{summary?`${summary.stocks.length}只股票 · ${scope.length}个证券日`:'正在读取研究样本…'}</span><span>历史研究 · {version}</span></>} sidebar={
      <aside className="tv-sidebar wb-sidebar" aria-label="研究样本列表">
        <WorkbenchListHeader title="研究样本" count={summary?filtered.length:null} unit="个证券日" ready={!!summary} search={{label:'搜索研究样本',value:search,onChange:value=>{setSearch(value);setPage(0);}}} filters={categories.map(c=>({id:c.id,label:c.label,count:summary?scope.filter(r=>categoryMatches(r,c.id,version)).length:null,active:category===c.id,onSelect:()=>setFilter(c.id)}))}/>
        {summary&&<button className="tv-guofang" aria-pressed={selection.code==='601086'} onClick={()=>select({code:'601086',date:'2026-10-27'})}><strong>国芳集团 <small>601086</small></strong><span>单例核对 <ChevronRight size={12}/></span></button>}
        <ul ref={listArea} className="tv-list wb-stock-list" aria-label={`${version}筛选后的样本`}>
          {!summary&&study.kind==='loading'&&<li><WorkbenchListSkeleton/></li>}
          {summary&&!visible.length&&<li className="tv-list-empty">当前范围没有结果。</li>}
          {visible.map(r=><li key={rowKey(r)}><button className="tv-row wb-stock-row" aria-label={`${names.get(r.code)||r.code} ${r.code} ${r.date}`} data-stock-key={rowKey(r)} aria-pressed={rowKey(selection)===rowKey(r)} onClick={()=>select(r)}><span><strong>{names.get(r.code)||r.code}</strong><b className={priceChangeClass(r.close_return_pct)}>{priceChangeText(r.close_return_pct)}</b></span><span><small>{r.code} · {r.date.slice(5)}</small><em className="wb-row-status">{prediction(r,version)==='PASS'?'当日入选':prediction(r,version)==='UNKNOWN'?'数据不足':r.reference==='POSITIVE'?'漏选':'未满足'}</em></span></button></li>)}
        </ul><WorkbenchListFooter count={filtered.length} unit="个证券日" page={page} onPageChange={setPage}/>
      </aside>}>
      {summary&&refreshing&&<output className="wb-update-note">正在更新…</output>}
      {summary&&refreshError&&<RetryMessage message={`刷新失败，仍显示上次结果。${refreshError}`} onRetry={reload}/>}
      {study.kind==='loading'&&<WorkbenchState state={{kind:'loading',label:'正在读取研究样本…'}}/>}
      {study.kind==='error'&&<WorkbenchState state={{kind:'error',message:study.message,onRetry:reload}}/>}
      {summary&&!availableStock&&<WorkbenchState state={detailError?{kind:'error',message:detailError,onRetry:retryDetail}:{kind:'loading',label:'正在读取个股行情…'}}/>}
      {summary&&availableStock&&<>
        <StockListNote outside={!filtered.some(row=>rowKey(row)===rowKey(selection))}/>
        <StockDetailHeader className="tv-stock-heading" name={names.get(selection.code)||selection.code} code={selection.code} date={selection.date} close={quoteBar?.close??null} percent={candleReturn(availableStock.history,selection.date)} badge={<span className="cw-screen-badge">{selection.code==='601086'?'单例核对':selectedIndex?trendReferenceText[selectedIndex.reference]:'历史样本'}</span>}/>
        <div className="wb-research-controls">{selectedIndex?.case_id&&<span className="wb-case-label">样例 {selectedIndex.case_id}</span>}<div className="tv-version-switch" aria-label="条件版本">{(['V2', 'V3'] satisfies TrendVersion[]).map(v => <button key={v} aria-pressed={version === v} onClick={() => { setVersion(v); setPage(0); }}>{v}</button>)}</div><label>统计范围<select value={period} onChange={e=>{setPeriod(e.target.value);setPage(0);}} aria-label="统计时间段"><option value="ALL">全部时间段</option>{summary.periods.map(p=><option key={p.id} value={p.id}>{p.label}</option>)}</select></label></div>
          <div className="tv-observation-bar"><div><span>观察日</span><button className="tv-step" aria-label="上一个观察日" disabled={dateIndex <= 0} onClick={() => step(-1)}><ChevronLeft size={15} /></button><select aria-label="观察日期" value={selection.date} disabled={!availableStock || !dates.length} onChange={e => setSelection({ ...selection, date: e.target.value })}>{dates.length ? dates.map(d => <option key={d}>{d}</option>) : <option>{selection.date}</option>}</select><button className="tv-step" aria-label="下一个观察日" disabled={dateIndex < 0 || dateIndex >= dates.length - 1} onClick={() => step(1)}><ChevronRight size={15} /></button></div>{selectedIndex && <State value={prediction(selectedIndex, version)} label={`${version} ${prediction(selectedIndex, version) === 'PASS' ? '当日入选' : prediction(selectedIndex, version) === 'UNKNOWN' ? '数据不足' : '当日未入选'}`} />}</div>
          {availableStock && <><TrendChart stock={availableStock} date={selection.date} screen={screen} periodValue={chartPeriod} onPeriodChange={setChartPeriod} /><div className="tv-result-strip wb-metrics"><Metric label="准备日" value={screen?.setup_date ?? '未找到'} /><Metric label="缩量比例" value={typeof screen?.shrink_gate.metrics.recent_to_reference_volume === 'number' ? percent(screen.shrink_gate.metrics.recent_to_reference_volume * 100) : '待核实'} /><Metric label="当日放量" value={ratio(screen?.volume_multiple ?? null)} /><Metric label="当日涨幅" value={priceChangeText(screen?.close_return_pct ?? null)} /></div></>}

        <section className="wb-inspector" aria-label="条件与研究定义"><InspectorTabs className="tv-inspector-tabs" label="研究核对面板" idPrefix="tv" tabs={[{id:'conditions',label:'条件核对'},{id:'statistics',label:'研究统计'},{id:'definition',label:'待验证定义'},{id:'evidence',label:'口径与来源'}]} value={inspectorTab} onChange={setInspectorTab}/><div className="tv-inspector-body" role="tabpanel" id={`tv-${inspectorTab}`} aria-labelledby={`tv-${inspectorTab}-tab`}>
          {inspectorTab === 'conditions' && <Conditions screen={screen} version={version} unit={volumeUnitLabel(availableStock?.volume_unit ?? '')} />}
          {inspectorTab === 'statistics' && <>      <section className="tv-overview" aria-label="V2与V3统计对比">
        <div className="tv-coverage"><Metric label="行情覆盖" value={`${activeStats.knownStocks} / ${activeStats.stocks}`} hint={`已知${activeStats.known} · 缺失${activeStats.unknown}个证券日`} /><Metric label="参考正例" value={activeStats.positiveCount} hint={`${activeStats.uncertain}条形态待定另计`} /></div>
        <div className="tv-comparison-wrap"><table className="tv-comparison"><caption className="tv-sr">冻结V2和V3对比，国芳单例不计入</caption><thead><tr><th>版本</th><th>命中 / 正例</th><th>漏选</th><th>误选</th><th>入选证券日 / 证券</th></tr></thead><tbody>{(['V2', 'V3'] satisfies TrendVersion[]).map(v => <tr key={v} data-active={version === v}><th><button onClick={() => { setVersion(v); setPage(0); }} aria-pressed={version === v}>{v}{v === 'V3' && <small>当前冻结</small>}</button></th><td>{stats[v].tp}<span> / {stats[v].positiveCount}</span></td><td>{stats[v].fn}<small>{stats[v].missRate === null ? '待核实' : `${(stats[v].missRate * 100).toFixed(0)}%`}</small></td><td>{stats[v].fp}<small>观察值</small></td><td>{stats[v].selected}<span> / {stats[v].selectedStocks}</span></td></tr>)}</tbody></table></div>
      </section>
      <div className="tv-stat-note">模型代理参考 · 非独立验证 · {activeStats.negativeCount}条自动反例多数由共同涨幅门槛排除，零误选不足以证明控制有效。国芳集团单例不计入上述统计。</div>
<DailyCounts rows={scope} version={version}/></>}
          {inspectorTab === 'definition' && <><div className="tv-definition-status">新定义 · 尚未计算</div><h2>下跌状态如何持续</h2><p className="tv-definition-lead">下跌趋势里的缩量、小涨，先保留下跌状态。大量且大涨后，再评估启动或反转。</p><ol className="tv-rule-list">{summary.rules.persistence.map((r, i) => <li key={i}>{r}</li>)}</ol><h3>后续需要观察的统计</h3><dl className="tv-pending-metrics">{['下跌段数量与持续天数', '下跌中缩量的次数和时长', '大量且大涨的联合事件', '启动后的结构确认与失败'].map(label => <div key={label}><dt>{label}</dt><dd>未计算</dd></div>)}</dl><p className="tv-unknown-note">新状态定义未写入V3。这里没有把单日放量大涨自动记成上升趋势，也没有加入黄金分割。</p><h3>剩余11个漏选的复看</h3><p>数值定义待研究 {summary.diagnosis.numeric_review}个，形态待定 {summary.diagnosis.unresolved}个。明确不符形态 {summary.diagnosis.mismatch_supported}个。</p><p className="tv-muted">复看是模型代理意见，原11个漏选统计保持不变。</p></>}
          {inspectorTab === 'evidence' && <><h2>统计与证据口径</h2><p className="tv-source-note">来源 {availableStock.provider} · 成交量原始单位 {volumeUnitLabel(availableStock.volume_unit)} · 历史采集时点未核定，按保存的K线前缀复盘。</p><details className="tv-candidates" ><summary>前5个交易日的准备检查 <span>{screen?.candidates.length ?? 0}条</span></summary><p>趋势与缩量须在同一天同时满足。以下为原筛选器保存的逐日检查。</p><div className="tv-table-scroll"><table><thead><tr><th>准备候选日</th><th>趋势</th><th>缩量</th><th>回落</th><th>3 / 10日量</th></tr></thead><tbody>{screen?.candidates.map(c => <tr key={c.date} data-chosen={c.date === screen.setup_date}><td>{c.date}{c.date === screen.setup_date ? ' ✓' : ''}</td><td><State value={c.trend_status} /></td><td><State value={c.shrink_status} /></td><td>{percent(c.pullback_pct)}</td><td>{c.shrink_ratio === null ? '待核实' : percent(c.shrink_ratio * 100)}</td></tr>)}</tbody></table></div>{!screen?.candidates.length && <p>没有可用的候选记录。</p>}</details><ul className="tv-limitations">{summary.limitations.map((r, i) => <li key={i}>{r}</li>)}</ul><details className="tv-rule-detail"><summary>查看 V2 / V3 冻结条件</summary>{(['V2', 'V3'] satisfies TrendVersion[]).map(v => <section key={v}><h3>{v}</h3><ol>{summary.rules[v].map((r, i) => <li key={i}>{r}</li>)}</ol></section>)}</details><h3>来源快照</h3>{summary.sources.map(s => <details className="tv-source-hash" key={s.label}><summary>{s.label}</summary><code>{s.sha256}</code></details>)}<p className="tv-muted">研究批次 {summary.dataset_id}<br />{summary.created_at.replace('T', ' ').slice(0, 19)}</p></>}
        </div></section>
      </>}
    </WorkbenchShell>
  </div>;
}
