import test from 'node:test';
import assert from 'node:assert/strict';
import {parseChipHistory,parseChipHistoryBatch} from '../../src/lib/chip-history.ts';
import {tradingGeometry} from '../../src/lib/chip-market.ts';
const stock={security_id:'999999',name:'INVENTED',dataset_id:'0123456789abcdef',as_of:'2026-10-19'};
function sample(){
 const snapshot=day=>({source_date:day,window_source_start_date:'2025-12-12',model_version:'chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1',price_grid_kind:'EASTMONEY_150_DYNAMIC_NODES',price_grid:{low_cny:'4.5',high_cny:'5.99',step_cny:'0.01',node_count:150},history_window_bars:210,loaded_window_rows:210,history_rows_processed:day==='2026-10-15'?210:209,peak_prices_cny:['5.00'],peak_bin_share_percent:'100',model_mean_cost_cny:'5',close_cny:'5',turnover_percent:'1',comparison_anchor_shares_percent:{'5.00':'100'},profile:Array.from({length:150},(_,i)=>({price_cny:(4.5+i*.01).toFixed(2),share_percent:i===50?'100':'0'}))});
 return {...stock,schema:'stock.chips.history.v1',published_as_of:stock.as_of,selected_date:'2026-10-15',previous_date:'2026-10-14',model:{version:'chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1',historical_turnover_decay_coefficient:'2',price_grid_kind:'EASTMONEY_150_DYNAMIC_NODES',price_node_count:150,history_window_bars:210,price_bin_width_cny:null},unchanged_threshold:null,comparison_status:'READY',reasons:[],snapshots:{'2026-10-14':snapshot('2026-10-14'),'2026-10-15':snapshot('2026-10-15')}};
}
test('history response binds the selected day, stock, batch, comparison window and unset threshold',()=>{
 const v=sample();assert.equal(parseChipHistory(v,stock,'2026-10-15').selected_date,'2026-10-15');
 for(const patch of [{dataset_id:'fedcba9876543210'},{security_id:'888888'},{selected_date:'2026-10-16'},{published_as_of:'2026-10-16'},{unchanged_threshold:0}])assert.throws(()=>parseChipHistory({...v,...patch},stock,'2026-10-15'));
 const different=structuredClone(v);different.snapshots['2026-10-14'].window_source_start_date='2025-12-11';assert.throws(()=>parseChipHistory(different,stock,'2026-10-15'));
 const missing=structuredClone(v);delete missing.snapshots['2026-10-15'].comparison_anchor_shares_percent;assert.throws(()=>parseChipHistory(missing,stock,'2026-10-15'));
});
test('batched frames must match every requested day, stock and original model',()=>{
 const s={...stock,history:[{date:'2026-10-15'}]},v={schema:'stock.chips.history.batch.v1',dataset_id:s.dataset_id,security_id:s.security_id,published_as_of:s.as_of,selected_date:'2026-10-15',dates:['2026-10-15','2026-10-19'],frames:{'2026-10-15':sample()},errors:{'2026-10-19':'INVENTED'}};
 assert.equal(parseChipHistoryBatch(v,s,'2026-10-15')['2026-10-15'].selected_date,'2026-10-15');
 for(const patch of [{security_id:'888888'},{dates:['2026-10-15']},{errors:{}},{selected_date:'2026-10-19'}])assert.throws(()=>parseChipHistoryBatch({...v,...patch},s,'2026-10-15'));
 const wrong=structuredClone(v);wrong.frames['2026-10-15'].unchanged_threshold=0;assert.throws(()=>parseChipHistoryBatch(wrong,s,'2026-10-15'));
});
test('unconfirmed previous session can show only current chips without inventing a comparison',()=>{
 const v=sample();v.comparison_status='PREVIOUS_SESSION_UNAVAILABLE';delete v.snapshots['2026-10-14'];
 assert.equal(parseChipHistory(v,stock,'2026-10-15').comparison_status,'PREVIOUS_SESSION_UNAVAILABLE');
 v.comparison_status='READY';assert.throws(()=>parseChipHistory(v,stock,'2026-10-15'));
});
test('selecting an older candle brings that date into the chosen chart period',()=>{
 const history=Array.from({length:500},(_,i)=>({date:new Date(Date.UTC(2024,0,i+1)).toISOString().slice(0,10),open:'5',high:'6',low:'4',close:'5',volume_shares:'100'}));
 const older=history[150].date,g=tradingGeometry(history,null,null,120,343,older);
 assert.equal(g.candles.length,120);assert.ok(g.candles.some(c=>c.date===older));assert.equal(g.W,343);
 assert.equal(tradingGeometry(history,null,null,120,343,history[0].date).candles.length,120);
 assert.equal(tradingGeometry(history,null,null,120,343,history.at(-1).date).candles.at(-1).date,history.at(-1).date);
});
