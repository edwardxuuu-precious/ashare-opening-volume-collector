"""Read-only, date-bound chip replay using the published stock's saved inputs.

No upstream requests or writes. The daily model and its publication are intact.
Older chart dates can load the hash-bound archive to recover the warm-up bars
which the 500-candle browser projection does not contain.
"""
from __future__ import annotations
import base64
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import gzip
import hashlib
import io
import json
import re
from collections import OrderedDict
from threading import Lock
from .calendar import CLOSED as CLOSED_2026
from .eastmoney_model import MODEL, VERSION, calculate, compare_snapshots
from .model import Bar, iso_day, number
from .ohlc4_input import prepare_eastmoney, validate_bar

CALENDARS = {
    2023: [('01-01','01-02'),('01-21','01-27'),('04-05','04-05'),('04-29','05-03'),('06-22','06-24'),('09-29','10-06')],
    2024: [('01-01','01-01'),('02-09','02-17'),('04-04','04-06'),('05-01','05-05'),('06-10','06-10'),('09-15','09-17'),('10-01','10-07')],
    2025: [('01-01','01-01'),('01-28','02-04'),('04-04','04-06'),('05-01','05-05'),('05-31','06-02'),('10-01','10-08')],
    2026: CLOSED_2026,
}
CALENDAR_SOURCES = {
    2023: 'https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20221227_5714459.shtml',
    2024: 'https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20231226_5733941.shtml',
    2025: 'https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml',
    2026: 'https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml',
}


def is_session(day):
    d = date.fromisoformat(iso_day(day))
    if d.year not in CALENDARS:
        raise ValueError('HISTORY_CALENDAR_YEAR_UNAVAILABLE')
    md = d.strftime('%m-%d')
    return d.weekday() < 5 and not any(a <= md <= b for a,b in CALENDARS[d.year])


def previous_session(day):
    if not is_session(day):
        raise ValueError('SELECTED_DATE_IS_NOT_A_MARKET_SESSION')
    d = date.fromisoformat(day)
    for _ in range(20):
        d -= timedelta(days=1)
        if is_session(d.isoformat()):
            return d.isoformat()
    raise ValueError('PREVIOUS_MARKET_SESSION_UNAVAILABLE')


def read_json(s3, bucket, key):
    stream = s3.get_object(Bucket=bucket, Key=key)['Body']
    raw = stream.read(8*1024*1024+1)
    if len(raw) > 8*1024*1024:
        raise ValueError('HISTORY_OBJECT_READ_BOUND_EXCEEDED')
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(16*1024*1024+1)
    if len(raw) > 16*1024*1024:
        raise ValueError('HISTORY_DECODED_READ_BOUND_EXCEEDED')
    return json.loads(raw)


def archive_keys(frame):
    code = frame['security_id']
    source = frame.get('source', {})
    candidates = [source.get('same_session_cached_raw_key')]
    for generation in (frame.get('dataset_id'), frame.get('reused_from_generation')):
        if isinstance(generation,str) and re.fullmatch('[a-f0-9]{16}', generation):
            candidates.extend(f'collector/chips/raw/{generation}/{code}{suffix}.json' for suffix in ('','-current-quote'))
    candidates.append(source.get('original_history_raw_key'))
    pattern = rf'collector/chips/raw/(?:[a-f0-9]{{16}}|repair-\d{{4}}-\d{{2}}-\d{{2}})/{code}(?:-current-quote|-[a-f0-9]{{16}})?\.json'
    return list(dict.fromkeys(k for k in candidates if isinstance(k,str) and re.fullmatch(pattern,k)))


def load_archive(s3, bucket, frame):
    expected = frame.get('source',{}).get('sha256')
    if not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected):
        raise ValueError('HISTORY_SOURCE_HASH_REQUIRED')
    for key in archive_keys(frame):
        try:
            saved = read_json(s3,bucket,key)
        except Exception as exc:
            # Without ListBucket, a missing immutable candidate may be 403.
            code = getattr(exc,'response',{}).get('Error',{}).get('Code')
            if code in ('NoSuchKey','AccessDenied','404','403'):
                continue
            raise
        if saved.get('receipt',{}).get('sha256') != expected:
            continue
        encoded = saved.get('raw_base64','')
        if not isinstance(encoded,str) or len(encoded) > 3*1024*1024:
            raise ValueError('HISTORY_RAW_SOURCE_BOUND_EXCEEDED')
        raw = base64.b64decode(encoded,validate=True)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('HISTORY_RAW_SOURCE_HASH_MISMATCH')
        return raw, key
    raise ValueError('HASH_BOUND_HISTORICAL_SOURCE_UNAVAILABLE')


