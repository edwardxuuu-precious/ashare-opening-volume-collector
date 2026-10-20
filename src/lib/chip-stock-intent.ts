type Clock={now:()=>number;set:(run:()=>void,delay:number)=>unknown;clear:(timer:unknown)=>void};
const browserClock:Clock={now:()=>Date.now(),set:(run,delay)=>setTimeout(run,delay),clear:timer=>clearTimeout(timer as ReturnType<typeof setTimeout>)};

/** Mouse intent and adjacent navigation share the same two-target serial queue.
 * Leaving before dwell never starts a read; sustained browsing is rate limited. */
export function createChipStockIntent(prefetch:(codes:string[])=>void,isPrepared:(code:string)=>boolean,clock:Clock=browserClock){
  let neighbors:string[]=[],neighborsReady=false,neighborTimer:unknown;
  let hovered:string|null=null,hoverReady=false,hoverTimer:unknown,lastStarted=-Infinity;
  const publish=()=>prefetch([...new Set([...(hoverReady&&hovered?[hovered]:[]),...(neighborsReady?neighbors:[])])].slice(0,2));
  function cancelHover(code?:string){
    if(code&&code!==hovered)return;
    clock.clear(hoverTimer);hoverTimer=undefined;hovered=null;hoverReady=false;publish();
  }
  return {
    neighbors:(codes:string[])=>{
      clock.clear(neighborTimer);neighbors=[...new Set(codes)].slice(0,2);neighborsReady=false;publish();
      if(neighbors.length)neighborTimer=clock.set(()=>{neighborsReady=true;publish();},600);
    },
    hover:(code:string)=>{
      if(hovered===code)return;
      cancelHover();hovered=code;
      const delay=Math.max(600,lastStarted+2000-clock.now());
      hoverTimer=clock.set(()=>{
        hoverTimer=undefined;hoverReady=true;
        if(!isPrepared(code))lastStarted=clock.now();
        publish();
      },delay);
    },
    cancelHover,
    clear:()=>{clock.clear(neighborTimer);clock.clear(hoverTimer);neighborTimer=hoverTimer=undefined;neighbors=[];neighborsReady=false;hovered=null;hoverReady=false;lastStarted=-Infinity;publish();},
  };
}
