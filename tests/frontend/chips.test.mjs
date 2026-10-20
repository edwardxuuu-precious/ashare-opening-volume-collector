import test from 'node:test';
import assert from 'node:assert/strict';
import { parseChipManifest, parseChipSample, parseChipScreen, anchoredShare, chipPlot } from '../../src/lib/chips.ts';

const manifest = () => ({ schema:'stock.chips.manifest.v1', dataset_id:'0123456789abcdef', as_of:'2001-01-03', dates:['2001-01-02','2001-01-03'], securities:[{code:'999999',name:'ALL_VALUES_INVENTED'}], covered_securities:1, market_complete:false, scope:'CALIBRATION_SAMPLE_ONLY', coefficient:2, unchanged_threshold:null });
const snapshot = (m, peak) => ({peak_prices_cny:[peak],peak_bin_share_percent:String(Math.max(...m)),model_mean_cost_cny:'5.004',close_cny:'5.00',turnover_percent:'10',profile:[{price_cny:'5.00',share_percent:String(m[0])},{price_cny:'5.01',share_percent:String(m[1])}]});
const sample = () => ({security_id:'999999',name:'ALL_VALUES_INVENTED',model:{historical_turnover_decay_coefficient:'2',price_bin_width_cny:'0.01'},unchanged_threshold:null,snapshots:{'2001-01-02':snapshot([80,20],'5.00'),'2001-01-03':snapshot([40,60],'5.01')}});
const screen = () => ({as_of:'2001-01-03',unchanged_threshold:null,screenings:[{security_id:'999999',name:'ALL_VALUES_INVENTED',as_of:'2001-01-03',close_cny:'5.00',conditions:{close_below_10:'PASS',closing_limit_up_in_10_sessions:'UNKNOWN',chip_unchanged:'UNKNOWN'},screen_status:'UNKNOWN'}]});

test('new chip reader keeps fixed model and sorted versioned catalog',()=>{
  assert.equal(parseChipManifest(manifest()).coefficient,2);
  assert.throws(()=>parseChipManifest({...manifest(),coefficient:1}));
  assert.throws(()=>parseChipManifest({...manifest(),dates:['2001-01-03','2001-01-02']}));
  assert.throws(()=>parseChipManifest({...manifest(),dataset_id:'../collector'}));
});
test('new chip reader rejects missing, negative or incomplete distribution data',()=>{
  assert.equal(parseChipSample(sample(),'999999',manifest().dates).security_id,'999999');
  const bad=sample();bad.snapshots['2001-01-03'].profile[0].share_percent='-1';
  assert.throws(()=>parseChipSample(bad,'999999',manifest().dates));
  const short=sample();short.snapshots['2001-01-03'].profile[0].share_percent='39';
  assert.throws(()=>parseChipSample(short,'999999',manifest().dates));
  assert.throws(()=>parseChipSample(sample(),'888888',manifest().dates));
});
test('new chip plot preserves an old price after the current peak moves',()=>{
  const a=sample().snapshots['2001-01-02'],b=sample().snapshots['2001-01-03'];
  assert.equal(anchoredShare(b,'5.00')-anchoredShare(a,'5.00'),-40);
  const p=chipPlot(b,a,true);
  assert.deepEqual(p,[{price:5,current:40,previous:80},{price:5.01,current:60,previous:20}]);
});
test('new screen reader never accepts a match before chip criterion exists',()=>{
  assert.equal(parseChipScreen(screen(),parseChipManifest(manifest()))[0].screen_status,'UNKNOWN');
  const bad=screen();bad.screenings[0].screen_status='PASS';
  assert.throws(()=>parseChipScreen(bad,parseChipManifest(manifest())));
  const wrong=screen();wrong.screenings[0].conditions.chip_unchanged='PASS';
  assert.throws(()=>parseChipScreen(wrong,parseChipManifest(manifest())));
});
test('new screen reader refuses mixed dates, duplicate codes and foreign securities',()=>{
  const bad=screen();bad.screenings[0].as_of='2001-01-04';
  assert.throws(()=>parseChipScreen(bad,parseChipManifest(manifest())));
  const foreign=screen();foreign.screenings[0].security_id='888888';
  assert.throws(()=>parseChipScreen(foreign,parseChipManifest(manifest())));
  const duplicate=screen();duplicate.screenings.push(duplicate.screenings[0]);
  assert.throws(()=>parseChipScreen(duplicate,parseChipManifest(manifest())));
});

test('grouped display never substitutes a band mass for an exact anchored share',()=>{
  const v=sample();
  for(const s of Object.values(v.snapshots)) Object.assign(s,{profile_format:'DISPLAY_BANDS_WITH_EXACT_ANCHORS',display_bin_width_cny:'0.02',
    original_profile_bin_count:5000,original_profile_sha256:'a'.repeat(64),exact_anchor_shares_percent:{'5.00':'0.7','5.01':'0.4'}});
  parseChipSample(v,'999999',manifest().dates);
  assert.equal(anchoredShare(v.snapshots['2001-01-03'],'5.00'),0.7);
  delete v.snapshots['2001-01-03'].exact_anchor_shares_percent['5.00'];
  assert.throws(()=>parseChipSample(v,'999999',manifest().dates));
  assert.throws(()=>anchoredShare(v.snapshots['2001-01-03'],'5.00'));
});
