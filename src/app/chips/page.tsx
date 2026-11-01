'use client';
import { useEffect,useMemo,useRef,useState,useSyncExternalStore } from 'react';
import { RefreshCw } from 'lucide-react';
import { SiteHeader } from '@/components/site-header';
import { SiteLink } from '@/components/site-router';
import { WorkbenchPageBar, WorkbenchShell, WorkbenchListHeader, WorkbenchListFooter, WorkbenchState, WorkbenchListSkeleton } from '@/components/workbench';
import { ChipStockHistory } from '@/components/chip-stock-history';
import { readChip } from '@/lib/chip-read';
import {startPublicationFollow} from '@/lib/publication-follow';
import { chipNumber } from '@/lib/chips';
import { chipReason } from '@/lib/chip-display';
import { useStockListNavigation } from '@/components/use-stock-list-navigation';
import { StockListNote } from '@/components/read-state';
import { stockListMatches } from '@/lib/stock-list';
import {createChipStockPreload,neighboringChipStocks} from '@/lib/chip-stock-preload';
import {createChipStockIntent} from '@/lib/chip-stock-intent';
import {prepareChipLocalHistory} from '@/lib/chip-local-preparation';
import {initialWorkbenchStock} from '@/lib/chip-workbench';
import {chipScreenState,chipScreenCounts} from '@/lib/chip-price-position';
import { parseMarketManifest,parseMarketScreen,parseMarketStock,currentMarketReasons,type MarketManifest,type MarketScreenRow,type MarketStock,type MarketState } from '@/lib/chip-market';
import './chips.css';
import '@/components/stock-navigation.css';
import './workbench.css';
export const dynamic='force-static';
const labels={PASS:'三项条件自动入选',FAIL:'筛选条件未满足',UNKNOWN:'筛选数据不足'};
const noSubscribe=()=>()=>{},zeroVersion=()=>0;
type Progress={state:string;as_of:string;dataset_id?:string;universe_total:number;scanned_count:number;model_ready_count:number;updated_at:string};
function progress(v:unknown):Progress{const x=v as Progress;if(!x||!Number.isSafeInteger(x.scanned_count)||!Number.isSafeInteger(x.universe_total)||x.scanned_count<0||x.scanned_count>x.universe_total||!Number.isSafeInteger(x.model_ready_count)||x.model_ready_count<0||x.model_ready_count>x.universe_total||typeof x.state!=='string')throw Error('每日计算状态无效。');return x;}
export default function ChipsPage(){
  const [manifest,setManifest]=useState<MarketManifest|null>(null),[rows,setRows]=useState<MarketScreenRow[]|null>(null),[run,setRun]=useState<Progress|null>(null);
  const [code,setCode]=useState(''),[stock,setStock]=useState<MarketStock|null>(null),[error,setError]=useState(''),[detailError,setDetailError]=useState('');
  const [filter,setFilter]=useState<MarketState|'ALL'>('PASS'),[search,setSearch]=useState(''),[page,setPage]=useState(0),[loaded,setLoaded]=useState(false),[statusError,setStatusError]=useState('');
  const [loadingStage,setLoadingStage]=useState<'manifest'|'screening'>('manifest');
  const [historyDate,setHistoryDate]=useState<string|null>(null),[chartPeriod,setChartPeriod]=useState(120),[detailReload,setDetailReload]=useState(0);
  const listArea=useRef<HTMLDivElement>(null);
  const publication=useRef<{refresh:()=>void;stop:()=>void}|null>(null);
  const selectedCode=useRef(code),hasPublication=useRef(false),stagedFrame=useRef<MarketStock|null>(null);
  selectedCode.current=code;
  const preload=useMemo(()=>manifest?createChipStockPreload((value,signal)=>{
    const saved=stagedFrame.current;
    if(saved?.dataset_id===manifest.dataset_id&&saved.security_id===value){stagedFrame.current=null;return Promise.resolve(saved);}
    return readChip(`/data/chips/daily/${manifest.active_slot}/${value}.json`,signal,v=>parseMarketStock(v,value,manifest));
  },prepareChipLocalHistory,5,value=>Object.keys(value.frames).length>0):null,[manifest]);
  const intent=useMemo(()=>preload?createChipStockIntent(preload.prefetch,value=>preload.peekPreview(value)!==undefined):null,[preload]);
  useSyncExternalStore(preload?.subscribe??noSubscribe,preload?.version??zeroVersion,zeroVersion);
  useEffect(()=>()=>intent?.clear(),[intent]);
  useEffect(()=>()=>preload?.clear(),[preload]);
  useEffect(()=>{
    const follow=startPublicationFollow({
      readStatus:signal=>readChip('/data/chips/daily/status.json',signal,progress),
      readManifest:signal=>readChip('/data/chips/daily/manifest.json',signal,parseMarketManifest),
      readPayload:async(m,signal)=>{
        setLoadingStage('screening');
        const r=await readChip(`/data/chips/daily/${m.active_slot}/screening.json`,signal,v=>parseMarketScreen(v,m));
        const requested=selectedCode.current,selected=hasPublication.current&&!requested?'':initialWorkbenchStock(r,requested);
        const frame=selected?await readChip(`/data/chips/daily/${m.active_slot}/${selected}.json`,signal,v=>parseMarketStock(v,selected,m)):null;
        return {rows:r,frame,requested,selected};
      },
      onStatus:setRun,
      onPublication:(m,r)=>{
        if(r.requested!==selectedCode.current)return false;
        stagedFrame.current=r.frame;hasPublication.current=true;selectedCode.current=r.selected;setManifest(m);setRows(r.rows);setStock(r.frame);setCode(r.selected);
      },
      onStatusError:e=>setStatusError(e?'每日计算状态暂时无法更新，保留已显示结果。':''),
      onPublicationError:e=>setError(e?.message??''),onSettled:()=>setLoaded(true),
      isRunning:v=>v.state==='RUNNING',publishedDataset:v=>v.state==='COMMITTED'&&typeof v.dataset_id==='string'?v.dataset_id:null,
    });
    publication.current=follow;
    return()=>{follow.stop();publication.current=null;};
  },[]);
  useEffect(()=>{
    if(!preload)return;
    if(!code){preload.clear();setStock(null);return;}
    preload.protect(code);setDetailError('');
    const saved=preload.peekStock(code);if(saved){setStock(saved);return;}
    let active=true;setStock(null);const lease=preload.acquireStock(code);
    lease.promise.then(value=>{if(active)setStock(value);}).catch(error=>{if(active)setDetailError(error.message);});
    return()=>{active=false;lease.release();};
  },[code,preload,detailReload]);
  const screenCounts=useMemo(()=>chipScreenCounts(rows??[]),[rows]);
  const matched=useMemo(()=>(rows??[]).filter(r=>(filter==='ALL'||chipScreenState(r)===filter)&&stockListMatches(search,r.name,r.security_id)),[rows,filter,search]),shown=matched.slice(page*60,(page+1)*60);
  const stockIndex=matched.findIndex(r=>r.security_id===code);
  const neighbors=useMemo(()=>neighboringChipStocks(matched.map(r=>r.security_id),code),[matched,code]);
  useEffect(()=>{if(stockIndex>=0)setPage(Math.floor(stockIndex/60));},[code,stockIndex,matched]);
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
  const selectStock=(value:string)=>{intent?.cancelHover();selectedCode.current=value;setCode(value);};
  const navigationKeys=useMemo(()=>matched.map(row=>row.security_id),[matched]);
  useStockListNavigation({keys:navigationKeys,selected:code,listRef:listArea,page,enabled:!!code,onSelect:(value,index)=>{selectStock(value);setPage(Math.floor(index/60));}});
  const refreshData=()=>{publication.current?.refresh();if(detailError)setDetailReload(value=>value+1);};
  const selectedRow=rows?.find(r=>r.security_id===code),detailReasons=selectedStock?currentMarketReasons(selectedStock,selectedRow):[];
  const current=selectedStock?.snapshots[manifest?.as_of??'']??null;
  const nodeModel=manifest?.model_version==='chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1';
  return <div className="chip-site chip-workbench wb-site">
    <SiteHeader active="chips"><button className="stock-header-refresh" onClick={refreshData} aria-label="刷新每日筹码" title="重新读取已发布批次"><RefreshCw size={14}/><span>刷新</span></button></SiteHeader>
    <WorkbenchPageBar title="每日筹码筛选" description="涨停回看 · 成本位置 · 顶格稳定" date={manifest?.as_of}><SiteLink href="/chips/calibration">历史校准</SiteLink></WorkbenchPageBar>
    <WorkbenchShell contentClassName="cw-workspace chip-detail" footer={<><span>{manifest?`全市场 ${manifest.universe_total.toLocaleString()}只 · 入选 ${screenCounts.PASS.toLocaleString()}只`:'正在读取每日结果…'}</span><span>{run?.state==='RUNNING'?'新结果计算中':manifest?`更新至 ${manifest.as_of}`:'每日筹码'}</span></>} sidebar={
      <aside className="cw-sidebar wb-sidebar" aria-label="每日筛选结果">
        <WorkbenchListHeader count={manifest?matched.length:null} ready={!!manifest} search={{label:'查找股票',value:search,onChange:value=>{setSearch(value);setPage(0);}}} filters={(['PASS','FAIL','UNKNOWN','ALL'] as const).map(k=>({id:k,label:k==='PASS'?'当日入选':k==='ALL'?'全部':k==='UNKNOWN'?'数据不足':'未满足',count:manifest?(k==='ALL'?manifest.universe_total:screenCounts[k]):null,active:filter===k,onSelect:()=>{setFilter(k);setPage(0);}}))}/>
        <div className="cw-stock-list wb-stock-list" ref={listArea}>
          {shown.map(r=>{return <button key={r.security_id} className="cw-stock-row wb-stock-row" type="button" aria-label={`${r.name} ${r.security_id}`} aria-pressed={r.security_id===code} data-stock-code={r.security_id} data-stock-key={r.security_id} onClick={()=>selectStock(r.security_id)} onPointerEnter={e=>{if(e.pointerType==='mouse'&&r.security_id!==code)intent?.hover(r.security_id);}} onPointerLeave={()=>intent?.cancelHover(r.security_id)}>
            <span className="cw-stock-line"><strong>{r.name}</strong><span className="cw-stock-price">{r.close_cny===null?'待核实':chipNumber(r.close_cny).toFixed(2)}</span></span>
            <span className="cw-stock-line"><span className="cw-code">{r.security_id}</span><span className="wb-row-status">{chipScreenState(r)==='PASS'?'当日入选':chipScreenState(r)==='UNKNOWN'?'数据不足':'未满足'}</span></span>
            {r.chip_status==='MISSING'&&<span className="cw-row-warning">筹码待补齐</span>}{r.last_trade_close_date&&<span className="cw-row-warning">收盘沿用 {r.last_trade_close_date}</span>}
          </button>;})}
          {!manifest&&!error?<WorkbenchListSkeleton/>:!shown.length&&<p className="cw-list-empty">{error?'名单暂不可用':'当前范围没有结果。'}</p>}
        </div>
        <WorkbenchListFooter count={matched.length} page={page} onPageChange={setPage} dateKeys/>
      </aside>}>
        <StockListNote outside={!!code&&!matched.some(row=>row.security_id===code)}/>
        {!manifest&&<WorkbenchState state={error?{kind:'error',message:error,onRetry:()=>publication.current?.refresh()}:loaded?{kind:'empty',title:run?.state==='RUNNING'?'正在生成今日结果':'今日结果待发布',message:run?`${run.as_of} · 已处理 ${run.scanned_count.toLocaleString()} / ${run.universe_total.toLocaleString()}只`:undefined}:{kind:'loading',label:loadingStage==='manifest'?'正在读取每日结果…':'正在读取股票与行情…'}}/>}
        {manifest&&!code&&<WorkbenchState state={{kind:'empty',title:'选择股票查看详情',message:'在左侧名单选择一只股票。'}}/>}
        {manifest&&code&&!selectedStock&&<WorkbenchState state={detailError?{kind:'error',message:detailError,onRetry:()=>setDetailReload(value=>value+1)}:{kind:'loading',label:`正在读取${selectedRow?.name??code}的行情…`}}/>}
        {selectedStock&&preload&&<>
          <ChipStockHistory key={`${code}-${manifest!.dataset_id}`} stock={selectedStock} previousDate={manifest!.previous_date} initialDate={historyDate} onDateChange={setHistoryDate} preload={preload} workbench screeningRow={selectedRow} periodValue={chartPeriod} onPeriodChange={setChartPeriod}/>
          {!current&&<p className="cw-notice">该股筹码暂不可用：{detailReasons.map(chipReason).join('；')}</p>}
          {detailReasons.length>0&&<details className="cw-data-notes"><summary>个股数据说明（{detailReasons.length}）</summary><p>{detailReasons.map(chipReason).join('；')}</p></details>}
          <div className="cw-detail-footer"><span className="chip-stock-position">{stockIndex>=0?`当前名单第${stockIndex+1}/${matched.length}只${stockIndex===0?' · 首只':stockIndex===matched.length-1?' · 末只':''}`:'当前股票不在所选名单中'}</span><button className="chip-site-link" onClick={()=>{intent?.clear();selectedCode.current='';setCode('');setHistoryDate(null);}}>清除选择</button></div>

        </>}
        {(error||statusError)&&manifest&&<p className="cw-coverage-warning" role="alert">{[error,statusError].filter(Boolean).join('；')}</p>}
        {manifest&&(!manifest.market_complete||manifest.universe_coverage==='SITE_CATALOG_UNVERIFIED')&&<p className="cw-coverage-warning" role="status">{!manifest.market_complete?'部分股票数据待补齐。':'当前股票清单覆盖范围尚待核对。'}</p>}
      {manifest&&<details className="chip-site-method cw-method"><summary>计算与数据说明</summary><p>筛选仅保留三项：最近10个市场交易日至少一次收盘涨停；收盘价高于当日平均成本；顶格未动。不设10元以上或以下等绝对价格门槛。平均成本使用完整精度比较，严格大于，等于不算；高于主峰不能替代高于平均成本。顶格未动自动比较最早已确认涨停前一交易日的固定顶格区：涨停日至本批日期逐日检查，顶格区偏移不超过1%，各原区占比不低于基准的90%。顶格区由当日最高峰90%以上的价格格组成；不是屏幕条形是否顶满。缺少历史、价格口径不可比或分辨率不足时显示数据不足，不需要人工确认。这个规则描述模型分布稳定，不代表原持有人未卖出或已验证收益。</p><p>{nodeModel?'每批使用最近210根有效不复权日线（上市不足时显示实际根数），从零筹码开始。三角顶点取开、高、低、收四价均值，按150个动态节点采样；a=min(1,2×换手率/100)，旧筹码乘以1-a，再直接注入a×采样高度，每天的新筹码不先归一化为1。昨日、今日在同一窗口内分别回放；原峰占比固定在昨日未舍入峰价±昨日半个节点步长的区间，网格变化也按相同价格区间追踪。':'当前已发布批次使用原成交均价、一分钱价格档模型；算法切换完成后，本处将显示210根行情、150动态节点的口径。'}</p><p>当前在市沪深京清单逐股计算，不等于历史退市股票完整回测。新模型已与四股冻结行情下的东方财富本地系数2源码逐项对齐，不能据此认证官方客户端全部设置或真实持仓。历史行情为事后下载，除权息变换尚未认证；数据异常保留原因，缺少交易日行情不会填零。</p><p>收盘涨停的正向证据采用行情商历史收盘涨停池，并与对应日期实际收盘价逐一核对。普通涨跌幅规则仅用于排除不可能命中的日期；疑似涨停但缺少实际确认、特殊无限制交易日证据不足时保留待核实。不会把统一涨幅阈值当作全市场涨停标准。</p>{manifest&&<p>近10个交易日：{manifest.official_sessions.join('、')}。<a href={manifest.calendar_source} target="_blank" rel="noreferrer">交易所休市日历</a></p>}{manifest&&<><p>本批 {manifest.as_of}，比较 {manifest.previous_date}；沪深京清单 {manifest.universe_total.toLocaleString()}只，已处理 {manifest.scanned_count.toLocaleString()}只，筹码可用 {manifest.model_ready_count.toLocaleString()}只。{manifest.market_complete?'本批三项筛选条件均已完成自动判断。':'仍有筛选数据不足或数据缺失。'}</p><p>{manifest.universe_coverage==='OFFICIAL_CURRENT_LIST_RECONCILED'?'本批股票集合已与沪、深、北交易所当前在市名单核对。':manifest.universe_coverage==='SITE_CATALOG_UNVERIFIED'?'本批复用网站当前股票清单，覆盖范围尚待核对。':'本批使用已发布的数据清单，不能据此认证历史退市股票覆盖。'}上市首日仅展示当日筹码，前日对比不适用。</p></>}{run&&<p>每日任务 {run.as_of}：{run.state} · {run.scanned_count.toLocaleString()} / {run.universe_total.toLocaleString()}只，筹码可用 {run.model_ready_count.toLocaleString()}只。</p>}<p><SiteLink href="/chips/calibration">历史校准样本</SiteLink></p></details>}
    </WorkbenchShell>
  </div>;
}
