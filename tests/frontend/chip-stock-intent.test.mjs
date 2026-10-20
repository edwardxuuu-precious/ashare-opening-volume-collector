import test from 'node:test';
import assert from 'node:assert/strict';
import {createChipStockIntent} from '../../src/lib/chip-stock-intent.ts';
function setup(prepared=[]){
 let now=0,id=0;const timers=new Map(),calls=[];
 const clock={now:()=>now,set:(run,delay)=>{timers.set(++id,{at:now+delay,run});return id;},clear:id=>timers.delete(id)};
 const advance=ms=>{const end=now+ms;for(;;){const next=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;now=next[1].at;timers.delete(next[0]);next[1].run();}now=end;};
 const intent=createChipStockIntent(codes=>calls.push([...codes]),code=>prepared.includes(code),clock);
 return{intent,calls,advance,last:()=>calls.at(-1)};
}
test('rapid row passes never request stocks and only a continuous dwell qualifies',()=>{
 const {intent,calls,advance,last}=setup();
 for(const code of ['a','b','c']){intent.hover(code);advance(599);intent.cancelHover(code);}
 assert.equal(calls.some(c=>c.length),false);
 intent.hover('d');advance(599);assert.deepEqual(last(),[]);advance(1);assert.deepEqual(last(),['d']);
 intent.clear();advance(5000);assert.deepEqual(last(),[]);
});
test('hover has priority inside the existing two-target budget and leaving restores neighbors',()=>{
 const {intent,advance,last}=setup();intent.neighbors(['next','previous']);advance(600);assert.deepEqual(last(),['next','previous']);
 intent.hover('hover');advance(600);assert.deepEqual(last(),['hover','next']);
 intent.cancelHover('old-row');assert.deepEqual(last(),['hover','next']);
 intent.cancelHover('hover');assert.deepEqual(last(),['next','previous']);
 intent.hover('next');advance(2000);assert.deepEqual(last(),['next','previous']);
});
test('new sustained targets are rate limited while changing rows restarts dwell',()=>{
 const {intent,advance,last}=setup();intent.hover('a');advance(600);assert.deepEqual(last(),['a']);
 intent.hover('b');advance(600);assert.deepEqual(last(),[]);
 intent.hover('c');advance(599);assert.deepEqual(last(),[]);advance(801);assert.deepEqual(last(),['c']);
});
test('click, filter change or page hiding cancels delayed intent without dropping the neighbor policy',()=>{
 const {intent,calls,advance,last}=setup();intent.neighbors(['next']);advance(600);
 intent.hover('clicked');advance(300);intent.cancelHover();advance(3000);
 assert.equal(calls.some(c=>c.includes('clicked')),false);assert.deepEqual(last(),['next']);
 intent.hover('hidden');intent.clear();advance(5000);assert.deepEqual(last(),[]);assert.equal(calls.some(c=>c.includes('hidden')),false);
});
test('cached hovering does not spend another new-target rate slot',()=>{
 const {intent,advance,last}=setup(['cached']);intent.hover('cached');advance(600);assert.deepEqual(last(),['cached']);
 intent.hover('fresh');advance(600);assert.deepEqual(last(),['fresh']);
});
