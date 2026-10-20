export type ChipStatus = 'PASS' | 'FAIL' | 'UNKNOWN';
export type ChipManifest = {
  schema: 'stock.chips.manifest.v1';
  dataset_id: string;
  as_of: string;
  dates: string[];
  securities: { code: string; name: string }[];
  covered_securities: number;
  market_complete: boolean;
  scope: string;
  coefficient: 2;
  unchanged_threshold: null;
};
export type ChipSnapshot = {
  window_source_start_date?: string;
  price_grid_kind?: 'EASTMONEY_150_DYNAMIC_NODES';
  price_grid?: {low_cny:string;high_cny:string;step_cny:string;node_count:150};
  comparison_anchor_shares_percent?: Record<string,string>;
  history_window_bars?: number;
  loaded_window_rows?: number;
  history_rows_processed?: number;
  profile_format?: 'DISPLAY_BANDS_WITH_EXACT_ANCHORS';
  display_bin_width_cny?: string;
  exact_anchor_shares_percent?: Record<string,string>;
  original_profile_bin_count?: number;
  original_profile_sha256?: string;
  market_session_status?: 'SOURCE_CONFIRMED_NO_TRADE';
  last_trade_date?: string;
  peak_prices_cny: string[];
  peak_bin_share_percent: string;
  model_mean_cost_cny: string;
  close_cny: string;
  turnover_percent: string;
  profile: { price_cny: string; share_percent: string }[];
};
export type ChipSample = {
  security_id: string;
  name: string;
  snapshots: Record<string, ChipSnapshot>;
};
export type ChipScreenRow = {
  security_id: string;
  name: string;
  as_of: string;
  close_cny: string;
  conditions: {
    close_below_10: ChipStatus;
    closing_limit_up_in_10_sessions: ChipStatus;
    chip_unchanged: 'UNKNOWN';
  };
  screen_status: ChipStatus;
  peak_prices: string;
  previous_peak_prices: string;
};
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('筹码数据格式不正确。');
  return value as Record<string, unknown>;
}
export function chipNumber(value: unknown): number {
  if ((typeof value !== 'number' && typeof value !== 'string') || (typeof value === 'string' && !value.trim()) || !Number.isFinite(Number(value))) throw Error('筹码数值无效。');
  return Number(value);
}
function isoDay(value: unknown): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value;
}
export function parseChipManifest(input: unknown): ChipManifest {
  const v = object(input);
  if (v.schema !== 'stock.chips.manifest.v1' || typeof v.dataset_id !== 'string' || !/^[a-f0-9]{16}$/.test(v.dataset_id)
    || !isoDay(v.as_of) || v.coefficient !== 2 || v.unchanged_threshold !== null || typeof v.market_complete !== 'boolean'
    || !Array.isArray(v.dates) || !v.dates.length || v.dates.length > 500 || !v.dates.every(isoDay)
    || new Set(v.dates).size !== v.dates.length || v.dates.at(-1) !== v.as_of || v.dates.some((d, i) => i > 0 && d <= (v.dates as string[])[i - 1])
    || !Array.isArray(v.securities) || v.securities.length > 10000 || v.covered_securities !== v.securities.length) throw Error('筹码目录或固定参数不匹配。');
  const codes = new Set<string>();
  for (const entry of v.securities) {
    const row = object(entry);
    if (typeof row.code !== 'string' || !/^\d{6}$/.test(row.code) || codes.has(row.code) || typeof row.name !== 'string' || !row.name) throw Error('筹码股票目录不正确。');
    codes.add(row.code);
  }
  return v as unknown as ChipManifest;
}
export function parseChipSample(input: unknown, code: string, dates: string[]): ChipSample {
  const v = object(input), model = object(v.model), snapshots = object(v.snapshots);
  const nodes=model.price_grid_kind==='EASTMONEY_150_DYNAMIC_NODES';
  const validGrid=nodes?model.version==='chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1'
    &&model.price_node_count===150&&model.history_window_bars===210&&model.price_bin_width_cny===null:model.price_bin_width_cny==='0.01';
  if (v.security_id !== code || model.historical_turnover_decay_coefficient !== '2' || !validGrid
    || v.unchanged_threshold !== null || typeof v.name !== 'string') throw Error('筹码股票或模型参数不匹配。');
  for (const day of dates) {
    const s = object(snapshots[day]);
    if(nodes){
      const grid=object(s.price_grid);
      if(s.model_version!==model.version||s.price_grid_kind!=='EASTMONEY_150_DYNAMIC_NODES'||grid.node_count!==150
        ||!Array.isArray(s.profile)||s.profile.length!==150||s.history_window_bars!==210
        ||!Number.isSafeInteger(s.loaded_window_rows)||Number(s.loaded_window_rows)<1||Number(s.loaded_window_rows)>210
        ||!Number.isSafeInteger(s.history_rows_processed)||Number(s.history_rows_processed)<1||Number(s.history_rows_processed)>Number(s.loaded_window_rows)
        ||chipNumber(grid.low_cny)<=0||chipNumber(grid.high_cny)<chipNumber(grid.low_cny)||chipNumber(grid.step_cny)<.01)throw Error('150节点或210根行情参数不匹配。');
      if(dates.length>1){const anchors=object(s.comparison_anchor_shares_percent);
        for(const price of object(snapshots[dates[0]]).peak_prices_cny as string[]){
          if(!Object.hasOwn(anchors,price)||chipNumber(anchors[price])<0||chipNumber(anchors[price])>100)throw Error('动态节点原峰价格区间占比缺失。');
        }
      }
    }
    if (s.market_session_status === 'SOURCE_CONFIRMED_NO_TRADE' && (!isoDay(s.last_trade_date) || s.last_trade_date >= day)) throw Error('未交易日筹码缺少已核实的最后交易日。');
    if (!Array.isArray(s.peak_prices_cny) || !s.peak_prices_cny.length || !Array.isArray(s.profile) || !s.profile.length || s.profile.length > 100000) throw Error('筹码日期或分布缺失。');
    for (const field of ['close_cny', 'model_mean_cost_cny', 'turnover_percent', 'peak_bin_share_percent']) chipNumber(s[field]);
    for (const p of s.peak_prices_cny) if (chipNumber(p) <= 0) throw Error('筹码峰价无效。');
    if (s.profile_format!==undefined) {
      if (s.profile_format!=='DISPLAY_BANDS_WITH_EXACT_ANCHORS' || s.profile.length>4096 || chipNumber(s.display_bin_width_cny)<=0.01
        || !Number.isSafeInteger(s.original_profile_bin_count) || Number(s.original_profile_bin_count)<=s.profile.length
        || !/^[0-9a-f]{64}$/.test(String(s.original_profile_sha256))) throw Error('筹码显示分组信息不完整。');
      const exact=object(s.exact_anchor_shares_percent);
      for (const d of dates) for (const price of object(snapshots[d]).peak_prices_cny as string[]) {
        if (!Object.hasOwn(exact,price) || chipNumber(exact[price])<0 || chipNumber(exact[price])>100) throw Error('分组筹码缺少原峰精确占比。');
      }
    }
    let last = -1, mass = 0;
    for (const entry of s.profile) {
      const row = object(entry), price = chipNumber(row.price_cny), share = chipNumber(row.share_percent);
      if (price < 0 || price <= last || share < 0 || share > 100) throw Error('筹码价格档无效。');
      last = price; mass += share;
    }
    if (Math.abs(mass - 100) > 0.000001) throw Error('筹码分布总占比不完整。');
  }
  return v as unknown as ChipSample;
}
export function parseChipScreen(input: unknown, manifest: ChipManifest): ChipScreenRow[] {
  const v = object(input);
  if (v.unchanged_threshold !== null || v.as_of !== manifest.as_of || !Array.isArray(v.screenings) || v.screenings.length !== manifest.covered_securities) throw Error('筛选结果格式不正确。');
  const seen = new Set<string>();
  for (const entry of v.screenings) {
    const row = object(entry), c = object(row.conditions);
    if (typeof row.security_id !== 'string' || !/^\d{6}$/.test(row.security_id) || seen.has(row.security_id) || !manifest.securities.some(s => s.code === row.security_id) || typeof row.name !== 'string' || row.as_of !== manifest.as_of
      || !['PASS', 'FAIL', 'UNKNOWN'].includes(String(row.screen_status)) || c.chip_unchanged !== 'UNKNOWN'
      || !['PASS', 'FAIL', 'UNKNOWN'].includes(String(c.close_below_10)) || !['PASS', 'FAIL', 'UNKNOWN'].includes(String(c.closing_limit_up_in_10_sessions))
      || row.screen_status === 'PASS') throw Error('筹码标准尚未确定，不能显示为正式入选。');
    chipNumber(row.close_cny);
    seen.add(row.security_id);
    const expected = Object.values(c).includes('FAIL') ? 'FAIL' : 'UNKNOWN';
    if (row.screen_status !== expected) throw Error('筛选状态与条件不一致。');
  }
  return v.screenings as ChipScreenRow[];
}
export function anchoredShare(snapshot: ChipSnapshot, price: string): number {
  if(snapshot.price_grid_kind==='EASTMONEY_150_DYNAMIC_NODES'){
    if(!snapshot.comparison_anchor_shares_percent||!Object.hasOwn(snapshot.comparison_anchor_shares_percent,price))throw Error('原峰价格区间占比缺失。');
    return chipNumber(snapshot.comparison_anchor_shares_percent[price]);
  }
  if (snapshot.profile_format==='DISPLAY_BANDS_WITH_EXACT_ANCHORS') {
    if (!snapshot.exact_anchor_shares_percent || !Object.hasOwn(snapshot.exact_anchor_shares_percent,price)) throw Error('原峰精确占比缺失。');
    return chipNumber(snapshot.exact_anchor_shares_percent[price]);
  }
  return chipNumber(snapshot.profile.find(p => p.price_cny === price)?.share_percent ?? '0');
}
export function chipPlot(current: ChipSnapshot, previous: ChipSnapshot | null, fullRange: boolean) {
  const a = new Map(current.profile.map(p => [Math.round(chipNumber(p.price_cny) * 100), chipNumber(p.share_percent)]));
  const b = new Map((previous?.profile ?? []).map(p => [Math.round(chipNumber(p.price_cny) * 100), chipNumber(p.share_percent)]));
  const peaks = [...current.peak_prices_cny, ...(previous?.peak_prices_cny ?? [])].map(chipNumber);
  const indexes = [...a.keys(), ...b.keys()];
  const low = fullRange ? indexes.reduce((a,b) => Math.min(a,b), Infinity) : Math.max(0, Math.floor((Math.min(...peaks) - .65) * 100));
  const high = fullRange ? indexes.reduce((a,b) => Math.max(a,b), -Infinity) : Math.ceil((Math.max(...peaks) + .65) * 100);
  return Array.from({ length: high - low + 1 }, (_, i) => ({ price: (low + i) / 100, current: a.get(low + i) ?? 0, previous: b.get(low + i) ?? 0 }));
}
