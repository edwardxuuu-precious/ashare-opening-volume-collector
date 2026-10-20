import { chipNumber } from './chips.ts';

export function chipChange(value:string|number):string {
  const text=(typeof value==='number'?value:chipNumber(value)).toFixed(4);
  return text==='-0.0000'?'0.0000':text.startsWith('-')||text==='0.0000'?text:`+${text}`;
}
export function chipReason(reason:string):string {
  if(reason.startsWith('SOURCE_OR_MODEL_ERROR:'))return '历史行情获取或计算未完成';
  if(reason.startsWith('MODEL_CACHE_REBUILT:'))return '历史数据有变化，已从完整输入重新计算';
  if(reason.startsWith('LAST_TRADE_CLOSE_DURING_CONFIRMED_NO_TRADE_SESSION:'))return `已核实未交易，价格沿用最后交易日${reason.split(':')[1]}的收盘价`;
  const known:Record<string,string>={
    BSE_HISTORY_UNAVAILABLE:'北交所历史换手率数据暂缺',
    LATEST_CLOSE_FROM_VERIFIED_DATED_TENCENT_QUOTE:'收盘价已按日期核实',
    HISTORICAL_OHLC_MISSING:'历史K线暂缺',LATEST_CLOSE_MISSING:'当日收盘价暂缺',
    LIMIT_HISTORY_INCOMPLETE:'近10日涨停证据尚未核实完整',
    MODEL_RESEEDED_AFTER_INVALID_SOURCE_HISTORY:'早期异常行情已保留说明，模型从其后的有效连续段重新起算',
    CHIP_MODEL_INPUT_INCOMPLETE:'筹码计算所需历史数据不完整',
    CURRENT_SESSION_REAL_FINAL_QUOTE_USED:'当日价量已使用核实的收盘报价',
    FIRST_LISTED_SESSION_NO_PREVIOUS_CHIP_COMPARISON:'上市首日已计算当前筹码；没有前一交易日筹码，原峰变化不适用',
  };
  return reason.split(';').map(part=>known[part]??part).join('；');
}
