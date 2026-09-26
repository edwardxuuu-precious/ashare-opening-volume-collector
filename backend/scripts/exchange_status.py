"""Bounded, fail-closed readers for exchange status and listing records."""
from datetime import datetime, timedelta
import re


SSE_URL = 'https://query.sse.com.cn/commonSoaQuery.do'
SSE_PAGE = 'https://www.sse.com.cn/disclosure/dealinstruc/suspension/'
SSE_SQL_ID = 'GW_PL_JYTS_TFPXX'
SSE_MAX_ROWS = 2000
SZSE_URL = 'https://www.szse.cn/api/report/ShowReport/data'
SZSE_PAGE = 'https://www.szse.cn/disclosure/memo/index.html'
SZSE_CATALOG = '1798'
SZSE_MAX_CODES = 20
SZSE_MAX_PAGES = 3
MARKET_SUSPENSION_PAGE = 'https://data.eastmoney.com/tfpxx/'
LISTING_SOURCES = {
    'szse': 'https://www.szse.cn/market/product/stock/list/index.html',
    'sse': 'https://www.sse.com.cn/assortment/stock/list/share/',
    'bse': 'https://www.bse.cn/nq/listedcompany.html',
}
OFFICIAL_DISCLOSURE_SUSPENSIONS = (
    dict(
        code='300862',
        startDate='2026-07-27',
        endDate='2026-08-07',
        resumeDate='2026-08-10',
        statusType='major_restructuring',
        label='重大资产重组停牌',
        reason='筹划发行股份及支付现金购买资产并募集配套资金事项',
        announcementTitle='关于筹划发行股份及支付现金购买资产并募集配套资金事项的停牌公告',
        sourceUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-07-28/79003dec-c51b-41c5-808a-2d9973a3b513.PDF'),
        resumeUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-08-08/39dda0e8-ce15-4746-8355-a31a04a9a828.PDF'),
    ),
    dict(
        code='002731',
        startDate='2026-05-06',
        endDate='2026-07-06',
        resumeDate='2026-07-07',
        statusType='suspension',
        label='定期报告未披露停牌',
        reason='未在法定期限内披露2025年年度报告',
        announcementTitle='关于股票交易被实施退市风险警示暨停复牌安排的公告',
        sourceUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-07-06/2ff408da-fb79-4c86-a18a-bd07ad1be299.PDF'),
        resumeUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-07-06/2ff408da-fb79-4c86-a18a-bd07ad1be299.PDF'),
    ),
    dict(
        code='000793',
        startDate='2026-06-18',
        endDate='2026-06-18',
        resumeDate='2026-06-22',
        statusType='suspension',
        label='重整转增股本停牌',
        reason='实施重整计划资本公积金转增股本事项',
        announcementTitle='关于公司股票停牌的提示性公告',
        sourceUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-06-18/c60e9d37-4b29-43fc-bcf5-364a61fc7cb7.PDF'),
        resumeUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-06-22/23950a07-eefd-4f01-834e-0d139ebd3eb2.PDF'),
    ),
    dict(
        code='301139', startDate='2026-05-11', endDate='2026-05-11',
        resumeDate='2026-05-12', statusType='suspension',
        label='重大违法退市风险警示停牌',
        reason='可能触及重大违法强制退市情形，实施退市风险警示',
        announcementTitle='关于公司股票交易将被实施退市风险警示暨停复牌的公告',
        sourceUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-05-09/593c8b8f-30f6-4be8-abbd-5289fafbbb15.PDF'),
        resumeUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-05-09/593c8b8f-30f6-4be8-abbd-5289fafbbb15.PDF'),
    ),
    dict(
        code='000016', startDate='2026-04-29', endDate='2026-04-29',
        resumeDate='2026-04-30', statusType='suspension',
        label='退市及其他风险警示停牌', reason='实施退市风险警示及其他风险警示',
        announcementTitle='关于公司股票被实施退市风险警示及其他风险警示暨股票停复牌的提示性公告',
        sourceUrl=('https://disc.static.szse.cn/disc/disk03/finalpage/'
                   '2026-04-29/33994a43-0f3a-4776-8857-ed8163a15ce6.PDF'),
        resumeUrl=('https://disc.static.szse.cn/disc/disk03/finalpage/'
                   '2026-04-29/33994a43-0f3a-4776-8857-ed8163a15ce6.PDF'),
    ),
    dict(
        code='002175', startDate='2026-04-29', endDate='2026-04-29',
        resumeDate='2026-04-30', statusType='suspension',
        label='退市风险警示停牌', reason='实施退市风险警示',
        announcementTitle='关于公司股票交易被实施退市风险警示暨股票停牌的公告',
        sourceUrl=('https://disc.static.szse.cn/disc/disk03/finalpage/'
                   '2026-04-29/01cd3652-6986-4e3d-beb3-7c702124695b.PDF'),
        resumeUrl=('https://disc.static.szse.cn/disc/disk03/finalpage/'
                   '2026-04-29/01cd3652-6986-4e3d-beb3-7c702124695b.PDF'),
    ),
    dict(
        code='000838', startDate='2026-04-24', endDate='2026-04-24',
        resumeDate='2026-04-27', statusType='suspension',
        label='退市及其他风险警示停牌', reason='实施退市风险警示及其他风险警示',
        announcementTitle='关于公司股票交易被实施退市风险警示、其他风险警示暨股票停复牌的公告',
        sourceUrl=('https://disc.static.szse.cn/disc/disk03/finalpage/'
                   '2026-04-24/5bcda419-ad92-4d75-9258-ff3a4642c802.PDF'),
        resumeUrl=('https://disc.static.szse.cn/disc/disk03/finalpage/'
                   '2026-04-24/5bcda419-ad92-4d75-9258-ff3a4642c802.PDF'),
    ),
    dict(
        code='000908', startDate='2026-03-25', endDate='2026-03-25',
        resumeDate='2026-03-26', statusType='suspension',
        label='风险警示变更停牌', reason='撤销退市风险警示并继续实施其他风险警示',
        announcementTitle='关于撤销退市风险警示并继续实施其他风险警示暨股票停复牌的公告',
        sourceUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-03-25/bf9310d2-f7ec-4e9a-94d4-f899c1c5d425.PDF'),
        resumeUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-03-25/bf9310d2-f7ec-4e9a-94d4-f899c1c5d425.PDF'),
    ),
    dict(
        code='300385', startDate='2026-03-17', endDate='2026-03-20',
        resumeDate='2026-03-23', statusType='suspension',
        label='预重整投资人遴选停牌', reason='预重整投资人遴选工作',
        announcementTitle='关于与预重整投资人签署《重整投资协议》暨公司股票复牌的公告',
        sourceUrl=('https://static.cninfo.com.cn/finalpage/'
                   '2026-03-21/1225022232.PDF'),
        resumeUrl=('https://static.cninfo.com.cn/finalpage/'
                   '2026-03-21/1225022232.PDF'),
    ),
    dict(
        code='000711', startDate='2026-03-13', endDate='2026-03-19',
        resumeDate='2026-03-20', statusType='suspension',
        label='交易异常波动核查停牌', reason='股票交易异常波动核查',
        announcementTitle='关于公司股票交易异常波动情况暨停牌核查的公告',
        sourceUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-03-13/5ea1dafb-9a10-43e0-b8a9-5991bcf69520.pdf'),
        resumeUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-03-20/f7a51b7c-eb07-41bd-bd94-90f4031f08b8.PDF'),
    ),
    dict(
        code='000908', startDate='2026-03-10', endDate='2026-03-10',
        resumeDate='2026-03-11', statusType='suspension',
        label='重整转增股本停牌', reason='实施重整计划资本公积金转增股本事项',
        announcementTitle='关于公司股票停牌的提示性公告',
        sourceUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-03-10/88a34429-8f96-49d0-9994-4f84c662c421.PDF'),
        resumeUrl=('https://disc.static.szse.cn/disc/disk03/finalpage/'
                   '2026-03-11/ac1585aa-e128-47df-a6af-91f6b22c5870.PDF'),
    ),
    dict(
        code='002445', startDate='2026-02-13', endDate='2026-03-06',
        resumeDate='2026-03-09', statusType='major_restructuring',
        label='重大资产重组停牌', reason='筹划发行股份及支付现金购买资产并募集配套资金事项',
        announcementTitle='关于筹划重大资产重组事项的停牌公告',
        sourceUrl=('https://disc.static.szse.cn/download/disc/disk03/finalpage/'
                   '2026-02-13/18a3ebf3-215f-4822-9dcc-51a24d6b9bd9.PDF'),
        resumeUrl=('https://static.cninfo.com.cn/finalpage/'
                   '2026-03-09/1224999965.PDF'),
    ),
)


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


