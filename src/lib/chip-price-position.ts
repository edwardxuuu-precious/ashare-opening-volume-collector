import type {MarketScreenRow,MarketState,ChipStabilitySummary} from './chip-market.ts';

export const chipPricePositionRule='CLOSE_GT_MEAN_COST_V2';
export const chipUnchangedRule='TOP_REGION_STABLE_1_90_90_V1';
export const chipStabilityThresholds={max_shift_fraction:'0.01',min_mass_ratio:'0.90',top_height_ratio:'0.90'} as const;
const states=['PASS','FAIL','UNKNOWN'];
function decimal(value:unknown,allowZero=false){
  if(typeof value!=='string')return null;
  const match=/^\+?(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(value);
  if(!match)return null;
  const exponent=Number(match[3]??0),digits=match[1]+(match[2]??'');
  if(Math.abs(exponent)>100||digits.length>200)return null;
  const amount=BigInt(digits);return amount>BigInt(0)||allowZero?{amount,scale:(match[2]?.length??0)-exponent}:null;
}
function compare(a:NonNullable<ReturnType<typeof decimal>>,b:NonNullable<ReturnType<typeof decimal>>){
  const scale=Math.max(a.scale,b.scale),left=a.amount*BigInt(10)**BigInt(scale-a.scale),right=b.amount*BigInt(10)**BigInt(scale-b.scale);
  return left>right?1:left<right?-1:0;
}
const isoDay=(value:unknown):value is string=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)
  && !Number.isNaN(Date.parse(value+'T00:00:00Z'))&&new Date(value+'T00:00:00Z').toISOString().slice(0,10)===value;
/** Verify the sealed summary, including exact decimal boundaries, before accepting its state. */
export function validateChipStability(input:unknown):MarketState{
  if(!input||typeof input!=='object'||Array.isArray(input))throw Error('顶格自动判断结果缺失。');
  const value=input as ChipStabilitySummary;
  const shift=value.max_shift_fraction===null?null:decimal(value.max_shift_fraction,true);
  const mass=value.min_mass_ratio===null?null:decimal(value.min_mass_ratio,true);
  if(value.rule_id!==chipUnchangedRule||!states.includes(value.status)
    || (value.baseline_date!==null&&!isoDay(value.baseline_date))||(value.first_limit_up_date!==null&&!isoDay(value.first_limit_up_date))
    || (value.baseline_date!==null&&value.first_limit_up_date!==null&&value.baseline_date>=value.first_limit_up_date)
    || !Number.isSafeInteger(value.evaluated_sessions)||!Number.isSafeInteger(value.required_sessions)
    || value.evaluated_sessions<0||value.required_sessions<0||value.required_sessions>10||value.evaluated_sessions>value.required_sessions
    || (value.max_shift_fraction!==null&&!shift)||(value.min_mass_ratio!==null&&!mass)
    || !Array.isArray(value.reasons)||!value.reasons.every(reason=>typeof reason==='string'&&reason.length>0))throw Error('顶格自动判断的规则、日期或指标不完整。');
  const moved=shift!==null&&compare(shift,decimal(chipStabilityThresholds.max_shift_fraction)!)>0;
  const weakened=mass!==null&&compare(mass,decimal(chipStabilityThresholds.min_mass_ratio)!)<0;
  if(value.status==='PASS'&&(!value.baseline_date||!value.first_limit_up_date||value.required_sessions<1
    ||value.evaluated_sessions!==value.required_sessions||!shift||!mass||moved||weakened||value.reasons.length!==0))throw Error('顶格自动入选缺少完整通过依据。');
  if(value.status==='FAIL'&&(!value.baseline_date||!value.first_limit_up_date||value.evaluated_sessions<1||(!moved&&!weakened)))
    throw Error('顶格未满足缺少明确失败指标。');
  if(value.status==='UNKNOWN'&&!value.reasons.length)throw Error('顶格判断缺少数据不足的原因。');
  return value.status;
}
/** Compare original decimal strings; rounding a mean to the displayed cents changes eligibility. */
function above(close:ReturnType<typeof decimal>,reference:ReturnType<typeof decimal>):MarketState{
  if(!close||!reference)return 'UNKNOWN';
  return compare(close,reference)>0?'PASS':'FAIL';
}
export function chipPricePosition(row:MarketScreenRow){
  const close=decimal(row.close_cny),valid=row.chip_status==='READY';
  const peaks=valid?row.peak_prices_cny.map(price=>above(close,decimal(price))):[];
  const peak:MarketState=peaks.includes('PASS')?'PASS':peaks.length&&peaks.every(state=>state==='FAIL')?'FAIL':'UNKNOWN';
  const mean=above(close,valid?decimal(row.model_mean_cost_cny):null);
  const position:MarketState=mean;
  const stability=row.chip_stability?validateChipStability(row.chip_stability):'UNKNOWN';
  const conditions=[row.prefilter_status,position,stability];
  const combined:MarketState=conditions.includes('FAIL')?'FAIL':conditions.every(state=>state==='PASS')?'PASS':'UNKNOWN';
  return{peak,mean,position,stability,combined};
}
export function validateChipPricePosition(row:MarketScreenRow){
  const value=chipPricePosition(row);
  if(row.model_mean_cost_cny!==null&&!decimal(row.model_mean_cost_cny)
    || !states.includes(row.above_peak_status??'')||row.above_peak_status!==value.peak
    || row.above_mean_cost_status!==value.mean||row.price_position_status!==value.position
    || !row.chip_stability||row.chip_unchanged_status!==value.stability||row.screen_status!==value.combined
    || (value.stability==='PASS'&&row.chip_status!=='READY'))
    throw Error('收盘价高于平均成本或顶格未动的筛选状态不一致。');
}
/** Legacy batches cannot silently satisfy a condition they did not calculate. */
export function chipScreenState(row:MarketScreenRow):MarketState{
  return row.screen_status??(row.prefilter_status==='FAIL'?'FAIL':'UNKNOWN');
}
export function chipScreenCounts(rows:MarketScreenRow[]){
  return rows.reduce((counts,row)=>{counts[chipScreenState(row)]++;return counts;},{PASS:0,FAIL:0,UNKNOWN:0});
}
export function chipPricePositionLabel(row:MarketScreenRow){
  return row.above_mean_cost_status==='PASS'?'高于平均成本'
    :row.price_position_status==='FAIL'?'未高于平均成本':'平均成本数据不足';
}

