/* oxlint-disable next/no-html-link-for-pages -- Static CDN routes use document navigation. */
'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { SiteHeader } from '@/components/site-header';
import { SiteLink } from '@/components/site-router';
import { WorkbenchPageBar, WorkbenchShell, WorkbenchListHeader, WorkbenchListFooter, WorkbenchState, WorkbenchListSkeleton, StockDetailHeader } from '@/components/workbench';
import { InspectorTabs } from '@/components/inspector-tabs';
import { useStockListNavigation } from '@/components/use-stock-list-navigation';
import { CandleReadout } from '@/components/candle-readout';
import { CandleInspectionControl } from '@/components/candle-inspection-control';
import { ChartPeriodControl } from '@/components/chart-period-control';
import { candleReturn } from '@/lib/candle-quote';
import { useCandleInspection } from '@/lib/use-candle-inspection';
import { candleIndexAtPoint } from '@/lib/chart-inspection';
import { authRead } from '@/lib/auth-fetch';
import { startPublicationFollow } from '@/lib/publication-follow';
import { goldenPresets, goldenPresetItems, type GoldenListItem } from '@/lib/golden-preset-list';
import { isProjectionIndex, categoryText, stateText, eventText, featuredCategories, projectionCategories, projectionCounts, projectionDistance, projectionList, projectionStrictMatch, parseProjectionManifest, parseProjectionScreening, parseProjectionStatus, parseProjectionStock, type ProjectionFilter, type ProjectionManifest, type ProjectionScreening, type ProjectionStatus, type ProjectionStock, type ProjectionRow, type ProjectionStructure } from '@/lib/golden-projection';
import '../../chips/chips.css';
import '../../chips/workbench.css';
import './daily.css';

