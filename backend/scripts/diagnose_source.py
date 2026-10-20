"""Inspect source-series alignment and exact raw fields, with bounded public requests."""
from collect import RequestBudget,write_json
from pathlib import Path
import pandas as pd
import requests,json
import akshare as ak
import akshare.stock.stock_zh_a_sina as sina
RequestBudget().install();p=Path('var/data/probe');report=[]
for market,code in [('sz','000001'),('sh','600519'),('bj','920002')]:
 symbol=market+code
 # Save actual API daily output for consistent same-provider denominators.
 daily=ak.stock_zh_a_daily(symbol=symbol,start_date='20260824',end_date='20260905',adjust='')
 daily.to_csv(p/f'{market}_daily_sina.csv',index=False)
 minute=pd.read_csv(p/f'sina_{market}_min15.csv')
 for day in ['2026-08-31','2026-09-01','2026-09-02','2026-09-03','2026-09-04']:
  a=minute[minute.day.str.startswith(day)];b=daily[daily.date.astype(str)==day].iloc[0]
  report.append({'symbol':symbol,'date':day,'minuteOpen':float(a.iloc[0].open),'dailyOpen':float(b.open),'minuteClose':float(a.iloc[-1].close),'dailyClose':float(b.close),'minuteHigh':float(a.high.max()),'dailyHigh':float(b.high),'minuteLow':float(a.low.min()),'dailyLow':float(b.low),'minuteVolume':int(a.volume.sum()),'dailyVolume':int(b.volume),'first15Volume':int(a.iloc[0].volume),'ratio':float(a.iloc[0].volume)/float(b.volume)*100})
 # Raw daily decoded output before AKShare dataframe merging/rounding; inspect only recent rows.
 response=requests.get(sina.zh_sina_a_stock_hist_url.format(symbol))
 decoder=sina.py_mini_racer.MiniRacer();decoder.eval(sina.hk_js_decode)
 raw=decoder.call('d',response.text.split('=')[1].split(';')[0].replace('"',''))
 recent=[x for x in raw if str(x.get('date',''))[:10]>='2026-08-24']
 write_json(p/f'{market}_raw_daily_decoded.json',recent)
write_json(p/'source_alignment.json',report)
print(json.dumps(report,indent=2,ensure_ascii=False))
