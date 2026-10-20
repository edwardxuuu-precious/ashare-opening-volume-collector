import test from 'node:test';
import assert from 'node:assert/strict';
import {replayLocalHistory,prepareLocalHistory} from '../../src/lib/chip-local-history.ts';
const version='chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1';
function fixture(){
 const history=Array.from({length:208},(_,i)=>({date:new Date(Date.UTC(2025,0,i+1)).toISOString().slice(0,10),open:'5',high:'5',low:'5',close:'5',volume_shares:'100',amount_cny:'500',turnover_percent:'50'}));
 history.push({...history[0],date:'2026-10-16'},{...history[0],date:'2026-10-19',open:'10',high:'10',low:'10',close:'10',amount_cny:'1000'});
 return {security_id:'999999',name:'INVENTED',dataset_id:'0123456789abcdef',as_of:'2026-10-19',history,snapshots:{},execution_identity:{files:{'eastmoney_model.py':'f95add1f60ec5fe4fdd5c9e9d6ffc8844b91283307b9525c61e8ba18a3529896'}},model:{version,historical_turnover_decay_coefficient:'2',history_window_bars:210,price_node_count:150,price_bin_width_cny:null,price_grid_kind:'EASTMONEY_150_DYNAMIC_NODES'},unchanged_threshold:null};
}
test('coefficient two replaces all mass at 50% turnover while the old peak interval becomes empty',()=>{
 const s=fixture(),v=replayLocalHistory(s,s.as_of),before=v.snapshots['2026-10-16'],after=v.snapshots[s.as_of];
 assert.deepEqual(before.peak_prices_cny,['5.00']);assert.deepEqual(after.peak_prices_cny,['10.00']);
 assert.equal(Number(before.peak_bin_share_percent),100);assert.equal(Number(after.peak_bin_share_percent),100);
 assert.equal(Number(before.comparison_anchor_shares_percent['5.00']),100);assert.equal(Number(after.comparison_anchor_shares_percent['5.00']),0);
 assert.equal(after.loaded_window_rows,210);assert.equal(before.history_rows_processed,209);assert.equal(v.unchanged_threshold,null);
 const changed=structuredClone(s);changed.history.push({...s.history.at(-1),date:'2026-10-20',high:'900',close:'800'});
 assert.deepEqual(replayLocalHistory(changed,s.as_of),v,'future bars never enter a selected historical window');
});
test('short windows, missing prior sessions and mismatched sealed snapshots use the server fallback',()=>{
 const s=fixture();assert.equal(replayLocalHistory({...s,history:s.history.slice(1)},s.as_of),null);
 assert.equal(replayLocalHistory({...s,history:s.history.filter(b=>b.date!=='2026-10-16')},s.as_of),null);
 const holiday=fixture();holiday.history.at(-2).date='2026-09-30';holiday.history.at(-1).date='2026-10-01';assert.equal(replayLocalHistory(holiday,'2026-10-01'),null);
 const v=replayLocalHistory(s,s.as_of);s.snapshots=v.snapshots;
 assert.deepEqual(prepareLocalHistory({...s,execution_identity:{files:{}}}),{});
 s.snapshots[s.as_of].peak_bin_share_percent='99';assert.deepEqual(prepareLocalHistory(s),{});
});
