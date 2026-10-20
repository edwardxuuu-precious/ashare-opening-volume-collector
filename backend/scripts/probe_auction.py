from pathlib import Path
exec(Path(__file__).with_name('probe_akshare.py').read_text().split('jobs=')[0])
for code in ['600519','000001','920002']:
 try:
  df=ak.stock_zh_a_hist_pre_min_em(symbol=code);df.to_csv(out/f'pre_{code}.csv',index=False);print(code,df.head(5).to_string(index=False),df.tail(1).to_string(index=False),flush=True)
 except Exception as ex:print(code,type(ex).__name__,str(ex)[:200],flush=True)
 time.sleep(2)
