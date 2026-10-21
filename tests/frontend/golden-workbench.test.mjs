import test from 'node:test';
import assert from 'node:assert/strict';
import {manualLines,autoLines,parseWatchManifest} from '../../src/lib/golden-workbench.ts';
test('manual golden drawings preserve exact two chosen anchors and remain distinct',()=>{
 const p=[{date:'2020-01-01',field:'high',price:'20'},{date:'2020-01-02',field:'low',price:'10'}];
 assert.deepEqual(manualLines('HL',p).map(v=>v.price),[20,10,15,12.5,11.25]);
 assert.deepEqual(manualLines('HM',p).map(v=>v.price),[20,10,0]);
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
