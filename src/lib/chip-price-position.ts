import type {MarketScreenRow,MarketState} from './chip-market.ts';

export const chipPricePositionRule='CLOSE_GT_ANY_PEAK_OR_MEAN_COST_V1';
const states=['PASS','FAIL','UNKNOWN'];
function decimal(value:unknown){
  if(typeof value!=='string')return null;
  const match=/^\+?(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(value);
  if(!match)return null;
  const exponent=Number(match[3]??0),digits=match[1]+(match[2]??'');
  if(Math.abs(exponent)>100||digits.length>200)return null;
  const amount=BigInt(digits);return amount>BigInt(0)?{amount,scale:(match[2]?.length??0)-exponent}:null;
}
/** Compare original decimal strings; rounding a mean to the displayed cents changes eligibility. */
function above(close:ReturnType<typeof decimal>,reference:ReturnType<typeof decimal>):MarketState{
  if(!close||!reference)return 'UNKNOWN';
  const scale=Math.max(close.scale,reference.scale);
  return close.amount*BigInt(10)**BigInt(scale-close.scale)>reference.amount*BigInt(10)**BigInt(scale-reference.scale)?'PASS':'FAIL';
}
export function chipPricePosition(row:MarketScreenRow){
  const close=decimal(row.close_cny),valid=row.chip_status==='READY';
  const peaks=valid?row.peak_prices_cny.map(price=>above(close,decimal(price))):[];
  const peak:MarketState=peaks.includes('PASS')?'PASS':peaks.length&&peaks.every(state=>state==='FAIL')?'FAIL':'UNKNOWN';
  const mean=above(close,valid?decimal(row.model_mean_cost_cny):null);
  const position:MarketState=peak==='PASS'||mean==='PASS'?'PASS':peak==='FAIL'&&mean==='FAIL'?'FAIL':'UNKNOWN';
  const combined:MarketState=row.prefilter_status==='FAIL'||position==='FAIL'?'FAIL':row.prefilter_status==='PASS'&&position==='PASS'?'PASS':'UNKNOWN';
  return{peak,mean,position,combined};
}
export function validateChipPricePosition(row:MarketScreenRow){
  const value=chipPricePosition(row);
  if(row.model_mean_cost_cny!==null&&!decimal(row.model_mean_cost_cny)
    || !states.includes(row.above_peak_status??'')||row.above_peak_status!==value.peak
    || row.above_mean_cost_status!==value.mean||row.price_position_status!==value.position||row.screen_status!==value.combined)
    throw Error('股价与主峰、平均成本的筛选条件不一致。');
}
/** Legacy batches cannot silently satisfy a condition they did not calculate. */
export function chipScreenState(row:MarketScreenRow):MarketState{
  return row.screen_status??(row.prefilter_status==='FAIL'?'FAIL':'UNKNOWN');
}
export function chipScreenCounts(rows:MarketScreenRow[]){
  return rows.reduce((counts,row)=>{counts[chipScreenState(row)]++;return counts;},{PASS:0,FAIL:0,UNKNOWN:0});
}
export function chipPricePositionLabel(row:MarketScreenRow){
  return row.above_peak_status==='PASS'&&row.above_mean_cost_status==='PASS'?'高于主峰及均成本'
    :row.above_peak_status==='PASS'?'高于主峰':row.above_mean_cost_status==='PASS'?'高于平均成本'
    :row.price_position_status==='FAIL'?'未高于主峰或均成本':'峰价或均成本待核实';
}
