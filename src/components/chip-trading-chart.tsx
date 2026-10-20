'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { chipNumber, type ChipSnapshot } from '@/lib/chips';
import { tradingGeometry, type DailyCandle } from '@/lib/chip-market';

const volumeText=(n:number)=>n>=100000000?`${(n/100000000).toFixed(2)}亿股`:`${(n/10000).toFixed(2)}万股`;
export function ChipTradingChart({history,current,previous,date,selectedDate,compareDate,loading=false,loadError=false,onSelectDate,onPreviewDate,appearance='classic',periodValue,hideToolbar=false,gridLines}:{history:DailyCandle[];current:ChipSnapshot|null;previous:ChipSnapshot|null;date:string;selectedDate?:string;compareDate:string;loading?:boolean;loadError?:boolean;onSelectDate?:(date:string)=>void;onPreviewDate?:(date:string|null)=>void;appearance?:'classic'|'workbench';periodValue?:number;hideToolbar?:boolean;gridLines?:{price:string;label:string;active:boolean}[]}) {
  const [localPeriod,setPeriod]=useState(gridLines?65:120),[hover,setHover]=useState<number|null>(null),[height,setHeight]=useState(360);
  const period=periodValue??localPeriod,light=appearance==='workbench';
  const c=light?{background:'#ffffff',grid:'#e9edf3',text:'#748196',heading:'#4b5f77',chip:'#345cd6',previous:'#8390a2',up:'#cc5362',down:'#118671',guide:'#345cd6',divider:'#e4e9ef'}:{background:'#101923',grid:'#263442',text:'#8fa0b1',heading:'#c5d4e2',chip:'#4c9de5',previous:'#b5ad70',up:'#e45c5b',down:'#1cae9f',guide:'#75b8f2',divider:'#354657'};
  const focusDate=selectedDate??date;
  useEffect(()=>{if(date===focusDate)setHover(null);},[date,focusDate]);
  const chart=useRef<HTMLDivElement>(null),[width,setWidth]=useState(1200);
  const hasHistory=history.length>0;
  useEffect(()=>{
    const node=chart.current;if(!node)return;
    const measure=()=>{const rect=node.getBoundingClientRect(),next=Math.round(rect.width);if(next>0)setWidth(next);if(light&&rect.height>0)setHeight(Math.round(rect.height));};
    measure();
    if(typeof ResizeObserver==='undefined'){window.addEventListener('resize',measure);return()=>window.removeEventListener('resize',measure);}
    const observer=new ResizeObserver(measure);observer.observe(node);return()=>observer.disconnect();
  },[hasHistory,light]);
  const g=useMemo(()=>tradingGeometry(history,current,previous,period,width,onSelectDate?focusDate:undefined,light?height:undefined),[history,current,previous,period,width,focusDate,onSelectDate,light,height]);
  if(!g)return <p>尚无可展示的日K线。</p>;
  const committed=g.candles.find(c=>c.date===focusDate),indicated=g.candles.find(c=>c.date===date);
  const selected=hover!==null?g.candles[Math.min(hover,g.candles.length-1)]:indicated??g.candles[g.candles.length-1];
  const candleWidth=Math.max(.45,Math.min(g.step*.62,12));
  const gridLabels=(gridLines??[]).map(line=>({...line,y:g.y(Number(line.price))})).sort((a,b)=>a.y-b.y);
  for(let i=0;i<gridLabels.length;i++)gridLabels[i].y=Math.max(gridLabels[i].y,i?gridLabels[i-1].y+30:g.top+10);
  for(let i=gridLabels.length-1;i>=0;i--)gridLabels[i].y=Math.min(gridLabels[i].y,i<gridLabels.length-1?gridLabels[i+1].y-30:g.bottom-10);
  function onMove(event:React.PointerEvent<SVGSVGElement>){const r=event.currentTarget.getBoundingClientRect(),x=(event.clientX-r.left)/r.width*g!.W,y=(event.clientY-r.top)/r.height*g!.H;const index=x>=g!.left&&x<=g!.right?Math.max(0,Math.min(g!.candles.length-1,Math.floor((x-g!.left)/g!.step))):null;setHover(index);if(event.pointerType!=='touch'&&index!==null&&y>=g!.top&&y<=g!.volBottom)onPreviewDate?.(g!.candles[index].date);}
  function onClick(event:React.MouseEvent<SVGSVGElement>){if(!onSelectDate)return;const r=event.currentTarget.getBoundingClientRect(),x=(event.clientX-r.left)/r.width*g!.W,y=(event.clientY-r.top)/r.height*g!.H;if(x>=g!.left&&x<=g!.right&&y>=g!.top&&y<=g!.volBottom)onSelectDate(g!.candles[Math.min(g!.candles.length-1,Math.floor((x-g!.left)/g!.step))].date);}
  const chartNotes=<>{(current?.profile_format||previous?.profile_format)&&<p className="chip-site-note">网页分布按不小于{Math.max(Number(current?.display_bin_width_cny??0),Number(previous?.display_bin_width_cny??0)).toFixed(2)}元分组合并占比；峰价和原峰同价位占比保留0.01元档的精确计算结果。</p>}{current&&<p className="chip-site-note">右侧横条按价格从低到高纵向排列，显示分组宽约{g.bandWidth.toFixed(3)}元；{current.price_grid_kind==='EASTMONEY_150_DYNAMIC_NODES'?'峰价按150动态节点确定，原峰占比固定在前一交易日节点对应的价格区间。':'峰价和原峰占比按0.01元原始价格档计算。'}{g.outsideShare>.001?`当前价格范围外还有${g.outsideShare.toFixed(4)}%估算筹码。`:''}筹码日期为{date}{previous?`，对比${compareDate}`:''}。</p>}</>;
  return <div className={`chip-trading${light?' chip-trading-workbench':''}`}>
    {!hideToolbar&&<div className="chip-chart-toolbar"><strong>日K线 / 成交量 / {gridLines?'价格格子':'筹码'}</strong><div aria-label="K线显示周期">{(gridLines?[65,120,250,500]:[60,120,250,500]).map(n=><button key={n} aria-pressed={period===n} onClick={()=>{setPeriod(n);setHover(null);onPreviewDate?.(null);}}>{n}日</button>)}</div><span>不复权 · {gridLines?'统计日期':selectedDate&&date!==selectedDate?'鼠标预览':'筹码日期'} {date}</span></div>}
    <div className="chip-candle-readout" aria-live="polite"><b>{selected.date}</b><span>开 {chipNumber(selected.open).toFixed(2)}</span><span>高 {chipNumber(selected.high).toFixed(2)}</span><span>低 {chipNumber(selected.low).toFixed(2)}</span><span>收 {chipNumber(selected.close).toFixed(2)}</span><span>量 {volumeText(chipNumber(selected.volume_shares))}</span><span>换手 {chipNumber(selected.turnover_percent).toFixed(2)}%</span></div>
    <div className={`chip-trading-scroll${onSelectDate?' chip-trading-selectable':''}`} ref={chart}><svg viewBox={`0 0 ${g.W} ${g.H}`} role="img" aria-label={gridLines?'日K线、下方成交量和黄金分割价格格子':'日K线、下方成交量和右侧同价轴筹码分布'} onClick={onClick} onPointerMove={onMove} onPointerDown={onMove} onPointerLeave={e=>{if(e.pointerType!=='touch'){setHover(null);onPreviewDate?.(null);}}}>
      <rect width={g.W} height={g.H} fill={c.background}/>
      {onSelectDate&&committed&&<g><rect x={committed.x-Math.max(g.step,3)/2} y={g.top} width={Math.max(g.step,3)} height={g.volBottom-g.top} fill={c.guide} opacity={light?'.035':'.12'}/><line x1={committed.x} x2={committed.x} y1={g.top} y2={g.volBottom} stroke={c.guide} opacity={light?'.3':'.7'}/></g>}
      {[0,1,2,3,4,5].map(i=>{const price=g.min+(g.max-g.min)*i/5,yy=g.y(price);return <g key={i}><line x1={g.left} x2={g.chipEnd} y1={yy} y2={yy} stroke={c.grid} strokeDasharray="3 5"/><text x={g.right+10} y={yy+4} fill={light?c.text:'#a9b8c7'} fontSize="11">{price.toFixed(2)}</text></g>;})}
      <text x={g.chipStart} y="15" fill={light?c.chip:c.heading} fontSize="11">{gridLines?'格子价格':'筹码占比 →'}</text>
      {gridLabels.map(line=><g key={line.price} data-grid-price={line.price}><line x1={g.left} x2={g.right} y1={g.y(Number(line.price))} y2={g.y(Number(line.price))} stroke={line.active?c.guide:c.previous} strokeWidth={line.active?1.5:.7} strokeDasharray={line.active?undefined:'4 4'}/><text x={g.chipStart} y={line.y-4} fill={line.active?c.guide:c.text} fontSize="10">{line.label}</text><text x={g.chipStart} y={line.y+9} fill={line.active?c.guide:c.heading} fontSize="11">{line.price}</text></g>)}
      {(loading||loadError)&&<g aria-label={`${date}筹码${loadError?'读取失败':'加载中'}`}><text x={g.chipStart} y="58" fill={loadError?c.up:c.heading} fontSize="11">{loadError?'读取失败':'筹码加载中'}</text><text x={g.chipStart} y="78" fill={c.text} fontSize="10">{date}</text>{loading&&<text x={g.chipStart} y="98" fill={c.text} fontSize="10">请稍候…</text>}</g>}
      {g.chips.map((bin,i)=><g key={i}>{previous&&bin.previousWidth>0&&(light?<line x1={g.chipStart} x2={g.chipStart+bin.previousWidth} y1={bin.y+bin.height/2} y2={bin.y+bin.height/2} stroke={c.previous} opacity=".5" strokeDasharray="2 2"/>:<rect x={g.chipStart} y={bin.y} width={bin.previousWidth} height={bin.height} fill={c.previous} opacity=".48"/>)}{current&&bin.currentWidth>0&&<rect data-chip-profile="current" x={g.chipStart} y={bin.y} width={bin.currentWidth} height={bin.height} fill={c.chip} opacity={light?'.6':'.9'}><title>{(bin.price-g.bandWidth/2).toFixed(3)}—{(bin.price+g.bandWidth/2).toFixed(3)}元：{bin.mass.toFixed(4)}%</title></rect>}</g>)}
      {g.candles.map((bar,i)=><g key={bar.date} data-candle-date={bar.date}><line x1={bar.x} x2={bar.x} y1={bar.highY} y2={bar.lowY} stroke={bar.up?c.up:c.down} strokeWidth="1"/><rect x={bar.x-candleWidth/2} y={Math.min(bar.openY,bar.closeY)} width={candleWidth} height={Math.max(1,Math.abs(bar.openY-bar.closeY))} fill={bar.up?c.up:c.down}/><rect data-volume-date={bar.date} x={bar.x-candleWidth/2} y={bar.volumeY} width={candleWidth} height={g.volBottom-bar.volumeY} fill={bar.up?c.up:c.down} opacity={light?'.72':1}/>{g.dateTicks.has(i)&&<text x={bar.x} y={g.H-14} textAnchor={i===0?'start':i===g.candles.length-1?'end':'middle'} fill={c.text} fontSize="11">{bar.date.slice(5)}</text>}</g>)}
      <line x1={g.left} x2={light?g.right:g.chipEnd} y1={g.volumeDivider} y2={g.volumeDivider} stroke={c.divider}/><line x1={g.chipStart-8} x2={g.chipStart-8} y1={0} y2={light?g.bottom:g.H} stroke={c.divider}/>
      <text x={g.left} y={g.volumeDivider+14} fill={c.heading} fontSize="11">成交量（股）</text><text x={g.right} y={g.volumeDivider+14} textAnchor="end" fill={c.text} fontSize="10">{volumeText(g.maxVolume)}</text>
      {light&&current&&current.peak_prices_cny.length===1&&<text x={g.chipEnd} y={Math.max(g.top+12,g.y(chipNumber(current.peak_prices_cny[0]))-4)} textAnchor="end" fill={c.chip} fontSize="11">峰 {current.peak_prices_cny[0]}</text>}
      {hover!==null&&<g pointerEvents="none"><line x1={selected.x} x2={selected.x} y1={g.top} y2={g.volBottom} stroke={light?c.guide:'#8ea8c1'} strokeDasharray="4 4" opacity={light?'.35':1}/><line x1={g.left} x2={g.chipEnd} y1={selected.closeY} y2={selected.closeY} stroke={light?c.guide:'#69839e'} strokeDasharray="4 4" opacity={light?'.35':1}/></g>}
    </svg></div>
    <p className="chip-chart-legend">{current&&<span><i style={{background:c.chip}}/>{date}筹码</span>}{previous&&<span><i className={light?'cw-legend-previous':undefined} style={{background:light?'transparent':c.previous,borderColor:c.previous}}/>{compareDate}对比筹码</span>}<span className="cw-chart-focus">{light?`${selectedDate&&date!==selectedDate?'鼠标预览':'已固定'} ${date}${selectedDate&&date!==selectedDate?` · 固定 ${selectedDate}`:''}`:'价格轴共用 · 量柱与K线逐日对齐'}</span></p>
    {current?.market_session_status==='SOURCE_CONFIRMED_NO_TRADE'&&<p className="chip-site-note">{date}已核实未交易，筹码沿用最后交易日{current.last_trade_date}的分布；未补造停牌期间的K线或成交量。</p>}
    {light?<details className="cw-chart-notes"><summary>图表说明{g.outsideShare>.001?` · 范围外筹码 ${g.outsideShare.toFixed(4)}%`:''}</summary>{chartNotes}</details>:chartNotes}
  </div>;
}
