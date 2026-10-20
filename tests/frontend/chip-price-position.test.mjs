import test from 'node:test';
import assert from 'node:assert/strict';
import {chipPricePosition,chipPricePositionRule,chipScreenCounts,chipScreenState,validateChipPricePosition} from '../../src/lib/chip-price-position.ts';
import {parseMarketManifest,parseMarketScreen} from '../../src/lib/chip-market.ts';
const base=()=>({security_id:'999999',name:'INVENTED',market:'MAIN',close_cny:'5',prefilter_status:'PASS',recent_limit_up_dates:['2026-10-16'],chip_status:'READY',peak_prices_cny:['5.2'],previous_peak_prices_cny:[],original_peak_changes:[],model_mean_cost_cny:'5.3',reasons:[]});
const projected=row=>{const state=chipPricePosition(row);return {...row,above_peak_status:state.peak,above_mean_cost_status:state.mean,price_position_status:state.position,screen_status:state.combined};};
test('new position is an OR inside the existing conjunction; equality fails and exact mean precision matters',()=>{
 for(const [peaks,mean,expected] of [[['4.9'],'5.1','PASS'],[['5.1'],'4.9','PASS'],[['4.9'],'4.8','PASS'],[['5'],'5','FAIL'],[['5.2'],'5.3','FAIL'],[['4.8','5.5'],'5.6','PASS'],[['5.2'],'4.999999999999999999','PASS'],[['5.2'],'4.999e0','PASS']]){
  const row=projected({...base(),peak_prices_cny:peaks,model_mean_cost_cny:mean});
  validateChipPricePosition(row);assert.equal(row.screen_status,expected);
 }
 assert.equal(chipPricePosition({...base(),model_mean_cost_cny:'4',prefilter_status:'FAIL'}).combined,'FAIL');
 assert.equal(chipPricePosition({...base(),model_mean_cost_cny:'4',prefilter_status:'UNKNOWN'}).combined,'UNKNOWN');
});
test('missing chip evidence stays unknown, while a known false conjunct excludes; legacy pass cannot satisfy a new condition',()=>{
 assert.equal(chipPricePosition({...base(),chip_status:'MISSING'}).combined,'UNKNOWN');
 assert.equal(chipPricePosition({...base(),model_mean_cost_cny:null}).combined,'UNKNOWN');
 assert.equal(chipPricePosition({...base(),model_mean_cost_cny:null,peak_prices_cny:['4']}).combined,'PASS');
 assert.equal(chipPricePosition({...base(),model_mean_cost_cny:null,prefilter_status:'FAIL'}).combined,'FAIL');
 assert.equal(chipScreenState(base()),'UNKNOWN');
 assert.deepEqual(chipScreenCounts([projected(base()),projected({...base(),model_mean_cost_cny:'4'}),base()]),{PASS:1,FAIL:1,UNKNOWN:1});
});
test('real daily reader rejects forged position matches, altered counts, and unsupported policy identity',()=>{
 const days=['2026-09-28','2026-09-29','2026-09-30','2026-10-08','2026-10-09','2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16'];
 const m={schema:'stock.chips.market.manifest.v1',dataset_id:'0123456789abcdef',active_slot:'0123456789abcdef',as_of:days.at(-1),previous_date:days.at(-2),official_sessions:days,universe_total:1,scanned_count:1,model_ready_count:1,screen_counts:{PASS:1,FAIL:0,UNKNOWN:0},market_complete:true,coefficient:2,unchanged_threshold:null,price_position_rule:chipPricePositionRule,final_screen_counts:{PASS:0,FAIL:1,UNKNOWN:0}};
 const manifest=parseMarketManifest(m),row=projected(base()),screen={dataset_id:m.dataset_id,as_of:m.as_of,rows:[row]};
 assert.equal(parseMarketScreen(screen,manifest)[0].screen_status,'FAIL');
 assert.throws(()=>parseMarketScreen({...screen,rows:[{...row,screen_status:'PASS'}]},manifest));
 assert.throws(()=>parseMarketScreen({...screen,rows:[{...row,above_mean_cost_status:'PASS'}]},manifest));
 assert.throws(()=>parseMarketScreen(screen,{...manifest,final_screen_counts:{PASS:1,FAIL:0,UNKNOWN:0}}));
 assert.throws(()=>parseMarketManifest({...m,price_position_rule:'UNKNOWN_POLICY'}));
 assert.throws(()=>parseMarketScreen({...screen,rows:[{...row,model_mean_cost_cny:'NaN'}]},manifest));
});
