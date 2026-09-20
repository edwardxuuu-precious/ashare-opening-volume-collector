"""Bounded, fail-closed readers for exchange status and listing records."""
from datetime import datetime, timedelta


SSE_URL = 'https://query.sse.com.cn/commonSoaQuery.do'
SSE_PAGE = 'https://www.sse.com.cn/disclosure/dealinstruc/suspension/'
SSE_SQL_ID = 'GW_PL_JYTS_TFPXX'
SSE_MAX_ROWS = 2000
MARKET_SUSPENSION_PAGE = 'https://data.eastmoney.com/tfpxx/'
LISTING_SOURCES = {
    'szse': 'https://www.szse.cn/market/product/stock/list/index.html',
    'sse': 'https://www.sse.com.cn/assortment/stock/list/share/',
    'bse': 'https://www.bse.cn/nq/listedcompany.html',
}


def _date(value):
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        return None
    try:
        return datetime.strptime(value, '%Y%m%d').date().isoformat()
    except ValueError:
        return None


def _iso_date(value):
    """Normalize provider date objects without importing pandas into validation."""
    if value is None or str(value) in ('', 'NaT', 'nan', 'None'):
        return None
    text = value.isoformat() if hasattr(value, 'isoformat') else str(value)
    text = text[:10]
    try:
        return datetime.strptime(text, '%Y-%m-%d').date().isoformat()
    except ValueError:
        return None


def parse_listing_records(rows, *, code_key, date_key, provider, source_url):
    if not isinstance(rows, list) or provider not in LISTING_SOURCES or source_url != LISTING_SOURCES[provider]:
        raise ValueError('Invalid exchange listing result')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get(code_key, '')).zfill(6)
        listing_date = _iso_date(row.get(date_key))
        if not re_full_code(code) or not listing_date:
            continue
        evidence = dict(kind='exchange_listing_record', provider=provider, code=code,
                        listingDate=listing_date, sourceUrl=source_url)
        if code in result and result[code] != evidence:
            raise ValueError('Conflicting exchange listing dates')
        result[code] = evidence
    if not result:
        raise ValueError('Empty exchange listing result')
    return result


def re_full_code(code):
    return isinstance(code, str) and len(code) == 6 and code.isascii() and code.isdigit()


def listing_evidence(catalog, code, day):
    record = catalog.get(code) if isinstance(catalog, dict) else None
    target = _iso_date(day)
    if not valid_listing_record(record, code) or not target or target >= record['listingDate']:
        return None
    return dict(kind='not_yet_listed', provider=record['provider'], code=code, date=target,
                listingDate=record['listingDate'], sourceUrl=record['sourceUrl'],
                validatedDates=[target])


def valid_listing_record(value, code):
    return (isinstance(value, dict) and value.get('kind') == 'exchange_listing_record'
            and value.get('code') == code and re_full_code(code)
            and value.get('provider') in LISTING_SOURCES
            and value.get('sourceUrl') == LISTING_SOURCES[value['provider']]
            and _iso_date(value.get('listingDate')) == value.get('listingDate'))


def load_listing_catalog(ak=None, *, retries=2, sleep=None):
    """Load one bounded current listing catalog from each official exchange."""
    if ak is None:
        import akshare as ak
    if type(retries) is not int or not 0 <= retries <= 4:
        raise ValueError('Invalid exchange listing retry count')
    if sleep is None:
        from time import sleep
    specs = (
        ('szse', 'A股列表', lambda: ak.stock_info_sz_name_code('A股列表'), 'A股代码', 'A股上市日期'),
        ('sse', '主板A股', lambda: ak.stock_info_sh_name_code('主板A股'), '证券代码', '上市日期'),
        ('sse', '科创板', lambda: ak.stock_info_sh_name_code('科创板'), '证券代码', '上市日期'),
        ('bse', '上市公司', ak.stock_info_bj_name_code, '证券代码', '上市日期'),
    )
    result = {}
    for provider, label, loader, code_key, date_key in specs:
        failure = None
        for attempt in range(retries + 1):
            try:
                frame = loader()
                parsed = parse_listing_records(frame.to_dict('records'), code_key=code_key,
                    date_key=date_key, provider=provider, source_url=LISTING_SOURCES[provider])
                break
            except (Exception, SystemExit) as exc:
                failure = exc
                if attempt < retries:
                    sleep(2 ** attempt)
        else:
            raise RuntimeError(f'Official listing source unavailable: {provider} {label}') from failure
        for code, evidence in parsed.items():
            if code in result and result[code] != evidence:
                raise ValueError('Conflicting exchange listing catalogs')
            result[code] = evidence
    if len(result) < 1000:
        raise ValueError('Exchange listing catalog is unexpectedly small')
    return result