def replay(frame, selected_date, *, archived_raw=None):
    iso_day(selected_date)
    if frame.get('model',{}).get('version') != VERSION or frame.get('unchanged_threshold') is not None:
        raise ValueError('HISTORY_REQUIRES_CURRENT_COEFFICIENT_TWO_MODEL')
    dates = [r['date'] for r in frame.get('history',[])]
    if selected_date != frame['as_of'] and selected_date not in dates:
        raise ValueError('SELECTED_DATE_HAS_NO_PUBLISHED_CANDLE')
    result = {
        'schema':'stock.chips.history.v1','dataset_id':frame['dataset_id'],
        'published_as_of':frame['as_of'],'security_id':frame['security_id'],'name':frame['name'],
        'selected_date':selected_date,'previous_date':None,'model':MODEL,
        'unchanged_threshold':None,'snapshots':{},'comparison_status':'READY',
        'reasons':[],'historical_PIT_authenticated':False,
    }
    # The latest view must remain exactly the sealed daily screen comparison.
    if selected_date == frame['as_of']:
        snapshots = deepcopy(frame['snapshots'])
        result.update(snapshots=snapshots,previous_date=max((d for d in snapshots if d<selected_date),default=None),
                      input_kind='PUBLISHED_DAILY_SNAPSHOTS')
        if not result['previous_date']:
            result['comparison_status']='NO_PREVIOUS_LISTED_SESSION'
        return result
    eligible = [r for r in frame['history'] if r['date'] <= selected_date]
    no_trade = set()
    if archived_raw is not None:
        decoded = json.loads(archived_raw)
        # Same projection used by the daily runner for normalized THS/Bao input.
        raw = json.dumps({'rc':decoded['rc'],'data':decoded['data']},ensure_ascii=False,separators=(',',':')).encode()
        bars,audit = prepare_eastmoney(raw,expected_code=frame['security_id'],cutoff=selected_date,allow_valid_suffix=True)
        no_trade = set(decoded.get('confirmed_no_trade_sessions',[]))
        result.update(input_kind='HASH_BOUND_ARCHIVE',input_source_sha256=hashlib.sha256(archived_raw).hexdigest())
        if audit['invalid_source_rows']:
            result['reasons'].append('MODEL_RESEEDED_AFTER_INVALID_SOURCE_HISTORY')
    else:
        if len(eligible)<210:
            raise ValueError('OLDER_DATE_REQUIRES_ARCHIVED_WARMUP')
        bars = [Bar(r['date'],*(number(r[k]) for k in ('open','high','low','close','volume_shares','amount_cny','turnover_percent'))) for r in eligible]
        for bar in bars:
            validate_bar(bar)
        result['input_kind']='PUBLISHED_VALIDATED_CANDLES'
    if not bars or bars[-1].source_date != selected_date:
        raise ValueError('SELECTED_SOURCE_CANDLE_MISSING')
    window = bars[-210:]
    current = calculate(window,security_id=frame['security_id'],window_start=window[0].source_date,window_rows=len(window))
    result['snapshots'][selected_date] = current
    try:
        prior = previous_session(selected_date)
    except ValueError as exc:
        result.update(comparison_status='PREVIOUS_SESSION_UNAVAILABLE',reasons=[*result['reasons'],str(exc)])
        return result
    result['previous_date'] = prior
    result['calendar_source'] = CALENDAR_SOURCES[int(selected_date[:4])]
    prefix = [b for b in window if b.source_date <= prior]
    if not prefix:
        result['comparison_status']='NO_PREVIOUS_LISTED_SESSION' if frame.get('listing_context',{}).get('listed') == selected_date else 'PREVIOUS_SESSION_UNAVAILABLE'
        return result
    if prefix[-1].source_date != prior:
        d = date.fromisoformat(prefix[-1].source_date)+timedelta(days=1)
        while d.isoformat()<=prior:
            day=d.isoformat()
            if is_session(day) and day not in no_trade:
                result.update(comparison_status='PREVIOUS_SESSION_UNAVAILABLE',reasons=[*result['reasons'],'UNCONFIRMED_PREVIOUS_SESSION_GAP'])
                return result
            d+=timedelta(days=1)
    previous = calculate(prefix,security_id=frame['security_id'],window_start=window[0].source_date,window_rows=len(window))
    if prefix[-1].source_date != prior:
        previous.update(source_date=prior,last_trade_date=prefix[-1].source_date,market_session_status='SOURCE_CONFIRMED_NO_TRADE',turnover_percent='0',effective_replacement='0')
    comparison=compare_snapshots(previous,current)
    for snapshot,field in ((previous,'previous_share_percent'),(current,'current_same_price_share_percent')):
        snapshot['comparison_anchor_shares_percent']={r['anchor_price_cny']:r[field] for r in comparison['anchors']}
    result['snapshots'][prior]=previous
    result['comparison']=comparison
    return result


