'use client';
import {useState,useEffect,useRef} from 'react';
import type {DailyCandle} from '@/lib/chip-market';
import type {Anchor,Axis,ManualPoint,PriceLine} from '@/lib/golden-workbench';
import {manualLines} from '@/lib/golden-workbench';
import {pickManualPoint,pointLabel,type SnapMode,type DrawingScale} from '@/lib/golden-drawing';
import type {PointerEvent as ReactPointerEvent} from 'react';

type Gesture={kind:'create'|'add'|'move';index:number;pointer:number;startX:number;startY:number;points:ManualPoint[];scale:DrawingScale;moved:boolean};

export function GoldenCanvas({history,lines=[],H,L,axis,focus='all',mini=false,drawing=false,field='auto',manualMode='HL',points=[],windowStart,onPointsChange,onCancel}:{history:DailyCandle[];lines?:PriceLine[];H?:Anchor|null;L?:Anchor|null;axis?:Axis|null;focus?:string;mini?:boolean;drawing?:boolean;field?:SnapMode;manualMode?:'HL'|'HM';points?:ManualPoint[];windowStart?:string|null;onPointsChange?:(p:ManualPoint[])=>void;onCancel?:()=>void}){
  const [hover,setHover]=useState<number|null>(null);
  const svgRef=useRef<SVGSVGElement>(null),active=useRef<Gesture|null>(null);
  const [gesture,setGesture]=useState<Gesture|null>(null),[snap,setSnap]=useState<ManualPoint|null>(null),[notice,setNotice]=useState('');
  const host=useRef<HTMLDivElement>(null),[width,setWidth]=useState(1100);
  useEffect(()=>{const node=host.current;if(!node||mini)return;const observer=new ResizeObserver(()=>setWidth(Math.max(320,node.clientWidth)));observer.observe(node);setWidth(Math.max(320,node.clientWidth));return()=>observer.disconnect();},[mini,history.length]);
  useEffect(()=>{const cancel=(e:KeyboardEvent)=>{if(e.key==='Escape'&&(active.current||drawing)){e.preventDefault();active.current=null;setGesture(null);setSnap(null);onCancel?.();}};document.addEventListener('keydown',cancel);return()=>document.removeEventListener('keydown',cancel);},[drawing,onCancel]);
  if(!history.length)return <div className="gw-no-chart">行情暂不可用，未补造K线。</div>;
  const W=mini?500:width,HEIGHT=mini?230:490,left=mini?12:22,right=mini?465:W-(W<620?105:160),top=mini?18:25,bottom=mini?170:350,volTop=mini?183:380,volBottom=mini?215:452;
  const shownPoints=gesture?.points??points,shownLines=gesture?[...lines.filter(l=>!l.manual),...manualLines(manualMode,shownPoints)]:lines;
  const prices=history.flatMap(r=>[Number(r.high),Number(r.low)]).concat(lines.map(l=>l.price));const lo=Math.min(...prices),hi=Math.max(...prices),pad=Math.max((hi-lo)*.08,hi*.002),min=gesture?.scale.min??lo-pad,max=gesture?.scale.max??hi+pad;
  const scale={left,right,top,bottom,min,max};
  const y=(p:number)=>bottom-(p-min)/(max-min)*(bottom-top),step=(right-left)/history.length,x=(i:number)=>left+(i+.5)*step;
  const dateX=(date:string)=>{const i=history.findIndex(r=>r.date===date);return i<0?null:x(i);};
  const maxV=Math.max(1,...history.map(r=>Number(r.volume_shares))),bar=hover===null?history.at(-1)!:history[Math.min(hover,history.length-1)]??history.at(-1)!;
  const pin=(p:Anchor|null|undefined,kind:string)=>{if(!p)return null;const xx=dateX(p.date);if(xx===null)return null;const highlight=focus===kind.toLowerCase()||focus==='all';return <g data-anchor={kind} data-date={p.date} data-price={p.price} opacity={highlight?1:.45}>
    {highlight&&[...(p.left_dates??[]),p.date,...(p.right_dates??[])].map(d=>{const dx=dateX(d);return dx===null?null:<rect key={d} x={dx-step/2} y={top} width={step} height={bottom-top} fill="#d2dfb9" opacity=".09"/>;})}
    <circle cx={xx} cy={y(Number(p.price))} r="4" fill="#e0e9cf"/><line x1={xx} x2={xx+10} y1={y(Number(p.price))} y2={y(Number(p.price))} stroke="#cdd7c0"/>
    <text x={Math.min(right-95,xx+10)} y={y(Number(p.price))+(kind==='H'?-12:20)} fill="#e2e9d5" fontSize="12">{kind} {p.price} / {p.date.slice(5)}</text>
    {highlight&&p.available_after_close_on&&dateX(p.available_after_close_on)!==null&&<g data-confirm-kind={kind} data-confirm-date={p.available_after_close_on}><line x1={dateX(p.available_after_close_on)!} x2={dateX(p.available_after_close_on)!} y1={top} y2={bottom} stroke="#b8c8a5" opacity=".3" strokeDasharray="3 4"/><text x={dateX(p.available_after_close_on)!+3} y={bottom-5} fill="#b8c8a5" fontSize="10">{p.available_after_close_on.slice(5)}确认{kind}</text></g>}
  </g>;};
  const coordinates=(e:{clientX:number;clientY:number})=>{const svg=svgRef.current,matrix=svg?.getScreenCTM();if(!svg||!matrix)return null;const point=svg.createSVGPoint();point.x=e.clientX;point.y=e.clientY;const p=point.matrixTransform(matrix.inverse());return {x:p.x,y:p.y,unitsPerPixel:1/Math.hypot(matrix.c,matrix.d)};};
  function clearGesture(){active.current=null;setGesture(null);setSnap(null);}
  function begin(e:ReactPointerEvent,index?:number){
    if(mini||!onPointsChange||e.button!==0||active.current||(!drawing&&index===undefined))return;
    const xy=coordinates(e);if(!xy)return;
    const picked=pickManualPoint(history,scale,xy.x,xy.y,e.shiftKey?'custom':field,xy.unitsPerPixel);if(!picked)return;
    e.preventDefault();e.stopPropagation();setNotice('');
    const value:Gesture={kind:index!==undefined?'move':points.length?'add':'create',index:index??points.length,pointer:e.pointerId,startX:e.clientX,startY:e.clientY,points:index!==undefined?[...points]:points.length?[points[0],picked]:[picked],scale,moved:false};
    active.current=value;setGesture(value);svgRef.current?.setPointerCapture(e.pointerId);setSnap(index!==undefined?points[index]:picked);
  }
  function move(e:ReactPointerEvent){
    if(mini)return;const xy=coordinates(e);if(!xy)return;
    setHover(xy.x>=left&&xy.x<=right?Math.max(0,Math.min(history.length-1,Math.floor((xy.x-left)/step))):null);
    const current=active.current;
    const bounded=current?{...xy,x:Math.max(left,Math.min(right,xy.x)),y:Math.max(top,Math.min(bottom,xy.y))}:xy;
    const picked=pickManualPoint(history,current?.scale??scale,bounded.x,bounded.y,e.shiftKey?'custom':field,xy.unitsPerPixel);
    if(!current){setSnap(drawing?picked:null);return;}
    if(current.pointer!==e.pointerId||!picked)return;
    const moved=current.moved||Math.hypot(e.clientX-current.startX,e.clientY-current.startY)>5;
    const next=[...current.points];
    if(current.kind==='move'){if(moved)next[current.index]=picked;}
    else if(current.kind==='add')next[1]=picked;
    else if(moved)next[1]=picked;
    const updated={...current,points:next,moved};active.current=updated;setGesture(updated);setSnap(picked);
  }
  function finish(e:ReactPointerEvent){
    if(!active.current||active.current.pointer!==e.pointerId)return;move(e);const current=active.current!;
    const next=current.points;clearGesture();
    if(svgRef.current?.hasPointerCapture(e.pointerId))svgRef.current.releasePointerCapture(e.pointerId);
    if(next.length===2&&Number(next[0].price)<=Number(next[1].price)){setNotice('H需高于L / M，本次调整已取消。');return;}
    if(current.kind!=='move'||current.moved)onPointsChange?.(next);
  }
  function nudge(key:string,index:number){
    const point=points[index],at=history.findIndex(r=>r.date===point.date);if(!point||at<0)return;
    let next=point;
    if(key==='ArrowLeft'||key==='ArrowRight'){
      const row=history[at+(key==='ArrowLeft'?-1:1)];if(!row)return;
      next={...point,date:row.date,price:point.field==='custom'?point.price:String(row[point.field as keyof DailyCandle])};
    }else{
      const candidates=Array.from(new Set(['open','high','low','close'].map(f=>Number(history[at][f as keyof DailyCandle])))).sort((a,b)=>a-b);
      const price=key==='ArrowUp'?candidates.find(p=>p>Number(point.price)):candidates.toReversed().find(p=>p<Number(point.price));
      if(price===undefined)return;const matched=['high','low','open','close'].find(f=>Number(history[at][f as keyof DailyCandle])===price)!;
      next={date:point.date,field:matched,price:String(history[at][matched as keyof DailyCandle])};
    }
    const changed=points.map((p,i)=>i===index?next:p);
    if(changed.length===2&&Number(changed[0].price)<=Number(changed[1].price)){setNotice('H需高于L / M，本次调整已取消。');return;}
    onPointsChange?.(changed);
  }
  const markers=axis&&(!mini)&&(focus==='m'||focus==='all')?[
    ...Array.from(new Set(axis.source_fields.map(p=>p.date))).slice(0,4).map(date=>({date,label:'源',color:'#8ea2b7'})),
    ...axis.supports.slice(0,4).map(r=>({date:r.date,label:'支',color:'#b5c98b'})),
    {date:axis.break_event.date,label:'收破',color:'#e1b67d'},
    ...axis.later_touch_below_events.slice(0,4).map(r=>({date:r.date,label:'抽',color:'#d9a9ad'})),
  ]:[];
  const labelYs=shownLines.map((l,i)=>({i,y:y(l.price)})).sort((a,b)=>a.y-b.y);
  for(let i=0;i<labelYs.length;i++)labelYs[i].y=Math.max(labelYs[i].y,i?labelYs[i-1].y+26:top+8);
  for(let i=labelYs.length-1;i>=0;i--)labelYs[i].y=Math.min(labelYs[i].y,i<labelYs.length-1?labelYs[i+1].y-26:bottom-8);
  return <div ref={host} className={`gw-canvas ${mini?'gw-canvas-mini':''} ${drawing?'gw-drawing':''}`}>
    {!mini&&<><div className="gw-readout"><b>{bar.date}</b>{(['open','high','low','close'] as const).map((k,i)=><span key={k}>{['开','高','低','收'][i]} {bar[k]}</span>)}<span>量 {Number(bar.volume_shares).toLocaleString()}</span></div><div className="gw-snap-readout" aria-live="off">{notice||((drawing||gesture)&&snap?`${snap.field==='custom'?'自由价位':'已吸附'} · ${snap.date} · ${pointLabel(snap.field)} ${snap.price}`:drawing?'在H处按下并拖至L / M，也可依次点两下。':points.length?'拖动紫色圆点调整人工画线；Shift临时自由取价。':'新增画线后，在图上拖出H和L / M。')}</div></>}
    <svg ref={svgRef} viewBox={`0 0 ${W} ${HEIGHT}`} role={mini?'img':'group'} aria-label={mini?'自动画格概览':'自动选点与人工黄金分割K线及成交量'} onPointerDown={e=>{if(points.length<2)begin(e);}} onPointerMove={move} onPointerUp={finish} onPointerCancel={clearGesture} onLostPointerCapture={()=>{if(active.current)clearGesture();}} onPointerLeave={()=>{if(!active.current){setHover(null);setSnap(null);}}} style={{touchAction:drawing?'none':'pan-y'}}>
      <rect width={W} height={HEIGHT} fill="#14261f"/>
      {[0,1,2,3,4].map(i=>{const p=min+(max-min)*i/4;return <g key={i}><line x1={left} x2={right} y1={y(p)} y2={y(p)} stroke="#33483b" strokeDasharray="2 5"/>{!mini&&<text x={right+12} y={y(p)+4} fill="#879e8e" fontSize="11">{p.toFixed(2)}</text>}</g>;})}
      {windowStart&&dateX(windowStart)!==null&&history[0].date<windowStart&&<g><line x1={dateX(windowStart)!-step/2} x2={dateX(windowStart)!-step/2} y1={top} y2={volBottom} stroke="#96ad9a" strokeDasharray="4 4"/><text x={dateX(windowStart)!+4} y={top+12} fill="#b2c2b2" fontSize="10">←背景 / 60根计算→</text></g>}
      {shownLines.map((l,i)=>{const yy=labelYs.find(v=>v.i===i)!.y;return <g key={i} data-grid-price={l.price} data-manual={!!l.manual}><line x1={left} x2={right} y1={y(l.price)} y2={y(l.price)} stroke={l.manual?'#c4b2ea':'#c4bb83'} strokeWidth={l.manual?1.5:1} strokeDasharray={l.manual?'6 3':'4 5'}/>{!mini&&<g fill={l.manual?'#d4c5f0':'#d5c99a'} fontSize="10"><line x1={right} x2={right+7} y1={y(l.price)} y2={yy} stroke={l.manual?'#c4b2ea':'#c4bb83'} opacity=".5"/><text x={right+10} y={yy-4}>{l.label}</text><text x={right+10} y={yy+8}>{l.price.toFixed(3)}</text></g>}</g>;})}
      {history.map((r,i)=>{const up=Number(r.close)>=Number(r.open),color=up?'#ca8272':'#72a08e',xx=x(i),bw=Math.max(.8,step*.58);return <g key={r.date} data-candle-date={r.date}>
        <line x1={xx} x2={xx} y1={y(Number(r.high))} y2={y(Number(r.low))} stroke={color}/><rect x={xx-bw/2} width={bw} y={Math.min(y(Number(r.open)),y(Number(r.close)))} height={Math.max(1,Math.abs(y(Number(r.open))-y(Number(r.close))))} fill={color}/>
        <rect data-volume-date={r.date} x={xx-bw/2} y={volBottom-Number(r.volume_shares)/maxV*(volBottom-volTop)} width={bw} height={Number(r.volume_shares)/maxV*(volBottom-volTop)} fill={color} opacity=".75"/>
        {!mini&&i%Math.ceil(history.length/6)===0&&<text x={xx} y={HEIGHT-12} fill="#9bad9f" fontSize="11">{r.date.slice(5)}</text>}
      </g>;})}
      {!mini&&<><line x1={left} x2={right} y1={volTop-14} y2={volTop-14} stroke="#385044"/><text x={left} y={volTop-3} fill="#91a895" fontSize="10">成交量{history.length>0?' / 与K线逐日对应':''}</text>{pin(H,'H')}{pin(L,'L')}</>}
      {markers.map((m,i)=>{const xx=dateX(m.date);if(xx===null)return null;return <g key={i} data-evidence={m.label} data-evidence-date={m.date}><line x1={xx} x2={xx} y1={y(Number(axis!.price))-25-(i%3)*15} y2={y(Number(axis!.price))+4} stroke={m.color} opacity=".7"/><text x={xx} y={y(Number(axis!.price))-28-(i%3)*15} textAnchor="middle" fill={m.color} fontSize="10">{m.label}</text></g>;})}
      {!mini&&shownPoints.map((p,i)=>{const xx=dateX(p.date),name=!i?'H':manualMode==='HL'?'L':'M';return xx===null?null:<g key={i} className="gw-drag-anchor" data-manual-anchor={i} data-date={p.date} data-price={p.price} data-field={p.field} role="button" tabIndex={0} aria-label={`人工${name}锚点 ${p.date} ${p.price}，拖动或方向键修改`} onPointerDown={e=>begin(e,i)} onKeyDown={e=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.stopPropagation();e.preventDefault();nudge(e.key,i);}}}><circle cx={xx} cy={y(Number(p.price))} r="18" fill="transparent"/><circle cx={xx} cy={y(Number(p.price))} r={gesture?.index===i?9:7} stroke="#e5d5ff" strokeWidth="2" fill="#71578e"/><text pointerEvents="none" x={Math.min(right-110,xx+12)} y={y(Number(p.price))+(i?-17:22)} fill="#e2d2fa" fontSize="11">人工{name}</text></g>;})}
      {!mini&&snap&&(drawing||gesture)&&dateX(snap.date)!==null&&<g pointerEvents="none" data-snap-field={snap.field}><line x1={left} x2={right} y1={y(Number(snap.price))} y2={y(Number(snap.price))} stroke="#d4c5f0" opacity=".4"/><circle cx={dateX(snap.date)!} cy={y(Number(snap.price))} r="11" fill="none" stroke="#dfc5ff" strokeDasharray={snap.field==='custom'?'2 3':undefined}/></g>}
      {!mini&&hover!==null&&<line pointerEvents="none" x1={x(hover)} x2={x(hover)} y1={top} y2={volBottom} stroke="#b2c3b3" opacity=".35" strokeDasharray="3 3"/>}
    </svg>
  </div>;
}
