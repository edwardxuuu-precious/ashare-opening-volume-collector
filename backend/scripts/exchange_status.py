"""Bounded, fail-closed readers for official exchange suspension records."""
from datetime import datetime, timedelta


SSE_URL = 'https://query.sse.com.cn/commonSoaQuery.do'
SSE_PAGE = 'https://www.sse.com.cn/disclosure/dealinstruc/suspension/'
SSE_SQL_ID = 'GW_PL_JYTS_TFPXX'
SSE_MAX_ROWS = 2000


def _date(value):
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        return None
    try:
        return datetime.strptime(value, '%Y%m%d').date().isoformat()
    except ValueError:
        return None


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
