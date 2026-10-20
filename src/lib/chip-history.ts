import { parseChipSample, type ChipSample } from './chips.ts';
import type { MarketStock } from './chip-market.ts';

export type ChipHistory = ChipSample & {
  schema: 'stock.chips.history.v1'; dataset_id:string; published_as_of:string;
  selected_date:string; previous_date:string|null;
  comparison_status:'READY'|'NO_PREVIOUS_LISTED_SESSION'|'PREVIOUS_SESSION_UNAVAILABLE';
  reasons:string[];
};
const day=(v:unknown):v is string=>typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v)&&!Number.isNaN(Date.parse(v+'T00:00:00Z'))&&new Date(v+'T00:00:00Z').toISOString().slice(0,10)===v;
export function parseChipHistory(input:unknown,stock:MarketStock,date:string):ChipHistory{
  if(!input||typeof input!=='object'||Array.isArray(input))throw Error('历史筹码格式不正确。');
  const v=input as Record<string,unknown>;
  if(v.schema!=='stock.chips.history.v1'||v.dataset_id!==stock.dataset_id||v.security_id!==stock.security_id
    ||v.published_as_of!==stock.as_of||v.selected_date!==date||!day(date)||date>stock.as_of
    ||(v.previous_date!==null&&(!day(v.previous_date)||v.previous_date>=date))
    ||!['READY','NO_PREVIOUS_LISTED_SESSION','PREVIOUS_SESSION_UNAVAILABLE'].includes(String(v.comparison_status))
    ||!Array.isArray(v.reasons)||!v.reasons.every(r=>typeof r==='string')||v.unchanged_threshold!==null)throw Error('历史筹码与所选股票、日期或批次不一致。');
  const dates=v.comparison_status==='READY'&&v.previous_date?[String(v.previous_date),date]:[date];
  if(v.comparison_status==='READY'&&!v.previous_date)throw Error('历史筹码缺少前一交易日。');
  const sample=parseChipSample(v,stock.security_id,dates);
  if(Object.keys(sample.snapshots).length!==dates.length)throw Error('历史筹码混入了其他日期。');
  const rawSnapshots=v.snapshots as Record<string,Record<string,unknown>>;
  for(const d of dates){
    if(rawSnapshots[d].source_date!==d||!day(rawSnapshots[d].window_source_start_date)||String(rawSnapshots[d].window_source_start_date)>d)throw Error('历史筹码回放窗口不正确。');
  }
  if(dates.length===2&&rawSnapshots[dates[0]].window_source_start_date!==rawSnapshots[date].window_source_start_date)throw Error('前日与所选日期的筹码不在同一回放窗口。');
  return v as unknown as ChipHistory;
}

export function publishedChipHistory(stock:MarketStock,previousDate:string):ChipHistory{
  const previous=stock.snapshots[previousDate];
  return {schema:'stock.chips.history.v1',dataset_id:stock.dataset_id,published_as_of:stock.as_of,
    security_id:stock.security_id,name:stock.name,selected_date:stock.as_of,
    previous_date:previous?previousDate:null,comparison_status:previous?'READY':'NO_PREVIOUS_LISTED_SESSION',
    snapshots:stock.snapshots,reasons:[]};
}

export function parseChipHistoryBatch(input:unknown,stock:MarketStock,anchor:string):Record<string,ChipHistory>{
  if(!input||typeof input!=='object')throw Error('历史筹码批次格式不正确。');
  const v=input as Record<string,unknown>;
  const dates=[...new Set([...stock.history.map(r=>r.date),stock.as_of])].sort(),index=dates.indexOf(anchor);
  const expected=dates.slice(Math.floor(index/8)*8,Math.floor(index/8)*8+8);
  if(index<0||v.schema!=='stock.chips.history.batch.v1'||v.dataset_id!==stock.dataset_id||v.security_id!==stock.security_id||v.published_as_of!==stock.as_of||v.selected_date!==anchor||JSON.stringify(v.dates)!==JSON.stringify(expected)
    ||!v.frames||typeof v.frames!=='object'||Array.isArray(v.frames)||!v.errors||typeof v.errors!=='object'||Array.isArray(v.errors))throw Error('历史筹码批次与所选股票、日期不一致。');
  const frames=v.frames as Record<string,unknown>,errors=v.errors as Record<string,unknown>,result:Record<string,ChipHistory>={};
  if([...Object.keys(frames),...Object.keys(errors)].sort().join()!==[...expected].sort().join()||Object.values(errors).some(e=>typeof e!=='string'))throw Error('历史筹码批次日期不完整。');
  for(const [day,value] of Object.entries(frames))result[day]=parseChipHistory(value,stock,day);
  return result;
}
