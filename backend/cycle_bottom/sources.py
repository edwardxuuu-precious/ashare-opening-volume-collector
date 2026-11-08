from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler

from backend.chips.calendar import sessions as official_sessions

MAX_RESPONSE = 8 * 1024 * 1024
CSI = 'https://www.csindex.com.cn/csindex-home/perf/index-perf'
SHANGHAI = 'https://finance.sina.com.cn/realstock/company/sh000001/hisdata/klc_kl.js'
SECONDARY_ORIGIN = 'https://jnindex.com.cn'
SECONDARY_CODE = '900330.CSJ'
SECONDARY_NAME = '中证报二级债券型基金指数'
CN = timezone(timedelta(hours=8))


def valid_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('INVALID_SOURCE_DATE')
    return value


def positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('INVALID_SOURCE_VALUE')
    return float(value)


def eligible_end(moment=None):
    moment = (moment or datetime.now(timezone.utc)).astimezone(CN)
    return (moment.date() if (moment.hour, moment.minute) >= (16, 30) else moment.date() - timedelta(days=1)).isoformat()


def get(url):
    # One public GET per source per run; never retry throttling or denied access.
    opener = build_opener(ProxyHandler({}))
    request = Request(url, headers={'User-Agent': 'StockCycleResearch/1.0', 'Accept-Encoding': 'identity'})
    with opener.open(request, timeout=25) as response:
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError('SOURCE_RESPONSE_BOUND')
    return raw


def parse_csi(raw, code, *, minimum, end, calendar):
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('success') is not True or not isinstance(value.get('data'), list):
        raise ValueError('CSI_RESPONSE_FORMAT')
    result, seen, excluded = [], set(), 0
    for row in value['data']:
        if not isinstance(row, dict) or row.get('indexCode') != code or not isinstance(row.get('tradeDate'), str):
            raise ValueError('CSI_SOURCE_IDENTITY')
        text = row['tradeDate']
        if not re.fullmatch(r'\d{8}', text):
            raise ValueError('CSI_DATE_FORMAT')
        day = valid_day(text[:4] + '-' + text[4:6] + '-' + text[6:])
        if day in seen:
            raise ValueError('CSI_DUPLICATE_DATE')
        seen.add(day)
        close = positive(row.get('close'))
        # The public endpoint can return a synthetic start-date anchor and a
        # holiday row. Retain raw evidence, exclude both from calculations.
        if day < minimum or day > end or day not in calendar:
            excluded += 1
            continue
        result.append({'date': day, 'close': close})
    result.sort(key=lambda row: row['date'])
    if len(result) < 500:
        raise ValueError('CSI_HISTORY_INSUFFICIENT')
    return result, excluded


def parse_shanghai(raw, *, end):
    import py_mini_racer
    from akshare.stock.cons import hk_js_decode

    text = raw.decode('utf-8')
    match = re.search(r'(?:var\s+)?KLC_KL_sh000001\s*=\s*"([^"]+)"', text)
    if not match:
        raise ValueError('SHANGHAI_SOURCE_IDENTITY')
    decoder = py_mini_racer.MiniRacer()
    try:
        decoder.eval(hk_js_decode)
        rows = decoder.call('d', match.group(1))
    finally:
        del decoder
    if not isinstance(rows, list) or len(rows) > 15000:
        raise ValueError('SHANGHAI_RESPONSE_FORMAT')
    result, seen = [], set()
    current_calendar = set(official_sessions('2026-01-01', '2026-12-31'))
    for row in rows:
        day = valid_day(str(row['date']).split('T')[0])
        if day in seen:
            raise ValueError('SHANGHAI_DUPLICATE_DATE')
        seen.add(day)
        frame = {key: positive(row.get(key)) for key in ('open', 'high', 'low', 'close')}
        if frame['low'] > min(frame['open'], frame['close']) or frame['high'] < max(frame['open'], frame['close']):
            raise ValueError('SHANGHAI_INVALID_OHLC')
        if day > end or (day.startswith('2026-') and day not in current_calendar):
            continue
        result.append({'date': day, **frame})
    result.sort(key=lambda row: row['date'])
    if len(result) < 7000 or result[0]['date'] > '1997-07-01':
        raise ValueError('SHANGHAI_LONG_HISTORY_INSUFFICIENT')
    return result