def load_history(s3,bucket,dataset,code,selected_date):
    iso_day(selected_date)
    frame=read_json(s3,bucket,f'data/chips/daily/{dataset}/{code}.json')
    if (frame.get('dataset_id')!=dataset or frame.get('security_id')!=code
            or frame.get('schema')!='stock.chips.market.stock.v1' or frame.get('chip_status')!='READY'):
        raise ValueError('HISTORY_STOCK_IDENTITY_MISMATCH')
    if selected_date>frame['as_of']:
        raise ValueError('SELECTED_DATE_AFTER_PUBLICATION')
    eligible=[r for r in frame['history'] if r['date']<=selected_date]
    if selected_date!=frame['as_of'] and selected_date not in {r['date'] for r in frame['history']}:
        raise ValueError('SELECTED_DATE_HAS_NO_PUBLISHED_CANDLE')
    prior_missing=False
    try:
        prior_missing=previous_session(selected_date) not in {r['date'] for r in eligible}
    except ValueError:
        pass
    if selected_date==frame['as_of'] or (len(eligible)>=210 and not prior_missing):
        return replay(frame,selected_date)
    raw,key=load_archive(s3,bucket,frame)
    value=replay(frame,selected_date,archived_raw=raw)
    value['input_archive_key']=key
    return value


class ImmutableHistoryStore:
    """Warm Lambda input cache, bounded by encoded bytes; never caches auth.

    Only dataset-bound published frames and hash-verified archive candidates pass
    through this wrapper. Mutable manifests and screening pointers bypass it.
    """
    def __init__(self, store, max_bytes=16*1024*1024, max_entries=128):
        self.store=store;self.max_bytes=max_bytes;self.max_entries=max_entries
        self.values=OrderedDict();self.size=0;self.lock=Lock()

    def get_object(self, *, Bucket, Key):
        identity=(Bucket,Key)
        with self.lock:
            value=self.values.get(identity)
            if value is not None:self.values.move_to_end(identity)
        if value is None:
            body=self.store.get_object(Bucket=Bucket,Key=Key)['Body']
            try:value=body.read(8*1024*1024+1)
            finally:body.close()
            if len(value)>8*1024*1024:raise ValueError('HISTORY_OBJECT_READ_BOUND_EXCEEDED')
            with self.lock:
                if identity not in self.values and len(value)<=self.max_bytes:
                    self.values[identity]=value;self.size+=len(value)
                    while self.size>self.max_bytes or len(self.values)>self.max_entries:
                        _,old=self.values.popitem(last=False);self.size-=len(old)
        return {'Body':io.BytesIO(value)}


def load_history_batch(s3,bucket,dataset,code,selected_date):
    """Eight actual candle dates per request; each retains its own 210-bar replay."""
    iso_day(selected_date)
    frame=read_json(s3,bucket,f'data/chips/daily/{dataset}/{code}.json')
    if (frame.get('dataset_id')!=dataset or frame.get('security_id')!=code
            or frame.get('schema')!='stock.chips.market.stock.v1' or frame.get('chip_status')!='READY'):
        raise ValueError('HISTORY_STOCK_IDENTITY_MISMATCH')
    dates=sorted({r['date'] for r in frame.get('history',[])}|{frame['as_of']})
    if selected_date not in dates:raise ValueError('SELECTED_DATE_HAS_NO_PUBLISHED_CANDLE')
    index=dates.index(selected_date)
    # Canonical blocks make independently selected neighbors share one read.
    block=dates[index//8*8:index//8*8+8]
    frames={};errors={}
    for day in block:
        try:frames[day]=load_history(s3,bucket,dataset,code,day)
        except ValueError as exc:errors[day]=str(exc)[:180]
    return {'schema':'stock.chips.history.batch.v1','dataset_id':dataset,'security_id':code,
            'published_as_of':frame['as_of'],'selected_date':selected_date,'dates':block,
            'frames':frames,'errors':errors}
