// New, wholly invented display contracts. No saved cases or network are used.
import test from 'node:test';
import assert from 'node:assert/strict';
import {parseMarketManifest,parseMarketScreen,parseMarketStock,currentMarketReasons,tradingGeometry} from '../../src/lib/chip-market.ts';
const days=['2026-09-28','2026-09-29','2026-09-30','2026-10-08','2026-10-09','2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16'];
const manifest=()=>({schema:'stock.chips.market.manifest.v1',price_filter_rule:'NO_ABSOLUTE_PRICE_FILTER_V1',price_position_rule:'CLOSE_GT_MEAN_COST_V2',chip_unchanged_rule:'TOP_REGION_STABLE_1_90_90_V1',final_screen_counts:{PASS:0,FAIL:0,UNKNOWN:1},dataset_id:'0123456789abcdef',active_slot:'0123456789abcdef',as_of:days[9],previous_date:days[8],official_sessions:days,universe_total:1,scanned_count:1,model_ready_count:0,screen_counts:{PASS:1,FAIL:0,UNKNOWN:0},market_complete:false,coefficient:2,unchanged_threshold:null,generated_at:'2026-10-18T01:00:00Z',calendar_source:'https://invented.invalid'});
const row=()=>({security_id:'999999',name:'ALL_VALUES_INVENTED',market:'MAIN',close_cny:'14.50',prefilter_status:'PASS',recent_limit_up_dates:[days[9]],chip_status:'MISSING',peak_prices_cny:[],previous_peak_prices_cny:[],original_peak_changes:[],model_mean_cost_cny:null,above_peak_status:'UNKNOWN',above_mean_cost_status:'UNKNOWN',price_position_status:'UNKNOWN',chip_unchanged_status:'UNKNOWN',chip_stability:{rule_id:'TOP_REGION_STABLE_1_90_90_V1',status:'UNKNOWN',baseline_date:null,first_limit_up_date:null,evaluated_sessions:0,required_sessions:0,max_shift_fraction:null,min_mass_ratio:null,reasons:['CHIP_HISTORY_INCOMPLETE']},screen_status:'UNKNOWN',reasons:['INVENTED_MISSING_MODEL']});
test('daily list can pass limit-up prefilter while mean cost and chip stability stay unknown',()=>{const m=parseMarketManifest(manifest());assert.equal(parseMarketScreen({dataset_id:m.dataset_id,as_of:m.as_of,rows:[row()]},m)[0].prefilter_status,'PASS');assert.throws(()=>parseMarketManifest({...manifest(),unchanged_threshold:0.01}));});
test('daily readers reject mixed generations and impossible pass rows',()=>{const m=parseMarketManifest(manifest());assert.throws(()=>parseMarketManifest({...manifest(),active_slot:'fedcba9876543210'}));assert.throws(()=>parseMarketScreen({dataset_id:m.dataset_id,as_of:m.as_of,rows:[{...row(),recent_limit_up_dates:[]}]},m));assert.throws(()=>parseMarketScreen({dataset_id:'fedcba9876543210',as_of:m.as_of,rows:[row()]},m));});

test('current rule rejects legacy price batches and admits prices on both sides of ten yuan',()=>{
  assert.throws(()=>parseMarketManifest({...manifest(),price_filter_rule:undefined}),/等待/);
  assert.throws(()=>parseMarketManifest({...manifest(),price_filter_rule:'CLOSE_LT_10_CNY_V1'}),/等待/);
  assert.throws(()=>parseMarketManifest({...manifest(),price_filter_rule:'CLOSE_GT_10_CNY_V1'}),/等待/);
  const m=parseMarketManifest(manifest());
  for(const price of ['0.99','9.99','10.00']) assert.equal(parseMarketScreen({dataset_id:m.dataset_id,as_of:m.as_of,rows:[{...row(),close_cny:price}]},m)[0].prefilter_status,'PASS');
  assert.equal(parseMarketScreen({dataset_id:m.dataset_id,as_of:m.as_of,rows:[{...row(),close_cny:'10.01'}]},m)[0].prefilter_status,'PASS');
});
test('explicit missing stock frame retains its reason without drawing a fabricated candle',()=>{const m=parseMarketManifest(manifest());const s=parseMarketStock({schema:'stock.chips.market.stock.v1',dataset_id:m.dataset_id,as_of:m.as_of,security_id:'999999',chip_status:'MISSING',history:[],snapshots:{},reasons:['INVENTED_SOURCE_MISSING']},'999999',m);assert.equal(s.reasons[0],'INVENTED_SOURCE_MISSING');assert.equal(tradingGeometry(s.history,null,null,120),null);});
test('chart uses a shared price mapping and one dated record for candle and volume',()=>{const history=[{date:days[8],open:'4.80',high:'5.20',low:'4.60',close:'5.00',volume_shares:'1000'},{date:days[9],open:'5.00',high:'5.30',low:'4.90',close:'5.10',volume_shares:'2000'}];const snapshot={peak_prices_cny:['5.00'],profile:[{price_cny:'5.00',share_percent:'100'}]};const g=tradingGeometry(history,snapshot,null,120);assert.equal(g.candles[0].closeY,g.y(5));assert.deepEqual(g.candles.map(c=>c.date),days.slice(-2));assert.equal(g.candles[1].volumeY,g.volTop);assert.ok(g.candles[0].volumeY>g.candles[1].volumeY);assert.ok(g.chips.some(c=>Math.abs(c.price-5)<g.bandWidth&&c.currentWidth>0));assert.equal(g.outsideShare,0);});

