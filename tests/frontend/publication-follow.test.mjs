import test from 'node:test';
import assert from 'node:assert/strict';
import {createPublicationNavigation,startPublicationFollow,followsPublication,olderPublication} from '../../src/lib/publication-follow.ts';

const flush=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const batch=(id='a',day='2026-10-26',time='09:00')=>({dataset_id:id,as_of:day,generated_at:`${day}T${time}:00Z`});
function harness(initial={}){
 let visible=initial.visible??true,clock=0,id=0,listener,active=0,maximum=0;
 const timers=new Map(),publications=[],statuses=[],statusErrors=[],publicationErrors=[];
 const calls={status:0,manifest:0,payload:0};
 const feeds={status:{state:'COMPLETE',dataset_id:'a'},manifest:batch(),
  statusFn:null,manifestFn:null,payloadFn:null};
 const wrapped=async(kind,fn)=>{calls[kind]++;maximum=Math.max(maximum,++active);try{return await fn();}finally{active--;}};
 const options={
  readStatus:signal=>wrapped('status',()=>feeds.statusFn?feeds.statusFn(signal):feeds.status),
  readManifest:signal=>wrapped('manifest',()=>feeds.manifestFn?feeds.manifestFn(signal):feeds.manifest),
  readPayload:(m,signal)=>wrapped('payload',()=>feeds.payloadFn?feeds.payloadFn(m,signal):{dataset_id:m.dataset_id}),
  onStatus:s=>statuses.push(s),onPublication:(m,p,previous)=>publications.push({m,p,previous}),
  onStatusError:e=>statusErrors.push(e),onPublicationError:e=>publicationErrors.push(e),
  isRunning:s=>s.state==='RUNNING',publishedDataset:s=>s.state==='COMPLETE'?s.dataset_id:null,
  isVisible:()=>visible,now:()=>clock,
  setTimer:(fn,ms)=>{const value=++id;timers.set(value,{fn,at:clock+ms});return value;},
  clearTimer:value=>timers.delete(value),listenVisibility:fn=>{listener=fn;return()=>{listener=null;};},
 };
 Object.assign(options,initial.options);
 const follow=startPublicationFollow(options);
 return{follow,feeds,calls,publications,statuses,statusErrors,publicationErrors,timers,
  get maximum(){return maximum;},
  async advance(ms){clock+=ms;const due=[...timers].filter(([,t])=>t.at<=clock);for(const [key,t] of due){timers.delete(key);t.fn();}await flush();},
  show(value){visible=value;listener?.();},
 };
}

test('same generation polls metadata without reloading payload or replacing selected data',async()=>{
 const h=harness();await flush();assert.equal(h.publications.length,1);
 await h.advance(60000);assert.deepEqual(h.calls,{status:2,manifest:1,payload:1});
 h.follow.refresh();await flush();assert.deepEqual(h.calls,{status:3,manifest:2,payload:1});
 assert.equal(h.publications.length,1);h.follow.stop();
});

test('new announced generation is committed only after its matching payload finishes',async()=>{
 const h=harness();await flush();const pending=deferred();
 h.feeds.status={state:'COMPLETE',dataset_id:'b'};h.feeds.manifest=batch('b');
 h.feeds.payloadFn=async m=>{assert.equal(m.dataset_id,'b');return pending.promise;};
 await h.advance(60000);assert.equal(h.publications.length,1);
 pending.resolve({dataset_id:'b',rows:['b']});await flush();
 assert.equal(h.publications.length,2);assert.equal(h.publications[1].previous.dataset_id,'a');
 assert.equal(h.publications[1].m.dataset_id,h.publications[1].p.dataset_id);h.follow.stop();
});

test('unreadable or mismatched new payload preserves the last complete publication and retries',async()=>{
 const h=harness();await flush();h.feeds.status={state:'COMPLETE',dataset_id:'b'};h.feeds.manifest=batch('b');
 h.feeds.payloadFn=()=>{throw Error('payload generation differs from manifest');};
 await h.advance(60000);assert.equal(h.publications.length,1);
 assert.match(h.publicationErrors.at(-1).message,/generation/);
 h.feeds.payloadFn=m=>({dataset_id:m.dataset_id});await h.advance(60000);
 assert.equal(h.publications.length,2);assert.equal(h.publicationErrors.at(-1),null);h.follow.stop();
});

test('status read failure retains displayed status and still allows the published directory refresh',async()=>{
 const h=harness();await flush();h.feeds.statusFn=()=>{throw Error('status offline');};
 h.feeds.manifest=batch('b');h.follow.refresh();await flush();
 assert.equal(h.statuses.length,1);assert.equal(h.publications.at(-1).m.dataset_id,'b');
 assert.match(h.statusErrors.at(-1).message,/offline/);h.follow.stop();
});

test('hidden page pauses entirely, aborts an in-flight response, and resumes without overlap',async()=>{
 const h=harness({visible:false});await flush();assert.equal(h.calls.status,0);assert.equal(h.timers.size,0);
 const pending=deferred();let signal;h.feeds.statusFn=s=>{signal=s;return pending.promise;};
 h.show(true);await flush();assert.equal(h.calls.status,1);
 h.show(false);assert.equal(signal.aborted,true);h.show(true);await flush();
 assert.equal(h.calls.status,1);pending.resolve({state:'COMPLETE',dataset_id:'obsolete'});await flush();
 assert.equal(h.statuses.length,0);assert.equal(h.publications.length,0);
 h.feeds.statusFn=null;await h.advance(0);assert.equal(h.publications.length,1);assert.equal(h.maximum,1);
 h.show(false);await h.advance(600000);assert.equal(h.calls.status,2);h.follow.stop();
});

