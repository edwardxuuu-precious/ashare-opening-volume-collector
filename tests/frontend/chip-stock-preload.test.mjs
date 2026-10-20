import test from 'node:test';
import assert from 'node:assert/strict';
import {createChipStockPreload,neighboringChipStocks} from '../../src/lib/chip-stock-preload.ts';
const tick=()=>new Promise(r=>setImmediate(r));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
test('only actual matched-list neighbors are used, including across pagination boundaries',()=>{
 const codes=Array.from({length:205},(_,i)=>String(i));
 assert.deepEqual(neighboringChipStocks(codes,'99'),['100','98']);
 assert.deepEqual(neighboringChipStocks(['a','c','e'],'c'),['e','a']);
 assert.deepEqual(neighboringChipStocks(['a','c'],'a'),['c']);
 assert.deepEqual(neighboringChipStocks(['a','c'],'c'),['a']);
 assert.deepEqual(neighboringChipStocks([],'missing'),[]);
});
test('foreground navigation shares an in-flight neighbor read and its prepared history',async()=>{
 const pending=deferred();let reads=0,preparations=0,signal;
 const cache=createChipStockPreload(async(code,s)=>{reads++;signal=s;return pending.promise;},async stock=>{preparations++;return{frames:stock.code};});
 cache.protect('current');cache.prefetch(['next','previous']);await tick();
 cache.protect('next');const front=cache.acquireStock('next');cache.prefetch([]);
 pending.resolve({code:'next'});await front.promise;front.release();await tick();
 const preview=cache.acquirePreview('next');assert.deepEqual(await preview.promise,{frames:'next'});preview.release();
 assert.equal(reads,1);assert.equal(preparations,1);assert.equal(signal.aborted,false);
 assert.deepEqual(cache.peekStock('next'),{code:'next'});assert.deepEqual(cache.peekPreview('next'),{frames:'next'});cache.clear();
});
test('background is serial and a changed search discards queued old neighbors',async()=>{
 const pending=new Map(),started=[];let running=0,max=0;
 const cache=createChipStockPreload(async(code)=>{const p=deferred();pending.set(code,p);started.push(code);max=Math.max(max,++running);try{return await p.promise;}finally{running--;}},async stock=>stock);
 cache.protect('current');cache.prefetch(['old-active','old-queued','outside-budget']);await tick();
 cache.prefetch(['new-next','new-previous']);pending.get('old-active').resolve({code:'old-active'});await tick();
 assert.deepEqual(started,['old-active','new-next']);pending.get('new-next').resolve({code:'new-next'});await tick();
 assert.deepEqual(started,['old-active','new-next','new-previous']);pending.get('new-previous').resolve({code:'new-previous'});await tick();
 assert.equal(max,1);assert.equal(started.includes('old-queued'),false);assert.equal(started.includes('outside-budget'),false);cache.clear();
});
test('a failed preload retries on selection and never becomes a cached missing stock',async()=>{
 let reads=0;const cache=createChipStockPreload(async code=>{if(++reads===1)throw Error('temporary');return{code};},async stock=>stock);
 cache.prefetch(['a']);await tick();assert.equal(cache.peekStock('a'),undefined);
 const front=cache.acquireStock('a');assert.deepEqual(await front.promise,{code:'a'});front.release();assert.equal(reads,2);cache.clear();
});
test('data and prepared history share one bounded stock budget and preserve the current stock',async()=>{
 const cache=createChipStockPreload(async code=>({code}),async stock=>({code:stock.code}),2);cache.protect('pinned');
 for(const code of ['pinned','a','b','c']){const lease=cache.acquirePreview(code);await lease.promise;lease.release();}
 assert.ok(cache.peekStock('pinned'));assert.ok(cache.peekPreview('pinned'));assert.ok(cache.peekPreview('c'));
 assert.equal(cache.peekStock('a'),undefined);assert.equal(cache.peekPreview('a'),undefined);
 const present=['pinned','a','b','c'].filter(c=>cache.peekStock(c)||cache.peekPreview(c));assert.equal(present.length,2);cache.clear();
});
test('closing or refreshing aborts reads and late results cannot enter another dataset cache',async()=>{
 const pending=deferred();let signal;
 const first=createChipStockPreload(async(c,s)=>{signal=s;return pending.promise;},async stock=>stock);
 first.prefetch(['same-code']);await tick();first.clear();assert.equal(signal.aborted,true);
 pending.resolve({dataset:'old'});await tick();assert.equal(first.peekStock('same-code'),undefined);assert.equal(first.peekPreview('same-code'),undefined);
 const next=createChipStockPreload(async()=>({dataset:'new'}),async stock=>stock);const lease=next.acquirePreview('same-code');
 assert.deepEqual(await lease.promise,{dataset:'new'});lease.release();next.clear();
});

