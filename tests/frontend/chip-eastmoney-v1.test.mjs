// Invented dynamic-grid contracts; no network or real market snapshots.
import test from 'node:test';
import assert from 'node:assert/strict';
import {parseChipSample,anchoredShare} from '../../src/lib/chips.ts';

const version='chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1';
const dates=['2026-10-15','2026-10-16'];
function sample(){
  const step=3.01/149;
  const snapshots=Object.fromEntries(dates.map((date,i)=>[date,{
    model_version:version,price_grid_kind:'EASTMONEY_150_DYNAMIC_NODES',
    price_grid:{low_cny:'5',high_cny:'8.01',step_cny:String(step),node_count:150},
    history_window_bars:210,loaded_window_rows:210,history_rows_processed:209+i,
    peak_prices_cny:['5.20'],peak_bin_share_percent:String(i?60:100),model_mean_cost_cny:'5.2',close_cny:'5.2',turnover_percent:'2',
    comparison_anchor_shares_percent:{'5.20':String(i?60:100)},
    profile:Array.from({length:150},(_,j)=>({price_cny:String(5+step*j),share_percent:String(j===10?(i?60:100):j===11&&i?40:0)})),
  }]));
  return {security_id:'999999',name:'ALL_VALUES_INVENTED',unchanged_threshold:null,
    model:{version,historical_turnover_decay_coefficient:'2',price_grid_kind:'EASTMONEY_150_DYNAMIC_NODES',price_node_count:150,history_window_bars:210,price_bin_width_cny:null},snapshots};
}
test('dynamic node reader uses fixed original interval occupancy, not rounded peak lookup',()=>{
  const s=parseChipSample(sample(),'999999',dates);
  assert.equal(s.snapshots[dates[0]].profile.find(p=>p.price_cny==='5.20'),undefined);
  assert.equal(anchoredShare(s.snapshots[dates[0]],'5.20'),100);
  assert.equal(anchoredShare(s.snapshots[dates[1]],'5.20'),60);
});
test('dynamic model rejects wrong window, node count and missing comparison anchors',()=>{
  const wrong=sample();wrong.model.history_window_bars=500;assert.throws(()=>parseChipSample(wrong,'999999',dates));
  const short=sample();short.snapshots[dates[1]].profile.pop();assert.throws(()=>parseChipSample(short,'999999',dates));
  const missing=sample();delete missing.snapshots[dates[1]].comparison_anchor_shares_percent;assert.throws(()=>parseChipSample(missing,'999999',dates));
  assert.throws(()=>anchoredShare(missing.snapshots[dates[1]],'5.20'));
});
