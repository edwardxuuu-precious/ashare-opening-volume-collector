import test from 'node:test';
import assert from 'node:assert/strict';
import {pickManualPoint,draftHistory,blankDraft} from '../../src/lib/golden-drawing.ts';
const candles=[{date:'2026-10-20',open:'3740.5700',high:'3743.0801',low:'3703.1200',close:'3713.0300'},{date:'2026-10-21',open:'10.12345678',high:'11',low:'9',close:'10'}];
const scale={left:20,right:220,top:20,bottom:420,min:0,max:4000};
test('magnet preserves exact source field precision and uses a screen-pixel radius',()=>{
 const y=420-3743.0801/4000*400;
 assert.deepEqual(pickManualPoint(candles,scale,70,y,'auto'),{date:'2026-10-20',field:'high',price:'3743.0801'});
 const small={left:0,right:100,top:0,bottom:100,min:8,max:12};
 const sparse=[{...candles[1],open:'9',close:'9'}];
 assert.equal(pickManualPoint(sparse,small,50,25+20,'auto',2).field,'high');
 assert.equal(pickManualPoint(sparse,small,50,25+20,'auto',.5).field,'custom');
});
test('free placement and out-of-chart input never pretend to be an OHLC anchor',()=>{
 assert.equal(pickManualPoint(candles,scale,70,100,'custom').field,'custom');
 assert.equal(pickManualPoint(candles,scale,0,100,'auto'),null);
 assert.equal(pickManualPoint(candles,scale,70,430,'auto'),null);
 assert.equal(pickManualPoint(candles,scale,220,100,'close').date,'2026-10-21');
 assert.equal(pickManualPoint([candles[1]],scale,100,100,'open').price,'10.12345678');
});
test('a completed two-point drag is undone and redone as one edit, fresh edits discard redo',()=>{
 const initial={present:blankDraft(),past:[],future:[]};
 const drawn={...initial.present,points:[{date:'2026-10-20',field:'high',price:'20'},{date:'2026-10-21',field:'low',price:'10'}]};
 const a=draftHistory(initial,{type:'edit',value:drawn});
 const b=draftHistory(a,{type:'undo'});assert.deepEqual(b.present.points,[]);
 assert.deepEqual(draftHistory(b,{type:'redo'}).present.points,drawn.points);
 assert.equal(draftHistory(b,{type:'edit',value:{...b.present,title:'another'}}).future.length,0);
 assert.equal(draftHistory(a,{type:'reset',value:drawn}).past.length,0);
});
