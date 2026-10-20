type Lease<T>={promise:Promise<T>;release:()=>void};
type Task<T>={controller:AbortController;users:number;promise:Promise<T>};

/** Per-stock cache: shared reads, two background workers, and cancelable readers. */
export function createChipHistoryCache<T>(load:(day:string,signal:AbortSignal)=>Promise<T>,capacity=128){
  const values=new Map<string,T>(),tasks=new Map<string,Task<T>>();
  let protectedDay:string|null=null,queue:string[]=[],workers=0;
  function remember(day:string,value:T){
    values.delete(day);values.set(day,value);
    while(values.size>capacity){const oldest=[...values.keys()].find(d=>d!==protectedDay);if(!oldest)break;values.delete(oldest);}
  }
  function acquire(day:string):Lease<T>{
    if(values.has(day)){
      const value=values.get(day)!;values.delete(day);values.set(day,value);
      return {promise:Promise.resolve(value),release:()=>{}};
    }
    let task=tasks.get(day);
    if(!task){
      const controller=new AbortController();
      const created:Task<T>={controller,users:0,promise:null!};
      created.promise=Promise.resolve().then(()=>load(day,controller.signal)).then(value=>{
        if(!controller.signal.aborted&&tasks.get(day)===created){
          remember(day,value);
        }
        return value;
      }).finally(()=>{if(tasks.get(day)===created)tasks.delete(day);});
      tasks.set(day,created);task=created;
    }
    task.users++;
    const shared=task;let released=false;
    return {promise:shared.promise,release:()=>{
      if(released)return;released=true;shared.users--;
      if(!shared.users&&tasks.get(day)===shared){tasks.delete(day);shared.controller.abort();}
    }};
  }
  function pump(){
    while(workers<2&&queue.length){
      const day=queue.shift()!;
      if(values.has(day)||tasks.has(day))continue;
      const lease=acquire(day);workers++;
      void lease.promise.catch(()=>{}).finally(()=>{lease.release();workers--;pump();});
    }
  }
  return {
    peek:(day:string)=>values.get(day),
    pending:(day:string)=>tasks.has(day),
    remember,
    forget:(day:string)=>{values.delete(day);},
    protect:(day:string)=>{protectedDay=day;},
    acquire,
    prefetch:(days:string[])=>{queue=[...new Set(days)].filter(d=>!values.has(d)&&!tasks.has(d));pump();},
    clear:()=>{queue=[];for(const task of tasks.values())task.controller.abort();tasks.clear();values.clear();},
  };
}

/** Share one eight-day request even if the pointer moves within that block.
 * In-flight blocks finish into bounded memory; stock unmount aborts all of them.
 * No cookies, credentials, or market data are persisted to browser storage.
 */
export function createChipHistoryBatchLoader<T>(dates:string[],load:(anchor:string,signal:AbortSignal)=>Promise<Record<string,T>>,individualPrefix=0){
  const blocks=createChipHistoryCache(load,16);
  const anchorFor=(index:number)=>dates[index<individualPrefix?index:Math.floor(index/8)*8];
  return {
    peek:(day:string)=>{const index=dates.indexOf(day);return index<0?undefined:blocks.peek(anchorFor(index))?.[day];},
    load:async(day:string,signal:AbortSignal)=>{
      const index=dates.indexOf(day);
      if(index<0)throw Error('该日期没有已发布K线。');
      if(signal.aborted)throw new DOMException('Aborted','AbortError');
      const anchor=anchorFor(index),lease=blocks.acquire(anchor);
      try{
        const frames=await lease.promise;
        if(signal.aborted)throw new DOMException('Aborted','AbortError');
        if(!frames[day]){blocks.forget(anchor);throw Error('该日期筹码暂无法核实，请重试或选择其他日期。');}
        return frames[day];
      }finally{lease.release();}
    },
    clear:()=>blocks.clear(),
  };
}

export function neighboringChipDates(dates:string[],day:string,limit=8){
  const index=dates.indexOf(day),result:string[]=[];
  if(index<0)return result;
  for(let distance=1;distance<dates.length&&result.length<limit;distance++){
    for(const offset of [-distance,distance]){const value=dates[index+offset];if(value&&result.length<limit)result.push(value);}
  }
  return result;
}
