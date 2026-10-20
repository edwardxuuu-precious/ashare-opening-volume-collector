/** Saved-input browser replay of eastmoney_model.py (same IEEE-754 order).
 * Complete 210-bar windows only. Missing sessions and archive warm-up fall back
 * to the authenticated server; the sealed latest daily comparison is untouched.
 */
import type {DailyCandle,MarketStock} from './chip-market.ts';
import type {ChipSnapshot} from './chips.ts';
import type {ChipHistory} from './chip-history.ts';
import calendars from './chip-history-calendar.json' with {type:'json'};

const VERSION='chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1';
const SERVER_MODEL_SHA256='f95add1f60ec5fe4fdd5c9e9d6ffc8844b91283307b9525c61e8ba18a3529896';
type Snapshot=ChipSnapshot&{source_date:string;model_version:string;window_source_start_date:string;raw_node_masses:string[];peak_node_indices:number[]};
const sum=(values:number[])=>{let result=0;for(const v of values)result+=v;return result;};
// Decimal(string)*100 truncated toward zero, as in Python's interval proof.
function cents(text:string){
  const m=/^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text);
  if(!m)throw Error('EXACT_DECIMAL_REQUIRED');
  const digits=(m[2]+(m[3]??'')).replace(/^0+/,'')||'0',power=2+Number(m[4]??0)-(m[3]?.length??0);
  const integer=power>=0?digits+'0'.repeat(power):digits.slice(0,Math.max(0,digits.length+power))||'0';
  const value=Number((m[1]==='-'?'-':'')+integer);
  if(!Number.isSafeInteger(value))throw Error('BOUNDED_PRICE_REQUIRED');return value;
}
function previousSession(day:string):string|null{
  const value=new Date(day+'T00:00:00Z');
  const currentClosed=(calendars as Record<string,string[][]>)[day.slice(0,4)],currentMd=day.slice(5);
  if(!currentClosed||value.getUTCDay()%6===0||currentClosed.some(([a,b])=>a<=currentMd&&currentMd<=b))return null;
  for(let i=0;i<20;i++){
    value.setUTCDate(value.getUTCDate()-1);const key=value.toISOString().slice(0,10),year=key.slice(0,4),md=key.slice(5);
    const closed=(calendars as Record<string,string[][]>)[year];if(!closed)return null;
    if(value.getUTCDay()%6!==0&&!closed.some(([a,b])=>a<=md&&md<=b))return key;
  }return null;
}
function calculate(bars:DailyCandle[],code:string,start:string,rows:number):Snapshot{
  const low=Math.min(...bars.map(b=>Number(b.low))),high=Math.max(...bars.map(b=>Number(b.high))),step=Math.max(.01,(high-low)/149);
  const lowCent=Math.min(...bars.map(b=>cents(b.low))),spanCent=Math.max(149,Math.max(...bars.map(b=>cents(b.high)))-lowCent);
  const raw=Array<number>(150).fill(0),negativeEndpoints=new Set<number>();
  for(const b of bars){
    const op=Number(b.open),close=Number(b.close),hi=Number(b.high),lo=Number(b.low),apex=(op+close+hi+lo)/4,a=Math.min(1,2*Number(b.turnover_percent)/100);
    if(![op,close,hi,lo,a].every(Number.isFinite)||lo<=0||lo>Math.min(op,close)||hi<Math.max(op,close)||a<0)throw Error('INVALID_SOURCE_BAR');
    for(let i=0;i<150;i++)raw[i]*=1-a;
    if(hi===lo){const i=Math.floor((apex-low)/step);if(i<0||i>=150)throw Error('NODE_INDEX_OUT_OF_RANGE');raw[i]+=(149/2)*a;}
    else{
      const height=2/(hi-lo);
      for(let i=Math.ceil((lo-low)/step);i<=Math.floor((hi-low)/step);i++){
        if(i<0||i>=150)throw Error('NODE_INDEX_OUT_OF_RANGE');const price=low+step*i;
        const shape=price<=apex?(Math.abs(apex-lo)<1e-8?height:(price-lo)/(apex-lo)*height):(Math.abs(hi-apex)<1e-8?height:(hi-price)/(hi-apex)*height);
        if(shape<0){if(lowCent*149+spanCent*i!==cents(price<=apex?b.low:b.high)*149)throw Error('NEGATIVE_SHAPE_WITHOUT_EXACT_ENDPOINT_PROOF');negativeEndpoints.add(i);}
        raw[i]+=shape*a;
      }
    }
  }
  let mass=sum(raw);if(!(mass>0)||!raw.every(Number.isFinite))throw Error('EMPTY_DISTRIBUTION');
  let repaired=false;for(let i=0;i<150;i++)if(raw[i]<0){if(!negativeEndpoints.has(i))throw Error('NEGATIVE_NODE_WITHOUT_EXACT_ENDPOINT_PROOF');raw[i]=0;repaired=true;}if(repaired)mass=sum(raw);
  const peak=Math.max(...raw),indices=raw.flatMap((v,i)=>v===peak?[i]:[]),shares=raw.map(v=>v/mass*100),last=bars.at(-1)!;
  return {
    source_date:last.date,security_id:code,model_version:VERSION,seed_source_date:start,window_source_start_date:start,
    history_window_bars:210,loaded_window_rows:rows,history_rows_processed:bars.length,
    price_grid_kind:'EASTMONEY_150_DYNAMIC_NODES',price_grid:{low_cny:String(low),high_cny:String(high),step_cny:String(step),node_count:150},
    raw_total_mass:String(mass),raw_node_masses:raw.map(String),peak_node_indices:indices,
    peak_prices_cny:indices.map(i=>(low+step*i).toFixed(2)),peak_bin_share_percent:String(peak/mass*100),
    model_mean_cost_cny:String(sum(shares.map((v,i)=>(low+step*i)*v/100))),close_cny:last.close,turnover_percent:last.turnover_percent,
    profile:shares.map((v,i)=>({price_cny:String(low+step*i),share_percent:String(v)})),
    coefficient:'2',unchanged_threshold:null,historical_PIT_authenticated:false,
  } as Snapshot;
}
function coordinate(s:Snapshot,i:number){const low=cents(s.price_grid!.low_cny),high=cents(s.price_grid!.high_cny);return low*149+Math.max(149,high-low)*i;}
function compare(before:Snapshot,after:Snapshot){
  const old:Record<string,string>={},now:Record<string,string>={};
  const span=Math.max(149,cents(before.price_grid!.high_cny)-cents(before.price_grid!.low_cny));
  for(const i of before.peak_node_indices){
    const center=2*coordinate(before,i),price=Number(before.profile[i].price_cny).toFixed(2);
    const share=(s:Snapshot)=>sum(s.profile.flatMap((p,j)=>center-span<=2*coordinate(s,j)&&2*coordinate(s,j)<center+span?[Number(p.share_percent)]:[]));
    old[price]=String(share(before));now[price]=String(share(after));
  }
  before.comparison_anchor_shares_percent=old;after.comparison_anchor_shares_percent=now;
}
export function replayLocalHistory(stock:MarketStock,day:string):ChipHistory|null{
  const index=stock.history.findIndex(b=>b.date===day);if(index<209)return null;
  const prior=previousSession(day);if(!prior||stock.history[index-1]?.date!==prior)return null;
  const window=stock.history.slice(index-209,index+1),current=calculate(window,stock.security_id,window[0].date,210),previous=calculate(window.slice(0,-1),stock.security_id,window[0].date,210);
  compare(previous,current);
  return {schema:'stock.chips.history.v1',dataset_id:stock.dataset_id,published_as_of:stock.as_of,security_id:stock.security_id,name:stock.name,
    selected_date:day,previous_date:prior,comparison_status:'READY',reasons:[],snapshots:{[day]:current,[prior]:previous},
    model:(stock as unknown as Record<string,unknown>).model,unchanged_threshold:null,
    input_kind:'BROWSER_PUBLISHED_VALIDATED_CANDLES',historical_PIT_authenticated:false} as ChipHistory;
}
/** Numerical equality, with no tolerance or substituted price/occupancy bands. */
export function sameLocalSnapshot(local:ChipSnapshot,server:ChipSnapshot){
  const a=local as unknown as Record<string,unknown>,b=server as unknown as Record<string,unknown>;
  for(const key of ['source_date','model_version','window_source_start_date','history_window_bars','loaded_window_rows','history_rows_processed'])if(a[key]!==b[key])return false;
  for(const key of ['peak_bin_share_percent','model_mean_cost_cny','close_cny','turnover_percent'])if(Number(a[key])!==Number(b[key]))return false;
  if(JSON.stringify(a.peak_prices_cny)!==JSON.stringify(b.peak_prices_cny)||local.profile.length!==server.profile.length)return false;
  if(local.profile.some((p,i)=>Number(p.price_cny)!==Number(server.profile[i].price_cny)||Number(p.share_percent)!==Number(server.profile[i].share_percent)))return false;
  for(const key of ['low_cny','high_cny','step_cny'] as const)if(Number(local.price_grid?.[key])!==Number(server.price_grid?.[key]))return false;
  const old=local.comparison_anchor_shares_percent,expected=server.comparison_anchor_shares_percent;
  return !!old&&!!expected&&Object.keys(old).sort().join()===Object.keys(expected).sort().join()&&Object.keys(old).every(p=>Number(old[p])===Number(expected[p]));
}
export function prepareLocalHistory(stock:MarketStock):Record<string,ChipHistory>{
  const identity=(stock as unknown as {execution_identity?:{files?:Record<string,string>}}).execution_identity;
  if(identity?.files?.['eastmoney_model.py']!==SERVER_MODEL_SHA256)return {};
  const latest=replayLocalHistory(stock,stock.as_of);
  if(!latest||!latest.previous_date||!Object.entries(latest.snapshots).every(([day,s])=>stock.snapshots[day]&&sameLocalSnapshot(s,stock.snapshots[day])))return {};
  const frames:Record<string,ChipHistory>={};
  for(const bar of stock.history.slice(209)){
    if(bar.date===stock.as_of)continue;
    try{const frame=replayLocalHistory(stock,bar.date);if(frame)frames[bar.date]=frame;}catch{/* A particular unsupported day still has the server path. */}
  }
  return frames;
}
