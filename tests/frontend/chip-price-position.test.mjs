import test from 'node:test';
import assert from 'node:assert/strict';
import {chipPricePosition,chipPricePositionRule,chipUnchangedRule,chipStabilityThresholds,chipScreenCounts,chipScreenState,validateChipPricePosition,validateChipStability,chipStabilityDetails,chipStabilityLabel} from '../../src/lib/chip-price-position.ts';
import {parseMarketManifest,parseMarketScreen} from '../../src/lib/chip-market.ts';
const days=['2026-09-28','2026-09-29','2026-09-30','2026-10-08','2026-10-09','2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16'];
const unknown=()=>({rule_id:chipUnchangedRule,status:'UNKNOWN',baseline_date:null,first_limit_up_date:null,evaluated_sessions:0,required_sessions:0,max_shift_fraction:null,min_mass_ratio:null,reasons:['CHIP_HISTORY_INCOMPLETE']});
const passed=()=>({...unknown(),status:'PASS',baseline_date:days.at(-2),first_limit_up_date:days.at(-1),evaluated_sessions:1,required_sessions:1,max_shift_fraction:'0.01',min_mass_ratio:'0.90',reasons:[]});
const failed=()=>({...passed(),status:'FAIL',max_shift_fraction:'0.01001',reasons:['PEAK_REGION_MOVED']});
const base=()=>({security_id:'999999',name:'INVENTED',market:'MAIN',close_cny:'5',prefilter_status:'PASS',recent_limit_up_dates:[days.at(-1)],chip_status:'READY',chip_unchanged_status:'UNKNOWN',chip_stability:unknown(),peak_prices_cny:['5.2'],previous_peak_prices_cny:[],original_peak_changes:[],model_mean_cost_cny:'5.3',reasons:[]});
const projected=row=>{const state=chipPricePosition(row);return {...row,above_peak_status:state.peak,above_mean_cost_status:state.mean,price_position_status:state.position,chip_unchanged_status:state.stability,screen_status:state.combined};};
const manifest=(final={PASS:0,FAIL:1,UNKNOWN:0})=>({schema:'stock.chips.market.manifest.v1',price_filter_rule:'NO_ABSOLUTE_PRICE_FILTER_V1',chip_unchanged_rule:chipUnchangedRule,chip_stability_thresholds:chipStabilityThresholds,dataset_id:'0123456789abcdef',active_slot:'0123456789abcdef',as_of:days.at(-1),previous_date:days.at(-2),official_sessions:days,universe_total:1,scanned_count:1,model_ready_count:1,screen_counts:{PASS:1,FAIL:0,UNKNOWN:0},market_complete:false,coefficient:2,unchanged_threshold:null,price_position_rule:chipPricePositionRule,final_screen_counts:final});

test('mean cost remains required; a peak match cannot replace it and exact mean precision matters',()=>{
 for(const [peaks,mean,expected] of [[['4.9'],'5.1','FAIL'],[['5.1'],'4.9','PASS'],[['5'],'5','FAIL'],[['4.8','5.5'],'5.6','FAIL'],[['5.2'],'4.999999999999999999','PASS'],[['5.2'],'4.999e0','PASS']]){
  const row=projected({...base(),peak_prices_cny:peaks,model_mean_cost_cny:mean});
  validateChipPricePosition(row);assert.equal(row.price_position_status,expected);assert.equal(row.screen_status,expected==='FAIL'?'FAIL':'UNKNOWN');
 }
 assert.equal(chipPricePosition({...base(),model_mean_cost_cny:null,peak_prices_cny:['4']}).combined,'UNKNOWN');
});

