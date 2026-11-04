import {PRICE_GEOMETRY,priceScaleFromBounds} from './price-geometry.ts';
import type {DailyCandle} from './chip-market';
import type {ManualPoint} from './golden-workbench';

export type SnapMode='auto'|'custom'|'open'|'high'|'low'|'close';
export type ManualDraft={id:string;title:string;mode:'HL'|'HM';points:ManualPoint[];note:string;price_geometry:typeof PRICE_GEOMETRY};
export const blankDraft=():ManualDraft=>({id:'',title:'人工画线',mode:'HL',points:[],note:'',price_geometry:PRICE_GEOMETRY});
export type DraftHistory={present:ManualDraft;past:ManualDraft[];future:ManualDraft[]};
export function draftHistory(state:DraftHistory,action:{type:'edit'|'reset';value:ManualDraft}|{type:'undo'|'redo'}):DraftHistory{
 if(action.type==='reset')return {present:action.value,past:[],future:[]};
 if(action.type==='edit')return JSON.stringify(action.value)===JSON.stringify(state.present)?state:{present:action.value,past:[...state.past,state.present].slice(-50),future:[]};
 if(action.type==='undo'&&state.past.length)return {present:state.past.at(-1)!,past:state.past.slice(0,-1),future:[state.present,...state.future]};
 if(action.type==='redo'&&state.future.length)return {present:state.future[0],past:[...state.past,state.present],future:state.future.slice(1)};
 return state;
}
export type DrawingScale={left:number;right:number;top:number;bottom:number;min:number;max:number};
export function pickManualPoint(history:DailyCandle[],scale:DrawingScale,x:number,y:number,mode:SnapMode,svgUnitsPerScreenPixel=1):ManualPoint|null{
 if(!history.length||![scale.min,scale.max,scale.top,scale.bottom,x,y,svgUnitsPerScreenPixel].every(Number.isFinite)||svgUnitsPerScreenPixel<=0||x<scale.left||x>scale.right||y<scale.top||y>scale.bottom||scale.min<=0||scale.max<=scale.min||scale.bottom<=scale.top)return null;
 const index=Math.max(0,Math.min(history.length-1,Math.floor((x-scale.left)/(scale.right-scale.left)*history.length)));
 const row=history[index],projection=priceScaleFromBounds(scale),price=projection.priceAt(y);
 const fields=['high','low','open','close'] as const;
 if(mode!=='auto'&&mode!=='custom')return {date:row.date,field:mode,price:String(row[mode])};
 if(mode==='auto'){
  const nearest=fields.map(field=>({field,distance:Math.abs(projection.y(Number(row[field]))-y)})).sort((a,b)=>a.distance-b.distance)[0];
  if(nearest.distance<=12*svgUnitsPerScreenPixel)return {date:row.date,field:nearest.field,price:String(row[nearest.field])};
 }
 return {date:row.date,field:'custom',price:Math.max(.00000001,price).toFixed(8).replace(/\.?0+$/,'')};
}
export const pointLabel=(field:string)=>({open:'开盘',high:'最高',low:'最低',close:'收盘',custom:'自由价位'} as Record<string,string>)[field]??field;