test('resizing keeps the full price-volume-chip layout inside the viewport without changing the data',()=>{
  const history=Array.from({length:210},(_,i)=>({date:`row-${i}`,open:'98',high:'102',low:'97',close:'100',volume_shares:String(1000+i)}));
  const current={peak_prices_cny:['100.00'],profile:[{price_cny:'100.00',share_percent:'70'},{price_cny:'99.00',share_percent:'30'}]};
  const previous={peak_prices_cny:['99.00'],profile:[{price_cny:'100.00',share_percent:'20'},{price_cny:'99.00',share_percent:'80'}]};
  const baseline=tradingGeometry(history,current,previous,120);
  for(const width of [288,358,689,976,1408]){
    const g=tradingGeometry(history,current,previous,120,width);
    assert.equal(g.W,width);
    assert.ok(0<g.left&&g.left<g.right&&g.right+40<=g.chipStart&&g.chipStart<g.chipEnd&&g.chipEnd<width);
    assert.ok(g.top<g.bottom&&g.bottom<g.volumeDivider&&g.volumeDivider<g.volTop&&g.volTop<g.volBottom&&g.volBottom<g.H);
    assert.deepEqual(g.candles.map(c=>c.date),baseline.candles.map(c=>c.date));
    assert.deepEqual(g.chips.map(c=>[c.mass,c.previousMass]),baseline.chips.map(c=>[c.mass,c.previousMass]));
    assert.equal(g.candles.at(-1).volumeY,g.volTop);
    assert.ok(g.chips.every(c=>g.chipStart+c.currentWidth<=g.chipEnd&&g.chipStart+c.previousWidth<=g.chipEnd));
    const ticks=[...g.dateTicks].sort((a,b)=>a-b);
    assert.equal(ticks[0],0);assert.equal(ticks.at(-1),g.candles.length-1);
    for(let i=1;i<ticks.length;i++)assert.ok(g.candles[ticks[i]].x-g.candles[ticks[i-1]].x>40);
  }
});

test('first listing is current-only and requires matching first-day evidence',()=>{
  const m=parseMarketManifest(manifest()),code='999999';
  const s={schema:'stock.chips.market.stock.v1',dataset_id:m.dataset_id,as_of:m.as_of,security_id:code,name:'ALL_VALUES_INVENTED',chip_status:'READY',
    model:{historical_turnover_decay_coefficient:'2',price_bin_width_cny:'0.01'},unchanged_threshold:null,reasons:[],
    comparison_status:'NO_PREVIOUS_LISTED_SESSION',reported_listing_date:m.as_of,
    history:[{date:m.as_of,open:'5',high:'5',low:'5',close:'5',volume_shares:'1000',amount_cny:'5000',turnover_percent:'1'}],
    snapshots:{[m.as_of]:{peak_prices_cny:['5.00'],peak_bin_share_percent:'100',model_mean_cost_cny:'5',close_cny:'5',turnover_percent:'1',profile:[{price_cny:'5.00',share_percent:'100'}]}}};
  assert.equal(parseMarketStock(s,code,m).comparison_status,'NO_PREVIOUS_LISTED_SESSION');
  assert.throws(()=>parseMarketStock({...s,reported_listing_date:m.previous_date},code,m));
  assert.throws(()=>parseMarketStock({...s,comparison_status:undefined},code,m));
});

test('repaired screening replaces obsolete frame notes while preserving model warnings and raw input',()=>{
  const stock={security_id:'999999',reasons:['LIMIT_HISTORY_INCOMPLETE','REFERENCE_QUANTITY_CORROBORATION_ERROR:HTTPError','MODEL_RESEEDED_AFTER_INVALID_SOURCE_HISTORY']};
  const before=structuredClone(stock);
  assert.deepEqual(currentMarketReasons(stock,{...row(),reasons:[]}),['MODEL_RESEEDED_AFTER_INVALID_SOURCE_HISTORY']);
  assert.deepEqual(stock,before);
  assert.throws(()=>currentMarketReasons(stock,{...row(),security_id:'000001'}));
});

test('current unknown and current source warnings never disappear from detail notes',()=>{
  const stock={security_id:'999999',reasons:['LIMIT_HISTORY_INCOMPLETE','MODEL_RESEEDED_AFTER_INVALID_SOURCE_HISTORY']};
  const unknown={...row(),prefilter_status:'UNKNOWN',reasons:['LIMIT_HISTORY_INCOMPLETE','HISTORICAL_REFERENCE_FETCH_ERROR:TimeoutError']};
  assert.ok(currentMarketReasons(stock,unknown).includes('LIMIT_HISTORY_INCOMPLETE'));
  assert.ok(currentMarketReasons(stock,unknown).includes('HISTORICAL_REFERENCE_FETCH_ERROR:TimeoutError'));
  assert.ok(currentMarketReasons(stock,{...row(),reasons:['LIMIT_HISTORY_INCOMPLETE']}).includes('LIMIT_HISTORY_INCOMPLETE'));
  assert.deepEqual(currentMarketReasons(stock),stock.reasons);
});
