"""Exchange-published 2026 calendar; unsupported years fail closed."""
from datetime import date, timedelta

SOURCES = [
    'https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml',
    'https://www.szse.cn/disclosure/notice/general/t20251222_618087.html',
    'https://www.bse.cn/important_news/200027428.html',
]
CLOSED = [('01-01','01-03'),('02-15','02-23'),('04-04','04-06'),('05-01','05-05'),
          ('06-19','06-21'),('09-25','09-27'),('10-01','10-07')]

def sessions(start, end):
    a,b=date.fromisoformat(start),date.fromisoformat(end)
    if a.year != 2026 or b.year != 2026 or b<a:
        raise ValueError('OFFICIAL_CALENDAR_YEAR_UNAVAILABLE')
    days=[]
    while a<=b:
        md=a.strftime('%m-%d')
        if a.weekday()<5 and not any(lo<=md<=hi for lo,hi in CLOSED): days.append(a.isoformat())
        a+=timedelta(days=1)
    return days

def last_ten(as_of):
    values=sessions('2026-01-01',as_of)
    if len(values)<10 or values[-1]!=as_of: raise ValueError('TEN_COMPLETED_SESSIONS_REQUIRED')
    return values[-10:]
