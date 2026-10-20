from pathlib import Path
exec(Path(__file__).with_name('probe_akshare.py').read_text().split('jobs=')[0])
jobs=[('sina_sh_min15',lambda:ak.stock_zh_a_minute(symbol='sh600519',period='15')),('sina_bj_min15',lambda:ak.stock_zh_a_minute(symbol='bj920002',period='15')),('sina_sz_min1',lambda:ak.stock_zh_a_minute(symbol='sz000001',period='1')),('sina_sh_min1',lambda:ak.stock_zh_a_minute(symbol='sh600519',period='1')),('sina_bj_min1',lambda:ak.stock_zh_a_minute(symbol='bj920002',period='1')),('sh_daily',lambda:ak.stock_zh_a_hist(symbol='600519',start_date='20260824',end_date='20260905')),('bj_daily',lambda:ak.stock_zh_a_hist(symbol='920002',start_date='20260824',end_date='20260905'))]
report=[]
for name,fn in jobs:
    tick=time.monotonic()
    try:
        df=fn();df.to_csv(out/f'{name}.csv',index=False)
        item={'name':name,'status':'success','rows':len(df),'columns':list(df.columns),'head':df.head(2).astype(str).to_dict('records'),'tail':df.tail(2).astype(str).to_dict('records')}
    except Exception as ex:item={'name':name,'status':'error','error':f'{type(ex).__name__}: {str(ex)[:350]}'}
    item['seconds']=round(time.monotonic()-tick,2);report.append(item)
    (out/'sina_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(item,ensure_ascii=False),flush=True);time.sleep(2)
