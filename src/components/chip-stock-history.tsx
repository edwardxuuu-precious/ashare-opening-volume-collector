'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MarketStock,MarketScreenRow } from '@/lib/chip-market';
import { parseChipHistory, parseChipHistoryBatch, publishedChipHistory, type ChipHistory } from '@/lib/chip-history';
import { readChip } from '@/lib/chip-read';
import { chipNavigationKey } from '@/lib/chip-keyboard';
import { createChipHistoryCache,createChipHistoryBatchLoader,neighboringChipDates } from '@/lib/chip-history-cache';
import { ChipTradingChart } from './chip-trading-chart';
import { ChartPeriodControl } from './chart-period-control';
import { ChipPeakComparison } from './chip-peak-comparison';
import { RetryMessage } from './read-state';
import {workbenchQuote} from '@/lib/chip-workbench';
import { StockDetailHeader, WorkbenchCheck } from './workbench';
import { InspectorTabs } from './inspector-tabs';
import {chipNumber} from '@/lib/chips';
import {chipScreenState,chipPricePositionLabel,chipStabilityLabel,chipStabilityDetails} from '@/lib/chip-price-position';
import type {PreparedChipHistory} from '@/lib/chip-local-preparation';
import type {createChipStockPreload} from '@/lib/chip-stock-preload';

