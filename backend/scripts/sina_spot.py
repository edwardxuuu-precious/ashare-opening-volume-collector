"""One bounded post-close Sina market snapshot for the current trading day."""
from __future__ import annotations

from datetime import datetime, timedelta
import math
import re
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')
PREFIXES = {'sz': 'SZ', 'sh': 'SH', 'bj': 'BJ'}
REQUIRED = {'代码', '最新价', '昨收', '最高', '最低', '成交量', '时间戳'}
DAILY_FALLBACK_LIMIT = 10


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
    volume = _number(row.get('成交量'))
    if volume == 0:
        return None, None
    if None in (close, previous, high, low) or previous <= 0:
        return None, None
    return round((close-previous)/previous*100, 4), round((high-low)/previous*100, 4)


def _post_close_time(value):
    match = re.search(r'(\d{2}:\d{2}:\d{2})', str(value))
    return match.group(1) if match and match.group(1) >= '15:00:00' else None


def _daily_metrics(frame, day):
    date_col = 'date' if 'date' in frame else ('日期' if '日期' in frame else None)
    close_col = 'close' if 'close' in frame else '收盘'
    high_col = 'high' if 'high' in frame else '最高'
    low_col = 'low' if 'low' in frame else '最低'
    if date_col is None or not {close_col, high_col, low_col} <= set(frame.columns):
        return None, None
    values = frame[[date_col, close_col, high_col, low_col]].copy()
    values[date_col] = values[date_col].astype(str).str[:10]
    values = values.drop_duplicates(subset=date_col, keep='last').sort_values(date_col).reset_index(drop=True)
    hit = values.index[values[date_col] == day]
    if len(hit) != 1 or int(hit[0]) == 0:
        return None, None
    index = int(hit[0])
    previous = _number(values.iloc[index - 1][close_col])
    close = _number(values.iloc[index][close_col])
    high = _number(values.iloc[index][high_col])
    low = _number(values.iloc[index][low_col])
    if None in (previous, close, high, low) or previous <= 0:
        return None, None
    return round((close - previous) / previous * 100, 4), round((high - low) / previous * 100, 4)


def _daily_quote_fallback(ak, code, day):
    """Fill rare symbols omitted by the bulk snapshot without changing volume."""
    prefix = 'sh' if code.startswith(('60', '68')) else 'sz' if code.startswith(('00', '30')) else 'bj'
    symbol = prefix + code
    start = (datetime.fromisoformat(day).date() - timedelta(days=10)).strftime('%Y%m%d')
    if prefix in ('sh', 'sz'):
        frame = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start,
                                      end_date=day.replace('-', ''), adjust='')
        adapter = 'tencent_daily_quote_fallback'
        provider = 'tencent'
    else:
        frame = ak.stock_zh_a_daily(symbol=symbol, start_date=start,
                                    end_date=day.replace('-', ''), adjust='')
        adapter = 'sina_daily_quote_fallback'
        provider = 'sina'
    pct_change, amplitude = _daily_metrics(frame, day)
    if pct_change is None or amplitude is None:
        return None
    return dict(code=code, date=day, market=PREFIXES[prefix], pctChange=pct_change,
                amplitude=amplitude, sourceProvider=provider, dailyAdapter=adapter)


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
    for code in sorted(expected - set(rows))[:DAILY_FALLBACK_LIMIT]:
        try:
            fallback = _daily_quote_fallback(ak, code, day)
        except Exception:
            fallback = None
        if fallback:
            rows[code] = fallback
    available = expected & set(rows)
    return dict(date=day, rows={code:rows[code] for code in sorted(available)},
                availableCount=len(available), missingCodes=sorted(expected-set(rows)),
                extraCodes=sorted(set(rows)-expected), coverage=coverage,
                source='AKShare.stock_zh_a_spot')
