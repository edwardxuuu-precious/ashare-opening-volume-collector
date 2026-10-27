/** Follow committed publications without overlapping requests or preloading an inventory. */
export type PublicationIdentity = {dataset_id:string;as_of:string;generated_at:string};
type Timer = ReturnType<typeof setTimeout>;
type Options<S,M extends PublicationIdentity,P> = {
  readStatus:(signal:AbortSignal)=>Promise<S>;
  readManifest:(signal:AbortSignal)=>Promise<M>;
  readPayload:(manifest:M,signal:AbortSignal)=>Promise<P>;
  onStatus:(status:S)=>void;
  onPublication:(manifest:M,payload:P,previous:M|null)=>boolean|void;
  onStatusError:(error:Error|null)=>void;
  onPublicationError:(error:Error|null)=>void;
  onSettled?:()=>void;
  isRunning:(status:S)=>boolean;
  publishedDataset:(status:S)=>string|null;
  isVisible?:()=>boolean;
  listenVisibility?:(changed:()=>void)=>()=>void;
  now?:()=>number;
  setTimer?:(callback:()=>void,ms:number)=>Timer;
  clearTimer?:(timer:Timer)=>void;
  activeStatusMs?:number;
  idleStatusMs?:number;
  activeManifestMs?:number;
  idleManifestMs?:number;
};

export function olderPublication(next:PublicationIdentity,current:PublicationIdentity){
  if(next.as_of!==current.as_of)return next.as_of<current.as_of;
  const a=Date.parse(next.generated_at),b=Date.parse(current.generated_at);
  return Number.isFinite(a)&&Number.isFinite(b)&&a<b;
}

/** A selected historical day or review round stays pinned as latest data changes. */
export function followsPublication(view:PublicationIdentity|null,previous:PublicationIdentity|null,pinned:boolean){
  return !pinned&&(!view||view.dataset_id===previous?.dataset_id);
}

/** Pending navigation blocks automatic adoption without changing the committed view mode. */
export function createPublicationNavigation(){
  let sequence=0,pinned=false,pending=false;
  return {
    get pinned(){return pinned;},get pending(){return pending;},
    begin(){pending=true;return ++sequence;},
    isCurrent(ticket:number){return ticket===sequence;},
    commit(ticket:number,pin:boolean){if(ticket!==sequence)return false;pending=false;pinned=pin;return true;},
    fail(ticket:number){if(ticket!==sequence)return false;pending=false;return true;},
    cancel(){++sequence;pending=false;},
  };
}

export function startPublicationFollow<S,M extends PublicationIdentity,P>(options:Options<S,M,P>){
  const visible=options.isVisible??(()=>document.visibilityState==='visible');
  const now=options.now??Date.now,schedule=options.setTimer??setTimeout,cancel=options.clearTimer??clearTimeout;
  let stopped=false,busy=false,force=true,timer:Timer|undefined,controller:AbortController|null=null;
  let current:M|null=null,status:S|null=null,lastManifestRead=-Infinity;
  const clear=()=>{if(timer!==undefined){cancel(timer);timer=undefined;}};
  const delay=()=>status&&options.isRunning(status)?options.activeStatusMs??20000:options.idleStatusMs??60000;
  const error=(value:unknown)=>value instanceof Error?value:Error('数据更新暂时无法读取。');
  async function tick(){
    if(stopped||busy||!visible())return;
    clear();busy=true;controller=new AbortController();const signal=controller.signal,forced=force;force=false;
    try{
      try{
        const next=await options.readStatus(signal);
        if(stopped||signal.aborted)return;
        status=next;options.onStatus(next);options.onStatusError(null);
      }catch(value){if(stopped||signal.aborted)return;options.onStatusError(error(value));}
      if(stopped||signal.aborted)return;
      const interval=status&&options.isRunning(status)?options.activeManifestMs??120000:options.idleManifestMs??300000;
      const announced=status?options.publishedDataset(status):null;
      if(forced||!current||now()-lastManifestRead>=interval||(announced&&announced!==current.dataset_id)){
        lastManifestRead=now();
        try{
          const manifest=await options.readManifest(signal);
          if(stopped||signal.aborted)return;
          if(!current||!olderPublication(manifest,current)){
            if(manifest.dataset_id!==current?.dataset_id){
              const payload=await options.readPayload(manifest,signal);
              if(stopped||signal.aborted)return;
              const previous=current;
              if(options.onPublication(manifest,payload,previous)!==false)current=manifest;
            }
          }
          options.onPublicationError(null);
        }catch(value){if(stopped||signal.aborted)return;options.onPublicationError(error(value));}
      }
    }finally{
      busy=false;controller=null;
      if(!stopped&&!signal.aborted)options.onSettled?.();
      if(!stopped&&visible())timer=schedule(()=>void tick(),force?0:delay());
    }
  }
  const changed=()=>{
    clear();
    if(!visible()){force=true;controller?.abort();}
    else{force=true;if(!busy)void tick();}
  };
  const unlisten=options.listenVisibility?options.listenVisibility(changed):(()=>{
    document.addEventListener('visibilitychange',changed);
    return()=>document.removeEventListener('visibilitychange',changed);
  })();
  void tick();
  return {
    refresh(){if(stopped)return;force=true;clear();if(!busy&&visible())void tick();},
    stop(){stopped=true;clear();controller?.abort();unlisten();},
  };
}
