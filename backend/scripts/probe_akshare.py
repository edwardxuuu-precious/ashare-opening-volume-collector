"""Bounded live probe; raw outputs are saved under var/data/probe."""
import json,time,sys
from pathlib import Path
import akshare as ak
import requests
out=Path('var/data/probe');out.mkdir(parents=True,exist_ok=True)
original=requests.sessions.Session.request
def bounded(self,method,url,**kwargs):
    kwargs.setdefault('timeout',15)
    return original(self,method,url,**kwargs)
requests.sessions.Session.request=bounded
jobs=[('sz_min15',lambda:ak.stock_zh_a_hist_min_em(symbol='000001',period='15')),('sh_min15',lambda:ak.stock_zh_a_hist_min_em(symbol='600519',period='15')),('bj_min15',lambda:ak.stock_zh_a_hist_min_em(symbol='920002',period='15')),('sz_min1',lambda:ak.stock_zh_a_hist_min_em(symbol='000001',period='1')),('sz_daily',lambda:ak.stock_zh_a_hist(symbol='000001',start_date='20260801',end_date='20260905')),('universe',lambda:ak.stock_info_a_code_name()),('sina_sz_min15',lambda:ak.stock_zh_a_minute(symbol='sz000001',period='15'))]
report=[]
for name,fn in jobs:
    tick=time.monotonic()
    try:
        df=fn();df.to_csv(out/f'{name}.csv',index=False)
        item={'name':name,'status':'success','rows':len(df),'columns':list(df.columns),'head':df.head(2).astype(str).to_dict('records'),'tail':df.tail(2).astype(str).to_dict('records')}
    except Exception as ex:
        item={'name':name,'status':'error','error':f'{type(ex).__name__}: {str(ex)[:350]}'}
    item['seconds']=round(time.monotonic()-tick,2);report.append(item)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(item,ensure_ascii=False),flush=True);time.sleep(2)
