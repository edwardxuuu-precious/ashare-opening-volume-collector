import {createChipHistoryCache} from './chip-history-cache.ts';

export type ChipStockPreloadStatus='preparing'|'ready'|'partial'|'error'|null;
export const chipStockPreloadLabels={preparing:'准备中',ready:'已预加载',partial:'行情已备好',error:'准备失败，可点击重试'};
export const chipStockPreloadHints={preparing:'正在排队、读取行情或准备近期筹码；此时点击会复用同一任务。',ready:'行情与近期筹码均已准备；较早归档日期仍可能首次读取。',partial:'行情已缓存，历史筹码仍需按需读取。',error:'预加载未完成，点击股票可重试，不影响筛选结果。'};

/** One background stock at a time, shared with foreground readers. Page-owned:
 * no persistent storage, polling, or market-wide preloads. */
export function createChipStockPreload<S,P>(load:(code:string,signal:AbortSignal)=>Promise<S>,prepare:(stock:S,signal:AbortSignal)=>Promise<P>,capacity=5,isReady:(preview:P)=>boolean=()=>true){
  const stocks=createChipHistoryCache(load,capacity+2);
  const previews=createChipHistoryCache(async(code,signal)=>{
    const lease=acquireStock(code);
    try{const stock=await lease.promise;if(signal.aborted)throw new DOMException('Aborted','AbortError');return await prepare(stock,signal);}
    finally{lease.release();}
  },capacity+2);
  let protectedCode:string|null=null,queue:string[]=[],wanted=new Set<string>(),background:object|null=null,revision=0,generation=0;
  const lru=new Map<string,true>();
  const failures=new Map<string,true>();
  const listeners=new Set<()=>void>();
  function notify(){revision++;for(const listener of listeners)listener();}
  function remember(code:string){
    lru.delete(code);lru.set(code,true);
    while(lru.size>capacity){const oldest=[...lru.keys()].find(c=>c!==protectedCode);if(!oldest)break;lru.delete(oldest);stocks.forget(oldest);previews.forget(oldest);}
  }
  function acquire<T>(code:string,cache:ReturnType<typeof createChipHistoryCache<T>>){
    const lease=cache.acquire(code),epoch=generation;failures.delete(code);notify();
    void lease.promise.then(()=>{
      if(epoch!==generation)return;
      if(cache.peek(code)!==undefined){failures.delete(code);remember(code);}notify();
    },error=>{
      if(epoch!==generation)return;
      if(error?.name!=='AbortError'){failures.set(code,true);while(failures.size>capacity)failures.delete(failures.keys().next().value!);}notify();
    });
    return{promise:lease.promise,release:()=>{lease.release();if(epoch===generation)notify();}};
  }
  function acquireStock(code:string){return acquire(code,stocks);}
  function acquirePreview(code:string){return acquire(code,previews);}
  function status(code:string):ChipStockPreloadStatus{
    const saved=previews.peek(code);
    if(saved!==undefined)return isReady(saved)?'ready':'partial';
    if(stocks.pending(code)||previews.pending(code)||queue.includes(code))return 'preparing';
    if(failures.has(code))return 'error';
    return stocks.peek(code)!==undefined?'partial':null;
  }
  function pump(){
    if(background)return;
    let code=queue.shift();
    while(code&&stocks.peek(code)!==undefined&&previews.peek(code)!==undefined)code=queue.shift();
    if(!code)return;
    const target=code,task={};background=task;
    void(async()=>{
      const stock=acquireStock(target);
      try{
        await stock.promise;
        // An obsolete request may finish into bounded memory, but must not
        // start more work after the user changes the current list or stock.
        if(background!==task||(!wanted.has(target)&&protectedCode!==target))return;
        const preview=acquirePreview(target);
        try{await preview.promise;}finally{preview.release();}
      }catch{/* Background failure is retryable and never replaces current UI. */}
      finally{stock.release();if(background===task){background=null;pump();notify();}}
    })();
  }
  return {
    peekStock:(code:string)=>stocks.peek(code),peekPreview:(code:string)=>previews.peek(code),status,
    acquireStock,acquirePreview,
    protect:(code:string)=>{protectedCode=code;stocks.protect(code);previews.protect(code);if(stocks.peek(code)!==undefined||previews.peek(code)!==undefined)remember(code);},
    prefetch:(codes:string[])=>{queue=[...new Set(codes)].filter(c=>c!==protectedCode).slice(0,2);wanted=new Set(queue);pump();notify();},
    subscribe:(listener:()=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener);};},
    version:()=>revision,
    clear:()=>{generation++;queue=[];wanted.clear();background=null;protectedCode=null;lru.clear();failures.clear();stocks.clear();previews.clear();notify();},
  };
}

export function neighboringChipStocks(codes:string[],current:string){
  const index=codes.indexOf(current);if(index<0)return [];
  return [codes[index+1],codes[index-1]].filter((c):c is string=>!!c);
}