def secondary_url(value, path):
    if not isinstance(value, str) or not re.fullmatch(re.escape(path) + r'(?:\?t=\d+)?', value):
        raise ValueError('SECONDARY_SOURCE_PATH')
    return SECONDARY_ORIGIN + value


def parse_secondary(catalog_raw, history_raw, *, end, calendar):
    catalog, history = json.loads(catalog_raw), json.loads(history_raw)
    if not isinstance(catalog, dict) or not isinstance(catalog.get('infos'), list) or len(catalog['infos']) > 1000:
        raise ValueError('SECONDARY_CATALOG_FORMAT')
    matches = [row for row in catalog['infos'] if isinstance(row, dict) and row.get('indexCode') == SECONDARY_CODE]
    if len(matches) != 1:
        raise ValueError('SECONDARY_SOURCE_IDENTITY')
    info = matches[0]
    if (info.get('indexName') != SECONDARY_NAME or info.get('baseDate') != '2007-12-28'
            or positive(info.get('baseValue')) != 1000 or info.get('publishDate') != '2022-04-01'):
        raise ValueError('SECONDARY_SOURCE_IDENTITY')
    if not isinstance(history, dict) or not isinstance(history.get('values'), list) or not 500 <= len(history['values']) <= 20000:
        raise ValueError('SECONDARY_HISTORY_FORMAT')
    rows, excluded, previous = [], 0, ''
    for row in history['values']:
        if not isinstance(row, dict):
            raise ValueError('SECONDARY_HISTORY_FORMAT')
        day, close = valid_day(row.get('tradeDate')), positive(row.get('indexValue'))
        if day <= previous:
            raise ValueError('SECONDARY_DATE_ORDER')
        previous = day
        if day < info['baseDate'] or day > end or day not in calendar:
            excluded += 1
            continue
        rows.append({'date': day, 'close': close})
    first, last = history['values'][0], history['values'][-1]
    if (first['tradeDate'] != info['baseDate'] or first['indexValue'] != info['baseValue']
            or last['tradeDate'] != valid_day(history.get('date'))
            or last['tradeDate'] != valid_day(info.get('tradeDate'))
            or abs(last['indexValue'] - positive(info.get('indexValue'))) > 1e-8):
        raise ValueError('SECONDARY_CATALOG_HISTORY_MISMATCH')
    if len(rows) < 500:
        raise ValueError('SECONDARY_HISTORY_INSUFFICIENT')
    return rows, excluded, info


def fetch_secondary(*, fetcher, end, calendar):
    raw_site = fetcher(SECONDARY_ORIGIN + '/json/site.json')
    site = json.loads(raw_site)
    if not isinstance(site, dict):
        raise ValueError('SECONDARY_SITE_FORMAT')
    catalog_url = secondary_url(site.get('fundIndexJson'), '/json/fund_index.json')
    raw_catalog = fetcher(catalog_url)
    catalog = json.loads(raw_catalog)
    if not isinstance(catalog, dict) or not isinstance(catalog.get('infos'), list) or len(catalog['infos']) > 1000:
        raise ValueError('SECONDARY_CATALOG_FORMAT')
    matches = [item for item in catalog['infos'] if isinstance(item, dict) and item.get('indexCode') == SECONDARY_CODE]
    if len(matches) != 1:
        raise ValueError('SECONDARY_SOURCE_IDENTITY')
    history_url = secondary_url(matches[0].get('valueJson'), '/json/900330.CSJ_perf.json')
    raw_history = fetcher(history_url)
    rows, excluded, info = parse_secondary(raw_catalog, raw_history, end=end, calendar=calendar)
    missing = len({day for day in calendar if rows[0]['date'] <= day <= rows[-1]['date']} - {row['date'] for row in rows})
    source = {
        'id': 'secondary', 'code': SECONDARY_CODE, 'name': SECONDARY_NAME, 'provider': '中国证券报金牛财富指数',
        'firstDate': rows[0]['date'], 'lastDate': rows[-1]['date'], 'rows': len(rows),
        'excludedRows': excluded, 'missingSessions': missing, 'url': history_url,
        'baseDate': info['baseDate'], 'publishDate': info['publishDate'],
        'note': '正式二级债基类别参照，非原图Wind885007.WI。份额对数加权、半年调样；2022-04-01发布，之前历史为回溯数据。按真实共同日计算，不填充缺失值。',
        'sha256': hashlib.sha256(raw_history).hexdigest(),
        'catalogSha256': hashlib.sha256(raw_catalog).hexdigest(),
        'siteSha256': hashlib.sha256(raw_site).hexdigest(),
    }
    return rows, source, {'secondary': raw_history, 'secondary-catalog': raw_catalog, 'secondary-site': raw_site}