def official_disclosure_suspensions(day):
    """Return frozen exact-day evidence backed by official exchange disclosures."""
    target = _iso_date(day)
    if not target:
        raise ValueError('Invalid official disclosure target date')
    result = {}
    for record in OFFICIAL_DISCLOSURE_SUSPENSIONS:
        if not record['startDate'] <= target <= record['endDate']:
            continue
        code = record['code']
        result[code] = dict(
            kind='official_disclosure_suspension', provider='szse', code=code,
            date=target, startDate=record['startDate'], endDate=record['endDate'],
            resumeDate=record['resumeDate'], reason=record['reason'], sourceUrl=record['sourceUrl'],
            resumeUrl=record['resumeUrl'], validatedDates=[target],
            specialStatus=dict(
                type=record['statusType'], label=record['label'],
                description=(f"深交所公告显示该股自{record['startDate']}起因"
                             f"「{record['reason']}」停牌，并于{record['resumeDate']}开市起复牌；"
                             '目标交易日无交易。'),
                startedAt=record['startDate'], source='深圳证券交易所公司公告',
                announcementTitle=record['announcementTitle'],
                announcementUrl=record['sourceUrl'],
            ),
        )
    return result


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
    if value.get('kind') == 'official_disclosure_suspension':
        try:
            return official_disclosure_suspensions(day).get(code) == value
        except ValueError:
            return False
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
    if value.get('provider') == 'szse':
        try:
            expected = parse_szse_suspensions(value.get('queryRecords'), value.get('date'), code=code).get(code)
        except (ValueError, TypeError):
            return False
        return expected is not None and value == expected
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


