import test from 'node:test';
import assert from 'node:assert/strict';
import {parseGoldenMarket,parseGoldenHistory,goldenLines,goldenVersion} from '../../src/lib/golden-grid.ts';

const manifest={dataset_id:'a'.repeat(16),as_of:'2026-10-19',universe_total:1};
const geometry={H:'20',L:'14',H_date:'2026-08-01',L_date:'2026-09-01',ratio:'0.25',midpoint:'15.5',upper:'17',drawdown_percent:'30',close:'16',main_midpoint:'17',quarter:'15.5',eighth:'14.75',close_side:'ABOVE',distance_percent:'3.22580645',main_space_position_percent:'33.33333333',intraday_touch:true,crossing:'UP',comparison:'SAME_VALID_STRUCTURE'};
const fixture=()=>({schema:'stock.golden-grid.market.v1',dataset_id:manifest.dataset_id,as_of:manifest.as_of,version:goldenVersion,universe_total:1,counts:{PASS:1,FAIL:0,UNKNOWN:0},rows:[{security_id:'999999',name:'FICTIONAL',as_of:manifest.as_of,version:goldenVersion,status:'PASS',reason:'DECLARED_60_5_STRUCTURE',geometry:structuredClone(geometry),known_at:null,research_only:true}]});
test('all-market denominator and batch identity are enforced',()=>{
  assert.equal(parseGoldenMarket(fixture(),manifest).rows.length,1);
  assert.throws(()=>parseGoldenMarket({...fixture(),dataset_id:'b'.repeat(16)},manifest));
  assert.throws(()=>parseGoldenMarket({...fixture(),counts:{PASS:0,FAIL:1,UNKNOWN:0}},manifest));
  assert.throws(()=>parseGoldenMarket({...fixture(),rows:[]},manifest));
});
test('failed or changed structures cannot display a confirmed crossing',()=>{
  const changed=fixture();changed.rows[0].geometry.comparison='STRUCTURE_CHANGED';
  assert.throws(()=>parseGoldenMarket(changed,manifest));
  changed.rows[0].geometry.crossing='UNKNOWN';
  assert.equal(parseGoldenMarket(changed,manifest).rows[0].geometry.crossing,'UNKNOWN');
  const failed=fixture();failed.rows[0].status='FAIL';failed.counts={PASS:0,FAIL:1,UNKNOWN:0};
  assert.throws(()=>parseGoldenMarket(failed,manifest));
});
test('coincident half/quarter levels retain their roles without duplicate lines',()=>{
  const lines=goldenLines(geometry);
  assert.equal(lines.length,5);
  assert.equal(lines.find(l=>Number(l.price)===15.5).active,true);
  assert.match(lines.find(l=>Number(l.price)===17).label,/50%.*子空间上沿/);
});
test('candle source remains independent of chip readiness and rejects mixed identities',()=>{
  const frame={schema:'stock.chips.market.stock.v1',dataset_id:manifest.dataset_id,as_of:manifest.as_of,security_id:'999999',history:[{date:manifest.as_of,open:'15',high:'17',low:'14',close:'16',volume_shares:'100',amount_cny:'1600',turnover_percent:'1'}]};
  assert.equal(parseGoldenHistory(frame,'999999',manifest).history[0].close,'16');
  assert.throws(()=>parseGoldenHistory(frame,'000001',manifest));
  frame.history[0].low='18';assert.throws(()=>parseGoldenHistory(frame,'999999',manifest));
});