export function ChipStockHistory({stock,previousDate,initialDate=null,onDateChange,preload,workbench=false,screeningRow,periodValue,onPeriodChange}:{stock:MarketStock;previousDate:string;initialDate?:string|null;onDateChange?:(date:string|null)=>void;preload:ReturnType<typeof createChipStockPreload<MarketStock,PreparedChipHistory>>;workbench?:boolean;screeningRow?:MarketScreenRow;periodValue?:number;onPeriodChange?:(period:number)=>void}){
  const requestedDate=useRef(initialDate);
  const [infoTab,setInfoTab]=useState<'conditions'|'rules'>('conditions');
  const [localPeriod,setPeriod]=useState(120),period=periodValue??localPeriod;
  const [date,setDate]=useState(()=>initialDate&&(initialDate===stock.as_of||stock.history.some(r=>r.date===initialDate))?initialDate:stock.as_of),[historical,setHistorical]=useState<ChipHistory|null>(null);
  const [previewDate,setPreviewDate]=useState<string|null>(null);
  const [localFrames,setLocalFrames]=useState<Record<string,ChipHistory>>(()=>preload.peekPreview(stock.security_id)?.frames??{}),[localPreparing,setLocalPreparing]=useState(()=>!preload.peekPreview(stock.security_id));
  const [,setLocalFallback]=useState(()=>preload.peekPreview(stock.security_id)?.fallback??'');
  const [failed,setFailed]=useState<{date:string;message:string}|null>(null),[retry,setRetry]=useState(0);
  const dates=useMemo(()=>[...new Set([...stock.history.map(r=>r.date),stock.as_of])].sort(),[stock]);
  useEffect(()=>{
    const saved=preload.peekPreview(stock.security_id);
    if(saved){setLocalFrames(saved.frames);setLocalFallback(saved.fallback);setLocalPreparing(false);return;}
    let active=true;setLocalFrames({});setLocalPreparing(true);setLocalFallback('');
    const lease=preload.acquirePreview(stock.security_id);
    lease.promise.then(value=>{if(active){setLocalFrames(value.frames);setLocalFallback(value.fallback);setLocalPreparing(false);}}).catch(error=>{if(active){setLocalFrames({});setLocalFallback(error.message);setLocalPreparing(false);}});
    return()=>{active=false;lease.release();};
  },[stock,preload]);
  // Earlier candles need archive warm-up: keep their first read single-day so
  // batching cannot multiply that slower validation work on the critical path.
  const batch=useMemo(()=>createChipHistoryBatchLoader<ChipHistory>(dates,(anchor,signal)=>{
    const single=dates.indexOf(anchor)<216;
    return readChip(`/data/chips/daily/${stock.dataset_id}/${stock.security_id}/history/${anchor}.json${single?'':'?batch=8'}`,signal,v=>single?{[anchor]:parseChipHistory(v,stock,anchor)}:parseChipHistoryBatch(v,stock,anchor));
  },216),[stock,dates]);
  const cache=useMemo(()=>createChipHistoryCache<ChipHistory>(batch.load),[batch]);
  useEffect(()=>()=>{cache.clear();batch.clear();},[cache,batch]);
  useEffect(()=>cache.protect(date),[cache,date]);
  const dateIndex=dates.indexOf(date),previousAvailableDate=dates[dateIndex-1],nextAvailableDate=dates[dateIndex+1];
  const selectDate=useCallback((value:string)=>{setPreviewDate(null);setDate(value);},[]);
  const preview=useCallback((value:string|null)=>setPreviewDate(value===date?null:value),[date]);
  const stepDate=useCallback((direction:-1|1)=>{setPreviewDate(null);setDate(selected=>dates[dates.indexOf(selected)+direction]??selected);},[dates]);
  useEffect(()=>onDateChange?.(date===stock.as_of?null:date),[date,stock.as_of,onDateChange]);
  useEffect(()=>{
    const onKeyDown=(event:KeyboardEvent)=>{
      const key=chipNavigationKey(event);
      if(key!=='ArrowLeft'&&key!=='ArrowRight')return;
      event.preventDefault();
      stepDate(key==='ArrowLeft'?-1:1);
    };
    window.addEventListener('keydown',onKeyDown);
    return()=>window.removeEventListener('keydown',onKeyDown);
  },[stepDate]);
  const displayDate=previewDate??date;
  const latest=useMemo(()=>publishedChipHistory(stock,previousDate),[stock,previousDate]);
  useEffect(()=>{
    if(displayDate===stock.as_of)return;
    let active=true,lease:ReturnType<typeof cache.acquire>|undefined;
    setFailed(null);
    const saved=localFrames[displayDate]??cache.peek(displayDate)??batch.peek(displayDate);
    if(saved){cache.remember(displayDate,saved);setHistorical(saved);return;}
    setHistorical(null);
    // Give the worker a short head start; a slow/blocked worker must not delay
    // the selected-day server fallback until its overall timeout.
    const preparingDay=localPreparing&&stock.history.findIndex(r=>r.date===displayDate)>=209;
    const timer=window.setTimeout(()=>{
      lease=cache.acquire(displayDate);
      lease.promise.then(value=>{if(active)setHistorical(value);}).catch(error=>{if(active)setFailed({date:displayDate,message:error.message});});
    },preparingDay?600:previewDate===null?0:120);
    return()=>{active=false;window.clearTimeout(timer);lease?.release();};
  },[displayDate,previewDate,stock,retry,cache,batch,localFrames,localPreparing]);
  const frame=displayDate===stock.as_of?latest:localFrames[displayDate]??cache.peek(displayDate)??batch.peek(displayDate)??(historical?.selected_date===displayDate?historical:null);
  useEffect(()=>{
    if(!frame||localPreparing)return;
    const timer=window.setTimeout(()=>cache.prefetch(neighboringChipDates(dates,displayDate,2).filter(d=>d!==stock.as_of&&!localFrames[d])),250);
    return()=>window.clearTimeout(timer);
  },[frame,dates,displayDate,stock.as_of,cache,localFrames,localPreparing]);
  const error=!frame&&failed?.date===displayDate?failed.message:'';
  const current=frame?.snapshots[displayDate]??null,previous=frame?.previous_date?frame.snapshots[frame.previous_date]??null:null;
  const loading=!frame&&!error;
  const quote=workbenchQuote(stock.history,displayDate,current);
  const dateControls=<div className="chip-history-controls">
      <label><span>筹码日期</span> <select aria-label="筹码日期" value={date} onChange={e=>selectDate(e.target.value)}>{dates.toReversed().map(d=><option key={d} value={d}>{d}{d===stock.as_of?' · 最新':''}</option>)}</select></label>
      <div className="chip-history-stepper" role="group" aria-label="按交易日切换筹码">
        <button type="button" aria-label="前一交易日" aria-keyshortcuts="ArrowLeft" disabled={!previousAvailableDate} title={previousAvailableDate?`切换到${previousAvailableDate}（←）`:'已到最早可用日期'} onClick={()=>stepDate(-1)}>{workbench?'‹':'前一交易日'}</button>
        <button type="button" aria-label="后一交易日" aria-keyshortcuts="ArrowRight" disabled={!nextAvailableDate} title={nextAvailableDate?`切换到${nextAvailableDate}（→）`:'已到最新可用日期'} onClick={()=>stepDate(1)}>{workbench?'›':'后一交易日'}</button>
      </div>
      <button className="chip-site-link" disabled={date===stock.as_of} onClick={()=>selectDate(stock.as_of)}>{workbench?'最新':'回到最新'}</button>
    </div>;
  return <div className={workbench?'chip-history-workbench':undefined}>
    {workbench&&<StockDetailHeader name={stock.name} code={stock.security_id} date={displayDate} close={quote.close} percent={quote.percent} quoteNote={!quote.hasCandle?(current?.market_session_status==='SOURCE_CONFIRMED_NO_TRADE'?`未交易 · 最近交易 ${current.last_trade_date}`:'当日行情待补齐'):undefined} badge={screeningRow&&<span className={`cw-screen-badge cw-screen-${chipScreenState(screeningRow).toLowerCase()}`}>{chipScreenState(screeningRow)==='PASS'?'当日入选':chipScreenState(screeningRow)==='UNKNOWN'?'数据不足':'未满足'}</span>}/>}
    {workbench?<div className="cw-chart-controls"><ChartPeriodControl period={period} periods={[60,120,250,500]} availableCount={Math.min(period,stock.history.length)} onPeriodChange={value=>{setPeriod(value);onPeriodChange?.(value);setPreviewDate(null);}}/>{dateControls}</div>:dateControls}

    {requestedDate.current&&!dates.includes(requestedDate.current)&&<p className="chip-site-note">该股没有此前所选{requestedDate.current}的可用K线，已从最新日期{stock.as_of}开始展示。</p>}
    <ChipTradingChart history={stock.history} current={current} previous={previous} date={displayDate} selectedDate={date} compareDate={frame?.previous_date??''} loading={loading} loadError={!!error} onSelectDate={selectDate} onPreviewDate={preview} appearance={workbench?'workbench':'classic'} periodValue={workbench?period:undefined} hideToolbar={workbench}/>
    {previewDate&&<p className="chip-history-status chip-preview-status" role="status">鼠标预览 {displayDate} · 已固定 {date}；点击K线固定此日，移出图表恢复。</p>}
    {loading&&<p className="chip-history-status" role="status">正在计算{displayDate}及前一交易日的筹码…</p>}
    {error&&<RetryMessage message={`${displayDate}：${error}`} onRetry={()=>setRetry(n=>n+1)}/>}
    {current&&<><ChipPeakComparison current={current} previous={previous} date={displayDate} previousDate={frame?.previous_date??''} workbench={workbench}/>{frame?.comparison_status==='PREVIOUS_SESSION_UNAVAILABLE'&&<p className="chip-site-note">前一交易日{frame.previous_date??''}缺少可核实行情，暂不展示峰价及原峰占比对比。</p>}{frame?.comparison_status==='NO_PREVIOUS_LISTED_SESSION'&&<p className="chip-site-note">该日期没有可用的前一交易日筹码。</p>}</>}
    {workbench&&screeningRow&&<section className="wb-inspector" aria-label="每日筹码条件"><InspectorTabs className="tv-inspector-tabs" label="筹码核对面板" idPrefix="chip-info" tabs={[{id:'conditions',label:'条件核对'},{id:'rules',label:'规则与数据'}]} value={infoTab} onChange={setInfoTab}/><div className="tv-inspector-body" role="tabpanel" id={`chip-info-${infoTab}`} aria-labelledby={`chip-info-${infoTab}-tab`}>
      {infoTab==='conditions'&&<>
        <div className="tv-inspector-intro"><b>入选条件</b><p>筛选日 {stock.as_of} · {chipPricePositionLabel(screeningRow)} · {chipStabilityLabel(screeningRow)}</p></div>
        <WorkbenchCheck title="01 近期涨停" state={screeningRow.recent_limit_up_dates.length?'PASS':screeningRow.prefilter_status==='UNKNOWN'?'UNKNOWN':'FAIL'}><dl><div><dt>已确认涨停日</dt><dd>{screeningRow.recent_limit_up_dates.join('、')||'未找到'}</dd></div></dl><p>近10个交易日至少一次收盘涨停。</p></WorkbenchCheck>
        <WorkbenchCheck title="02 成本位置" state={screeningRow.above_mean_cost_status??'UNKNOWN'}><dl><div><dt>筛选日收盘</dt><dd>{screeningRow.close_cny?chipNumber(screeningRow.close_cny).toFixed(2):'待核实'}</dd></div><div><dt>平均成本</dt><dd>{screeningRow.model_mean_cost_cny?chipNumber(screeningRow.model_mean_cost_cny).toFixed(4):'待核实'}</dd></div></dl><p>收盘价高于平均成本。</p></WorkbenchCheck>
        <WorkbenchCheck title="03 顶格稳定" state={screeningRow.chip_unchanged_status??'UNKNOWN'}><dl><div><dt>基准日</dt><dd>{screeningRow.chip_stability?.baseline_date??'待核实'}</dd></div><div><dt>已核交易日</dt><dd>{screeningRow.chip_stability?`${screeningRow.chip_stability.evaluated_sessions} / ${screeningRow.chip_stability.required_sessions}`:'待核实'}</dd></div></dl><p>顶格偏移≤1%，原区占比保留≥90%。</p></WorkbenchCheck>
      </>}
      {infoTab==='rules'&&<><h3>每日筛选</h3><p>近10日收盘涨停、收盘高于平均成本、顶格稳定，三项同时满足。</p><p>{chipStabilityDetails(screeningRow)}</p><p>行情不复权 · 筹码系数2 · 最近210根有效日线。</p></>}
    </div></section>}

  </div>;
}
