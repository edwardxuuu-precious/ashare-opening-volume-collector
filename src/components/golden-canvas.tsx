'use client';
import {useState,useEffect,useRef} from 'react';
import type {DailyCandle} from '@/lib/chip-market';
import type {Anchor,Axis,ManualPoint,PriceLine} from '@/lib/golden-workbench';

export function GoldenCanvas({history,lines=[],H,L,axis,focus='all',mini=false,drawing=false,field='high',points=[],windowStart,onPoint}:{history:DailyCandle[];lines?:PriceLine[];H?:Anchor|null;L?:Anchor|null;axis?:Axis|null;focus?:string;mini?:boolean;drawing?:boolean;field?:string;points?:ManualPoint[];windowStart?:string|null;onPoint?:(p:ManualPoint)=>void}){
  const [hover,setHover]=useState<number|null>(null);
  const host=useRef<HTMLDivElement>(null),[width,setWidth]=useState(1100);
  useEffect(()=>{const node=host.current;if(!node||mini)return;const observer=new ResizeObserver(()=>setWidth(Math.max(320,node.clientWidth)));observer.observe(node);setWidth(Math.max(320,node.clientWidth));return()=>observer.disconnect();},[mini,history.length]);
  if(!history.length)return <div className="gw-no-chart">行情暂不可用，未补造K线。</div>;
  const W=mini?500:width,HEIGHT=mini?230:490,left=mini?12:22,right=mini?465:W-(W<620?105:160),top=mini?18:25,bottom=mini?170:350,volTop=mini?183:380,volBottom=mini?215:452;
  const prices=history.flatMap(r=>[Number(r.high),Number(r.low)]).concat(lines.map(l=>l.price));const lo=Math.min(...prices),hi=Math.max(...prices),pad=Math.max((hi-lo)*.08,hi*.002),min=lo-pad,max=hi+pad;
  const y=(p:number)=>bottom-(p-min)/(max-min)*(bottom-top),step=(right-left)/history.length,x=(i:number)=>left+(i+.5)*step;
  const dateX=(date:string)=>{const i=history.findIndex(r=>r.date===date);return i<0?null:x(i);};
  const maxV=Math.max(1,...history.map(r=>Number(r.volume_shares))),bar=hover===null?history.at(-1)!:history[Math.min(hover,history.length-1)]??history.at(-1)!;
  const pin=(p:Anchor|null|undefined,kind:string)=>{if(!p)return null;const xx=dateX(p.date);if(xx===null)return null;const highlight=focus===kind.toLowerCase()||focus==='all';return <g data-anchor={kind} data-date={p.date} data-price={p.price} opacity={highlight?1:.45}>
    {highlight&&[...(p.left_dates??[]),p.date,...(p.right_dates??[])].map(d=>{const dx=dateX(d);return dx===null?null:<rect key={d} x={dx-step/2} y={top} width={step} height={bottom-top} fill="#d2dfb9" opacity=".09"/>;})}
    <circle cx={xx} cy={y(Number(p.price))} r="4" fill="#e0e9cf"/><line x1={xx} x2={xx+10} y1={y(Number(p.price))} y2={y(Number(p.price))} stroke="#cdd7c0"/>
    <text x={Math.min(right-95,xx+10)} y={y(Number(p.price))+(kind==='H'?-12:20)} fill="#e2e9d5" fontSize="12">{kind} {p.price} / {p.date.slice(5)}</text>
    {highlight&&p.available_after_close_on&&dateX(p.available_after_close_on)!==null&&<g data-confirm-kind={kind} data-confirm-date={p.available_after_close_on}><line x1={dateX(p.available_after_close_on)!} x2={dateX(p.available_after_close_on)!} y1={top} y2={bottom} stroke="#b8c8a5" opacity=".3" strokeDasharray="3 4"/><text x={dateX(p.available_after_close_on)!+3} y={bottom-5} fill="#b8c8a5" fontSize="10">{p.available_after_close_on.slice(5)}确认{kind}</text></g>}
  </g>;};
  const coordinates=(e:{currentTarget:SVGSVGElement;clientX:number;clientY:number})=>{const svg=e.currentTarget,point=svg.createSVGPoint(),matrix=svg.getScreenCTM();if(!matrix)return null;point.x=e.clientX;point.y=e.clientY;return point.matrixTransform(matrix.inverse());};
  const markers=axis&&(!mini)&&(focus==='m'||focus==='all')?[
    ...Array.from(new Set(axis.source_fields.map(p=>p.date))).slice(0,4).map(date=>({date,label:'源',color:'#8ea2b7'})),
    ...axis.supports.slice(0,4).map(r=>({date:r.date,label:'支',color:'#b5c98b'})),
    {date:axis.break_event.date,label:'收破',color:'#e1b67d'},
    ...axis.later_touch_below_events.slice(0,4).map(r=>({date:r.date,label:'抽',color:'#d9a9ad'})),
  ]:[];
  const labelYs=lines.map((l,i)=>({i,y:y(l.price)})).sort((a,b)=>a.y-b.y);
  for(let i=0;i<labelYs.length;i++)labelYs[i].y=Math.max(labelYs[i].y,i?labelYs[i-1].y+26:top+8);
  for(let i=labelYs.length-1;i>=0;i--)labelYs[i].y=Math.min(labelYs[i].y,i<labelYs.length-1?labelYs[i+1].y-26:bottom-8);
  return <div ref={host} className={`gw-canvas ${mini?'gw-canvas-mini':''} ${drawing?'gw-drawing':''}`}>
    {!mini&&<div className="gw-readout"><b>{bar.date}</b>{(['open','high','low','close'] as const).map((k,i)=><span key={k}>{['开','高','低','收'][i]} {Number(bar[k]).toFixed(2)}</span>)}<span>量 {Number(bar.volume_shares).toLocaleString()}</span>{drawing&&<em>点击选{points.length===0?'H':points.length===1?'第二点':'点（先重画）'} · {field==='custom'?'鼠标价位':field.toUpperCase()}</em>}</div>}
    <svg viewBox={`0 0 ${W} ${HEIGHT}`} role="img" aria-label={mini?'自动画格概览':'自动选点与人工黄金分割K线及成交量'} onPointerMove={e=>{if(mini)return;const point=coordinates(e);if(!point)return;const xx=point.x;setHover(xx>=left&&xx<=right?Math.max(0,Math.min(history.length-1,Math.floor((xx-left)/step))):null);}} onPointerLeave={()=>setHover(null)} onClick={e=>{if(!drawing||!onPoint||points.length>=2)return;const point=coordinates(e);if(!point)return;const xx=point.x,yy=point.y;if(xx<left||xx>right||yy<top||yy>bottom)return;const row=history[Math.max(0,Math.min(history.length-1,Math.floor((xx-left)/step)))];onPoint({date:row.date,field,price:field==='custom'?Math.max(.00000001,min+(bottom-yy)/(bottom-top)*(max-min)).toFixed(6):String(row[field as keyof DailyCandle])});}}>
      <rect width={W} height={HEIGHT} fill="#14261f"/>
      {[0,1,2,3,4].map(i=>{const p=min+(max-min)*i/4;return <g key={i}><line x1={left} x2={right} y1={y(p)} y2={y(p)} stroke="#33483b" strokeDasharray="2 5"/>{!mini&&<text x={right+12} y={y(p)+4} fill="#879e8e" fontSize="11">{p.toFixed(2)}</text>}</g>;})}
      {windowStart&&dateX(windowStart)!==null&&history[0].date<windowStart&&<g><line x1={dateX(windowStart)!-step/2} x2={dateX(windowStart)!-step/2} y1={top} y2={volBottom} stroke="#96ad9a" strokeDasharray="4 4"/><text x={dateX(windowStart)!+4} y={top+12} fill="#b2c2b2" fontSize="10">←背景 / 60根计算→</text></g>}
      {lines.map((l,i)=>{const yy=labelYs.find(v=>v.i===i)!.y;return <g key={i} data-grid-price={l.price} data-manual={!!l.manual}><line x1={left} x2={right} y1={y(l.price)} y2={y(l.price)} stroke={l.manual?'#c4b2ea':'#c4bb83'} strokeWidth={l.manual?1.5:1} strokeDasharray={l.manual?'6 3':'4 5'}/>{!mini&&<g fill={l.manual?'#d4c5f0':'#d5c99a'} fontSize="10"><line x1={right} x2={right+7} y1={y(l.price)} y2={yy} stroke={l.manual?'#c4b2ea':'#c4bb83'} opacity=".5"/><text x={right+10} y={yy-4}>{l.label}</text><text x={right+10} y={yy+8}>{l.price.toFixed(3)}</text></g>}</g>;})}
      {history.map((r,i)=>{const up=Number(r.close)>=Number(r.open),color=up?'#ca8272':'#72a08e',xx=x(i),bw=Math.max(.8,step*.58);return <g key={r.date} data-candle-date={r.date}>
        <line x1={xx} x2={xx} y1={y(Number(r.high))} y2={y(Number(r.low))} stroke={color}/><rect x={xx-bw/2} width={bw} y={Math.min(y(Number(r.open)),y(Number(r.close)))} height={Math.max(1,Math.abs(y(Number(r.open))-y(Number(r.close))))} fill={color}/>
        <rect data-volume-date={r.date} x={xx-bw/2} y={volBottom-Number(r.volume_shares)/maxV*(volBottom-volTop)} width={bw} height={Number(r.volume_shares)/maxV*(volBottom-volTop)} fill={color} opacity=".75"/>
        {!mini&&i%Math.ceil(history.length/6)===0&&<text x={xx} y={HEIGHT-12} fill="#9bad9f" fontSize="11">{r.date.slice(5)}</text>}
      </g>;})}
      {!mini&&<><line x1={left} x2={right} y1={volTop-14} y2={volTop-14} stroke="#385044"/><text x={left} y={volTop-3} fill="#91a895" fontSize="10">成交量{history.length>0?' / 与K线逐日对应':''}</text>{pin(H,'H')}{pin(L,'L')}</>}
      {markers.map((m,i)=>{const xx=dateX(m.date);if(xx===null)return null;return <g key={i} data-evidence={m.label} data-evidence-date={m.date}><line x1={xx} x2={xx} y1={y(Number(axis!.price))-25-(i%3)*15} y2={y(Number(axis!.price))+4} stroke={m.color} opacity=".7"/><text x={xx} y={y(Number(axis!.price))-28-(i%3)*15} textAnchor="middle" fill={m.color} fontSize="10">{m.label}</text></g>;})}
      {!mini&&points.map((p,i)=>{const xx=dateX(p.date);return xx===null?null:<g key={i} data-manual-anchor={i}><circle cx={xx} cy={y(Number(p.price))} r="5" stroke="#d4c5f0" fill="#604e7c"/><text x={xx+8} y={y(Number(p.price))+15} fill="#d4c5f0" fontSize="12">人工{!i?'H':'第二点'} {p.price}</text></g>;})}
      {!mini&&hover!==null&&<line pointerEvents="none" x1={x(hover)} x2={x(hover)} y1={top} y2={volBottom} stroke="#b2c3b3" opacity=".35" strokeDasharray="3 3"/>}
    </svg>
  </div>;
}