def _szse_target(day, code):
    if not isinstance(day, str) or _iso_date(day) != day or not re_full_code(code) or not code.startswith(('00', '30')):
        raise ValueError('Invalid SZSE equity/date identity')
    return (datetime.fromisoformat(day) - timedelta(days=366)).date().isoformat()


def _szse_instant(value):
    if value == '':
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2} (开市|\d{2}:\d{2}:\d{2})', value):
        raise ValueError('Invalid SZSE suspension time')
    return datetime.strptime(value.replace('开市', '09:30:00'), '%Y-%m-%d %H:%M:%S')


def parse_szse_suspensions(rows, day, *, code):
    """Resolve complete, code-filtered official event history for one exact day.

    A later resumption closes a former open-ended stop. Only the most recent
    suspension can apply; intraday stops and the resumption date are excluded.
    The provider's reason is preserved without inferring a corporate event.
    """
    query_start = _szse_target(day, code)
    if not isinstance(rows, list) or len(rows) > SZSE_MAX_PAGES * 10:
        raise ValueError('Invalid or unbounded SZSE history')
    starts, resumptions, saved, seen = {}, [], [], set()
    fields = ('zqdm', 'zqjc', 'tpkssj', 'fpkssj', 'tpsj', 'tpyy')
    for raw in rows:
        if (not isinstance(raw, dict) or raw.get('zqdm') != code or
                any(not isinstance(raw.get(key), str) for key in fields) or
                not raw['zqjc'].strip() or not raw['tpyy'].strip()):
            raise ValueError('SZSE response identity or explanation mismatch')
        row = {key: raw[key] for key in fields}
        fingerprint = tuple(row[key] for key in fields)
        if fingerprint in seen:
            raise ValueError('Duplicate SZSE event')
        seen.add(fingerprint)
        saved.append(row)
        start, resume = _szse_instant(row['tpkssj']), _szse_instant(row['fpkssj'])
        if start and not query_start <= start.date().isoformat() <= day:
            raise ValueError('SZSE query date filter mismatch')
        if start is None:
            if row['tpsj'] != '取消停牌' or resume is None:
                raise ValueError('Invalid SZSE resumption event')
            resumptions.append(resume)
            continue
        if resume and resume <= start:
            raise ValueError('Conflicting SZSE suspension interval')
        if start in starts:
            raise ValueError('Conflicting SZSE suspension identity')
        starts[start] = (row, resume)
        if resume:
            resumptions.append(resume)
    if not starts:
        return {}
    start = max(starts)
    row, resume = starts[start]
    subsequent = [value for value in resumptions if value > start]
    resume = min(subsequent) if subsequent else None
    target_open = datetime.fromisoformat(day + 'T09:30:00')
    target_close = datetime.fromisoformat(day + 'T15:00:00')
    if start > target_open or resume and resume <= target_close:
        return {}
    if row['tpsj'] not in ('停牌', '连续停牌') and not re.fullmatch(r'[1-9]\d*天', row['tpsj']):
        return {}
    if row['tpsj'] not in ('停牌', '连续停牌') and resume is None:
        raise ValueError('Finite SZSE suspension has no resumption time')
    start_day = start.date().isoformat()
    resume_day = resume.date().isoformat() if resume else None
    reason = row['tpyy'].strip()
    description = f'深交所停复牌记录显示该股自{start_day}起因「{reason}」停牌，目标交易日{day}全天无交易。'
    if resume_day:
        description += f'交易所登记的复牌时间为{resume.strftime("%Y-%m-%d %H:%M:%S")}。'
    return {code: dict(
        kind='exchange_suspension_record', provider='szse', code=code, date=day,
        startDate=start_day, resumeDate=resume_day, reason=reason,
        sourceUrl=SZSE_PAGE, validatedDates=[day], queryStart=query_start, queryEnd=day,
        queryRecords=saved,
        specialStatus=dict(type='suspension', label='交易所公告停牌', description=description,
                           startedAt=start_day, source='深圳证券交易所停复牌提示',
                           announcementTitle=f'深交所停复牌提示：{reason}', announcementUrl=SZSE_PAGE),
    )}


