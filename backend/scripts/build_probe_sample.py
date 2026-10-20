"""Publish only actual probe observations. No demo or synthetic stock values."""
from collect import evaluate,publish,write_json
import pandas as pd
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
p=Path('var/data/probe');results=[];dates=['2026-08-31','2026-09-01','2026-09-02','2026-09-03','2026-09-04']
u=pd.read_csv(p/'universe.csv',dtype={'code':str});universe=u.set_index('code')['name'].to_dict()
for m,code in [('sz','000001'),('sh','600519'),('bj','920002')]:
 minute=pd.read_csv(p/f'sina_{m}_min15.csv');daily=pd.read_csv(p/f'{m}_daily_sina.csv')
 results.append({'code':code,'days':{d:evaluate(code,universe[code],d,minute,daily) for d in dates}})
publish(results,dates,Path('var/data'),'sample',len(u))
write_json(Path('var/data/universe.json'),{'asOf':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),'historicalMembership':False,'total':len(u),'rows':u.to_dict('records')})
