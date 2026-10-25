import { chipNumber, parseChipSample, type ChipSample, type ChipSnapshot } from './chips.ts';
import {chipPricePositionRule,chipUnchangedRule,chipStabilityThresholds,validateChipPricePosition} from './chip-price-position.ts';

export const chipPriceFilterRule = 'NO_ABSOLUTE_PRICE_FILTER_V1';
export type MarketState = 'PASS' | 'FAIL' | 'UNKNOWN';
export type ChipStabilitySummary={
  rule_id:typeof chipUnchangedRule;status:MarketState;baseline_date:string|null;first_limit_up_date:string|null;
  evaluated_sessions:number;required_sessions:number;max_shift_fraction:string|null;min_mass_ratio:string|null;reasons:string[];
};
export type DailyCandle = { date: string; open: string; high: string; low: string; close: string; volume_shares: string; amount_cny: string; turnover_percent: string };
export type MarketManifest = {
  schema: 'stock.chips.market.manifest.v1'; dataset_id: string; active_slot: string;
  as_of: string; previous_date: string; official_sessions: string[];
  universe_total: number; scanned_count: number; model_ready_count: number;
  screen_counts: Record<MarketState, number>; market_complete: boolean; coefficient: 2;
  calculation_complete?: boolean;
  unchanged_threshold: null; generated_at: string; calendar_source: string;
  model_version?: string; history_window_bars?: number;
  price_filter_rule: typeof chipPriceFilterRule;
  chip_unchanged_rule: typeof chipUnchangedRule;
  chip_stability_thresholds?:typeof chipStabilityThresholds;
  price_position_rule?:typeof chipPricePositionRule;final_screen_counts?:Record<MarketState,number>;
  universe_coverage?: 'SITE_CATALOG_UNVERIFIED'|'VENDOR_REPORTED_TOTAL_VALIDATED'|'OFFICIAL_CURRENT_LIST_RECONCILED';
};
export type MarketScreenRow = {
  security_id: string; name: string; market: string; close_cny: string | null;
  last_trade_close_date?: string | null;
  comparison_status?: 'NO_PREVIOUS_LISTED_SESSION';
  prefilter_status: MarketState; recent_limit_up_dates: string[];
  model_mean_cost_cny?:string|null;above_peak_status?:MarketState;above_mean_cost_status?:MarketState;
  price_position_status?:MarketState;screen_status?:MarketState;
  chip_unchanged_status?:MarketState;chip_stability?:ChipStabilitySummary;
  chip_status: 'READY' | 'MISSING'; peak_prices_cny: string[]; previous_peak_prices_cny: string[];
  original_peak_changes: { price_cny: string; previous_share_percent: string; current_share_percent: string; change_percentage_points: string }[];
  reasons: string[];
};
export type MarketStock = ChipSample & {
  schema: 'stock.chips.market.stock.v1'; dataset_id: string; as_of: string;
  history: DailyCandle[]; chip_status: 'READY' | 'MISSING'; reasons: string[];
  comparison_status?: 'NO_PREVIOUS_LISTED_SESSION'; reported_listing_date?: string;
};
function obj(v: unknown): Record<string, unknown> {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw Error('筹码数据格式不正确。');
  return v as Record<string, unknown>;
}
const day = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(v + 'T00:00:00Z')) && new Date(v+'T00:00:00Z').toISOString().slice(0,10)===v;
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
export function parseMarketManifest(input: unknown): MarketManifest {
  const v = obj(input), c = obj(v.screen_counts);
  if(v.price_filter_rule!==chipPriceFilterRule || v.price_position_rule!==chipPricePositionRule || v.chip_unchanged_rule!==chipUnchangedRule)
    throw Error('顶格未动已改为自动判断，等待符合自动规则的新每日批次。');
  if(v.chip_stability_thresholds!==undefined){
    const thresholds=obj(v.chip_stability_thresholds);
    if(Object.keys(thresholds).length!==3||Object.entries(chipStabilityThresholds).some(([key,value])=>thresholds[key]!==value))
      throw Error('顶格自动判断的固定阈值不匹配。');
  }
  if (v.schema !== 'stock.chips.market.manifest.v1' || !/^[0-9a-f]{16}$/.test(String(v.dataset_id)) || v.active_slot!==v.dataset_id
    || !day(v.as_of) || !day(v.previous_date) || v.previous_date >= v.as_of || v.coefficient !== 2 || v.unchanged_threshold !== null
    || !Array.isArray(v.official_sessions) || v.official_sessions.length !== 10 || !v.official_sessions.every(day)
    || v.official_sessions.at(-1) !== v.as_of || v.official_sessions.at(-2) !== v.previous_date
    || v.official_sessions.some((d,i) => i > 0 && d <= (v.official_sessions as string[])[i-1])
    || !count(v.universe_total) || v.universe_total < 1 || v.universe_total > 15000 || !count(v.scanned_count) || v.scanned_count !== v.universe_total
    || !count(v.model_ready_count) || v.model_ready_count > v.universe_total || typeof v.market_complete !== 'boolean'
    || !['PASS','FAIL','UNKNOWN'].every(k => count(c[k])) || Number(c.PASS)+Number(c.FAIL)+Number(c.UNKNOWN)!==v.universe_total) throw Error('每日筛选目录不完整或固定参数不匹配。');
  if(v.price_position_rule!==undefined||v.final_screen_counts!==undefined){
    const final=obj(v.final_screen_counts);
    if(v.price_position_rule!==chipPricePositionRule||!['PASS','FAIL','UNKNOWN'].every(k=>count(final[k]))
      ||Number(final.PASS)+Number(final.FAIL)+Number(final.UNKNOWN)!==v.universe_total)throw Error('新增价格位置条件或计数不完整。');
  }
  return v as unknown as MarketManifest;
}
export function parseMarketScreen(input: unknown, manifest: MarketManifest): MarketScreenRow[] {
  const v = obj(input);
  if (v.dataset_id !== manifest.dataset_id || v.as_of !== manifest.as_of || !Array.isArray(v.rows) || v.rows.length !== manifest.universe_total) throw Error('筛选结果与当前批次不一致，请重新读取。');
  const seen = new Set<string>(); const counts = { PASS:0, FAIL:0, UNKNOWN:0 },finalCounts={ PASS:0, FAIL:0, UNKNOWN:0 };
  for (const entry of v.rows) {
    const r=obj(entry); if (!/^\d{6}$/.test(String(r.security_id)) || seen.has(String(r.security_id)) || typeof r.name !== 'string'
      || !['PASS','FAIL','UNKNOWN'].includes(String(r.prefilter_status)) || !['READY','MISSING'].includes(String(r.chip_status))
      || !Array.isArray(r.recent_limit_up_dates) || !r.recent_limit_up_dates.every(d => manifest.official_sessions.includes(String(d)))
      || !Array.isArray(r.peak_prices_cny) || !Array.isArray(r.previous_peak_prices_cny) || !Array.isArray(r.original_peak_changes) || !Array.isArray(r.reasons)) throw Error('筛选行格式不正确。');
    if (r.close_cny !== null && chipNumber(r.close_cny) <= 0) throw Error('收盘价不正确。');
    if (r.last_trade_close_date != null && (!day(r.last_trade_close_date) || r.last_trade_close_date >= manifest.as_of)) throw Error('沿用收盘价的最后交易日不正确。');
    if (r.prefilter_status==='PASS' && (r.close_cny===null || !r.recent_limit_up_dates.length)) throw Error('涨停初筛缺少明确收盘价或涨停证据。');
    if(manifest.price_position_rule){
      if(!r.peak_prices_cny.every(p=>typeof p==='string'&&Number.isFinite(Number(p))&&Number(p)>0))throw Error('主峰价格不正确。');
      validateChipPricePosition(r as unknown as MarketScreenRow);finalCounts[r.screen_status as MarketState]++;
      const stability=(r as unknown as MarketScreenRow).chip_stability!;
      if(stability.first_limit_up_date!==null){
        const index=manifest.official_sessions.indexOf(stability.first_limit_up_date);
        if(index<0||stability.first_limit_up_date!==[...r.recent_limit_up_dates].sort()[0]
          ||stability.required_sessions!==manifest.official_sessions.length-index
          ||(index>0&&stability.baseline_date!==null&&stability.baseline_date!==manifest.official_sessions[index-1]))
          throw Error('顶格自动判断的基准或比较日期与涨停窗口不一致。');
      }else if(stability.required_sessions!==0)throw Error('顶格自动判断缺少起始涨停日期。');
    }
    seen.add(String(r.security_id)); counts[r.prefilter_status as MarketState]++;
  }
  if (Object.keys(counts).some(k => counts[k as MarketState] !== manifest.screen_counts[k as MarketState])) throw Error('筛选覆盖计数不一致。');
  if(manifest.final_screen_counts&&Object.keys(finalCounts).some(k=>finalCounts[k as MarketState]!==manifest.final_screen_counts![k as MarketState]))throw Error('新增价格位置条件计数不一致。');
  return v.rows as MarketScreenRow[];
}
export function parseMarketStock(input: unknown, code: string, manifest: MarketManifest): MarketStock {
  const v=obj(input); if (v.schema!=='stock.chips.market.stock.v1' || v.dataset_id!==manifest.dataset_id || v.as_of!==manifest.as_of || v.security_id!==code
    || !['READY','MISSING'].includes(String(v.chip_status)) || !Array.isArray(v.history) || (v.chip_status==='READY'&&!v.history.length) || v.history.length>10000 || !Array.isArray(v.reasons)) throw Error('个股图表与当前股票或日期不一致。');
  let last='';
  for (const entry of v.history) {
    const r=obj(entry); if (!day(r.date) || r.date<=last || r.date>manifest.as_of) throw Error('K线日期不正确。');
    const o=chipNumber(r.open),h=chipNumber(r.high),l=chipNumber(r.low),c=chipNumber(r.close);
    if (!(0<l && l<=Math.min(o,c) && Math.max(o,c)<=h) || chipNumber(r.volume_shares)<0 || chipNumber(r.amount_cny)<0 || chipNumber(r.turnover_percent)<0) throw Error('K线或成交量不完整。');
    last=r.date;
  }
  if (v.chip_status==='READY') {
    if(manifest.model_version&&obj(v.model).version!==manifest.model_version)throw Error('个股算法版本与批次不一致。');
    if (v.comparison_status==='NO_PREVIOUS_LISTED_SESSION') {
      if (v.reported_listing_date!==manifest.as_of || v.history.length!==1 || obj(v.history[0]).date!==manifest.as_of
        || Object.keys(obj(v.snapshots)).length!==1 || !obj(v.snapshots)[manifest.as_of]) throw Error('新上市股票缺少首日证据。');
      parseChipSample(v,code,[manifest.as_of]);
    } else parseChipSample(v,code,[manifest.previous_date,manifest.as_of]);
  }
  else if (!v.snapshots || Object.keys(obj(v.snapshots)).length) throw Error('缺失筹码不可显示为有效分布。');
  return v as unknown as MarketStock;
}