def parse_market_suspensions(rows, day):
    """Parse exact full-day suspension intervals from the cross-market catalog."""
    target = _iso_date(day)
    if not target or not isinstance(rows, list):
        raise ValueError('Invalid market suspension result')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get('代码', '')).zfill(6)
        started_at = _iso_date(row.get('停牌时间'))
        ended_at = _iso_date(row.get('停牌截止时间'))
        duration = row.get('停牌期限')
        reason = row.get('停牌原因')
        market = row.get('所属市场')
        if (not re_full_code(code) or not started_at or not isinstance(duration, str)
                or '停牌' not in duration or '盘中' in duration
                or not isinstance(reason, str) or not reason.strip()
                or not isinstance(market, str) or not market.strip()
                or started_at > target or ended_at and target > ended_at):
            continue
        evidence = dict(kind='market_suspension_record', provider='eastmoney', code=code,
                        date=target, startDate=started_at, endDate=ended_at,
                        reason=reason.strip(), market=market.strip(), duration=duration.strip(),
                        sourceUrl=MARKET_SUSPENSION_PAGE, validatedDates=[target])
        if code in result and result[code] != evidence:
            raise ValueError('Conflicting market suspension records')
        result[code] = evidence
    return result


def load_market_suspensions(day, ak=None):
    if ak is None:
        import akshare as ak
    frame = ak.stock_tfp_em(date=day.replace('-', ''))
    return parse_market_suspensions(frame.to_dict('records'), day)


def valid_historical_no_trade(value, code, day):
    if not isinstance(value, dict) or value.get('code') != code or value.get('date') != day:
        return False
    if value.get('validatedDates') != [day] or not re_full_code(code) or _iso_date(day) != day:
        return False
    if value.get('kind') == 'not_yet_listed':
        record = dict(kind='exchange_listing_record', provider=value.get('provider'), code=code,
                      listingDate=value.get('listingDate'), sourceUrl=value.get('sourceUrl'))
        return valid_listing_record(record, code) and day < value['listingDate']
    if value.get('kind') == 'market_suspension_record':
        row = {'代码':code, '停牌时间':value.get('startDate'),
               '停牌截止时间':value.get('endDate'), '停牌期限':value.get('duration'),
               '停牌原因':value.get('reason'), '所属市场':value.get('market')}
        return parse_market_suspensions([row], day).get(code) == value
    return valid_exchange_evidence(value, code)


def _special_status(reason, started_at):
    if reason == '拟筹划重大资产重组':
        status_type = 'major_restructuring'
        label = '重大资产重组停牌'
    else:
        status_type = 'suspension'
        label = '交易所公告停牌'
    return dict(
        type=status_type,
        label=label,
        description=f'上交所记录显示该股自{started_at}起因「{reason}」停牌，目标交易日无交易。',
        startedAt=started_at,
        source='上海证券交易所停复牌信息',
        announcementTitle=f'上交所停复牌信息：{reason}',
        announcementUrl=SSE_PAGE,
    )


def parse_sse_suspensions(rows, day):
    """Return exact-day evidence for equities whose official interval covers day."""
    try:
        target = datetime.strptime(day, '%Y-%m-%d').date()
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid target date') from exc
    if not isinstance(rows, list):
        raise ValueError('Invalid SSE suspension result')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('controlType') != 'TR':
            continue
        code = row.get('productCode')
        started_at = _date(row.get('startStopDate'))
        ended_at = _date(row.get('endStopDate')) if row.get('endStopDate') else None
        reason = row.get('stopReason')
        record_type = row.get('type')
        if (not isinstance(code, str) or len(code) != 6 or not code.isdigit() or
                not started_at or not isinstance(reason, str) or not reason.strip() or
                record_type not in ('LSTP', 'LXTP')):
            continue
        if datetime.fromisoformat(started_at).date() > target:
            continue
        if ended_at and datetime.fromisoformat(ended_at).date() < target:
            continue
        evidence = dict(
            kind='exchange_suspension_record', provider='sse', code=code, date=day,
            startDate=started_at, endDate=ended_at, reason=reason.strip(),
            recordType=record_type, controlType='TR', sourceUrl=SSE_PAGE,
            validatedDates=[day],
            specialStatus=_special_status(reason.strip(), started_at),
        )
        # If overlapping records exist, prefer the most recent applicable start.
        if code not in result or evidence['startDate'] > result[code]['startDate']:
            result[code] = evidence
    return result


def valid_exchange_evidence(value, code):
    if not isinstance(value, dict) or value.get('kind') != 'exchange_suspension_record':
        return False
    record = dict(productCode=value.get('code'), controlType=value.get('controlType'),
                  startStopDate=str(value.get('startDate', '')).replace('-', ''),
                  endStopDate=str(value.get('endDate') or '').replace('-', ''),
                  stopReason=value.get('reason'), type=value.get('recordType'))
    try:
        expected = parse_sse_suspensions([record], value.get('date')).get(code)
    except ValueError:
        return False
    return value.get('code') == code and expected == value


def load_sse_suspensions(day, get=None):
    """Load one year of start records in one request, then resolve exact-day intervals."""
    target = datetime.strptime(day, '%Y-%m-%d').date()
    if get is None:
        import requests
        get = requests.get
    response = get(
        SSE_URL,
        params=dict(
            isPagination='true', sqlId=SSE_SQL_ID,
            **{'pageHelp.pageSize': str(SSE_MAX_ROWS), 'pageHelp.pageNo': '1'},
            productCode='', keyWords='',
            startStopDate=(target - timedelta(days=366)).strftime('%Y%m%d'),
            endStopDate=target.strftime('%Y%m%d'),
        ),
        headers={'Referer': 'https://www.sse.com.cn/', 'User-Agent': 'Stock-opening-observer/1.0'},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get('result') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) >= SSE_MAX_ROWS:
        raise ValueError('Invalid or truncated SSE suspension result')
    return parse_sse_suspensions(rows, day)
