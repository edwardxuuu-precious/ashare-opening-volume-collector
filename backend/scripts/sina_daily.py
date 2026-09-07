"""Pinned Sina unadjusted day bars without AKShare's unused share-capital lookup.

Only the raw Sina response and AKShare's own decoder are used.  The returned
columns are the unadjusted price/volume subset of ``stock_zh_a_daily``; share
capital and turnover are intentionally absent.  In particular, a capital-change
date must never manufacture a trading row through an outer join/forward fill.
All HTTP requests still pass through the caller's shared request budget.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import inspect

import pandas as pd


AKSHARE_VERSION = '1.18.88'
SOURCE_SHA256 = '168f97acbacc65139e590476d9a22a0b763b14c31561762d8c264aaeceea8f47'
DECODER_SHA256 = '39a599c94dde4df1c2eb0882d4bff9560160cfd52ead0797cebb04b3122c2f52'
HISTORY_URL = 'https://finance.sina.com.cn/realstock/company/{}/hisdata_klc2/klc_kl.js'
VALUE_COLUMNS = ['open', 'high', 'low', 'close', 'volume', 'amount']
COLUMNS = ['date'] + VALUE_COLUMNS


def adapter_ready(ak):
    """Unknown package/source/decoder versions always retain the official path."""
    import akshare.stock.stock_zh_a_sina as provider
    try:
        return (getattr(ak, '__version__', '') == AKSHARE_VERSION and
                ak.stock_zh_a_daily is provider.stock_zh_a_daily and
                hashlib.sha256(inspect.getsource(ak.stock_zh_a_daily).encode()).hexdigest() == SOURCE_SHA256 and
                hashlib.sha256(provider.hk_js_decode.encode()).hexdigest() == DECODER_SHA256 and
                provider.zh_sina_a_stock_hist_url == HISTORY_URL)
    except (AttributeError, OSError, TypeError):
        return False


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    return datetime.strptime(text, '%Y%m%d' if len(text) == 8 else '%Y-%m-%d').date()


def _metadata(frame, degraded=False, reason=None):
    frame.attrs.update(speedDegraded=degraded,
                       dailyAdapter='akshare_official' if degraded else 'sina_daily_pinned',
                       dailyAdapterVersion=AKSHARE_VERSION,
                       dailyAdapterSourceSha256=SOURCE_SHA256)
    if reason:
        frame.attrs['degradationReason'] = reason
    return frame


def _decoded_frame(provider, response_text, start, end):
    decoder = provider.py_mini_racer.MiniRacer()
    decoder.eval(provider.hk_js_decode)
    rows = decoder.call('d', response_text.split('=')[1].split(';')[0].replace('"', ''))
    if not isinstance(rows, list):
        raise ValueError('Sina daily decoder returned an unexpected value')
    if not rows:
        # An empty source remains empty: it is not evidence of suspension.
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.DataFrame(rows)
    if not set(COLUMNS).issubset(frame.columns):
        raise ValueError('Sina daily response is missing required columns')
    frame = frame[COLUMNS].copy()
    frame['date'] = pd.to_datetime(frame['date'], errors='coerce').dt.date
    frame[VALUE_COLUMNS] = frame[VALUE_COLUMNS].astype(float)
    frame = frame[frame['date'].notna()]
    frame = frame[(frame['date'] >= start) & (frame['date'] <= end)].copy()
    # Match the pinned unadjusted branch's ordering and duplicate treatment.
    frame = frame.sort_values('date', kind='stable')
    frame = frame.drop_duplicates(subset=VALUE_COLUMNS)
    frame[['open', 'high', 'low', 'close']] = frame[['open', 'high', 'low', 'close']].round(2)
    frame = frame.dropna().drop_duplicates().reset_index(drop=True)
    return frame


def daily_unadjusted(ak, symbol, start_date='19900101', end_date='21000118'):
    """Return exact source-day bars and attach an observable fallback indicator.

    Compatibility/decode failures fall back to AKShare. Network exceptions and
    the shared cutoff exception propagate unchanged, letting the durable retry
    queue handle them without doubling a failed request. Callers can use
    ``frame.attrs['speedDegraded']`` to expose compatibility fallback in status.
    """
    start, end = _date(start_date), _date(end_date)
    if end < start:
        raise ValueError('Daily end date must not precede start date')

    def fallback(reason):
        frame = ak.stock_zh_a_daily(symbol=symbol, start_date=start.strftime('%Y%m%d'),
                                   end_date=end.strftime('%Y%m%d'), adjust='')
        return _metadata(frame, degraded=True, reason=reason)

    if not adapter_ready(ak):
        return fallback('pinned_source_mismatch')
    import akshare.stock.stock_zh_a_sina as provider
    # Keep network operations outside the compatibility fallback catch. The
    # existing parent-installed budget owns timeouts, backoff and request rate.
    response = provider.requests.get(provider.zh_sina_a_stock_hist_url.format(symbol))
    response.raise_for_status()
    try:
        frame = _decoded_frame(provider, response.text, start, end)
    except Exception as exc:
        return fallback('decode_or_schema_' + type(exc).__name__)
    return _metadata(frame)
