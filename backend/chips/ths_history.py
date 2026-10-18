"""Bounded public THS unadjusted daily rows, with original strings retained.

Protocol: https://pytdx-docs.readthedocs.io/zh-cn/latest/fuquan_factor/
00 is unadjusted. Historical volume, amount and turnover are supplied per row;
missing values stay missing and are rejected by the common model validator.
"""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext


def normalize(stock, value, raw, as_of, start_date=None, full=True):
    rows = value.get('data', '').split(';')
    if not rows or not rows[0] or len(rows) > 10000 or value.get('num') != len(rows):
        raise ValueError('THS_ROW_COUNT_INVALID')
    if full and int(value.get('total', -1)) != len(rows):
        raise ValueError('THS_FULL_HISTORY_CAPPED')
    lines = []
    previous = None
    last = ''
    for row in rows:
        fields = row.split(',')
        if len(fields) != 11:
            raise ValueError('THS_FIELD_COUNT_INVALID')
        day = datetime.strptime(fields[0], '%Y%m%d').date().isoformat()
        if day <= last:
            raise ValueError('THS_DATES_NOT_INCREASING')
        last = day
        if day > as_of:
            continue
        # Do not infer missing turnover or amount from other fields.
        try:
            with localcontext() as context:
                context.prec = 50
                close = Decimal(fields[4])
                volume = str(Decimal(fields[5]) / 100) if fields[5] else ''
                change = str(close - previous) if previous is not None else ''
                pct = str((close / previous - 1) * 100) if previous else ''
        except InvalidOperation as exc:
            raise ValueError('THS_NUMERIC_FORMAT_INVALID') from exc
        previous = close
        if day > as_of or (start_date and day < start_date):
            continue
        lines.append(','.join([day, fields[1], fields[4], fields[2], fields[3],
                               volume, fields[6], '', pct, change, fields[7]]))
    if not lines:
        raise ValueError('THS_REQUESTED_HISTORY_MISSING')
    normalized = {
        'rc': 0, 'data': {'code': stock['code'], 'name': stock['name'], 'klines': lines},
        'adapter': 'THS_UNADJUSTED_DAILY_STRING_ROWS_V1',
        'original_source_utf8': raw.decode('utf-8'),
        'original_source_sha256': hashlib.sha256(raw).hexdigest(),
        'first_source_date': datetime.strptime(rows[0].split(',')[0], '%Y%m%d').date().isoformat(),
        'full_history_received': full,
        'reference_close_method': 'PREVIOUS_OBSERVED_UNADJUSTED_CLOSE; corporate-action reference not certified',
    }
    bundle = json.dumps(normalized, ensure_ascii=False, separators=(',', ':')).encode()
    return normalized, bundle


def history(source, stock, as_of, start_date=None):
    suffix = 'last' if start_date else 'last10000'
    url = f'https://d.10jqka.com.cn/v6/line/hs_{stock["code"]}/00/{suffix}.js'
    raw = source.read_bytes(url)
    prefix = f'quotebridge_v6_line_hs_{stock["code"]}_00_{suffix}('
    text = raw.decode('utf-8').strip().rstrip(';')
    if not text.startswith(prefix) or not text.endswith(')'):
        raise ValueError('THS_CALLBACK_IDENTITY_MISMATCH')
    original = json.loads(text[len(prefix):-1])
    value, bundle = normalize(stock, original, raw, as_of, start_date, full=not start_date)
    receipt = {
        'provider': 'THS_UNADJUSTED', 'url': url,
        'sha256': hashlib.sha256(bundle).hexdigest(), 'bytes': len(bundle),
        'original_source_sha256': hashlib.sha256(raw).hexdigest(),
        'ingested_at': datetime.now(timezone.utc).isoformat(),
        'source_volume_unit': 'SHARES', 'source_amount_unit': 'CNY',
        'source_turnover_unit': 'PERCENT', 'adjustment': '00_UNADJUSTED',
        'original_source_preserved': True,
        'reference_close_method': value['reference_close_method'],
    }
    return value, receipt, bundle
