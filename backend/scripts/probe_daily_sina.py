from pathlib import Path
exec(Path(__file__).with_name('probe_akshare.py').read_text().split('jobs=')[0])
for market,code in [('sh','600519'),('bj','920002')]:
 try:
  df=ak.stock_zh_a_daily(symbol=market+code,start_date='20260824',end_date='20260905',adjust='');df.to_csv(out/f'{market}_daily_sina.csv',index=False);print(market,df.tail().to_string(index=False),flush=True)
 except Exception as ex:print(market,type(ex).__name__,str(ex)[:300],flush=True)
 time.sleep(2)
