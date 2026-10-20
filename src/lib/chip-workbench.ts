import {chipNumber,type ChipSnapshot} from './chips.ts';
import type {DailyCandle,MarketScreenRow} from './chip-market.ts';
import {chipScreenState} from './chip-price-position.ts';

/** Initial focus changes only the viewed security, never screening eligibility. */
export function initialWorkbenchStock(rows:MarketScreenRow[],current:string){
  return rows.some(row=>row.security_id===current)?current:(rows.find(row=>chipScreenState(row)==='PASS')??rows[0])?.security_id??'';
}

/** A price without a matching candle is a carried snapshot, not a new trade. */
export function workbenchQuote(history:DailyCandle[],date:string,snapshot:ChipSnapshot|null){
  const index=history.findIndex(candle=>candle.date===date),bar=history[index];
  if(!bar)return{close:snapshot?chipNumber(snapshot.close_cny):null,change:null,percent:null,hasCandle:false};
  const close=chipNumber(bar.close),previous=index>0?chipNumber(history[index-1].close):null;
  return{close,change:previous===null?null:close-previous,percent:previous===null?null:(close/previous-1)*100,hasCandle:true};
}