def load_szse_suspensions(day, codes, get=None):
    """Read at most 20 known equities and three complete pages per equity.

    No quote requests, guessed reasons, silent truncation, or unbounded retries.
    A source outage or conflicting identity rejects the batch without new facts.
    """
    if not isinstance(codes, (list, tuple)) or len(codes) > SZSE_MAX_CODES:
        raise ValueError('Unbounded SZSE suspension request')
    if not isinstance(day, str) or _iso_date(day) != day:
        raise ValueError('Invalid SZSE target date')
    for code in codes:
        _szse_target(day, code)
    if get is None:
        import requests
        get = requests.get
    result = {}
    for code in sorted(set(codes)):
        rows, expected, page = [], None, 1
        while True:
            response = get(SZSE_URL, params=dict(
                SHOWTYPE='JSON', CATALOGID=SZSE_CATALOG, TABKEY='tab1',
                txtDmorjc=code, txtKsrq=_szse_target(day, code), txtZzrq=day, PAGENO=str(page)),
                headers={'Referer': SZSE_PAGE, 'User-Agent': 'Stock-opening-observer/1.0'}, timeout=15)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
                raise ValueError('Invalid SZSE report envelope')
            report = payload[0]
            meta, data = report.get('metadata'), report.get('data')
            if (report.get('error') or not isinstance(meta, dict) or not isinstance(data, list) or
                    meta.get('catalogid') != SZSE_CATALOG or meta.get('tabkey') != 'tab1' or
                    any(type(meta.get(key)) is not int for key in ('pageno', 'pagecount', 'recordcount', 'pagesize'))):
                raise ValueError('Invalid SZSE report metadata')
            pages, total, size = meta['pagecount'], meta['recordcount'], meta['pagesize']
            if (meta['pageno'] != page or not 0 <= pages <= SZSE_MAX_PAGES or size != 10 or
                    not 0 <= total <= SZSE_MAX_PAGES * size or pages != (total + size - 1) // size or
                    len(data) != min(size, max(0, total - (page - 1) * size))):
                raise ValueError('Incomplete or unbounded SZSE response')
            signature = (pages, total, size)
            if expected is not None and expected != signature:
                raise ValueError('SZSE pagination changed during read')
            expected = signature
            rows.extend(data)
            if page >= pages:
                break
            page += 1
        if len(rows) != total:
            raise ValueError('Incomplete SZSE event history')
        result.update(parse_szse_suspensions(rows, day, code=code))
    return result