def fetch_sources(*, include_nonpure=False, include_secondary=False, moment=None, fetcher=get):
    end = eligible_end(moment)
    raw_shanghai = fetcher(SHANGHAI)
    shanghai = parse_shanghai(raw_shanghai, end=end)
    calendar = {row['date'] for row in shanghai}
    latest = shanghai[-1]['date']
    if end.startswith('2026-'):
        current = official_sessions('2026-01-01', end)
        calendar.update(current)
        if current:
            latest = current[-1]
    raw_files = {'shanghai': raw_shanghai}
    data = {'shanghai': shanghai}
    metadata = [{
        'id': 'shanghai', 'code': '000001', 'name': '上证综合指数', 'provider': '新浪财经',
        'firstDate': shanghai[0]['date'], 'lastDate': shanghai[-1]['date'], 'rows': len(shanghai),
        'excludedRows': 0, 'missingSessions': 0, 'url': SHANGHAI,
        'note': '不复权日线合成真实月K；2026交易日按交易所日历核对，早年按来源实有交易日。',
        'calendarVerified': end.startswith('2026-'),
        'sha256': hashlib.sha256(raw_shanghai).hexdigest(),
    }]
    if not end.startswith('2026-'):
        metadata[0]['note'] = '当前年度交易日历尚未核对，仅按来源实有日期更新；不据此声明最新交易日数据完整。'
    specs = [('equity', 'H00300', '沪深300全收益指数', '2004-12-31'),
             ('government', '000012', '上证国债指数', '2003-01-02')]
    if include_nonpure:
        specs.append(('nonpure', '930897', '中证非纯债债券型基金指数', '2008-01-01'))
    for key, code, name, minimum in specs:
        url = CSI + '?' + urlencode({'indexCode': code, 'startDate': minimum.replace('-', ''), 'endDate': end.replace('-', '')})
        raw = fetcher(url)
        rows, excluded = parse_csi(raw, code, minimum=minimum, end=end, calendar=calendar)
        data[key] = rows
        raw_files[key] = raw
        missing = len({day for day in calendar if rows[0]['date'] <= day <= rows[-1]['date']} - {row['date'] for row in rows})
        metadata.append({
            'id': key, 'code': code, 'name': name, 'provider': '中证指数公开历史行情',
            'firstDate': rows[0]['date'], 'lastDate': rows[-1]['date'], 'rows': len(rows),
            'excludedRows': excluded, 'missingSessions': missing, 'url': url,
            'note': ('仅可投资股票/可转债债券基金的公开参照，非Wind二级债基；基日前孤点排除；2016-08-31发布前属回溯数据。' if key == 'nonpure' else '按真实交易日交集计算，不填充缺失值；全收益含分红再投资，国债指数含利息及再投资。'),
            'sha256': hashlib.sha256(raw).hexdigest(),
        })
    if include_secondary:
        rows, source, secondary_raw = fetch_secondary(fetcher=fetcher, end=end, calendar=calendar)
        data['secondary'] = rows
        metadata.append(source)
        raw_files.update(secondary_raw)
    return data, metadata, raw_files, latest