test('multiple manual refresh clicks coalesce behind the in-flight request',async()=>{
 const h=harness({options:{}});
 // The first async status read is already running; refresh clicks cannot overlap it.
 h.follow.refresh();h.follow.refresh();h.follow.refresh();await flush();
 assert.equal(h.calls.status,1);await h.advance(0);
 assert.equal(h.calls.status,2);assert.equal(h.calls.payload,1);assert.equal(h.maximum,1);
 h.follow.stop();
});

test('background publication reads use the existing two-minute active and five-minute idle intervals',async()=>{
 const h=harness();await flush();h.feeds.status={state:'RUNNING',dataset_id:'next-not-yet-published'};
 await h.advance(60000);assert.equal(h.calls.manifest,1);
 await h.advance(20000);assert.equal(h.calls.manifest,1);
 await h.advance(40000);assert.equal(h.calls.manifest,2);
 h.feeds.status={state:'COMPLETE',dataset_id:'a'};await h.advance(20000);
 await h.advance(240000);assert.equal(h.calls.manifest,2);
 await h.advance(60000);assert.equal(h.calls.manifest,3);h.follow.stop();
});

test('an older manifest cannot roll back a newer date or corrected batch',async()=>{
 const h=harness();await flush();h.feeds.manifest=batch('old','2026-10-23');h.follow.refresh();await flush();
 assert.equal(h.publications.length,1);h.feeds.manifest=batch('older-same-day','2026-10-26','08:59');
 h.follow.refresh();await flush();assert.equal(h.publications.length,1);
 assert.equal(olderPublication(batch('later','2026-10-26','09:01'),batch()),false);h.follow.stop();
});

test('a selection race may defer adopting a batch and retry without consuming its generation',async()=>{
 let accepted=false;const adopted=[];
 const h=harness({options:{onPublication:(m,p,previous)=>{if(!accepted)return false;adopted.push({m,p,previous});}}});
 await flush();assert.equal(adopted.length,0);accepted=true;h.follow.refresh();await flush();
 assert.equal(adopted.length,1);assert.equal(adopted[0].previous,null);assert.equal(h.calls.payload,2);h.follow.stop();
});

test('latest mode follows new data while a historical day or saved review stays pinned',()=>{
 const latest=batch('latest'),next=batch('next'),history=batch('history','2026-10-23');
 assert.equal(followsPublication(null,null,false),true);
 assert.equal(followsPublication(latest,latest,false),true);
 assert.equal(followsPublication(history,latest,false),false);
 assert.equal(followsPublication(latest,latest,true),false);
 assert.equal(followsPublication(history,latest,true),false);
 assert.equal(followsPublication(next,next,false),true);
});

test('failed historical navigation preserves the previously committed latest-follow mode',()=>{
 const navigation=createPublicationNavigation(),ticket=navigation.begin();
 assert.equal(navigation.pending,true);assert.equal(navigation.pinned,false);
 assert.equal(navigation.fail(ticket),true);assert.equal(navigation.pending,false);
 assert.equal(navigation.pinned,false);assert.equal(followsPublication(batch(),batch(),navigation.pinned),true);
});

test('a newer publication arriving during a failed history navigation remains adoptable',async()=>{
 const navigation=createPublicationNavigation();let view=null;const adopted=[];
 const h=harness({options:{onPublication:(m,_p,previous)=>{
  if(navigation.pending)return false;
  if(followsPublication(view,previous,navigation.pinned)){view=m;adopted.push(m.dataset_id);}
 }}});await flush();const ticket=navigation.begin();
 h.feeds.status={state:'COMPLETE',dataset_id:'c'};h.feeds.manifest=batch('c');await h.advance(60000);
 assert.deepEqual(adopted,['a']);assert.equal(view.dataset_id,'a');
 navigation.fail(ticket);h.follow.refresh();await flush();
 assert.deepEqual(adopted,['a','c']);assert.equal(view.dataset_id,'c');h.follow.stop();
});

test('switching samples cancels pending navigation without pinning or allowing its late result',()=>{
 const navigation=createPublicationNavigation(),ticket=navigation.begin();navigation.cancel();
 assert.equal(navigation.pending,false);assert.equal(navigation.pinned,false);
 assert.equal(navigation.commit(ticket,true),false);assert.equal(navigation.fail(ticket),false);
 assert.equal(navigation.pinned,false);
});

test('a successful historical day or review pins the view and a failed later navigation preserves it',()=>{
 const navigation=createPublicationNavigation();assert.equal(navigation.commit(navigation.begin(),true),true);
 const next=navigation.begin();assert.equal(navigation.pinned,true);navigation.fail(next);
 assert.equal(navigation.pinned,true);navigation.cancel();assert.equal(navigation.pinned,true);
 assert.equal(navigation.commit(navigation.begin(),false),true);assert.equal(navigation.pinned,false);
});

test('an older navigation completion cannot commit or clear the newer pending navigation',()=>{
 const navigation=createPublicationNavigation(),old=navigation.begin(),fresh=navigation.begin();
 assert.equal(navigation.commit(old,true),false);assert.equal(navigation.fail(old),false);
 assert.equal(navigation.pending,true);assert.equal(navigation.isCurrent(fresh),true);
 assert.equal(navigation.commit(fresh,true),true);assert.equal(navigation.pending,false);
});

test('unmount stops timers and discards a late payload without publishing it',async()=>{
 const pending=deferred();let signal;
 const h=harness({options:{readPayload:async(_m,s)=>{signal=s;return pending.promise;}}});
 await flush();h.follow.stop();assert.equal(signal.aborted,true);pending.resolve({dataset_id:'a'});await flush();
 assert.equal(h.publications.length,0);assert.equal(h.timers.size,0);
});
