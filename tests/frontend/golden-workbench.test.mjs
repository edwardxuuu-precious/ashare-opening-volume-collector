import test from 'node:test';
import assert from 'node:assert/strict';
import {manualLines,autoLines,parseWatchManifest} from '../../src/lib/golden-workbench.ts';
test('manual golden drawings preserve exact two chosen anchors and remain distinct',()=>{
 const p=[{date:'2020-01-01',field:'high',price:'20'},{date:'2020-01-02',field:'low',price:'10'}];
 assert.ok(Math.abs(manualLines('HL',p)[2].price-Math.sqrt(200))<1e-12);
 assert.deepEqual(manualLines('HM',p).map(v=>v.price),[20,10,5]);
 assert.ok(manualLines('HL',p).every(v=>v.manual));
 assert.deepEqual(manualLines('HL',p.slice(0,1)),[]);
 assert.deepEqual(manualLines('HL',p.toReversed()),[]);
});
test('unknown H or L never gets filled by a manual anchor',()=>{
 assert.deepEqual(autoLines({H:null,L:{price:'10'}},'HL',null),[]);
 assert.deepEqual(autoLines({H:{price:'20'},L:null},'HL',null),[{price:20,label:'H 高点'}]);
});
test('daily catalog requires all twenty unique samples',()=>{
 const m={schema:'stock.golden.watchlist.v1',version:'golden-workbench.auto20.v1',dataset_id:'a'.repeat(16),calculation_window:60,archives:[],rows:Array.from({length:20},(_,i)=>({symbol:String(i),history:[]}))};
 assert.equal(parseWatchManifest(m).rows.length,20);assert.throws(()=>parseWatchManifest({...m,rows:m.rows.slice(1)}));
 m.rows[1].symbol=m.rows[0].symbol;assert.throws(()=>parseWatchManifest(m));
});
test('legacy snapshots remain readable and unknown geometry is rejected', async()=>{
 const {parseAnnotations}=await import('../../src/lib/golden-workbench.ts');
 const drawing={id:'original',points:[{date:'2020-01-01',field:'high',price:'40'},{date:'2020-01-02',field:'low',price:'10'}],history:[]};
 const original={schema:'stock.golden.annotations.v1',symbol:'600519',revision:1,drawings:[drawing],reviews:[{status:'CORRECT',drawing_snapshot:drawing,history:[]}]};
 assert.deepEqual(parseAnnotations(original),original);
 assert.throws(()=>parseAnnotations({...original,drawings:[{...drawing,price_geometry:'unknown.v9'}]}));
 assert.throws(()=>parseAnnotations({...original,reviews:[{...original.reviews[0],auto_context:{price_geometry:'unknown.v9'}}]}));
});
test('example geometry must match its publication', async()=>{
 const {parseExample}=await import('../../src/lib/golden-workbench.ts');
 const manifest={dataset_id:'a'.repeat(16),as_of:'2026-11-04',price_geometry:'log-price.v1'};
 const example={schema:'stock.golden.example.v1',dataset_id:manifest.dataset_id,as_of:manifest.as_of,symbol:'600519',history:[],result:null,price_geometry:'log-price.v1'};
 assert.equal(parseExample(example,manifest,'600519').price_geometry,'log-price.v1');
 assert.throws(()=>parseExample({...example,price_geometry:undefined},manifest,'600519'));
 assert.throws(()=>parseExample({...example,price_geometry:'future.v2'},manifest,'600519'));
});