test('all three conditions must pass; known failures dominate unknown conditions',()=>{
 const pass=projected({...base(),model_mean_cost_cny:'4.9',chip_stability:passed()});
 validateChipPricePosition(pass);assert.equal(pass.screen_status,'PASS');
 assert.equal(chipPricePosition({...pass,prefilter_status:'FAIL'}).combined,'FAIL');
 assert.equal(chipPricePosition({...pass,prefilter_status:'UNKNOWN'}).combined,'UNKNOWN');
 assert.equal(chipPricePosition({...pass,prefilter_status:'UNKNOWN',chip_stability:failed()}).combined,'FAIL');
 assert.equal(chipPricePosition({...pass,chip_stability:unknown()}).combined,'UNKNOWN');
 assert.equal(chipPricePosition({...pass,chip_status:'MISSING'}).combined,'UNKNOWN');
 assert.equal(chipScreenState(base()),'UNKNOWN');
 assert.deepEqual(chipScreenCounts([projected(base()),pass,projected({...pass,chip_stability:unknown()})]),{PASS:1,FAIL:1,UNKNOWN:1});
});

test('stability boundaries compare exact decimals, including zero displacement and scientific notation',()=>{
 assert.equal(validateChipStability(passed()),'PASS');
 assert.equal(validateChipStability({...passed(),max_shift_fraction:'0',min_mass_ratio:'9e-1'}),'PASS');
 for(const value of ['0.010000000000000001','0.01001'])assert.throws(()=>validateChipStability({...passed(),max_shift_fraction:value}));
 for(const value of ['0.899999999999999999','0.89'])assert.throws(()=>validateChipStability({...passed(),min_mass_ratio:value}));
 for(const value of ['NaN','Infinity','-0.1','broken',1,null,undefined]){
  assert.throws(()=>validateChipStability({...passed(),max_shift_fraction:value}));
  assert.throws(()=>validateChipStability({...passed(),min_mass_ratio:value}));
 }
});

test('stability pass requires a complete dated comparison; unsupported failure is rejected',()=>{
 for(const update of [{baseline_date:null},{first_limit_up_date:null},{baseline_date:'2026-02-30'},{baseline_date:days.at(-1)},{evaluated_sessions:0},{evaluated_sessions:2},{required_sessions:0},{required_sessions:11},{evaluated_sessions:0.5},{rule_id:'TOP_PEAK_UNCHANGED_MANUAL_REVIEW_V1'}])assert.throws(()=>validateChipStability({...passed(),...update}));
 assert.equal(validateChipStability(failed()),'FAIL');
 assert.equal(validateChipStability({...failed(),max_shift_fraction:null,min_mass_ratio:'0.89',reasons:['ANCHOR_BAND_MASS_DECREASED']}),'FAIL');
 assert.throws(()=>validateChipStability({...passed(),status:'FAIL',reasons:['INVENTED_FAILURE']}));
 assert.throws(()=>validateChipStability({...failed(),evaluated_sessions:0}));
 assert.throws(()=>validateChipStability({...unknown(),reasons:[]}));
 assert.equal(validateChipStability({...unknown(),max_shift_fraction:'0.02',reasons:['PRICE_BASIS_INCOMPARABLE']}),'UNKNOWN');
});

test('a pass summary cannot carry a contradictory data-gap or failure reason',()=>{
 assert.throws(()=>validateChipStability({...passed(),reasons:['PRICE_BASIS_INCOMPARABLE']}));
 assert.throws(()=>validateChipStability({...passed(),reasons:['PEAK_REGION_MOVED']}));
});

test('a coarse grid can prove failure with a strict displacement lower bound, without claiming a measured shift',()=>{
 const bounded={...failed(),max_shift_fraction:null,min_mass_ratio:'1.01',
  displacement_bound_evaluated_sessions:1,max_proven_shift_lower_bound_fraction:'0.01483165529933961377'};
 assert.equal(validateChipStability(bounded),'FAIL');
 assert.match(chipStabilityDetails({...base(),chip_stability:bounded}),/位移至少 1.48%/);
 for(const update of [
  {max_proven_shift_lower_bound_fraction:'0.01'},
  {max_proven_shift_lower_bound_fraction:'0.009999999999999999'},
  {max_proven_shift_lower_bound_fraction:'broken'},
  {displacement_bound_evaluated_sessions:0},
  {displacement_bound_evaluated_sessions:2},
  {displacement_bound_evaluated_sessions:undefined},
  {reasons:['NATIVE_RESOLUTION_INSUFFICIENT']},
 ])assert.throws(()=>validateChipStability({...bounded,...update}));
 assert.throws(()=>validateChipStability({...bounded,status:'PASS',max_shift_fraction:'0',reasons:[]}));
 const denominator='1'+'0'.repeat(70),numerator='1'+'0'.repeat(67)+'1';
 assert.equal(validateChipStability({...bounded,max_proven_shift_lower_bound_fraction:'0.01',
  max_proven_shift_lower_bound_exact_fraction:numerator+'/'+denominator}),'FAIL');
 for(const value of ['1/0','-1/5','bad','1/100'])assert.throws(()=>validateChipStability({...bounded,
  max_proven_shift_lower_bound_fraction:'0.01',max_proven_shift_lower_bound_exact_fraction:value}));
 assert.throws(()=>validateChipStability({...passed(),displacement_bound_evaluated_sessions:1,
  max_proven_shift_lower_bound_fraction:'0.5',max_proven_shift_lower_bound_exact_fraction:'0'}));
});

