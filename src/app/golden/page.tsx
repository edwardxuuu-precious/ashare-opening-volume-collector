'use client';
import {useEffect,useMemo,useState,useReducer,useRef} from 'react';
import {PencilLine,Magnet,Undo2,Redo2,Eye,EyeOff,SlidersHorizontal,MessageSquare,X,LayoutGrid,RefreshCw} from 'lucide-react';
import {SiteHeader} from '@/components/site-header';
import {useAccount} from '@/components/auth-gate';
import {WorkbenchPageBar,WorkbenchShell,WorkbenchListHeader,WorkbenchListSkeleton,WorkbenchState,StockDetailHeader} from '@/components/workbench';
import {candleReturn} from '@/lib/candle-quote';
import {GoldenUpdateStrip} from '@/components/golden-update-status';
import {parseGoldenStatus,chinaTime,type GoldenUpdateStatus} from '@/lib/golden-update-status';
import {GoldenCanvas} from '@/components/golden-canvas';
import {ChartPeriodControl} from '@/components/chart-period-control';
import {RetryMessage,StockListNote} from '@/components/read-state';
import {useStockListNavigation} from '@/components/use-stock-list-navigation';
import {stockListMatches} from '@/lib/stock-list';
import {GoldenReviewTools} from '@/components/golden-review-tools';
import {blankDraft,draftHistory,type ManualDraft,type SnapMode} from '@/lib/golden-drawing';
import {readChip} from '@/lib/chip-read';
import {createPublicationNavigation,followsPublication,startPublicationFollow} from '@/lib/publication-follow';
import {autoLines,manualLines,parseWatchManifest,parseExample,type WatchManifest,type Example,type ReviewRound} from '@/lib/golden-workbench';
import '../chips/chips.css';
import '../chips/workbench.css';
import './golden.css';
export const dynamic='force-static';
const fieldName:Record<string,string>={open:'开',high:'高',low:'低',close:'收'};

