"""Refresh the collection calendar while preserving saved historical dates."""
import argparse,json,fcntl
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import akshare as ak
from collect import RequestBudget,write_json
from retention import retain_six_months,six_month_start
p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args();out=Path(a.out)
lock=(out/'.collector.lock').open('a');fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
now=datetime.now(ZoneInfo('Asia/Shanghai'));today=now.date().isoformat();RequestBudget().install()
calendar=ak.tool_trade_date_hist_sina();dates=[str(d) for d in calendar.trade_date if six_month_start(today)<=str(d)<today or str(d)==today and now.strftime('%H:%M')>='15:00']
meta={'calendarAsOf':today,'calendarUpdatedAt':now.isoformat(),'retentionMonths':6,'retentionStart':six_month_start(today),'tradingDates':dates}
write_json(out/'calendar.json',meta)
path=out/'manifest.json'
if path.exists():
    m=json.loads(path.read_text());m.update(meta);write_json(path,m)
retain_six_months(out,today)
print(json.dumps({'calendarAsOf':today,'dates':len(dates),'retentionStart':meta['retentionStart']}))
