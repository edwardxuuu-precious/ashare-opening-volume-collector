"""One bounded post-close Sina market snapshot for the current trading day."""
from __future__ import annotations

from datetime import datetime
import math
import re
from zoneinfo import ZoneInfo


ZONE = ZoneInfo('Asia/Shanghai')
PREFIXES = {'sz': 'SZ', 'sh': 'SH', 'bj': 'BJ'}
REQUIRED = {'代码', '最新价', '昨收', '最高', '最低', '成交量', '时间戳'}


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _metrics(row):
    close = _number(row.get('最新价'))
    previous = _number(row.get('昨收'))
    high = _number(row.get('最高'))
    low = _number(row.get('最低'))
    if None in (close, previous, high, low) or previous <= 0:
        return None, None
    return round((close-previous)/previous*100, 4), round((high-low)/previous*100, 4)


def _post_close_time(value):
    match = re.search(r'(\d{2}:\d{2}:\d{2})', str(value))
    return match.group(1) if match and match.group(1) >= '15:00:00' else None


def load_snapshot(ak, universe, day, *, moment=None):
    """Return exact-share close rows joined to the frozen target universe."""
    moment = (moment or datetime.now(ZONE)).astimezone(ZONE)
    if day != moment.date().isoformat() or moment.strftime('%H:%M') < '15:30':
        raise ValueError('Sina spot snapshot is only valid for the current day after 15:30')
    frame = ak.stock_zh_a_spot()
    if not REQUIRED <= set(frame.columns):
        raise ValueError('Sina spot snapshot is missing required columns')
    rows = {}
    for source in frame.to_dict('records'):
        symbol = str(source.get('代码', '')).lower()
        match = re.fullmatch(r'(sz|sh|bj)(\d{6})', symbol)
        if not match:
            continue
        prefix, code = match.groups()
        volume = _number(source.get('成交量'))
        if volume is None or volume < 0 or volume != int(volume):
            continue
        pct_change, amplitude = _metrics(source)
        rows[code] = dict(code=code, date=day, market=PREFIXES[prefix],
                          dailyVolume=int(volume), pctChange=pct_change,
                          amplitude=amplitude, quoteTime=str(source.get('时间戳')),
                          sourceProvider='sina', dailyAdapter='sina_market_snapshot')
    expected = {item['code'] for item in universe}
    available = expected & set(rows)
    coverage = len(available) / len(expected) if expected else 0
    expected_markets = {PREFIXES['sh'] if code.startswith(('60', '68')) else
                        PREFIXES['sz'] if code.startswith(('00', '30')) else
                        PREFIXES['bj'] for code in expected}
    available_markets = {rows[code]['market'] for code in available}
    if coverage < .98 or not expected_markets <= available_markets:
        raise ValueError('Sina spot snapshot coverage is below the target-day safety gate')
    fresh_markets = {rows[code]['market'] for code in available
                     if _post_close_time(rows[code]['quoteTime'])}
    if not expected_markets <= fresh_markets:
        raise ValueError('Sina spot snapshot is stale for one or more target markets')
    return dict(date=day, rows={code:rows[code] for code in sorted(available)},
                availableCount=len(available), missingCodes=sorted(expected-set(rows)),
                extraCodes=sorted(set(rows)-expected), coverage=coverage,
                source='AKShare.stock_zh_a_spot')