function Detail({example,user,initialRound,onViewRound,period,onPeriodChange}:{example:Example;user:string;initialRound?:ReviewRound|null;onViewRound:(r:ReviewRound)=>void;period:number;onPeriodChange:(value:number)=>void}){
 const initialGroup=Math.max(0,example.result?.price_groups.findIndex(g=>g.records.some(r=>r.id===initialRound?.axis_id))??0);
 const initialEvent=Math.max(0,example.result?.price_groups[initialGroup]?.records.findIndex(r=>r.id===initialRound?.axis_id)??0);
 const [mode,setMode]=useState<'HL'|'HM'>(initialRound?.auto_mode??'HM'),[group,setGroup]=useState(initialGroup),[event,setEvent]=useState(initialEvent),[focus,setFocus]=useState('pivots'),[showAuto,setShowAuto]=useState(true);
 const key=`golden-draft-log-price-v1:${user}:${example.symbol}`;
 const [legacyDraftPresent]=useState(()=>{try{const value=JSON.parse(localStorage.getItem(`golden-draft-v1:${user}:${example.symbol}`)||'null');return !!value&&Array.isArray(value.points)&&value.points.length>0;}catch{return false;}});
 const [edits,dispatch]=useReducer(draftHistory,null,()=>{let draft=blankDraft();try{const value=JSON.parse(localStorage.getItem(key)||'null');if(value?.price_geometry==='log-price.v1'&&Array.isArray(value.points))draft=value;else {const old=JSON.parse(localStorage.getItem(`golden-draft-v1:${user}:${example.symbol}`)||'null');if(old&&Array.isArray(old.points))draft={...blankDraft(),title:old.title,mode:old.mode,points:old.points,note:old.note};}}catch{}const old=initialRound?.drawing_snapshot;if(old)draft={...blankDraft(),id:old.price_geometry==='log-price.v1'&&old.owner===user?old.id:'',title:old.title,mode:old.mode,points:old.points,note:old.note};return {present:draft,past:[],future:[]};});
 const draft=edits.present,setDraft=(value:ManualDraft)=>dispatch({type:'edit',value}),resetDraft=(value:ManualDraft)=>dispatch({type:'reset',value});
 const [panelOpen,setPanelOpen]=useState(false),[evidenceOpen,setEvidenceOpen]=useState(false);
 const panelToggle=useRef<HTMLButtonElement>(null),panel=useRef<HTMLElement>(null);
 const closePanel=()=>{setPanelOpen(false);panelToggle.current?.focus();};
 useEffect(()=>{if(!panelOpen||!window.matchMedia('(max-width:1180px)').matches)return;panel.current?.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')?.focus();const escape=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();setPanelOpen(false);panelToggle.current?.focus();}};document.addEventListener('keydown',escape);return()=>document.removeEventListener('keydown',escape);},[panelOpen]);
 const [drawing,setDrawing]=useState(false),[field,setField]=useState<SnapMode>('auto'),[showManual,setShowManual]=useState(true);
 useEffect(()=>{if(user)try{localStorage.setItem(key,JSON.stringify(draft));}catch{}},[key,draft,user]);
 useEffect(()=>{if(initialRound)document.querySelector('.gw-manual-toolbar')?.scrollIntoView({block:'start',behavior:'smooth'});},[initialRound]);
 useEffect(()=>{const keyboard=(e:KeyboardEvent)=>{if(!(e.ctrlKey||e.metaKey)||(e.target as HTMLElement)?.closest('input,select,textarea,[contenteditable="true"]'))return;if(e.key.toLowerCase()==='z'){e.preventDefault();dispatch({type:e.shiftKey?'redo':'undo'});}else if(e.key.toLowerCase()==='y'){e.preventDefault();dispatch({type:'redo'});}};document.addEventListener('keydown',keyboard);return()=>document.removeEventListener('keydown',keyboard);},[]);
 const r=example.result,g=r?.price_groups[group],axis=g?.records[event]??null;
 const lines=[...(showAuto?autoLines(r,mode,axis,example.price_geometry):[]),...(showManual?manualLines(draft.mode,draft.points):[])];
 const evidence=axis?[...axis.source_fields.map(p=>({type:'价格来源',date:p.date,detail:`${fieldName[p.field]} ${p.value}`})),...axis.supports.map(p=>({type:'此前支撑',date:p.date,detail:`Low≤M≤High，收盘 ${p.close} ≥ M`})),{type:'收盘跌破',date:axis.break_event.date,detail:`前收≥M，今收 ${axis.break_event.close} < M`},...axis.later_touch_below_events.map(p=>({type:'后来触价在线下',date:p.date,detail:`收盘 ${p.close} < M${axis.reclaim_observed_on&&p.date>=axis.reclaim_observed_on?'（首次收回之后）':''}`}))]:[];
 const sourceName=({PUBLISHED_HISTORY_PLUS_TENCENT_CLOSE:'收盘行情',EASTMONEY:'东方财富','BAOSTOCK_0.9.3':'BaoStock',BAOSTOCK_INDEX:'BaoStock',THS_UNADJUSTED:'同花顺'} as Record<string,string>)[example.source?.provider??'']??'已存行情';
 return <div className="gw-detail-layout" data-panel-open={panelOpen}>
  <section className="gw-main-chart" aria-label="画线工作区">
   <div className="gw-detail-top"><StockDetailHeader name={example.name} code={example.symbol==='sh000001'?'SH000001':example.symbol} date={example.history.at(-1)?.date??example.as_of} close={example.history.at(-1)?Number(example.history.at(-1)!.close):null} percent={candleReturn(example.history,example.history.at(-1)?.date??example.as_of)} basis={example.kind==='INDEX'?'指数点位':'不复权'} badge={<span className="cw-screen-badge">{sourceName}</span>}/><button ref={panelToggle} className="gw-panel-toggle gw-button" aria-expanded={panelOpen} onClick={()=>panelOpen?closePanel():setPanelOpen(true)}><MessageSquare size={15}/>批注</button></div>
   {example.freshness!=='CURRENT'&&<p className="gw-warning">行情待补齐 · 统计日{example.as_of}，最近K线{example.last_bar_date??'未知'}</p>}
   <div className="gw-anchor-strip"><span>高点 H <b>{r?.H?.price??'待核'}</b><small>{r?.H?.date.slice(5)}</small></span><span>低点 L <b>{r?.L?.price??'待核'}</b><small>{r?.L?.date.slice(5)}</small></span><span>M候选 <b>{r?.price_groups.length??0}</b><small>个价位 · 未选唯一轴</small></span></div>
   <div className="gw-chart-toolbar"><label>自动<select aria-label="自动格子显示" value={mode} onChange={e=>setMode(e.target.value as 'HL'|'HM')}><option value="HM">H/M 候选</option><option value="HL">{example.price_geometry==='log-price.v1'?'H/L 等分':'H/L 锚点'}</option></select></label>{mode==='HM'&&<label>M<select aria-label="自动中轴候选价位" disabled={!g} value={group} onChange={e=>{setGroup(Number(e.target.value));setEvent(0);}}>{r?.price_groups.length?r.price_groups.map((v,i)=><option key={i} value={i}>{v.price} · {i+1}/{r.price_groups.length}</option>):<option>待核</option>}</select></label>}<button className="gw-icon-button" aria-label={showAuto?'隐藏自动线':'显示自动线'} aria-pressed={showAuto} title="自动线" onClick={()=>setShowAuto(!showAuto)}>{showAuto?<Eye size={15}/>:<EyeOff size={15}/>}</button><div className="gw-chart-spacer"/><ChartPeriodControl period={period} periods={[60,120]} onPeriodChange={onPeriodChange} availableCount={Math.min(period,example.history.length)}/><button className="gw-button gw-evidence-trigger" aria-expanded={evidenceOpen} onClick={()=>setEvidenceOpen(!evidenceOpen)}><SlidersHorizontal size={14}/>选点依据</button></div>
   <div className="gw-manual-toolbar" role="toolbar" aria-label="人工画线工具"><strong>人工画线</strong><select aria-label="人工画线类型" value={draft.mode} onChange={e=>setDraft({...draft,mode:e.target.value as 'HL'|'HM'})}><option value="HL">H/L 等分</option><option value="HM">H/M 等高</option></select><button className="gw-primary" onClick={()=>{setDraft({...blankDraft(),mode:draft.mode});setShowManual(true);setDrawing(true);setPanelOpen(false);}}><PencilLine size={14}/>{drawing?'重新画线':'新增画线'}</button>{drawing&&<button className="gw-icon-button" aria-label="取消画线" title="取消 · Esc" onClick={()=>setDrawing(false)}><X size={15}/></button>}<span className="gw-toolbar-divider"/><button className="gw-icon-button gw-snap-toggle" aria-label="自动吸附" aria-pressed={field==='auto'} title="自动吸附开高低收 · Shift临时自由取价" onClick={()=>setField(field==='auto'?'custom':'auto')}><Magnet size={16}/><span>吸附</span></button><button className="gw-icon-button" aria-label="撤销人工画线调整" title="撤销 · Ctrl+Z" disabled={!edits.past.length} onClick={()=>dispatch({type:'undo'})}><Undo2 size={16}/></button><button className="gw-icon-button" aria-label="重做人工画线调整" title="重做 · Ctrl+Shift+Z" disabled={!edits.future.length} onClick={()=>dispatch({type:'redo'})}><Redo2 size={16}/></button><button className="gw-icon-button" aria-label={showManual?'隐藏人工线':'显示人工线'} aria-pressed={showManual} title="人工线" onClick={()=>setShowManual(!showManual)}>{showManual?<Eye size={15}/>:<EyeOff size={15}/>}</button></div>
   {(legacyDraftPresent||initialRound?.drawing_snapshot&&!initialRound.drawing_snapshot.price_geometry)&&<p className="gw-warning">历史算术画线的原始锚点已保留；当前对数画线为未核对的新草稿。</p>}<div className="gw-chart-stage"><GoldenCanvas fit history={example.history} displayPeriod={period} H={r?.H} L={r?.L} axis={mode==='HM'?axis:null} focus={focus} lines={lines} windowStart={example.window_start} drawing={drawing} field={field} manualMode={draft.mode} points={showManual?draft.points:[]} onPointsChange={points=>{setDraft({...draft,points});if(points.length===2)setDrawing(false);}} onCancel={()=>setDrawing(false)}/></div>
   <div className="gw-chart-bottom"><span><i className="gw-auto-key"/>{example.price_geometry==='log-price.v1'?'自动候选':'历史算术参考'}</span><span><i className="gw-manual-key"/>人工画线</span><span>60根计算 · 120根仅作背景</span>{example.error&&<span className="gw-inline-warning">行情/计算待核：{example.error}</span>}</div>
   <section className="gw-evidence" hidden={!evidenceOpen} aria-label="自动选点依据"><div className="gw-evidence-heading"><h3>自动选点依据</h3><button className="gw-icon-button" aria-label="关闭选点依据" onClick={()=>setEvidenceOpen(false)}><X size={16}/></button></div><div className="gw-focus"><span>图上标记</span>{[['pivots','H/L'],['all','全部'],['h','高点'],['l','低点'],['m','中轴']].map(([k,v])=><button key={k} aria-pressed={focus===k} onClick={()=>setFocus(k)}>{v}</button>)}</div><div className="gw-point-proofs">{([['高点H',r?.H],['低点L',r?.L]] as const).map(([title,point])=><div key={title}><b>{title} {point?.price??'待核'}</b><p>{point?`${point.date} · ${point.field}；左侧 ${point.left_dates?.join('、')}；右侧 ${point.right_dates?.join('、')}；${point.available_after_close_on}收盘后确认`:'同价歧义、后续越过或末端未确认，保留待核；不以人工点补齐。'}</p></div>)}</div>{mode==='HM'&&<><label className="gw-field">候选形成记录<select aria-label="中轴形成记录" disabled={!axis} value={event} onChange={e=>setEvent(Number(e.target.value))}>{g?.records.map((v,i)=><option key={v.id} value={i}>{v.break_event.date} · {i+1}/{g.records.length}</option>)}</select></label>{axis&&<p className="gw-muted">M {axis.price}是浏览候选，并非唯一轴。回抽资格：{axis.available_after_close_on??'尚未满足'}；首次收回：{axis.reclaim_observed_on??'未观察到'}。{example.price_geometry==='log-price.v1'?'M²÷H为条件投影':'历史算术参考为条件投影'}，不是已观察低点。图上源/支/抽各最多4个日期，完整记录如下。</p>}</>}{evidence.length>0&&<table><thead><tr><th>证据</th><th>K线日期</th><th>价格字段与关系</th></tr></thead><tbody>{evidence.map((v,i)=><tr key={i}><td>{v.type}</td><td>{v.date}</td><td>{v.detail}</td></tr>)}</tbody></table>}<p className="gw-muted">其他高点版本：{r?.other_H_formation_records??0}条形成记录。历史known_at未核定；确认日按保存的K线顺序推导。</p></section>
  </section>
  <aside ref={panel} className="gw-inspector" aria-label="人工校准面板"><button className="gw-panel-close gw-icon-button" aria-label="关闭批注面板" onClick={closePanel}><X size={16}/></button><GoldenReviewTools example={example} user={user} draft={draft} setDraft={setDraft} resetDraft={resetDraft} setDrawing={setDrawing} setShowManual={setShowManual} autoMode={mode} axisId={mode==='HM'?axis?.id??'':''} initialRound={initialRound} onViewRound={onViewRound}/></aside>
 </div>;
}

export default function GoldenPage(){
 const user=useAccount()?.user??'';
 const [viewRound,setViewRound]=useState<ReviewRound|null>(null),[roundSequence,setRoundSequence]=useState(0),[chartPeriod,setChartPeriod]=useState(60),[detailReload,setDetailReload]=useState(0);
 const listArea=useRef<HTMLElement>(null),navigationRetry=useRef<null|(()=>Promise<void>)>(null);
 const navigation=useRef(createPublicationNavigation()),navigationRead=useRef<AbortController|null>(null);
 const [status,setStatus]=useState<GoldenUpdateStatus|null>(null),[statusError,setStatusError]=useState('');
 const [manifest,setManifest]=useState<WatchManifest|null>(null),[latest,setLatest]=useState<WatchManifest|null>(null),[code,setCode]=useState('002693'),[example,setExample]=useState<Example|null>(null),[error,setError]=useState(''),[updateError,setUpdateError]=useState(''),[detailError,setDetailError]=useState(''),[search,setSearch]=useState('');
 const publication=useRef<{refresh:()=>void;stop:()=>void}|null>(null),viewed=useRef<WatchManifest|null>(null),selected=useRef(code);
 selected.current=code;
 useEffect(()=>{
  const follow=startPublicationFollow({
   readStatus:signal=>readChip('/data/golden-workbench/status.json',signal,parseGoldenStatus),
   readManifest:signal=>readChip('/data/golden-workbench/manifest.json',signal,parseWatchManifest),
   readPayload:async(m,signal)=>{const symbol=selected.current;return navigation.current.pending||navigation.current.pinned||symbol==='overview'?null:readChip(`/data/golden-workbench/${m.dataset_id}/${symbol}.json`,signal,v=>parseExample(v,m,symbol));},
   onStatus:setStatus,
   onPublication:(m,value,previous)=>{
    if(navigation.current.pending)return false;
    const follows=followsPublication(viewed.current,previous,navigation.current.pinned);
    if(follows&&selected.current!=='overview'&&value?.symbol!==selected.current)return false;
    setLatest(m);
    if(follows){viewed.current=m;setManifest(m);if(value)setExample(value);setDetailError('');}
   },
   onStatusError:e=>setStatusError(e?'更新状态暂时无法读取，保留已显示结果。':''),
   onPublicationError:e=>setUpdateError(e?.message??''),isRunning:v=>v.state==='RUNNING',
   publishedDataset:v=>['COMPLETE','PARTIAL'].includes(v.state)&&v.dataset_id?v.dataset_id:null,
   activeStatusMs:30000,
  });
  publication.current=follow;return()=>{follow.stop();publication.current=null;};
 },[]);

 useEffect(()=>{if(!manifest||code==='overview'){setExample(null);return;}if(example?.dataset_id===manifest.dataset_id&&example.symbol===code)return;const c=new AbortController();setExample(null);setDetailError('');readChip(`/data/golden-workbench/${manifest.dataset_id}/${code}.json`,c.signal,v=>parseExample(v,manifest,code)).then(v=>{if(!c.signal.aborted)setExample(v);}).catch(e=>{if(!c.signal.aborted)setDetailError(e.message);});return()=>c.abort();},[manifest,code,detailReload]);
 useEffect(()=>()=>navigationRead.current?.abort(),[]);
 const chooseSample=(symbol:string)=>{navigationRetry.current=null;setError('');const pending=navigation.current.pending;navigation.current.cancel();navigationRead.current?.abort();selected.current=symbol;setViewRound(null);setCode(symbol);if(pending)publication.current?.refresh();};
 const refreshData=()=>{setUpdateError('');publication.current?.refresh();if(detailError)setDetailReload(value=>value+1);};
 const rows=useMemo(()=>(manifest?.rows??[]).filter(row=>stockListMatches(search,row.name,row.symbol)),[manifest,search]);
 const navigationKeys=useMemo(()=>rows.map(row=>row.symbol),[rows]);
 useStockListNavigation({keys:navigationKeys,selected:code,listRef:listArea,onSelect:chooseSample});
 const availableExample=example?.dataset_id===manifest?.dataset_id&&example?.symbol===code?example:null;
 async function changeDay(dataset:string){
  if(!latest)return;navigationRetry.current=()=>changeDay(dataset);navigationRead.current?.abort();const c=new AbortController();navigationRead.current=c;const ticket=navigation.current.begin();setError('');
  try{const batch=dataset===latest.dataset_id?latest:await readChip(`/data/golden-workbench/${dataset}/manifest.json`,c.signal,parseWatchManifest);if(batch.dataset_id!==dataset)throw Error('核对日期与所选批次不一致。');if(!navigation.current.commit(ticket,dataset!==latest.dataset_id))return;setViewRound(null);viewed.current=batch;setManifest(batch);navigationRetry.current=null;}
  catch(e){if(navigation.current.fail(ticket))setError((e as Error).message);}
  finally{if(navigation.current.isCurrent(ticket)){navigationRead.current=null;publication.current?.refresh();}}
 }
 async function openRound(round:ReviewRound){
  if(!round.drawing_snapshot||!latest)return;navigationRetry.current=()=>openRound(round);navigationRead.current?.abort();const c=new AbortController();navigationRead.current=c;const ticket=navigation.current.begin();setError('');
  try{const batch=round.dataset_id===latest.dataset_id?latest:await readChip(`/data/golden-workbench/${round.dataset_id}/manifest.json`,c.signal,parseWatchManifest);if(batch.dataset_id!==round.dataset_id)throw Error('核对轮次与所选批次不一致。');if(!navigation.current.commit(ticket,true))return;setRoundSequence(n=>n+1);setViewRound(round);viewed.current=batch;setManifest(batch);navigationRetry.current=null;const symbol=round.auto_context?.symbol??code;selected.current=symbol;setCode(symbol);}
  catch(e){if(navigation.current.fail(ticket))setError((e as Error).message);}
  finally{if(navigation.current.isCurrent(ticket)){navigationRead.current=null;publication.current?.refresh();}}
 }
 return <div className="chip-site chip-workbench gw-site wb-site"><SiteHeader active="golden"><button className="stock-header-refresh" onClick={refreshData} aria-label="刷新黄金画格"><RefreshCw size={14}/><span>刷新</span></button></SiteHeader>
 <WorkbenchPageBar title="黄金画格核对" dateLabel="核对日" date={manifest?.as_of} explanation={{label:'核对说明',content:<><p>20个固定样本，包含19只股票及上证指数。</p><p>自动选点使用最后60根实际K线；120根仅作背景。人工画线和核对意见独立保存。</p><p>股票不复权，指数使用点位。完整计算规则在工作区下方展开查看。</p></>}}/>
 <WorkbenchShell contentClassName="gw-workspace" sidebar={<aside className="gw-sidebar wb-sidebar"><WorkbenchListHeader title="核对样本" count={manifest?rows.length:null} ready={!!manifest} search={{label:'查找核对样本',value:search,onChange:setSearch}} filters={[]}/><button className="gw-overview-link" aria-pressed={code==='overview'} onClick={()=>chooseSample('overview')}><LayoutGrid size={14}/>20图总览 <span>20</span></button><nav ref={listArea} className="wb-stock-list" aria-label="固定20个核对样本">{!manifest&&!updateError&&<WorkbenchListSkeleton/>}{rows.map(r=><button key={r.symbol} className="gw-stock wb-stock-row" data-stock-key={r.symbol} aria-pressed={code===r.symbol} onClick={()=>chooseSample(r.symbol)}><span><strong>{r.name}</strong><b className="wb-row-price">{r.history.at(-1)?Number(r.history.at(-1)!.close).toFixed(2):'—'}</b></span><span><small>{r.symbol==='sh000001'?'SH000001':r.symbol}</small><em className="wb-row-status">{r.H&&r.L?'H/L齐备':r.H?'L待核':'H待核'}</em></span></button>)}<StockListNote empty={!!manifest&&!rows.length}/></nav><p className="gw-sidebar-note">↑ ↓ 切换样本</p></aside>}>
 <GoldenUpdateStrip status={status} latest={latest} viewDate={manifest?.as_of} viewDatasetId={manifest?.dataset_id} viewGeneratedAt={manifest?.generated_at} error={statusError} onRefresh={refreshData}/><div className="gw-page-bar"><div className="gw-mobile-samples"><select aria-label="选择核对样本" value={code} onChange={e=>chooseSample(e.target.value)}><option value="overview">20图总览</option>{manifest?.rows.map(r=><option key={r.symbol} value={r.symbol}>{r.name} {r.symbol}</option>)}</select></div><span className="gw-page-context">{code==='overview'?'样本总览':'人工画线与校准'}</span><label>核对日期<select aria-label="切换每日画格记录" value={manifest?.dataset_id??''} onChange={e=>changeDay(e.target.value)}>{latest?.archives.slice().reverse().map(a=><option key={a.dataset_id} value={a.dataset_id}>{a.as_of}{a.generated_at?` · ${chinaTime(a.generated_at).slice(11,16)}`:''}{a.dataset_id===latest.dataset_id?' · 最新':''}</option>)}</select></label></div>
 {(error||updateError)&&<RetryMessage message={[error,updateError].filter(Boolean).join('；')} onRetry={()=>{if(error&&navigationRetry.current)void navigationRetry.current();else refreshData();}}/>}
 {!manifest&&!error&&!updateError&&<WorkbenchState state={{kind:'loading',label:'正在读取每日样本…'}}/>}
 {manifest&&code!=='overview'&&<StockListNote outside={!rows.some(row=>row.symbol===code)}/>}
 {code==='overview'&&manifest&&<><StockListNote empty={!rows.length}/><div className="gw-overview-heading"><h2>20个固定样本</h2><p>{manifest.as_of} · {manifest.rows.filter(r=>r.H).length}个H候选 / {manifest.rows.filter(r=>r.H&&r.L).length}组H/L齐备。</p><p>{manifest.price_geometry==='log-price.v1'?'选择股票，打开画线与批注工作区。':'历史算术批次：预览仅显示保存的 H/L 锚点。'}</p></div><div className="gw-overview-grid">{rows.map(r=><button className="gw-preview" key={r.symbol} onClick={()=>chooseSample(r.symbol)}><div><h3>{r.name}<small>{r.symbol==='sh000001'?'SH000001 · 指数':r.symbol}</small></h3><span>{r.H&&r.L?'H/L齐备':r.H?'L待核':'H待核'} ↗</span></div><GoldenCanvas mini history={r.history} lines={autoLines({H:r.H,L:r.L},'HL',null,manifest.price_geometry)}/><footer><span>H {r.H?.price??'待核'} / L {r.L?.price??'待核'}</span><span>M {r.M_count}候选</span></footer>{r.freshness!=='CURRENT'&&<p className="gw-warning">行情待核 / 最近 {r.last_bar_date??'未知'}</p>}</button>)}</div></>}
 {manifest&&code!=='overview'&&(availableExample?<Detail key={availableExample.dataset_id+availableExample.symbol+user+(viewRound?.dataset_id===availableExample.dataset_id&&(viewRound.auto_context?.symbol??availableExample.symbol)===availableExample.symbol?String(roundSequence):'')} example={availableExample} user={user} initialRound={viewRound?.dataset_id===availableExample.dataset_id&&(viewRound.auto_context?.symbol??availableExample.symbol)===availableExample.symbol?viewRound:null} onViewRound={openRound} period={chartPeriod} onPeriodChange={setChartPeriod}/>:<WorkbenchState state={detailError?{kind:'error',message:detailError,onRetry:()=>setDetailReload(value=>value+1)}:{kind:'loading',label:'正在读取个股行情…'}}/>)}
 <details className="gw-rules"><summary>统一规则与范围</summary><p>20个固定样本，其中19只股票及上证指数；收盘后独立采集这20个样本，不等待全市场任务；每个交易日15:45首次更新，16:15、17:15、20:15自动补跑，节假日不补造新K线。资料最新至{latest?.as_of??'—'}。股票不复权；上证指数是指数点位，不套用股票筹码模型。</p><p>每个样本用截至统计日最后60根实际K线；高低转折按左2右2确认。H取窗口内最高已确认转折，L取H之后最低已确认转折；同价歧义、后续越过及末端未确认保留待核。120根仅作背景，不改变计算。</p><p>M从OHLC价格生成：此前连续收在线上区间内至少2根触价支撑，前收≥M且本日收盘&lt;M，再观察独立回抽触价且收盘&lt;M。全部价位、形成及收回记录保留，不用未来低点拟合，不强选唯一轴。资格日与收回日单独列出。</p><p>{manifest?.price_geometry==='log-price.v1'?'H/L画50%、25%、12.5%逐层对数平分；H/M画对数等距条件下沿 M²÷H。':'历史批次展示保存的算术参考和原始锚点。'}人工画线独立保存，不覆盖自动规则。核对意见不自动修改算法，不生成交易信号。</p><code>{manifest?.policy_sha256}</code></details>
 </WorkbenchShell></div>;
}
