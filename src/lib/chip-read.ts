import { authRead } from './auth-fetch';
const api=(process.env.NEXT_PUBLIC_DATA_BASE||'').replace(/\/$/,'');
export async function readChip<T>(path:string,signal:AbortSignal,parse:(v:unknown)=>T):Promise<T>{
  return authRead(api+path,{signal,cache:'no-store'},async response=>{
    if(!response.ok)throw Error(response.status===401?'登录已失效，请重新登录。':`筹码数据暂时无法读取（HTTP ${response.status}）。`);
    return parse(await response.json());
  });
}
