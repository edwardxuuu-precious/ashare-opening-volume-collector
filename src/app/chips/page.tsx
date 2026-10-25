'use client';
import { useCallback,useEffect,useMemo,useRef,useState,useSyncExternalStore } from 'react';
import { BrandIdentity } from '@/components/brand';
import { AccountControls } from '@/components/auth-gate';
import { StockNavigation } from '@/components/stock-navigation';
import { ChipStockHistory } from '@/components/chip-stock-history';
import { readChip } from '@/lib/chip-read';
import { chipNumber } from '@/lib/chips';
import { chipReason } from '@/lib/chip-display';
import { chipNavigationKey } from '@/lib/chip-keyboard';
import {createChipStockPreload,neighboringChipStocks,chipStockPreloadLabels,chipStockPreloadHints} from '@/lib/chip-stock-preload';
import {createChipStockIntent} from '@/lib/chip-stock-intent';
import {prepareChipLocalHistory} from '@/lib/chip-local-preparation';
import {initialWorkbenchStock} from '@/lib/chip-workbench';
import {chipScreenState,chipScreenCounts} from '@/lib/chip-price-position';
import { parseMarketManifest,parseMarketScreen,parseMarketStock,currentMarketReasons,type MarketManifest,type MarketScreenRow,type MarketStock,type MarketState } from '@/lib/chip-market';
import './chips.css';
import '@/components/stock-navigation.css';
import './workbench.css';
export const dynamic='force-static';
const labels={PASS:'三项条件入选',FAIL:'筛选条件未满足',UNKNOWN:'筛选条件待核实'};
const noSubscribe=()=>()=>{},zeroVersion=()=>0;
type Progress={state:string;as_of:string;universe_total:number;scanned_count:number;model_ready_count:number;updated_at:string};
function progress(v:unknown):Progress{const x=v as Progress;if(!x||!Number.isSafeInteger(x.scanned_count)||!Number.isSafeInteger(x.universe_total)||x.scanned_count<0||x.scanned_count>x.universe_total||!Number.isSafeInteger(x.model_ready_count)||x.model_ready_count<0||x.model_ready_count>x.universe_total||typeof x.state!=='string')throw Error('每日计算状态无效。');return x;}
export default function ChipsPage(){
  const [manifest,setManifest]=useState<MarketManifest|null>(null),[rows,setRows]=useState<MarketScreenRow[]|null>(null),[run,setRun]=useState<Progress|null>(null);
  const [code,setCode]=useState(''),[stock,setStock]=useState<MarketStock|null>(null),[error,setError]=useState(''),[detailError,setDetailError]=useState('');
  const [filter,setFilter]=useState<MarketState|'ALL'>('UNKNOWN'),[search,setSearch]=useState(''),[page,setPage]=useState(0),[retry,setRetry]=useState(0),[loaded,setLoaded]=useState(false);
  const [loadingStage,setLoadingStage]=useState<'manifest'|'screening'>('manifest');
  const [historyDate,setHistoryDate]=useState<string|null>(null),[chartPeriod,setChartPeriod]=useState(120);
  const listArea=useRef<HTMLDivElement>(null);
  const preload=useMemo(()=>manifest?createChipStockPreload((value,signal)=>readChip(`/data/chips/daily/${manifest.active_slot}/${value}.json`,signal,v=>parseMarketStock(v,value,manifest)),prepareChipLocalHistory,5,value=>Object.keys(value.frames).length>0):null,[manifest]);
  const intent=useMemo(()=>preload?createChipStockIntent(preload.prefetch,value=>preload.peekPreview(value)!==undefined):null,[preload]);
  useSyncExternalStore(preload?.subscribe??noSubscribe,preload?.version??zeroVersion,zeroVersion);
  useEffect(()=>()=>intent?.clear(),[intent]);
  useEffect(()=>()=>preload?.clear(),[preload]);
  useEffect(()=>{const c=new AbortController();setLoaded(false);setLoadingStage('manifest');setManifest(null);setRows(null);setRun(null);setStock(null);setHistoryDate(null);setError('');
    void readChip('/data/chips/daily/status.json',c.signal,progress).then(v=>{if(!c.signal.aborted)setRun(v);}).catch(()=>{});
    void readChip('/data/chips/daily/manifest.json',c.signal,parseMarketManifest).then(async m=>{if(c.signal.aborted)return;setLoadingStage('screening');const r=await readChip(`/data/chips/daily/${m.active_slot}/screening.json`,c.signal,v=>parseMarketScreen(v,m));if(!c.signal.aborted){setManifest(m);setRows(r);setCode(previous=>initialWorkbenchStock(r,previous));}}).catch(e=>{if(!c.signal.aborted)setError(e.message);}).finally(()=>{if(!c.signal.aborted)setLoaded(true);});
    return()=>c.abort();
  },[retry]);
  useEffect(()=>{
    if(!preload)return;
    if(!code){preload.clear();setStock(null);return;}
    preload.protect(code);setDetailError('');
    const saved=preload.peekStock(code);if(saved){setStock(saved);return;}
    let active=true;setStock(null);const lease=preload.acquireStock(code);
    lease.promise.then(value=>{if(active)setStock(value);}).catch(error=>{if(active)setDetailError(error.message);});
    return()=>{active=false;lease.release();};
  },[code,preload]);
  useEffect(()=>{
    const list=listArea.current,row=list?.querySelector<HTMLElement>(`[data-stock-code="${code}"]`);
    if(!list||!row)return;
    const a=list.getBoundingClientRect(),b=row.getBoundingClientRect();
    if(b.top<a.top)list.scrollTop+=b.top-a.top;else if(b.bottom>a.bottom)list.scrollTop+=b.bottom-a.bottom;
  },[code,page]);
  const screenCounts=useMemo(()=>chipScreenCounts(rows??[]),[rows]);
  const matched=useMemo(()=>(rows??[]).filter(r=>(filter==='ALL'||chipScreenState(r)===filter)&&(!search||`${r.name} ${r.security_id}`.toLowerCase().includes(search.toLowerCase()))),[rows,filter,search]),shown=matched.slice(page*100,(page+1)*100);
  const stockIndex=matched.findIndex(r=>r.security_id===code);
  const neighbors=useMemo(()=>neighboringChipStocks(matched.map(r=>r.security_id),code),[matched,code]);
  const stepStock=useCallback((direction:-1|1)=>setCode(selected=>{
    const index=matched.findIndex(r=>r.security_id===selected);
    return matched[index<0?(direction===1?0:matched.length-1):index+direction]?.security_id??selected;
  }),[matched]);
  useEffect(()=>{if(stockIndex>=0)setPage(Math.floor(stockIndex/100));},[code,stockIndex,matched]);
  useEffect(()=>{
    if(!code||!matched.length)return;
    const onKeyDown=(event:KeyboardEvent)=>{
      const key=chipNavigationKey(event);
      if(key!=='ArrowUp'&&key!=='ArrowDown')return;
      event.preventDefault();stepStock(key==='ArrowUp'?-1:1);
    };
    window.addEventListener('keydown',onKeyDown);
    return()=>window.removeEventListener('keydown',onKeyDown);
  },[code,matched.length,stepStock]);
  const availableStock=preload?.peekStock(code)??stock;
  const selectedStock=availableStock?.security_id===code&&availableStock.dataset_id===manifest?.dataset_id&&availableStock.as_of===manifest.as_of?availableStock:null;
  useEffect(()=>{
    intent?.neighbors(selectedStock?neighbors:[]);
    return()=>intent?.neighbors([]);
  },[intent,selectedStock,neighbors]);
  useEffect(()=>{intent?.cancelHover();},[intent,filter,search,page,rows]);
  useEffect(()=>{
    const cancel=()=>intent?.cancelHover(),visibility=()=>{if(document.hidden)cancel();};
    window.addEventListener('blur',cancel);document.addEventListener('visibilitychange',visibility);
    return()=>{window.removeEventListener('blur',cancel);document.removeEventListener('visibilitychange',visibility);};
  },[intent]);
  const selectStock=(value:string)=>{intent?.cancelHover();setCode(value);};
  const selectedRow=rows?.find(r=>r.security_id===code),detailReasons=selectedStock?currentMarketReasons(selectedStock,selectedRow):[];
  const current=selectedStock?.snapshots[manifest?.as_of??'']??null;
  const nodeModel=manifest?.model_version==='chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1';
  return <div className="chip-site chip-workbench">
    <header className="chip-site-header cw-header"><a href="/" className="brand"><BrandIdentity/></a><StockNavigation active="chips"/><div className="cw-header-actions"><button className="chip-site-link" onClick={()=>setRetry(v=>v+1)} title="重新读取已发布批次">刷新</button><AccountControls/></div></header>
    <main className="cw-shell">
      <aside className="cw-sidebar" aria-label="每日筛选结果">
        <div className="cw-list-title"><h1>股票名单</h1><span>{manifest?`${matched.length.toLocaleString()}只`:'每日筛选'}</span></div>
        <p className="cw-selection-rule" aria-label="筛选条件">近10交易日收盘涨停 · 收盘高于平均成本<br/><b>顶格未动（待人工核实）</b></p>
        <div className="cw-search"><input aria-label="查找股票" type="search" placeholder="搜索名称或代码" value={search} onChange={e=>{setSearch(e.target.value);setPage(0);}}/></div>
        <div className="cw-filters" aria-label="筛选名单范围">{(['PASS','ALL','UNKNOWN','FAIL'] as const).map(k=><button key={k} aria-pressed={filter===k} title={k==='ALL'?'查看本批全部股票':labels[k]} onClick={()=>{setFilter(k);setPage(0);}}>{k==='PASS'?'入选':k==='ALL'?'全部':k==='UNKNOWN'?'待核实':'未满足'} <b>{manifest?(k==='ALL'?manifest.universe_total:screenCounts[k]).toLocaleString():'—'}</b></button>)}</div>
        <div className="cw-stock-list" ref={listArea}>
          {shown.map(r=>{const preloadStatus=preload?.status(r.security_id);return <button key={r.security_id} className="cw-stock-row" type="button" aria-label={`${r.name} ${r.security_id}`} aria-pressed={r.security_id===code} data-stock-code={r.security_id} onClick={()=>selectStock(r.security_id)} onPointerEnter={e=>{if(e.pointerType==='mouse'&&r.security_id!==code)intent?.hover(r.security_id);}} onPointerLeave={()=>intent?.cancelHover(r.security_id)}>
            <span className="cw-stock-line"><strong>{r.name}</strong><span className="cw-stock-price">{r.close_cny===null?'待核实':chipNumber(r.close_cny).toFixed(2)}</span></span>
            <span className="cw-stock-line"><span className="cw-code">{r.security_id}</span>{preloadStatus?<span className="chip-stock-cached" data-preload-status={preloadStatus} title={chipStockPreloadHints[preloadStatus]} aria-live="polite">{chipStockPreloadLabels[preloadStatus]}</span>:<span className={`cw-row-state cw-row-${chipScreenState(r).toLowerCase()}`}>{chipScreenState(r)==='PASS'?'三项条件入选':chipScreenState(r)==='UNKNOWN'?'条件待核实':'条件未满足'}</span>}</span>
            {r.chip_status==='MISSING'&&<span className="cw-row-warning">筹码待补齐</span>}{r.last_trade_close_date&&<span className="cw-row-warning">收盘沿用 {r.last_trade_close_date}</span>}
          </button>;})}
          {!shown.length&&<p className="cw-list-empty">{error||(!loaded?'正在读取每日名单…':'当前范围没有结果。')}</p>}
        </div>
        <div className="cw-list-footer"><div className="cw-pagination"><span>{page+1} / {Math.max(1,Math.ceil(matched.length/100))}页</span><button aria-label="上一页" title="上一页" disabled={page===0} onClick={()=>setPage(p=>p-1)}>‹</button><button aria-label="下一页" title="下一页" disabled={(page+1)*100>=matched.length} onClick={()=>setPage(p=>p+1)}>›</button></div><p>↑ ↓ 换股 <span>← → 换交易日</span></p><small title="停留600毫秒后准备；快速划过取消，连续悬停触发至少间隔2秒。">悬停预加载 · 最多缓存5只</small></div>
      </aside>
      <section className="cw-workspace chip-detail" aria-label="个股K线与筹码详情">
        {!manifest&&<div className="cw-empty" role="status"><h2>每日筹码筛选</h2><p role={error?'alert':undefined}>{error||(!loaded?(loadingStage==='manifest'?'正在读取已发布筹码目录…':'正在读取本批筛选名单…'):'首批每日计算尚未完成。')}</p>{run&&<p>{run.as_of} · 已扫描 {run.scanned_count.toLocaleString()} / {run.universe_total.toLocaleString()}只 · 筹码可用 {run.model_ready_count.toLocaleString()} / {run.universe_total.toLocaleString()}只</p>}<button className="chip-site-link" disabled={!loaded} onClick={()=>setRetry(v=>v+1)}>重新读取</button></div>}
        {manifest&&!code&&<div className="cw-empty"><h2>选择一只股票开始研究</h2><p>左侧名单支持搜索、悬停预加载与方向键切换。</p></div>}
        {manifest&&code&&!selectedStock&&<div className="cw-detail-loading"><h2>{selectedRow?.name} <small>{code}</small></h2>{detailError?<p role="alert" className="chip-site-error">{detailError}</p>:<p role="status">正在读取日K线、成交量和筹码分布…</p>}</div>}
        {selectedStock&&preload&&<>
          <ChipStockHistory key={`${code}-${manifest!.dataset_id}`} stock={selectedStock} previousDate={manifest!.previous_date} initialDate={historyDate} onDateChange={setHistoryDate} preload={preload} workbench screeningRow={selectedRow} periodValue={chartPeriod} onPeriodChange={setChartPeriod}/>
          {!current&&<p className="cw-notice">该股筹码暂不可用：{detailReasons.map(chipReason).join('；')}</p>}
          {detailReasons.length>0&&<details className="cw-data-notes"><summary>个股数据说明（{detailReasons.length}）</summary><p>{detailReasons.map(chipReason).join('；')}</p><small>{detailReasons.join('；')}</small></details>}
          <div className="cw-detail-footer"><span className="chip-stock-position">{stockIndex>=0?`当前名单第${stockIndex+1}/${matched.length}只${stockIndex===0?' · 首只':stockIndex===matched.length-1?' · 末只':''}`:'当前股票不在所选名单中'}</span><button className="chip-site-link" onClick={()=>{intent?.clear();setCode('');setHistoryDate(null);}}>清除选择</button></div>
          <details className="cw-neighbor-notes"><summary>相邻股票预加载</summary><p className="chip-stock-preload">{neighbors.length?neighbors.toReversed().map(value=>`${matched.find(r=>r.security_id===value)?.name??value} ${value}（${preload.status(value)?chipStockPreloadLabels[preload.status(value)!]:'等待预加载'}）`).join(' · '):'当前名单没有相邻股票。'}</p><p>后台最多2只、串行准备；悬停目标优先，快速划过不加载。</p></details>
        </>}
        {manifest&&<p className="cw-coverage-warning" role="status">顶格未动待人工核实，满足涨停和平均成本条件的股票列在“待核实”。</p>}
        {manifest&&(!manifest.market_complete||manifest.universe_coverage==='SITE_CATALOG_UNVERIFIED')&&<p className="cw-coverage-warning" role="status">{!manifest.market_complete?'仍有价格条件待核实或筹码缺失，结果并非全部已核实。':'当前股票清单覆盖范围尚待核对。'}详情见计算与数据说明。</p>}
      <details className="chip-site-method cw-method"><summary>计算与数据说明</summary><p>筛选仅保留三项：最近10个市场交易日至少一次收盘涨停；收盘价高于当日平均成本；顶格未动。不设10元以上或以下等绝对价格门槛。平均成本使用完整精度比较，严格大于，等于不算；高于主峰不能替代高于平均成本。顶格未动尚无已确认的自动判定标准，保留人工核实，前两项通过时仍列待核实。数据不足同样保留待核实。</p><p>{nodeModel?'每批使用最近210根有效不复权日线（上市不足时显示实际根数），从零筹码开始。三角顶点取开、高、低、收四价均值，按150个动态节点采样；a=min(1,2×换手率/100)，旧筹码乘以1-a，再直接注入a×采样高度，每天的新筹码不先归一化为1。昨日、今日在同一窗口内分别回放；原峰占比固定在昨日未舍入峰价±昨日半个节点步长的区间，网格变化也按相同价格区间追踪。':'当前已发布批次使用原成交均价、一分钱价格档模型；算法切换完成后，本处将显示210根行情、150动态节点的口径。'}</p><p>当前在市沪深京清单逐股计算，不等于历史退市股票完整回测。新模型已与四股冻结行情下的东方财富本地系数2源码逐项对齐，不能据此认证官方客户端全部设置或真实持仓。历史行情为事后下载，除权息变换尚未认证；数据异常保留原因，缺少交易日行情不会填零。</p><p>收盘涨停的正向证据采用行情商历史收盘涨停池，并与对应日期实际收盘价逐一核对。普通涨跌幅规则仅用于排除不可能命中的日期；疑似涨停但缺少实际确认、特殊无限制交易日证据不足时保留待核实。不会把统一涨幅阈值当作全市场涨停标准。</p>{manifest&&<p>近10个交易日：{manifest.official_sessions.join('、')}。<a href={manifest.calendar_source} target="_blank" rel="noreferrer">交易所休市日历</a></p>}{manifest&&<><p>本批 {manifest.as_of}，比较 {manifest.previous_date}；沪深京清单 {manifest.universe_total.toLocaleString()}只，已处理 {manifest.scanned_count.toLocaleString()}只，筹码可用 {manifest.model_ready_count.toLocaleString()}只。{manifest.market_complete?'本批价格条件均已核实。':'仍有待核实或缺失数据。'}</p><p>{manifest.universe_coverage==='OFFICIAL_CURRENT_LIST_RECONCILED'?'本批股票集合已与沪、深、北交易所当前在市名单核对。':manifest.universe_coverage==='SITE_CATALOG_UNVERIFIED'?'本批复用网站当前股票清单，覆盖范围尚待核对。':'本批使用已发布的数据清单，不能据此认证历史退市股票覆盖。'}上市首日仅展示当日筹码，前日对比不适用。</p></>}{run&&<p>每日任务 {run.as_of}：{run.state} · {run.scanned_count.toLocaleString()} / {run.universe_total.toLocaleString()}只，筹码可用 {run.model_ready_count.toLocaleString()}只。</p>}<p><a href="/chips/calibration">历史校准样本（旧模型） →</a></p></details>
      </section>
    </main>
    <footer className="cw-global-footer"><span>系数 2 / 210根行情 / “没动”阈值未设置</span><span>{manifest?`结果 ${manifest.as_of} · 筹码可用 ${manifest.model_ready_count.toLocaleString()} / ${manifest.universe_total.toLocaleString()}只`:'每日筹码筛选'}{run?.state==='RUNNING'?' · 新批次计算中':''}</span></footer>
  </div>;
}
