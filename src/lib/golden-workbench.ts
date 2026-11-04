import {geometricLevel,reflectedPrice} from './price-geometry.ts';
import type {DailyCandle} from './chip-market';
export type Anchor={id?:string;date:string;price:string;field:string;available_after_close_on?:string;left_dates?:string[];right_dates?:string[]};
export type Axis={id:string;price:string;H_ref:Anchor;source_fields:{date:string;field:string;value:string}[];supports:DailyCandle[];break_event:DailyCandle;available_after_close_on:string|null;reclaim_observed_on:string|null;later_touch_below_events:DailyCandle[];B_conditional:string;status:string};
export type AutoResult={H:Anchor|null;L:Anchor|null;H_unknown_reasons:string[];L_unknown_reasons:string[];h_nominees:Anchor[];l_nominees:Anchor[];pending_turns:(Anchor&{kind:string})[];price_groups:{price:string;records:Axis[];qualified_formation_records:number;reclaim_observed_records:number}[];retained_current_H_records:number;other_H_formation_records:number};
export type ExampleSummary={symbol:string;name:string;kind:'INDEX'|'EQUITY';as_of:string;last_bar_date:string|null;freshness:string;window_start:string|null;error:string|null;history:DailyCandle[];H:Anchor|null;L:Anchor|null;M_count:number;formation_count:number};
export type WatchManifest={price_geometry?:'log-price.v1';schema:string;version:string;dataset_id:string;source_dataset_id:string;as_of:string;generated_at:string;policy_sha256:string;calculation_window:60;rows:ExampleSummary[];archives:{as_of:string;dataset_id:string;generated_at?:string}[]};
export type Example={price_geometry?:'log-price.v1';schema:string;dataset_id:string;symbol:string;name:string;kind:'INDEX'|'EQUITY';as_of:string;history:DailyCandle[];window_start:string|null;last_bar_date:string|null;freshness:string;error:string|null;result:AutoResult|null;source?:{provider?:string;captured_at?:string}|null};
export type ManualPoint={date:string;field:string;price:string};
export type Drawing={id:string;title:string;mode:'HL'|'HM';points:ManualPoint[];note:string;price_geometry?:'log-price.v1';as_of:string;dataset_id:string;owner:string;updated_at:string;created_at:string;history:Omit<Drawing,'history'>[]};
export type DrawingSnapshot=Omit<Drawing,'history'>;
export type AutoContext={price_geometry?:'log-price.v1';symbol:string;dataset_id:string;as_of:string;policy_sha256:string;auto_mode:'HL'|'HM';axis_id:string;H:Anchor|null;L:Anchor|null;M: {id:string;price:string;B_conditional:string}|null};
export type ReviewRound={owner:string;as_of:string;dataset_id:string;status:string;note:string;updated_at:string;axis_id?:string;auto_mode?:'HL'|'HM';round_id?:string;drawing_snapshot?:DrawingSnapshot;auto_context?:AutoContext};
export type Review=ReviewRound&{history?:ReviewRound[]};
export type Annotations={schema:string;symbol:string;revision:number;drawings:Drawing[];reviews:Review[]};
export type PriceLine={price:number;label:string;manual?:boolean};
function geometry(v:unknown){if(v!==undefined&&v!=='log-price.v1')throw Error('未知价格几何版本');}
const record=(v:unknown):Record<string,unknown>=>{if(!v||typeof v!=='object'||Array.isArray(v))throw Error('核对数据格式错误');return v as Record<string,unknown>;};
function candles(v:unknown){if(!Array.isArray(v)||v.length>120)throw Error('K线数量错误');let last='';for(const r of v){if(!r||typeof r.date!=='string'||r.date<=last||!/^\d{4}-\d{2}-\d{2}$/.test(r.date))throw Error('K线日期错误');const [o,h,l,c,volume]=['open','high','low','close','volume_shares'].map(k=>Number(r[k]));if(![o,h,l,c,volume].every(Number.isFinite)||l<=0||l>Math.min(o,c)||h<Math.max(o,c)||volume<0)throw Error('K线价格或量无效');last=r.date;}}
export function parseWatchManifest(v:unknown):WatchManifest{const x=record(v);geometry(x.price_geometry);if(x.schema!=='stock.golden.watchlist.v1'||x.version!=='golden-workbench.auto20.v1'||!Array.isArray(x.rows)||x.rows.length!==20||x.calculation_window!==60||!Array.isArray(x.archives)||!/^\w{16}$/.test(String(x.dataset_id)))throw Error('每日20样本目录不完整');const seen=new Set();for(const r of x.rows){if(seen.has(r.symbol))throw Error('样本重复');seen.add(r.symbol);candles(r.history);}return x as unknown as WatchManifest;}
export function parseExample(v:unknown,manifest:WatchManifest,symbol:string):Example{const x=record(v);geometry(x.price_geometry);if(x.price_geometry!==manifest.price_geometry)throw Error('样本几何版本与批次不一致');if(x.schema!=='stock.golden.example.v1'||x.dataset_id!==manifest.dataset_id||x.symbol!==symbol||x.as_of!==manifest.as_of)throw Error('样本日期或身份不一致');candles(x.history);const r=x.result as AutoResult|null;if(r&&(!Array.isArray(r.price_groups)||r.price_groups.some(g=>!Number.isFinite(Number(g.price))||!g.records.length)))throw Error('中轴证据不完整');return x as unknown as Example;}
export function autoLines(r:AutoResult|null,mode:'HL'|'HM',axis:Axis|null):PriceLine[]{if(!r?.H)return [];const H=Number(r.H.price);const lines:PriceLine[]=[{price:H,label:'H 高点'}];if(mode==='HL'&&r.L){const L=Number(r.L.price);lines.push({price:L,label:'L 低点'},...[.5,.25,.125].map(ratio=>({price:geometricLevel(L,H,ratio),label:`${ratio*100}% 等分`})));}if(mode==='HM'&&axis){lines.push({price:Number(axis.price),label:'M 候选'},{price:reflectedPrice(H,Number(axis.price)),label:'M²÷H 条件下沿'});}return lines;}
export function manualLines(mode:'HL'|'HM',points:ManualPoint[]):PriceLine[]{if(points.length!==2)return [];const [H,V]=points.map(p=>Number(p.price));if(!Number.isFinite(H)||!Number.isFinite(V)||H<=V||V<=0)return [];return mode==='HM'?[{price:H,label:'人工 H',manual:true},{price:V,label:'人工 M',manual:true},{price:reflectedPrice(H,V),label:'人工 M²÷H',manual:true}]:[{price:H,label:'人工 H',manual:true},{price:V,label:'人工 L',manual:true},...[.5,.25,.125].map(ratio=>({price:geometricLevel(V,H,ratio),label:`人工 ${ratio*100}%`,manual:true}))];}
export const reviewLabels:Record<string,string>={CORRECT:'画法符合',H_ISSUE:'高点有问题',L_ISSUE:'低点有问题',M_ISSUE:'中轴有问题',UNCERTAIN:'暂不确定'};

export function parseAnnotations(v:unknown):Annotations {
 const x=record(v);
 if(x.schema!=='stock.golden.annotations.v1'||!Array.isArray(x.drawings)||!Array.isArray(x.reviews))throw Error('核对记录不完整');
 const checkDrawing=(value:unknown)=>{const item=record(value);geometry(item.price_geometry);};
 const checkReview=(value:unknown)=>{const item=record(value);if(item.drawing_snapshot!==undefined)checkDrawing(item.drawing_snapshot);if(item.auto_context!==undefined)geometry(record(item.auto_context).price_geometry);};
 for(const value of x.drawings){const item=record(value);checkDrawing(item);if(Array.isArray(item.history))item.history.forEach(checkDrawing);}
 for(const value of x.reviews){const item=record(value);checkReview(item);if(Array.isArray(item.history))item.history.forEach(checkReview);}
 return x as unknown as Annotations;
}