const stabilityReasons:Record<string,string>={
  PEAK_REGION_MOVED:'顶格区偏移超过1%',ANCHOR_BAND_MASS_DECREASED:'原顶格区占比下降超过10%',
  OTHER_CONDITION_FAILED_NOT_EVALUATED:'其他筛选条件未满足',LIMIT_EVENT_NOT_ESTABLISHED:'缺少已确认的涨停日期',
  EARLIER_LIMIT_STATUS_UNKNOWN:'较早交易日的涨停证据不足',BASELINE_NOT_AVAILABLE:'缺少涨停前一交易日的基准数据',
  CHIP_HISTORY_INCOMPLETE:'比较期间的筹码历史不完整',PRICE_BASIS_INCOMPARABLE:'比较期间的价格口径不可比',
  NATIVE_RESOLUTION_INSUFFICIENT:'筹码价格分辨率不足',COMPARISON_RESOURCE_BOUND:'筹码比较跨度过大',
  INVALID_STABILITY_INPUT:'比较数据不完整',NO_USABLE_DISTRIBUTION:'缺少可用筹码分布',SOURCE_MODEL_MISMATCH:'模型或来源版本不一致',
  DATA_TRADE_STATUS_CONFLICT:'交易状态证据存在冲突',DATA_PRICE_EVIDENCE_CONFLICT:'价格来源证据存在冲突',
};
export function chipStabilityLabel(row:MarketScreenRow){
  if(row.chip_stability?.reasons.includes('OTHER_CONDITION_FAILED_NOT_EVALUATED'))return '其他筛选条件未满足';
  return row.chip_unchanged_status==='PASS'?'顶格未动':row.chip_unchanged_status==='FAIL'?'顶格稳定条件未满足':'顶格判断数据不足';
}
export function chipStabilityDetails(row:MarketScreenRow){
  const value=row.chip_stability;if(!value)return '数据不足';
  const parts:string[]=[];
  if(value.baseline_date)parts.push(`基准 ${value.baseline_date}`);
  if(value.max_shift_fraction!==null)parts.push(`最大偏移 ${(Number(value.max_shift_fraction)*100).toFixed(2)}%`);
  if(value.min_mass_ratio!==null)parts.push(`原区占比最低保留 ${(Number(value.min_mass_ratio)*100).toFixed(2)}%（相对基准）`);
  if(value.required_sessions>0)parts.push(`已核 ${value.evaluated_sessions}/${value.required_sessions}日`);
  if(value.status!=='PASS')parts.push(...new Set(value.reasons.map(reason=>stabilityReasons[reason]??(value.status==='FAIL'?'不满足稳定标准':'数据不足'))));
  return parts.join(' · ')||(value.status==='PASS'?'自动判断通过':'数据不足');
}
