import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {priceScale, geometricLevel, reflectedPrice} from '../../src/lib/price-geometry.ts';
const vectors=JSON.parse(readFileSync(new URL('../fixtures/log-price-vectors.json',import.meta.url)));
test('equal price ratios have equal pixel distances and inverse round trips',()=>{
 const scale=priceScale({prices:[10,20,40],top:20,bottom:420});assert.ok(scale);
 assert.ok(Math.abs((scale.y(10)-scale.y(20))-(scale.y(20)-scale.y(40)))<1e-10);
 for(const price of [10,20,40])assert.ok(Math.abs(scale.priceAt(scale.y(price))-price)<1e-10);
});
test('flat and wide positive domains work and invalid inputs fail',()=>{
 for(const prices of [[5,5],[0.001,1000000]]){const scale=priceScale({prices,top:0,bottom:300});assert.ok(scale);assert.ok(scale.ticks(5).every(Number.isFinite));}
 for(const prices of [[],[0,10],[-1,10],[NaN,10],[Infinity,10]])assert.equal(priceScale({prices,top:0,bottom:100}),null);
});
test('golden ratios match shared vectors',()=>{
 for(const v of vectors.levels)assert.ok(Math.abs(geometricLevel(+v.low,+v.high,+v.ratio)-v.expected)<1e-12);
 for(const v of vectors.reflections)assert.ok(Math.abs(reflectedPrice(+v.origin,+v.middle)-v.expected)<1e-12);
});