test('observable status follows queued, loading, worker preparation and ready without extra reads',async()=>{
 const read=deferred(),prepare=deferred();let reads=0,preparations=0;
 const cache=createChipStockPreload(async code=>{reads++;return read.promise;},async stock=>{preparations++;return prepare.promise;});
 const observed=[];const stop=cache.subscribe(()=>observed.push(cache.status('a')));
 assert.equal(cache.status('a'),null);cache.prefetch(['a','queued']);
 assert.equal(cache.status('a'),'preparing');assert.equal(cache.status('queued'),'preparing');
 cache.prefetch(['a']);assert.equal(cache.status('queued'),null);await tick();
 read.resolve({code:'a'});await tick();assert.ok(cache.peekStock('a'));assert.equal(cache.status('a'),'preparing');
 const foreground=cache.acquirePreview('a');prepare.resolve({frames:['valid']});await foreground.promise;foreground.release();await tick();
 assert.equal(cache.status('a'),'ready');assert.equal(reads,1);assert.equal(preparations,1);
 assert.ok(observed.includes('preparing'));assert.ok(observed.includes('ready'));cache.clear();assert.equal(cache.status('a'),null);stop();
});

test('fallback and failed preparation never advertise ready; selecting a failure retries',async()=>{
 let attempts=0;
 const cache=createChipStockPreload(async code=>({code}),async stock=>{if(stock.code==='failed'&&++attempts===1)throw Error('worker failure');return{frames:stock.code==='partial'?[]:['valid']};},5,value=>value.frames.length>0);
 cache.prefetch(['failed','partial']);await tick();
 assert.equal(cache.status('failed'),'error');assert.equal(cache.status('partial'),'partial');
 const retry=cache.acquirePreview('failed');assert.equal(cache.status('failed'),'preparing');await retry.promise;retry.release();
 assert.equal(cache.status('failed'),'ready');assert.equal(attempts,2);cache.clear();
});

test('eviction and canceled or late tasks remove status instead of leaving a ready or preparing badge',async()=>{
 const cache=createChipStockPreload(async code=>({code}),async stock=>stock,1);
 for(const code of ['a','b']){const lease=cache.acquirePreview(code);await lease.promise;lease.release();}
 assert.equal(cache.status('a'),null);assert.equal(cache.status('b'),'ready');cache.clear();
 const pending=deferred();const old=createChipStockPreload(async()=>pending.promise,async stock=>stock);
 old.prefetch(['old']);await tick();old.clear();pending.resolve({code:'old'});await tick();assert.equal(old.status('old'),null);
 const canceled=createChipStockPreload(async(code,signal)=>{if(signal.aborted)throw new DOMException('Aborted','AbortError');return{code};},async stock=>stock);
 const lease=canceled.acquireStock('canceled');lease.release();assert.equal(canceled.status('canceled'),null);await assert.rejects(lease.promise,{name:'AbortError'});assert.equal(canceled.status('canceled'),null);canceled.clear();
});