export const dynamic = 'force-static';
const api = (process.env.NEXT_PUBLIC_DATA_BASE || '').replace(/\/$/, '');
const prefix = '/data/golden-projection/daily/';
const pageSize = 50;
const emptyRows: ProjectionRow[] = [];
type Publication = { manifest: ProjectionManifest; screening: ProjectionScreening };
type Detail = { kind: 'ready'; key: string; stock: ProjectionStock } | { kind: 'error'; key: string; message: string } | { kind: 'empty' };
type Filters = { category: ProjectionFilter; search: string; strict: boolean };
const initialFilters: Filters = { category: 'FEATURED', search: '', strict: false };
type ListState = {
  active: 'market' | 'preset';
  market: { filters: Filters; code: string; page: number };
  preset: { search: string; code: string };
};
const initialLists: ListState = { active: 'market', market: { filters: initialFilters, code: '', page: 0 }, preset: { search: '', code: goldenPresets[0]?.id ?? '' } };
const filterText = (filter: ProjectionFilter, indices = false) => filter === 'ALL' ? indices ? '全部标的' : '全部股票' : filter === 'FEATURED' ? '五类关注' : categoryText[filter];
const fieldText = { open: '开盘', high: '最高', low: '最低', close: '收盘' };
const routeText = { ROLE: '支撑／阻力反应', TURN: '完整次高／次低', BOTH: '转折与支撑／阻力重合' };
const priceText = (value: string | null) => value === null ? '—' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 4 });
const failed = (error: unknown) => error instanceof Error ? error.message : '画线数据暂时无法读取。';
function timeText(value: string) { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)); }
async function read<T>(path: string, signal: AbortSignal, parse: (value: unknown) => T): Promise<T> {
  return authRead(api + path, { signal, cache: 'no-store' }, async response => {
    if (!response.ok) throw Error(response.status === 401 ? '登录已失效，请重新登录。' : response.status === 404 ? '这一批画线结果尚未发布。' : `画线数据暂时无法读取（HTTP ${response.status}）。`);
    return parse(await response.json());
  });
}
function newCrossingText(row: ProjectionRow): string {
  if (row.new_crossing === 'UP') return '今日新突破';
  if (row.new_crossing === 'DOWN') return '今日新跌破';
  if (row.drawing_state === 'NEW_REFERENCE') return '新组合 · 仅说明当前收盘位置';
  if (row.category === 'ABOVE') return '收盘在高点上方';
  if (row.category === 'BELOW') return '收盘在低点下方';
  return stateText[row.drawing_state];
}
function StructureChart({ stock, structure, count }: { stock: ProjectionStock; structure: ProjectionStructure | null; count: number }) {
  const bars = stock.history.slice(-count);
  const chart = useRef<SVGSVGElement>(null), [size, setSize] = useState({ width: 900, height: 360 });
  const inspection = useCandleInspection({ history: stock.history, dates: bars.map(bar => bar.date), defaultDate: bars.at(-1)?.date ?? stock.as_of, contextDate: stock.as_of });
  useEffect(() => { const node = chart.current; if (!node) return; const measure = () => { const rect = node.getBoundingClientRect(); if (rect.width && rect.height) setSize({ width: Math.round(rect.width), height: Math.round(rect.height) }); }; measure(); const observer = new ResizeObserver(measure); observer.observe(node); return () => observer.disconnect(); }, [bars.length > 0]);
  if (!bars.length) return <div className="gp-no-chart">行情暂不可用，未补造 K 线。</div>;
  const width = size.width, height = size.height, left = width < 600 ? 12 : 28, right = width - 112, top = 25, bottom = height * .65, volTop = height * .78, volBottom = height - 29;
  const prices = bars.flatMap(bar => [Number(bar.high), Number(bar.low)]).concat(structure ? Object.values(structure.levels).map(Number) : []);
  const lo = Math.min(...prices), hi = Math.max(...prices), pad = Math.max((hi - lo) * .07, hi * .003), min = lo - pad, max = hi + pad;
  const y = (value: number) => bottom - (value - min) / (max - min) * (bottom - top), step = (right - left) / bars.length, x = (index: number) => left + (index + .5) * step;
  const dateX = (date: string) => { const at = bars.findIndex(bar => bar.date >= date); return at < 0 ? right : x(at); };
  const maxVolume = Math.max(1, ...bars.map(bar => Number(bar.volume_shares)));
  const bar = bars.find(item => item.date === inspection.activeDate) ?? bars[bars.length - 1];
  const guides = structure && (!structure.guides_end_on || structure.guides_end_on >= bars[0].date) ? [{ key: 'H', price: structure.levels.H, label: structure.direction === 'UP' ? '推算高点' : '原高点' }, { key: 'M', price: structure.levels.M, label: '中轴' }, { key: 'L', price: structure.levels.L, label: structure.direction === 'DOWN' ? '推算低点' : '原低点' }] : [];
  const labelYs = guides.map((guide, index) => ({ index, y: y(Number(guide.price)) })).sort((a, b) => a.y - b.y);
  for (let index = 0; index < labelYs.length; index++) labelYs[index].y = Math.max(labelYs[index].y, index ? labelYs[index - 1].y + 22 : top + 10);
  for (let index = labelYs.length - 1; index >= 0; index--) labelYs[index].y = Math.min(labelYs[index].y, index < labelYs.length - 1 ? labelYs[index + 1].y - 22 : bottom - 6);
  function inspectPoint(event: React.PointerEvent<SVGSVGElement>, focus = false) { const rect = event.currentTarget.getBoundingClientRect(), px = (event.clientX - rect.left) / rect.width * width, py = (event.clientY - rect.top) / rect.height * height, index = candleIndexAtPoint({ x: px, y: py, left, right, top, bottom: volBottom, count: bars.length }); if (focus && index !== null) { event.preventDefault(); inspection.focus(); } inspection.inspect(index === null ? null : bars[index].date); }
  return <div className="gp-chart"><CandleReadout candle={bar} percent={candleReturn(stock.history, bar.date)} volume={Number(bar.volume_shares)} volumeUnit="shares" /><CandleInspectionControl dates={bars.map(item => item.date)} date={bar.date} onDateChange={inspection.select} onBlur={inspection.clear} inputRef={inspection.control} />
    <svg ref={chart} className="candle-inspection" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="真实日K线、成交量及当前选点水平线" onPointerMove={inspectPoint} onPointerDown={event => inspectPoint(event, true)} onPointerCancel={inspection.clear} onPointerLeave={event => { if (event.pointerType !== 'touch') inspection.clear(); }}>
      {[0, 1, 2, 3, 4].map(index => { const value = min + (max - min) * index / 4; return <g key={index}><line x1={left} x2={right} y1={y(value)} y2={y(value)} stroke="#e9edf1" /><text x={left} y={y(value) - 6} fill="#94a0ae" fontSize="11">{priceText(String(value))}</text></g>; })}
      {structure?.role_zone && <rect data-role="role-zone" x={dateX(structure.role_zone.start_date)} width={Math.max(0, dateX(structure.role_zone.end_date) - dateX(structure.role_zone.start_date) + step)} y={y(Number(structure.role_zone.upper))} height={Math.max(1, y(Number(structure.role_zone.lower)) - y(Number(structure.role_zone.upper)))} fill="#e8c988" opacity=".22" />}
      {bars.map((item, index) => { const color = Number(item.close) >= Number(item.open) ? 'var(--ui-rise)' : 'var(--ui-fall)', px = x(index), bw = Math.max(1, Math.min(12, step * .6)); return <g key={item.date} data-candle-date={item.date}><title>{item.date} 开{item.open} 高{item.high} 低{item.low} 收{item.close}</title><line x1={px} x2={px} y1={y(Number(item.high))} y2={y(Number(item.low))} stroke={color} /><rect x={px - bw / 2} y={Math.min(y(Number(item.open)), y(Number(item.close)))} width={bw} height={Math.max(1, Math.abs(y(Number(item.open)) - y(Number(item.close))))} fill={color} /><rect data-volume-date={item.date} x={px - bw / 2} y={volBottom - Number(item.volume_shares) / maxVolume * (volBottom - volTop)} width={bw} height={Number(item.volume_shares) / maxVolume * (volBottom - volTop)} fill={color} opacity=".65" />{index % Math.ceil(bars.length / Math.max(2, Math.floor((right - left) / 80))) === 0 && <text x={px} y={height - 9} textAnchor="middle" fill="#8996a6" fontSize="10">{item.date.slice(5)}</text>}</g>; })}
      <line x1={left} x2={right} y1={volTop - 13} y2={volTop - 13} stroke="#e5e9ef" /><text x={left} y={volTop - 19} fill="#8996a6" fontSize="11">成交量 · 股</text>
      {guides.map((guide, index) => { const label = labelYs.find(item => item.index === index), py = y(Number(guide.price)), end = structure?.guides_end_on ? Math.min(right, dateX(structure.guides_end_on)) : right, start = structure ? Math.max(left, dateX(structure.evidence_through)) : left, stopped = end < right - step; return <g key={guide.key} data-guide={guide.key} data-price={guide.price} data-end={structure?.guides_end_on ?? stock.as_of}><line x1={Math.min(start, end)} x2={end} y1={py} y2={py} stroke={guide.key === 'M' ? '#b07c27' : '#7b91a6'} strokeWidth={guide.key === 'M' ? 1.5 : 1} strokeDasharray={guide.key === 'M' ? undefined : '5 4'} />{!stopped && <line x1={end} x2={right + 10} y1={py} y2={label?.y ?? py} stroke="#b2bdc8" />}<text x={stopped ? Math.max(left, end - 130) : right + 15} y={stopped ? py - 7 : (label?.y ?? py) + 4} fill={guide.key === 'M' ? '#9d7022' : '#62788d'} fontSize="12">{guide.key} {priceText(guide.price)}<title>{guide.label} {guide.price}</title></text></g>; })}
      {structure && [{ ...structure.origin, label: structure.origin.kind }, { ...structure.axis, label: 'M' }].map(point => { const at = bars.findIndex(item => item.date === point.date); return at < 0 ? null : <g key={point.label} data-point={point.label}><circle cx={x(at)} cy={y(Number(point.price))} r="4" fill={point.label === 'M' ? '#b07c27' : '#6688a6'} /><text x={Math.max(left, Math.min(right - 125, x(at) + 6))} y={y(Number(point.price)) - 12} fill="#60778e" fontSize="12">{point.label} · {point.date.slice(5)} · {point.price}</text></g>; })}
    </svg><div className="gp-chart-foot"><span>红涨绿跌 · {bars.length} 根实际日线 · {isProjectionIndex(stock.code) ? '指数点位' : '不复权'}</span><span>{structure?.guides_end_on ? `结构参考线截至 ${structure.guides_end_on}` : structure ? '水平线为本组合的价格参考' : '当前标的没有足够依据，保留 K 线并说明不画原因'}</span></div>
  </div>;
}
function StructureProof({ structure, title }: { structure: ProjectionStructure; title: string }) {
  return <section className="gp-proof"><h3>{title}</h3><dl><div><dt>画法方向</dt><dd>{structure.direction === 'DOWN' ? '高点与次高中轴推低点' : '低点与次低中轴推高点'}</dd></div><div><dt>中轴来源</dt><dd>{routeText[structure.route]}</dd></div><div><dt>起算端</dt><dd>{structure.origin.date} · {fieldText[structure.origin.field]} {structure.origin.price}</dd></div><div><dt>中轴取价</dt><dd>{structure.axis.date} · {fieldText[structure.axis.field]} {structure.axis.price}</dd></div><div><dt>证据检查至</dt><dd>{structure.evidence_through}</dd></div><div><dt>实际保存</dt><dd>{timeText(structure.recorded_at)} 北京时间</dd></div><div><dt>生效交易日</dt><dd>{structure.effective_from}</dd></div></dl><ul>{structure.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul><details><summary>展开原始选点证据（{structure.evidence.length}项）</summary><div className="gp-evidence-table"><table><thead><tr><th>日期</th><th>字段／原价</th><th>依据</th></tr></thead><tbody>{structure.evidence.map((item, index) => <tr key={index}><td>{item.date}</td><td>{fieldText[item.field]} {item.value}</td><td>{item.role}</td></tr>)}</tbody></table></div></details></section>;
}
export default function GoldenProjectionPage() {
  const [publication, setPublication] = useState<Publication | null>(null), [status, setStatus] = useState<ProjectionStatus | null>(null), [settled, setSettled] = useState(false), [error, setError] = useState(''), [statusError, setStatusError] = useState('');
  const [lists, setLists] = useState<ListState>(initialLists), [detail, setDetail] = useState<Detail>({ kind: 'empty' }), [detailReload, setDetailReload] = useState(0), [zoom, setZoom] = useState(60), [online, setOnline] = useState(true), [tab, setTab] = useState<'selection' | 'rules'>('selection');
  const current = useRef(initialLists), follow = useRef<{ refresh: () => void; stop: () => void } | null>(null), listArea = useRef<HTMLUListElement>(null);
  function changeLists(next: ListState) { current.current = next; setLists(next); }
  useEffect(() => { const update = () => setOnline(navigator.onLine); update(); window.addEventListener('online', update); window.addEventListener('offline', update); return () => { window.removeEventListener('online', update); window.removeEventListener('offline', update); }; }, []);
  useEffect(() => {
    const controller = startPublicationFollow({
      readStatus: signal => read(prefix + 'status.json', signal, parseProjectionStatus),
      readManifest: signal => read(prefix + 'manifest.json', signal, parseProjectionManifest),
      readPayload: (manifest, signal) => read(prefix + manifest.dataset_id + '/screening.json', signal, value => parseProjectionScreening(value, manifest)),
      onStatus: setStatus, onStatusError: value => setStatusError(value ? '更新状态暂不可读，保留已发布结果。' : ''),
      onPublication: (manifest, screening) => {
        const state = current.current, market = state.market, preset = state.preset;
        const marketRows = projectionList(screening.rows, market.filters.category, market.filters.search, market.filters.strict), marketCode = marketRows.find(row => row.code === market.code)?.code ?? marketRows[0]?.code ?? '';
        const presetRows = goldenPresetItems(screening.rows, preset.search), presetCode = presetRows.find(item => item.security.id === preset.code)?.security.id ?? presetRows[0]?.security.id ?? '';
        changeLists({ ...state, market: { ...market, code: marketCode, page: Math.max(0, Math.floor(marketRows.findIndex(row => row.code === marketCode) / pageSize)) }, preset: { ...preset, code: presetCode } });
        setPublication({ manifest, screening });
      },
      onPublicationError: value => setError(value?.message ?? ''), onSettled: () => setSettled(true), isRunning: value => value.state === 'RUNNING' || value.state === 'SOURCE_PENDING', publishedDataset: value => value.state === 'COMMITTED' ? value.dataset_id : null,
      activeManifestMs: 30000, idleManifestMs: 60000, activeStatusMs: 20000, idleStatusMs: 60000,
    }); follow.current = controller; return () => { controller.stop(); follow.current = null; };
  }, []);
  const manifest = publication?.manifest, rows = publication?.screening.rows ?? emptyRows;
  const presetMode = lists.active === 'preset', filters = lists.market.filters, code = presetMode ? lists.preset.code : lists.market.code, page = presetMode ? 0 : lists.market.page;
  const shown = useMemo<GoldenListItem[]>(() => lists.active === 'preset' ? goldenPresetItems(rows, lists.preset.search) : projectionList(rows, lists.market.filters.category, lists.market.filters.search, lists.market.filters.strict).map(row => ({ kind: 'published', security: isProjectionIndex(row.code) ? { kind: 'index', id: row.code, name: row.name } : { kind: 'equity', id: row.code, name: row.name }, row })), [rows, lists.active, lists.preset.search, lists.market.filters]);
  const visible = presetMode ? shown : shown.slice(page * pageSize, (page + 1) * pageSize), selectedItem = shown.find(item => item.security.id === code), selected = selectedItem?.kind === 'published' ? selectedItem.row : null, missing = selectedItem?.kind === 'missing' ? selectedItem : null;
  const counts = useMemo(() => projectionCounts(rows.filter(row => projectionStrictMatch(row, filters.strict))), [rows, filters.strict]);
  const key = manifest && selected ? `${manifest.dataset_id}:${selected.code}` : '', stock = detail.kind === 'ready' && detail.key === key ? detail.stock : null, detailError = detail.kind === 'error' && detail.key === key ? detail.message : '';
  useEffect(() => { if (!manifest || !selected) return; const controller = new AbortController(), detailKey = `${manifest.dataset_id}:${selected.code}`; read(prefix + manifest.dataset_id + '/' + selected.code + '.json', controller.signal, value => parseProjectionStock(value, manifest, selected)).then(value => { if (!controller.signal.aborted) setDetail({ kind: 'ready', key: detailKey, stock: value }); }).catch(value => { if (!controller.signal.aborted) setDetail({ kind: 'error', key: detailKey, message: failed(value) }); }); return () => controller.abort(); }, [manifest, selected, detailReload]);
  function filter(next: Filters) { const state = current.current, list = projectionList(rows, next.category, next.search, next.strict), selectedRow = list.find(row => row.code === state.market.code) ?? list[0]; changeLists({ ...state, market: { code: selectedRow?.code ?? '', filters: next, page: Math.max(0, Math.floor(list.findIndex(row => row.code === selectedRow?.code) / pageSize)) } }); }
  function searchPreset(search: string) { const state = current.current, items = goldenPresetItems(rows, search), selectedItem = items.find(item => item.security.id === state.preset.code) ?? items[0]; changeLists({ ...state, preset: { search, code: selectedItem?.security.id ?? '' } }); }
  function choose(value: string, index?: number) { const state = current.current; changeLists(state.active === 'preset' ? { ...state, preset: { ...state.preset, code: value } } : { ...state, market: { ...state.market, code: value, page: index === undefined ? state.market.page : Math.floor(index / pageSize) } }); }
  const navigationKeys = useMemo(() => shown.map(item => item.security.id), [shown]);
  useStockListNavigation({ keys: navigationKeys, selected: code, listRef: listArea, page, onSelect: choose });
  const refreshData = () => { follow.current?.refresh(); if (detailError) setDetailReload(value => value + 1); };
  const running = status?.state === 'RUNNING', newerTarget = manifest && status && status.as_of > manifest.as_of;
  const updateMessage = !online ? `网络离线，保留已加载的 ${manifest?.as_of ?? '原'} 结果。` : running ? `正在计算 ${status.as_of} · ${status.scanned_count.toLocaleString()} / ${status.universe_total.toLocaleString()}${(publication?.manifest.index_total ?? 0) > 0 ? '项' : '只'}；当前显示 ${manifest?.as_of ?? '等待发布'}。` : newerTarget ? `${status.as_of} 尚未完整发布，当前显示 ${manifest?.as_of}。` : status?.state === 'FAILED' || status?.state === 'SOURCE_PENDING' ? status.message : manifest?.source_generation_stale ? '来源已有更新批次，等待下一轮计算。' : '';
  const primaryFilters: ProjectionFilter[] = ['FEATURED', ...featuredCategories], secondaryFilters: ProjectionFilter[] = ['ALL', 'FAR', 'NO_DRAW', 'HISTORY', 'UNKNOWN', 'AMBIGUOUS'];
  const selectedStructure = stock?.structure ?? null, includesIndices = (manifest?.index_total ?? 0) > 0;
  return <div className="chip-site chip-workbench gp-site wb-site">
    <SiteHeader active="golden"><button className="stock-header-refresh" aria-label="刷新每日画线" onClick={refreshData}><RefreshCw size={14} /><span>刷新</span></button></SiteHeader>
    <WorkbenchPageBar title="每日黄金画线" date={manifest?.as_of} dateLabel="行情日" explanation={{ label: '画线与分类口径', content: <ol><li>先确定波段及层级，再找有意义的支撑／阻力反应，或完整次高／次低；依据不足不强画。</li><li>附近为收盘价距离关键位不超过 2%；突破高点、跌破低点优先分类，盘中越线后收回另标。</li><li>当日使用开盘前已生效的组合；收盘新组合下一交易日启用。首次计算只描述当前收盘位置。</li></ol> }}><SiteLink href="/golden">人工核对工作台</SiteLink></WorkbenchPageBar>
    <WorkbenchShell contentClassName="gp-workspace" sidebar={<aside className="gp-sidebar wb-sidebar" aria-label="每日画线股票名单">
      <div className="gp-list-switch" role="group" aria-label="黄金分割列表"><button type="button" aria-pressed={!presetMode} onClick={() => changeLists({ ...current.current, active: 'market' })}>每日分类</button><button type="button" aria-pressed={presetMode} onClick={() => changeLists({ ...current.current, active: 'preset' })}>指定标的 <span>{goldenPresets.length}</span></button></div>
      <WorkbenchListHeader title={presetMode ? '指定标的' : '每日画线'} count={presetMode || manifest ? shown.length : null} unit={presetMode || includesIndices ? '项' : '只'} ready={presetMode || !!manifest} search={presetMode ? { label: '搜索指定标的', value: lists.preset.search, onChange: searchPreset } : { label: '搜索画线股票', value: filters.search, onChange: value => filter({ ...filters, search: value }) }} filters={presetMode ? [] : primaryFilters.map(category => ({ id: category, label: filterText(category, includesIndices), count: manifest ? category === 'FEATURED' ? featuredCategories.reduce((sum, key) => sum + counts[key], 0) : category === 'ALL' ? rows.length : counts[category] : null, active: filters.category === category, onSelect: () => filter({ ...filters, category }) }))} />
      {presetMode ? <p className="gp-preset-note">按指定顺序展示，保留不画、历史和数据不足的结果。</p> : <><div className="gp-secondary-filter"><label>更多分类<select aria-label="更多画线分类" value={secondaryFilters.includes(filters.category) ? filters.category : ''} disabled={!manifest} onChange={event => { const category = secondaryFilters.find(value => value === event.currentTarget.value); if (category) filter({ ...filters, category }); }}><option value="">选择其他分类</option>{secondaryFilters.map(category => <option key={category} value={category}>{filterText(category, includesIndices)} · {manifest ? category === 'ALL' ? rows.length : category === 'FEATURED' ? 0 : counts[category] : '—'}</option>)}</select></label></div>
      <label className="gp-strict"><input type="checkbox" checked={filters.strict} disabled={!manifest} onChange={event => filter({ ...filters, strict: event.target.checked })} />近高／近低只看盘中也未越线</label></>}
      <ul ref={listArea} className="gp-list wb-stock-list" aria-label={presetMode ? '指定标的结果' : '当前分类股票'}>
        {!presetMode && !publication && !error && <li><WorkbenchListSkeleton /></li>}
        {!visible.length && (presetMode || publication) && <li className="gp-list-empty">{presetMode ? '没有匹配的指定标的。' : '当前分类没有符合条件的股票。可切换其他分类查看不画原因。'}</li>}
        {visible.map(item => { const { id, name } = item.security, row = item.kind === 'published' ? item.row : null; return <li key={id}><button className="gp-stock-row wb-stock-row" data-stock-key={id} aria-label={`${name} ${id}`} aria-pressed={id === code} onClick={() => choose(id)}><span><strong>{name}</strong><b className="wb-row-price">{priceText(row?.close ?? null)}</b></span><span><small>{id}</small><em className="wb-row-status">{row ? categoryText[row.category] : item.kind === 'missing' && item.reason === 'index-unavailable' ? '等待指数结果' : !publication ? error ? '结果读取失败' : '等待发布结果' : '本批暂无结果'}</em></span>{row ? <><small className="gp-distance">{Number.isFinite(projectionDistance(row)) ? `距线 ${projectionDistance(row).toFixed(2)}%` : stateText[row.drawing_state]}{row.new_crossing === 'UP' || row.new_crossing === 'DOWN' ? ' · 今日新跨线' : row.drawing_state === 'NEW_REFERENCE' ? ' · 新位置参考' : ''}</small>{row.intraday_tags.includes('UPPER_REJECTED') || row.intraday_tags.includes('LOWER_RECOVERED') ? <i>盘中越线后收回</i> : null}</> : <small className="gp-distance">{item.security.kind === 'index' ? '暂无每日画线结果' : publication ? '当前发布批次未包含该股票' : error ? '重新读取后核对本股结果' : '正在等待完整结果'}</small>}</button></li>; })}
      </ul>{presetMode ? <div className="gp-preset-footer">15 只股票 · 2 个指数 · 固定顺序</div> : <WorkbenchListFooter count={shown.length} page={page} pageSize={pageSize} onPageChange={value => changeLists({ ...current.current, market: { ...current.current.market, page: value } })} />}
    </aside>}>
      {publication && (updateMessage || error || statusError) && <div className="wb-update-note" role={error || statusError ? 'alert' : 'status'}>{[updateMessage, error || statusError].filter(Boolean).join(' · ')}<button className="gp-inline-retry" onClick={refreshData}>重新读取</button></div>}
      {!publication && <WorkbenchState state={error ? { kind: 'error', message: error, onRetry: refreshData } : settled ? { kind: 'empty', title: running ? '正在生成每日画线' : '每日画线待发布', message: status ? `${status.as_of} · 已处理 ${status.scanned_count.toLocaleString()} / ${status.universe_total.toLocaleString()}项；${status.message}` : '完整结果发布后自动显示。' } : { kind: 'loading', label: '正在读取每日画线…' }} />}
      {publication && !selectedItem && <WorkbenchState state={{ kind: 'empty', title: presetMode ? '没有匹配的指定标的' : '当前分类没有股票', message: presetMode ? '调整搜索名称或代码后查看。' : '不符合的组合会保留为空。可切换其他分类查看不画原因。' }} />}
      {publication && missing && <WorkbenchState state={{ kind: 'empty', title: `${missing.security.name}暂无每日画线结果`, message: missing.reason === 'index-unavailable' ? `${missing.security.id} · 当前发布批次未包含该指数，等待更新结果。` : `${missing.security.id} · ${manifest?.as_of} 发布批次未包含该股票。` }} />}
      {selected && !stock && <WorkbenchState state={detailError ? { kind: 'error', message: detailError, onRetry: () => setDetailReload(value => value + 1) } : { kind: 'loading', label: `正在读取${selected.name}的实际行情与选点依据…` }} />}
      {selected && stock && <>
        <StockDetailHeader name={selected.name} code={selected.code} date={selected.as_of} close={selected.close === null ? null : Number(selected.close)} percent={candleReturn(stock.history, selected.as_of)} basis={isProjectionIndex(selected.code) ? '指数点位' : '不复权'} badge={<span className={`gp-position gp-position-${selected.category.toLowerCase()}`}>{categoryText[selected.category]}</span>} />
        <p className="gp-context-note">{newCrossingText(selected)}</p>
        {selected.drawing_state === 'NEW_REFERENCE' && <p className="gp-note">本组合为当前收盘计算的位置参考；新线从所列下一交易日启用，首次展示不补造“今日新突破”。</p>}
        {selected.drawing_state === 'HISTORY' && <p className="gp-note">这是已结束结构的历史参照，不列入当前有效画法。</p>}
        {selected.intraday_tags.length > 0 && <div className="gp-events">{selected.intraday_tags.map(tag => <span key={tag} className={tag === 'UPPER_REJECTED' || tag === 'LOWER_RECOVERED' ? 'gp-event-warning' : ''}>{eventText[tag]}</span>)}</div>}
        {selected.near_tags.length > 1 && <p className="gp-note">同时靠近：{selected.near_tags.map(tag => tag === 'NEAR_H' ? '高点' : tag === 'NEAR_L' ? '低点' : '中轴').join('、')}。{selected.category === 'AMBIGUOUS' ? '距离相同，保留并列。' : '主分类采用比例距离最近的一条。'}</p>}
        <div className="gp-chart-toolbar"><ChartPeriodControl period={zoom} periods={[60, 120]} availableCount={stock.history.length} onPeriodChange={setZoom} /><small>{isProjectionIndex(selected.code) ? '指数点位' : '不复权'} · 截至 {manifest?.as_of}</small></div>
        <StructureChart stock={stock} structure={selectedStructure} count={zoom} />
        <div className="gp-levels wb-metrics"><span>{selected.direction === 'UP' ? '推算高点 H' : '原高点 H'}<strong>{priceText(selected.H)}</strong></span><span>中轴 M<strong>{priceText(selected.M)}</strong></span><span>{selected.direction === 'DOWN' ? '推算低点 L' : '原低点 L'}<strong>{priceText(selected.L)}</strong></span></div>
        <section className="wb-inspector" aria-label="画线选点核对"><InspectorTabs className="tv-inspector-tabs" label="画线依据面板" idPrefix="gp" tabs={[{ id: 'selection', label: '选点依据' }, { id: 'rules', label: '规则与数据' }]} value={tab} onChange={setTab} /><div className="tv-inspector-body" role="tabpanel" id={`gp-${tab}`} aria-labelledby={`gp-${tab}-tab`}>
          {tab === 'selection' && <><section className="gp-decision"><h3>{selectedStructure ? '为什么选这组点' : '为什么不画'}</h3>{selected.reasons.length ? <ul>{selected.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : <p>请查看本次结构判断与原始选点证据。</p>}</section>
            {selectedStructure && <StructureProof structure={selectedStructure} title={selected.basis === 'PREVIOUS_LOCK' ? '当日使用的已锁定画法' : selected.drawing_state === 'HISTORY' ? '已结束画法的选点依据' : '当前参考组合的选点依据'} />}
            {stock.candidate_next_session && stock.candidate_next_session.id !== selectedStructure?.id && <details className="gp-next"><summary>下一交易日候选 · {stock.candidate_next_session.effective_from} 起生效</summary><div className="gp-next-levels">H {stock.candidate_next_session.levels.H} · M {stock.candidate_next_session.levels.M} · L {stock.candidate_next_session.levels.L}</div><StructureProof structure={stock.candidate_next_session} title="新组合与今日画法分别保留" /></details>}
            {stock.rejected_candidates.length > 0 && <details className="gp-rejected"><summary>未选候选及原因（{stock.rejected_candidates.length}项）</summary><div className="gp-evidence-table"><table><thead><tr><th>方向</th><th>起算端</th><th>中轴候选</th><th>未采用原因</th></tr></thead><tbody>{stock.rejected_candidates.map((candidate, index) => <tr key={index}><td>{candidate.direction === 'DOWN' ? '推低' : '推高'}</td><td>{candidate.origin_date}<br />{candidate.origin_price}</td><td>{candidate.axis_date}<br />{candidate.axis_price}</td><td>{candidate.reason}</td></tr>)}</tbody></table></div></details>}
          </>}
          {tab === 'rules' && <div className="gp-rules"><h3>统一画线与分类口径</h3><ol><li>先核当前波段及层级，中轴来自有意义的支撑／阻力反应，或完整次高／次低。</li><li>高点与中轴推低点：L＝2M−H；低点与中轴推高点：H＝2M−L。起点或中轴证据不充分时不强画。</li><li>先判断收盘在高点上方或低点下方，其余按离关键位不超过 2% 分类。影线越线后收回另标记。</li><li>当日使用开盘前已生效的画法；收盘新组合下一交易日启用。今日新跨线须有同结构前收，换线日只说明位置。</li></ol>{manifest?.limitations.map((limitation, index) => <p key={index}>{limitation}</p>)}<p>{manifest?.scope} · 已发布行情日 {manifest?.as_of}</p><p>结果更新 {manifest && timeText(manifest.generated_at)} 北京时间。</p></div>}
        </div></section>
      </>}
    </WorkbenchShell>
  </div>;
}