test('daily reader accepts new automatic pass and rejects forged summaries, counts and dated coverage',()=>{
 const m=parseMarketManifest(manifest({PASS:1,FAIL:0,UNKNOWN:0}));
 const row=projected({...base(),model_mean_cost_cny:'4.9',chip_stability:passed()});
 const screen={dataset_id:m.dataset_id,as_of:m.as_of,rows:[row]};
 assert.equal(parseMarketScreen(screen,m)[0].screen_status,'PASS');
 for(const update of [{chip_stability:undefined},{chip_unchanged_status:'UNKNOWN'},{above_mean_cost_status:'FAIL'},{model_mean_cost_cny:'NaN'},{chip_status:'MISSING'},{chip_stability:{...passed(),first_limit_up_date:days.at(-2),required_sessions:1}},{chip_stability:{...passed(),baseline_date:days.at(-3)}},{chip_stability:{...passed(),required_sessions:2,evaluated_sessions:2}},{chip_stability:{...passed(),first_limit_up_date:'2026-10-17',baseline_date:days.at(-2)}}])assert.throws(()=>parseMarketScreen({...screen,rows:[{...row,...update}]},m));
 assert.throws(()=>parseMarketScreen(screen,{...m,final_screen_counts:{PASS:0,FAIL:0,UNKNOWN:1}}));
 const pending=projected({...row,chip_stability:unknown()});
 assert.throws(()=>parseMarketScreen({...screen,rows:[{...pending,screen_status:'PASS'}]},m));
});

test('manual batches and altered automatic thresholds cannot masquerade as the adopted rule',()=>{
 assert.throws(()=>parseMarketManifest({...manifest(),chip_unchanged_rule:'TOP_PEAK_UNCHANGED_MANUAL_REVIEW_V1'}),/自动.*等待|等待.*自动/);
 assert.throws(()=>parseMarketManifest({...manifest(),price_position_rule:'UNKNOWN_POLICY'}));
 assert.equal(parseMarketManifest({...manifest(),chip_stability_thresholds:undefined}).chip_unchanged_rule,chipUnchangedRule);
 for(const update of [{max_shift_fraction:'0.02'},{min_mass_ratio:'0.89'},{top_height_ratio:'0.89'},{top_height_ratio:0.90},{unexpected:'value'}])assert.throws(()=>parseMarketManifest({...manifest(),chip_stability_thresholds:{...chipStabilityThresholds,...update}}));
});

test('stability display uses Chinese labels and never exposes new internal reason keys',()=>{
 const row={...base(),chip_stability:{...unknown(),reasons:['NATIVE_RESOLUTION_INSUFFICIENT','FUTURE_UNKNOWN_REASON']}};
 assert.equal(chipStabilityLabel(row),'顶格判断数据不足');
 assert.match(chipStabilityDetails(row),/分辨率不足/);
 assert.doesNotMatch(chipStabilityDetails(row),/NATIVE_|FUTURE_|NaN/);
 assert.match(chipStabilityDetails({...base(),chip_unchanged_status:'PASS',chip_stability:passed()}),/最大偏移 1.00%.*原区占比最低保留 90.00%（相对基准）/);
});
