import type {MarketManifest,MarketState,DailyCandle} from './chip-market';

export const goldenVersion='golden-grid.research60-5.tiers20-50.v1';
export type GoldenGeometry={H:string;L:string;H_date:string;L_date:string;ratio:string;midpoint:string;upper:string;drawdown_percent:string;close:string;main_midpoint:string;quarter:string;eighth:string;close_side:'ABOVE'|'BELOW'|'AT';distance_percent:string;main_space_position_percent:string;intraday_touch:boolean;crossing:'UP'|'DOWN'|'NONE'|'UNKNOWN';comparison:string};
export type GoldenRow={security_id:string;name:string;as_of:string;version:string;status:MarketState;reason:string;geometry:GoldenGeometry|null;known_at:null;research_only:true};
export type GoldenMarket={schema:'stock.golden-grid.market.v1';dataset_id:string;as_of:string;version:string;universe_total:number;universe_coverage:string;counts:Record<MarketState,number>;rows:GoldenRow[]};
const numeric=(v:unknown)=>typeof v==='string'&&/^-?\d+(?:\.\d+)?$/.test(v)&&Number.isFinite(Number(v));
export function parseGoldenMarket(input:unknown,manifest:MarketManifest):GoldenMarket{
  const v=input as GoldenMarket;
  if(!v||v.schema!=='stock.golden-grid.market.v1'||v.version!==goldenVersion||v.dataset_id!==manifest.dataset_id||v.as_of!==manifest.as_of||v.universe_total!==manifest.universe_total||!Array.isArray(v.rows)||v.rows.length!==v.universe_total)throw Error('格子统计与当前行情批次不一致。');
  const counts={PASS:0,FAIL:0,UNKNOWN:0},codes=new Set<string>();
  for(const row of v.rows){
    if(!row||!/^\d{6}$/.test(row.security_id)||codes.has(row.security_id)||typeof row.name!=='string'||row.as_of!==v.as_of||row.version!==goldenVersion||!['PASS','FAIL','UNKNOWN'].includes(row.status)||typeof row.reason!=='string'||row.known_at!==null||row.research_only!==true)throw Error('格子统计行不完整。');
    const g=row.geometry;
    if(row.status==='PASS'&&!g)throw Error('有效格子缺少高低点。');
    if(g&&(!['H','L','midpoint','upper','close','main_midpoint','quarter','eighth','drawdown_percent','distance_percent','main_space_position_percent'].every(k=>numeric(g[k as keyof GoldenGeometry]))||!(Number(g.H)>Number(g.L)&&Number(g.L)>0)||!['0.125','0.25','0.5'].includes(g.ratio)||!/^\d{4}-\d{2}-\d{2}$/.test(g.H_date)||!(g.H_date<g.L_date&&g.L_date<=v.as_of)||!['ABOVE','BELOW','AT'].includes(g.close_side)||typeof g.intraday_touch!=='boolean'||!['UP','DOWN','NONE','UNKNOWN'].includes(g.crossing)))throw Error('格子价格或日期不完整。');
    if(g&&g.crossing!=='UNKNOWN'&&(row.status!=='PASS'||g.comparison!=='SAME_VALID_STRUCTURE'))throw Error('跨线缺少相同有效结构依据。');
    counts[row.status]++;codes.add(row.security_id);
  }
  if(!v.counts||(['PASS','FAIL','UNKNOWN'] as const).some(k=>counts[k]!==v.counts[k]))throw Error('全市场格子统计计数不一致。');
  return v;
}
export const goldenReasons:Record<string,string>={
  DECLARED_60_5_STRUCTURE:'研究版有效结构',BELOW_MINIMUM_20_PERCENT_DEPTH:'跌幅不足20%',LATER_LOW_BREACH:'确认后再次跌破低点',
  MISSING_OR_INVALID_PRICE_SESSION:'所需交易日行情缺失或异常',NO_LOW_STRICTLY_AFTER_HIGH:'高点后尚无可用低点',
  INSUFFICIENT_60_5_WARMUP:'所需行情根数不足',HISTORY_UNAVAILABLE:'历史行情待补齐',
  STRUCTURE_CHANGED:'格子选点或分层已变化',STRUCTURE_UNAVAILABLE_OR_INVALID:'前后结构尚不可比较',SAME_VALID_STRUCTURE:'与前一日同一有效格子',
};
export const goldenReason=(value:string)=>goldenReasons[value]??value;
export function parseGoldenHistory(input:unknown,code:string,manifest:MarketManifest):{security_id:string;history:DailyCandle[]}{
  const v=input as {schema:string;dataset_id:string;as_of:string;security_id:string;history:DailyCandle[]};
  if(!v||v.schema!=='stock.chips.market.stock.v1'||v.dataset_id!==manifest.dataset_id||v.as_of!==manifest.as_of||v.security_id!==code||!Array.isArray(v.history)||v.history.length>10000)throw Error('日K线与当前股票或批次不一致。');
  let previous='';
  for(const row of v.history){
    if(!row||!/^\d{4}-\d{2}-\d{2}$/.test(row.date)||row.date<=previous||row.date>manifest.as_of||!['open','high','low','close','volume_shares','amount_cny','turnover_percent'].every(k=>numeric(row[k as keyof DailyCandle])))throw Error('日K线日期或数值不完整。');
    const o=Number(row.open),h=Number(row.high),l=Number(row.low),c=Number(row.close);
    if(!(0<l&&l<=Math.min(o,c)&&Math.max(o,c)<=h)||Number(row.volume_shares)<0||Number(row.amount_cny)<0||Number(row.turnover_percent)<0)throw Error('日K线或成交量异常。');
    previous=row.date;
  }
  return {security_id:code,history:v.history};
}
export function goldenLines(g:GoldenGeometry){
  const entries=[{price:g.H,label:'原高点 H',active:false},{price:g.L,label:'原低点 L',active:false},{price:g.main_midpoint,label:'50%',active:false},{price:g.quarter,label:'25%',active:false},{price:g.eighth,label:'12.5%',active:false},{price:g.upper,label:'子空间上沿',active:false},{price:g.midpoint,label:'选中轴',active:true}];
  const lines=new Map<number,{price:string;label:string;active:boolean}>();
  for(const entry of entries){const key=Number(entry.price),before=lines.get(key);lines.set(key,before?{...before,label:before.label+' / '+entry.label,active:before.active||entry.active}:entry);}
  return [...lines.values()];
}
