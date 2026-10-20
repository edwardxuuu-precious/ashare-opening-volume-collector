import type {MarketStock} from './chip-market.ts';
import {parseChipHistory,type ChipHistory} from './chip-history.ts';
import ChipHistoryWorker from '../workers/chip-history.worker.ts?worker';

export type PreparedChipHistory={frames:Record<string,ChipHistory>;fallback:string};
export function prepareChipLocalHistory(stock:MarketStock,signal:AbortSignal):Promise<PreparedChipHistory>{
  return new Promise((resolve,reject)=>{
    if(signal.aborted){reject(new DOMException('Aborted','AbortError'));return;}
    let worker:Worker|undefined,settled=false;
    const finish=(value?:PreparedChipHistory,error?:Error)=>{
      if(settled)return;settled=true;window.clearTimeout(timeout);signal.removeEventListener('abort',abort);worker?.terminate();
      if(error)reject(error);else resolve(value!);
    };
    const abort=()=>finish(undefined,new DOMException('Aborted','AbortError'));
    const timeout=window.setTimeout(()=>finish(undefined,Error('PREPARATION_TIMEOUT')),15000);
    signal.addEventListener('abort',abort,{once:true});
    try{
      worker=new ChipHistoryWorker();
      worker.onmessage=event=>{
        try{
          const value=event.data;
          if(value.dataset_id!==stock.dataset_id||value.security_id!==stock.security_id||!value.frames||typeof value.frames!=='object'||Array.isArray(value.frames)||Object.keys(value.frames).length>500)throw Error('LOCAL_HISTORY_IDENTITY');
          const frames:Record<string,ChipHistory>={};
          for(const [day,frame] of Object.entries(value.frames))frames[day]=parseChipHistory(frame,stock,day);
          finish({frames,fallback:Object.keys(frames).length?'':'SEALED_SNAPSHOT_OR_WINDOW_UNAVAILABLE'});
        }catch(error){finish(undefined,error instanceof Error?error:Error('INVALID_LOCAL_RESPONSE'));}
      };
      worker.onerror=event=>finish(undefined,Error(event.message||'WORKER_UNAVAILABLE'));
      worker.postMessage(stock);
    }catch(error){finish(undefined,error instanceof Error?error:Error('WORKER_UNAVAILABLE'));}
  });
}
