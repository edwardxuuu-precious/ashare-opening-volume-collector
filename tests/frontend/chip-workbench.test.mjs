import test from 'node:test';
import assert from 'node:assert/strict';
import {initialWorkbenchStock,workbenchQuote} from '../../src/lib/chip-workbench.ts';
import {tradingGeometry} from '../../src/lib/chip-market.ts';
const bar=(date,close,volume='1000')=>({date,open:close,high:close,low:close,close,volume_shares:volume,amount_cny:'10000',turnover_percent:'2'});
test('initial viewing prefers a valid existing selection and does not turn unknown stocks into matches',()=>{
 const rows=[{security_id:'unknown',prefilter_status:'UNKNOWN'},{security_id:'failed',prefilter_status:'FAIL'},{security_id:'pass',prefilter_status:'PASS',screen_status:'PASS'}];
 assert.equal(initialWorkbenchStock(rows,''),'pass');assert.equal(initialWorkbenchStock(rows,'failed'),'failed');
 assert.equal(initialWorkbenchStock(rows.slice(0,2),''),'unknown');assert.equal(initialWorkbenchStock([],''),'');assert.equal(rows[0].prefilter_status,'UNKNOWN');
});
test('historical header follows the selected candle and discloses a carried close without inventing a daily change',()=>{
 const history=[bar('2026-10-15','4.06'),bar('2026-10-16','4.47'),bar('2026-10-19','4.31')];
 const selected=workbenchQuote(history,'2026-10-16',null);assert.equal(selected.close,4.47);assert.ok(Math.abs(selected.change-.41)<1e-12);assert.ok(Math.abs(selected.percent-(4.47/4.06-1)*100)<1e-12);
 assert.deepEqual(workbenchQuote(history,'2026-10-20',{close_cny:'4.31'}),{close:4.31,change:null,percent:null,hasCandle:false});
 assert.deepEqual(workbenchQuote(history,'2026-10-20',null),{close:null,change:null,percent:null,hasCandle:false});
 assert.equal(workbenchQuote(history,'2026-10-15',null).change,null);
});
test('workbench height keeps candles, volume and both profiles on the same price scale at narrow and wide sizes',()=>{
 const history=[bar('2026-10-16','4.47','1000'),bar('2026-10-19','4.31','2000')];
 const current={peak_prices_cny:['4.31'],profile:[{price_cny:'4.31',share_percent:'100'}]},previous={peak_prices_cny:['4.47'],profile:[{price_cny:'4.47',share_percent:'100'}]};
 for(const width of [290,640,1200])for(const height of [280,330,480]){
  const g=tradingGeometry(history,current,previous,120,width,'2026-10-16',height);
  assert.equal(g.H,height);assert.equal(g.candles[1].closeY,g.y(4.31));assert.equal(g.candles[1].volumeY,g.volTop);
  assert.ok(g.left<g.right&&g.right<g.chipStart&&g.chipStart<g.chipEnd&&g.chipEnd<g.W);
  assert.ok(g.top<g.bottom&&g.bottom<g.volumeDivider&&g.volumeDivider<g.volTop&&g.volTop<g.volBottom);
  assert.ok(g.chips.some(c=>c.currentWidth>0));assert.ok(g.chips.some(c=>c.previousWidth>0));
 }
});
