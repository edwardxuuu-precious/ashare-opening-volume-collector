import {prepareLocalHistory} from '../lib/chip-local-history.ts';
import type {MarketStock} from '../lib/chip-market.ts';
const scope=self as unknown as {onmessage:((event:MessageEvent<MarketStock>)=>void)|null;postMessage:(value:unknown)=>void};
scope.onmessage=event=>{
  const stock=event.data;
  try{scope.postMessage({dataset_id:stock.dataset_id,security_id:stock.security_id,frames:prepareLocalHistory(stock)});}
  catch{scope.postMessage({dataset_id:stock.dataset_id,security_id:stock.security_id,frames:{}});}
};
