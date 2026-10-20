"""Read-only public Eastmoney probe, runnable with Python's standard library in another runtime.

No credentials, retries, alternate hosts, system proxy changes, or writes to stock data.
Example: python backend/scripts/probe_eastmoney_endpoint.py --code 600519 --date 2026-09-04
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import time
import urllib.error
import urllib.parse
import urllib.request


def probe(code, day, timeout=15):
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
    common = {'fields1': 'f1,f2,f3,f4,f5,f6',
              'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
              'ut': '7eea3edcaed734bea9cbfc24409ed989', 'fqt': '0',
              'secid': ('1.' if code.startswith('6') else '0.') + code}
    report = {'at': datetime.now(timezone.utc).isoformat(), 'code': code, 'date': day,
              'host': urllib.parse.urlsplit(url).hostname, 'observations': []}
    parsed = {}
    for period, beg, end in [('15', '0', '20500000'), ('101', day.replace('-', ''), day.replace('-', ''))]:
        started = time.monotonic()
        item = {'period': period}
        request = urllib.request.Request(url + '?' + urllib.parse.urlencode(dict(common, klt=period, beg=beg, end=end)),
                                         headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                item['httpStatus'] = response.status
                data = json.load(response)
            rows = [row.split(',') for row in (data.get('data') or {}).get('klines', [])]
            selected = [row for row in rows if row[0][:10] == day]
            item.update(status='ok' if selected else 'empty', returnedRows=len(rows), selectedRows=len(selected),
                        firstTimestamp=rows[0][0] if rows else None, lastTimestamp=rows[-1][0] if rows else None)
            parsed[period] = selected
        except urllib.error.HTTPError as exc:
            item.update(status='error', error=type(exc).__name__, httpStatus=exc.code)
        except Exception as exc:
            item.update(status='error', error=type(exc).__name__)
        item['seconds'] = round(time.monotonic()-started, 3)
        report['observations'].append(item)
        if item.get('httpStatus') in (403, 429):
            report['stopReason'] = 'access_denied_or_rate_limited'
            break
        if period == '15':
            time.sleep(1)
    minute, daily = parsed.get('15', []), parsed.get('101', [])
    if minute and len(daily) == 1:
        first = [r for r in minute if r[0].endswith('09:45') or r[0].endswith('09:45:00')]
        total = sum(Decimal(r[5]) for r in minute) * 100
        dv = Decimal(daily[0][5]) * 100
        report['comparison'] = {'minuteBars': len(minute), 'uniqueTimestamps': len({r[0] for r in minute}),
            'minuteDayVolumeShares': str(total), 'dailyVolumeShares': str(dv),
            'differenceShares': str(total-dv), 'minuteClose': minute[-1][2], 'dailyClose': daily[0][2],
            'first15VolumeShares': str(Decimal(first[0][5])*100) if len(first) == 1 else None,
            'note': 'Diagnostic only; production evaluator must also validate timestamps, integer volumes and close.'}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', default='600519')
    parser.add_argument('--date', default='2026-09-04')
    args = parser.parse_args()
    if not (len(args.code) == 6 and args.code.isdigit()):
        parser.error('code must contain six digits')
    datetime.strptime(args.date, '%Y-%m-%d')
    print(json.dumps(probe(args.code, args.date), ensure_ascii=False, indent=2))