export function currentMarketReasons(stock: MarketStock, row?: MarketScreenRow): string[] {
  if (!row) return [...stock.reasons];
  if (row.security_id!==stock.security_id) throw Error('数据说明与当前股票不一致。');
  if (row.prefilter_status==='UNKNOWN') return [...new Set([...stock.reasons,...row.reasons])];
  // Reused model frames retain calculation-time notes. Replace only obsolete
  // screening notes with the current immutable row; keep model/source warnings.
  const screening=(reason:string)=>reason==='LATEST_CLOSE_MISSING'||reason==='LIMIT_HISTORY_INCOMPLETE'
    || ['HISTORICAL_REFERENCE_FETCH_ERROR:','REFERENCE_QUANTITY_CORROBORATION_ERROR:',
      'LAST_TRADE_CLOSE_DURING_CONFIRMED_NO_TRADE_SESSION:'].some(prefix=>reason.startsWith(prefix));
  return [...new Set([...stock.reasons.filter(reason=>!screening(reason)),...row.reasons])];
}

export function tradingGeometry(history: DailyCandle[], current: ChipSnapshot | null, previous: ChipSnapshot | null, period: number, viewportWidth = 1200, focusDate?: string, viewportHeight?:number) {
  const focus=focusDate?history.findIndex(r=>r.date===focusDate):-1;
  const end=focus>=0&&focus<history.length-period?Math.min(history.length,Math.max(period,focus+1+Math.floor(period*.25))):history.length;
  const candles=history.slice(Math.max(0,end-period),end); if (!candles.length) return null;
  const peaks=[...(current?.peak_prices_cny??[]),...(previous?.peak_prices_cny??[])].map(chipNumber);
  let min=Math.min(...candles.map(c=>chipNumber(c.low)),...peaks),max=Math.max(...candles.map(c=>chipNumber(c.high)),...peaks);
  const pad=Math.max((max-min)*.065,.02); min=Math.max(.001,min-pad);max+=pad;
  // Lay out in actual CSS pixels: keep labels readable instead of shrinking a
  // desktop-sized SVG (or forcing it to overflow a narrow window).
  const W=Math.max(1,Math.round(viewportWidth)),H=viewportHeight===undefined?Math.round(Math.max(380,Math.min(568,W*.42+110))):Math.round(Math.max(260,Math.min(568,viewportHeight)));
  const left=W<600?10:22,chipEnd=W-(W<600?10:24),chipStart=chipEnd-Math.min(198,W*.22);
  const axisWidth=Math.max(W<600?44:62,Math.max(min.toFixed(2).length,max.toFixed(2).length)*7+16);
  const right=chipStart-axisWidth,top=24,volBottom=H-32,volTop=volBottom-Math.round(H*.2),volumeDivider=volTop-22,bottom=volumeDivider-18;
  const y=(price:number)=>top+(max-price)/(max-min)*(bottom-top),step=(right-left)/candles.length;
  const tickCount=Math.max(2,Math.min(6,Math.floor((right-left)/72)+1));
  const dateTicks=new Set(Array.from({length:tickCount},(_,i)=>Math.round(i*(candles.length-1)/(tickCount-1))));
  const maxVolume=Math.max(1,...candles.map(c=>chipNumber(c.volume_shares)));
  const candleData=candles.map((c,i)=>({ ...c,x:left+(i+.5)*step,openY:y(chipNumber(c.open)),closeY:y(chipNumber(c.close)),highY:y(chipNumber(c.high)),lowY:y(chipNumber(c.low)),up:chipNumber(c.close)>=chipNumber(c.open),volumeY:volBottom-chipNumber(c.volume_shares)/maxVolume*(volBottom-volTop) }));
  // The chart may combine bars; peak occupancy always uses the original model's exact price intervals.
  const sourceWidth=Math.max(chipNumber(current?.price_grid?.step_cny??current?.display_bin_width_cny??'0'),chipNumber(previous?.price_grid?.step_cny??previous?.display_bin_width_cny??'0'));
  const bands=sourceWidth>(max-min)/240?Math.max(1,Math.floor((max-min)/sourceWidth)):240,width=(max-min)/bands;
  function histogram(s:ChipSnapshot|null) {
    const mass=Array<number>(bands).fill(0);let outside=0;
    for (const r of s?.profile??[]) { const price=chipNumber(r.price_cny),value=chipNumber(r.share_percent); if(price<min||price>max){outside+=value;continue;} mass[Math.min(bands-1,Math.floor((price-min)/width))]+=value; }
    return {mass,outside};
  }
  const a=histogram(current),b=histogram(previous),peak=Math.max(...a.mass,...b.mass,.000001);
  const chips=a.mass.map((mass,i)=>({price:min+(i+.5)*width,y:y(min+(i+1)*width),height:(bottom-top)/bands+.2,currentWidth:mass/peak*(chipEnd-chipStart),previousWidth:b.mass[i]/peak*(chipEnd-chipStart),mass,previousMass:b.mass[i]}));
  return {W,H,left,right,top,bottom,volTop,volBottom,volumeDivider,chipStart,chipEnd,dateTicks,min,max,y,step,candles:candleData,chips,bandWidth:width,outsideShare:a.outside,previousOutsideShare:b.outside,maxVolume};
}
