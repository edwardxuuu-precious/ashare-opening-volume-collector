import test from 'node:test';
import assert from 'node:assert/strict';
import {createChipHistoryCache,createChipHistoryBatchLoader,neighboringChipDates} from '../../src/lib/chip-history-cache.ts';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};

test('eight neighboring selections share one request, including a canceled hover',async()=>{
 const dates=Array.from({length:16},(_,i)=>String(i)),pending=deferred();let calls=0,signal;
 const loader=createChipHistoryBatchLoader(dates,async(anchor,s)=>{calls++;signal=s;return pending.promise;});
 const abort=new AbortController();const first=loader.load('2',abort.signal).catch(e=>e.name);await tick();abort.abort();
 const second=loader.load('4',new AbortController().signal);await tick();assert.equal(calls,1);assert.equal(signal.aborted,false);
 pending.resolve(Object.fromEntries(dates.slice(0,8).map(day=>[day,{day}])));
 assert.equal(await first,'AbortError');assert.deepEqual(await second,{day:'4'});
 for(const day of dates.slice(0,8))assert.deepEqual(await loader.load(day,new AbortController().signal),{day});
 assert.equal(calls,1);loader.clear();
});
test('changing stock aborts all batched reads; a failed block can be retried',async()=>{
 let calls=0,signal;const dates=['a','b'];
 const loader=createChipHistoryBatchLoader(dates,async(anchor,s)=>{calls++;signal=s;if(calls===1)throw Error('temporary');return{a:{day:'a'},b:{day:'b'}};});
 await assert.rejects(loader.load('a',new AbortController().signal),/temporary/);
 assert.deepEqual(await loader.load('b',new AbortController().signal),{day:'b'});assert.equal(calls,2);
 const pending=deferred(),active=createChipHistoryBatchLoader(dates,async(a,s)=>{signal=s;return pending.promise;});
 const read=active.load('a',new AbortController().signal);await tick();active.clear();assert.equal(signal.aborted,true);pending.resolve({a:{day:'a'}});await read;loader.clear();
});

test('hover reuses an in-flight neighbor read without canceling the background reader',async()=>{
 const pending=deferred();let calls=0,signal;
 const cache=createChipHistoryCache(async(day,s)=>{calls++;signal=s;return pending.promise;});
 cache.prefetch(['2026-09-30']);await tick();
 const hover=cache.acquire('2026-09-30');hover.release();
 assert.equal(calls,1);assert.equal(signal.aborted,false);
 pending.resolve({day:'2026-09-30'});await hover.promise;await tick();
 assert.deepEqual(cache.peek('2026-09-30'),{day:'2026-09-30'});cache.clear();
});
test('a canceled slow date never repopulates the cache after the cursor moves',async()=>{
 const pending=new Map(),signals=new Map();
 const cache=createChipHistoryCache((day,signal)=>{const d=deferred();pending.set(day,d);signals.set(day,signal);return d.promise;});
 const first=cache.acquire('old');await tick();first.release();
 const last=cache.acquire('selected');await tick();
 pending.get('selected').resolve({day:'selected'});await last.promise;
 pending.get('old').resolve({day:'old'});await first.promise;
 assert.equal(signals.get('old').aborted,true);assert.equal(cache.peek('old'),undefined);
 assert.deepEqual(cache.peek('selected'),{day:'selected'});last.release();cache.clear();
});
test('the pinned date survives eviction while visited history remains bounded',async()=>{
 const cache=createChipHistoryCache(async day=>({day}),2);cache.protect('pinned');
 cache.remember('pinned',{day:'pinned'});
 for(const day of ['pinned','a','b','c']){const read=cache.acquire(day);await read.promise;read.release();}
 assert.deepEqual(cache.peek('pinned'),{day:'pinned'});assert.deepEqual(cache.peek('c'),{day:'c'});
 assert.equal(cache.peek('a'),undefined);assert.equal(cache.peek('b'),undefined);cache.clear();
});
test('neighbor preloading stays at two workers and abandons old queued dates on a new focus',async()=>{
 const started=[],pending=new Map(),signals=new Map();let running=0,maxRunning=0;
 const cache=createChipHistoryCache((day,signal)=>{const d=deferred();pending.set(day,d);signals.set(day,signal);started.push(day);maxRunning=Math.max(maxRunning,++running);return d.promise.finally(()=>running--);});
 cache.prefetch(['a','b','old-queue']);await tick();assert.deepEqual(started,['a','b']);
 cache.prefetch(['x','y']);pending.get('a').resolve({day:'a'});await tick();assert.deepEqual(started,['a','b','x']);
 cache.clear();assert.equal(signals.get('b').aborted,true);assert.equal(signals.get('x').aborted,true);
 pending.get('b').resolve({day:'b'});pending.get('x').resolve({day:'x'});await tick();
 assert.equal(maxRunning,2);assert.equal(started.includes('y'),false);assert.equal(cache.peek('b'),undefined);
});
test('a failed background read can be retried by a foreground selection',async()=>{
 let calls=0;const cache=createChipHistoryCache(async day=>{if(++calls===1)throw Error('temporary');return{day};});
 cache.prefetch(['2026-08-14']);await tick();const retry=cache.acquire('2026-08-14');
 assert.deepEqual(await retry.promise,{day:'2026-08-14'});assert.equal(calls,2);retry.release();cache.clear();
});
test('neighbors follow available trading dates across a weekend, not calendar-day arithmetic',()=>{
 const days=['2026-10-13','2026-10-14','2026-10-15','2026-10-16','2026-10-19'];
 assert.deepEqual(neighboringChipDates(days,'2026-10-16',3),['2026-10-15','2026-10-19','2026-10-14']);
 assert.deepEqual(neighboringChipDates(days,'missing'),[]);
});
