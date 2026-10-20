import pandas as pd,json
from pathlib import Path
p=Path('var/data/probe'); results=[]
for market in ['sz','sh','bj']:
    a=pd.read_csv(p/f'sina_{market}_min15.csv');b=pd.read_csv(p/f'sina_{market}_min1.csv');d=pd.read_csv(p/f'{market}_daily.csv') if market=='sz' else pd.read_csv(p/f'{market}_daily_sina.csv').rename(columns={'date':'日期','volume':'成交量'})
    if market!='sz':d['成交量']=d['成交量']/100
    dates=sorted(set(b.day.str[:10]))[-5:]
    for day in dates:
        x=a[a.day.str.startswith(day)];y=b[b.day.str.startswith(day)];z=d[d['日期']==day]
        r={'market':market,'date':day,'bars15':len(x),'bars1':len(y),'first1Timestamp':str(y.day.min()),'first15':int(x.iloc[0].volume),'first1Sum':int(y[y.day.str[11:19]<='09:45:00'].volume.sum()),'sum15':int(x.volume.sum()),'sum1':int(y.volume.sum()),'dailyShares':float(z.iloc[0]['成交量'])*100 if len(z) else None}
        r['dayDiff']=r['sum15']-r['dailyShares'] if r['dailyShares'] is not None else None;results.append(r)
(p/'reconciliation.json').write_text(json.dumps(results,indent=2,ensure_ascii=False));print(json.dumps(results,indent=2))
