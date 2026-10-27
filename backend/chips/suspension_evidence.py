"""Pure bridge from saved official suspension rows; never fetch or create candles."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import re

from .calendar import sessions
from ..scripts.exchange_status import (valid_exchange_evidence, parse_sse_suspensions,
                                      parse_szse_suspensions)

SCHEMA='stock.chips.suspension-evidence.v1'
LINEAGE='stock.chips.suspension-lineage.v1'
MAX_DAYS=366
MAX_PUBLICATION=20_000_000
MAX_MODEL_RAW=2*1024*1024


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def digest(value):return hashlib.sha256(encoded(value)).hexdigest()


def day(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('SUSPENSION_DATE_INVALID')
    return date.fromisoformat(value)


def instant(value):
    if not isinstance(value,str):raise ValueError('SUSPENSION_SOURCE_TIME_INVALID')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:raise ValueError('SUSPENSION_SOURCE_TIME_INVALID')
    return result


def code_ok(code):return isinstance(code,str) and re.fullmatch(r'[0-9]{6}',code) is not None


def _proof(row,code,source_day):
    proof=row.get('noTradeEvidence')
    if (row.get('code')!=code or row.get('date',source_day)!=source_day
        or type(row.get('verificationVersion')) is not int or row['verificationVersion']!=3
        or row.get('status')!='suspended' or row.get('priceStatus')!='not_traded'
        or any(field not in row or row[field] is not None for field in ('open','high','low','close'))):
        raise ValueError('OFFICIAL_SUSPENSION_ROW_CONFLICT')
    for field in ('first15Volume','dailyVolume','volume_shares','amount_cny','turnover_percent','ratio'):
        if row.get(field) is None:continue
        value=Decimal(str(row[field]))
        if isinstance(row[field],bool) or not value.is_finite() or value!=0:
            raise ValueError('OFFICIAL_SUSPENSION_POSITIVE_TRADE_CONFLICT')
    if (not isinstance(proof,dict) or proof.get('kind')!='exchange_suspension_record'
        or proof.get('provider') not in ('sse','szse') or proof.get('date')!=source_day
        or proof.get('validatedDates')!=[source_day] or not valid_exchange_evidence(proof,code)):
        raise ValueError('OFFICIAL_SUSPENSION_PROOF_INVALID')
    if not 0<=(day(source_day)-day(proof['startDate'])).days<=MAX_DAYS:
        raise ValueError('OFFICIAL_SUSPENSION_INTERVAL_BOUND')
    if proof['provider']=='sse' and proof['recordType']=='LSTP' and proof.get('endDate')!=proof['startDate']:
        raise ValueError('OFFICIAL_TEMPORARY_SUSPENSION_EXACT_DAY_REQUIRED')
    return proof


def extract(publication,code,source_key,source_etag=None):
    """Keep the exact selected row, not a fabricated historical query response.

    The publication hash is explicitly canonical JSON, not an HTTP/raw-byte hash.
    The caller freezes this bundle in its generation before using it.
    """
    if not code_ok(code) or not isinstance(publication,dict):raise ValueError('SUSPENSION_PUBLICATION_IDENTITY')
    source_day=publication.get('date');day(source_day)
    if source_key!=f'data/{source_day}.json':raise ValueError('SUSPENSION_PUBLICATION_KEY')
    rows=publication.get('rows')
    if not isinstance(rows,list) or len(rows)>15000:raise ValueError('SUSPENSION_PUBLICATION_ROWS')
    candidates=[row for row in rows if isinstance(row,dict) and row.get('code')==code]
    if len(candidates)>1:raise ValueError('SUSPENSION_DUPLICATE_SECURITY')
    if not candidates:return None
    row=candidates[0];proof=row.get('noTradeEvidence')
    if not isinstance(proof,dict) or proof.get('kind')!='exchange_suspension_record' or proof.get('provider') not in ('sse','szse'):
        return None
    proof=_proof(row,code,source_day)
    raw=encoded(publication)
    if len(raw)>MAX_PUBLICATION:raise ValueError('SUSPENSION_PUBLICATION_BOUND')
    generated=publication.get('generatedAt');instant(generated)
    if source_etag is not None and (not isinstance(source_etag,str) or not 0<len(source_etag)<=200):
        raise ValueError('SUSPENSION_PUBLICATION_ETAG')
    value={'schema':SCHEMA,'code':code,'publication_date':source_day,'source_key':source_key,
        'source_etag':source_etag,'publication_generated_at':generated,
        'publication_sha256':hashlib.sha256(raw).hexdigest(),'publication_bytes':len(raw),
        'publication_hash_basis':'CANONICAL_SORTED_SAVED_PUBLICATION_JSON',
        'source_observed_at':datetime.now(timezone.utc).isoformat(),
        'row':deepcopy(row),'row_sha256':digest(row),'proof_sha256':digest(proof),
        'historical_PIT_authenticated':False}
    value['bundle_sha256']=digest(value)
    return value


def _verified(bundle,code,as_of):
    if (not isinstance(bundle,dict) or bundle.get('schema')!=SCHEMA or bundle.get('code')!=code
        or bundle.get('historical_PIT_authenticated') is not False
        or bundle.get('publication_hash_basis')!='CANONICAL_SORTED_SAVED_PUBLICATION_JSON'):
        raise ValueError('SUSPENSION_BUNDLE_IDENTITY')
    source_day=bundle.get('publication_date')
    if not 0<=(day(as_of)-day(source_day)).days<=MAX_DAYS or bundle.get('source_key')!=f'data/{source_day}.json':
        raise ValueError('SUSPENSION_BUNDLE_DATE_OR_KEY')
    for key in ('publication_sha256','row_sha256','proof_sha256','bundle_sha256'):
        if not isinstance(bundle.get(key),str) or not re.fullmatch('[a-f0-9]{64}',bundle[key]):
            raise ValueError('SUSPENSION_BUNDLE_HASH_FORMAT')
    if type(bundle.get('publication_bytes')) is not int or not 0<bundle['publication_bytes']<=MAX_PUBLICATION:
        raise ValueError('SUSPENSION_PUBLICATION_BOUND')
    instant(bundle.get('publication_generated_at'));instant(bundle.get('source_observed_at'))
    if bundle.get('source_etag') is not None and (not isinstance(bundle['source_etag'],str) or not 0<len(bundle['source_etag'])<=200):
        raise ValueError('SUSPENSION_PUBLICATION_ETAG')
    row=bundle.get('row')
    if not isinstance(row,dict):raise ValueError('SUSPENSION_BUNDLE_ROW')
    proof=_proof(row,code,source_day)
    if (digest(row)!=bundle['row_sha256'] or digest(proof)!=bundle['proof_sha256']
        or digest({k:v for k,v in bundle.items() if k!='bundle_sha256'})!=bundle['bundle_sha256']):
        raise ValueError('SUSPENSION_BUNDLE_HASH_CONFLICT')
    return proof


def _covers(bundle,proof,target):
    # A current interval can establish earlier sessions. An older publication
    # never establishes a later session beyond its original source date.
    if target>bundle['publication_date'] or target<proof['startDate']:return False
    if proof['provider']=='sse' and proof['recordType']=='LSTP' and target!=proof['startDate']:return False
    if proof.get('resumeDate') and target>=proof['resumeDate']:return False
    if proof['provider']=='szse':
        result=parse_szse_suspensions(proof['queryRecords'],target,code=proof['code'])
    else:
        record={'productCode':proof['code'],'controlType':proof['controlType'],
            'startStopDate':proof['startDate'].replace('-',''),
            'endStopDate':str(proof.get('endDate') or '').replace('-',''),
            'stopReason':proof['reason'],'type':proof['recordType']}
        result=parse_sse_suspensions([record],target)
    return proof['code'] in result


def _positive_line(line):
    fields=line.split(',')
    if len(fields)!=11:raise ValueError('SUSPENSION_HISTORY_ROW_INVALID')
    values=[Decimal(fields[index]) for index in (5,6,10)]
    if any(not v.is_finite() or v<0 for v in values):raise ValueError('SUSPENSION_HISTORY_QUANTITY_INVALID')
    return any(v>0 for v in values)


def overlay(raw,receipt,stock,asof,bundles):
    """Append exact no-trade facts and lineage; leave every OHLC row unchanged."""
    code=stock.get('code') if isinstance(stock,dict) else stock
    if not code_ok(code) or not isinstance(raw,bytes) or not 0<len(raw)<=MAX_MODEL_RAW:
        raise ValueError('SUSPENSION_MODEL_INPUT_IDENTITY')
    day(asof)
    if not isinstance(receipt,dict) or receipt.get('sha256')!=hashlib.sha256(raw).hexdigest():
        raise ValueError('SUSPENSION_MODEL_INPUT_HASH')
    value=json.loads(raw);data=value.get('data')
    if not isinstance(data,dict) or data.get('code')!=code or value.get('rc')!=0:
        raise ValueError('SUSPENSION_MODEL_SECURITY_CONFLICT')
    lines=data.get('klines')
    if not isinstance(lines,list) or not 0<len(lines)<=10000:raise ValueError('SUSPENSION_HISTORY_BOUND')
    observed={};previous=None
    for line in lines:
        if not isinstance(line,str):raise ValueError('SUSPENSION_HISTORY_ROW_INVALID')
        target=line.split(',')[0];day(target)
        if target>asof or previous and target<=previous:raise ValueError('SUSPENSION_HISTORY_DATE_INVALID')
        observed[target]=line;previous=target
    if (day(asof)-day(max(observed))).days>MAX_DAYS:raise ValueError('SUSPENSION_GAP_LOOKBACK_BOUND')
    original=value.get('confirmed_no_trade_sessions',[])
    if not isinstance(original,list) or not all(isinstance(d,str) for d in original) or len(set(original))!=len(original):
        raise ValueError('SUSPENSION_EXISTING_DATES_INVALID')
    for target in original:
        day(target)
        if target>asof:raise ValueError('SUSPENSION_EXISTING_FUTURE_DATE')
        if target in observed and _positive_line(observed[target]):raise ValueError('SUSPENSION_POSITIVE_TRADE_CONFLICT')
    existing=value.get('official_no_trade_evidence',[])
    if not isinstance(existing,list):raise ValueError('SUSPENSION_EXISTING_LINEAGE_INVALID')
    all_bundles=list(bundles or [])
    for entry in existing:
        if (not isinstance(entry,dict) or entry.get('schema')!=LINEAGE or entry.get('code')!=code
            or entry.get('historical_PIT_authenticated') is not False or day(entry.get('as_of'))>day(asof)
            or not isinstance(entry.get('derived_confirmed_dates'),list)):
            raise ValueError('SUSPENSION_EXISTING_LINEAGE_INVALID')
        proof=_verified(entry.get('bundle'),code,asof)
        for target in entry['derived_confirmed_dates']:
            if target not in original or target>entry['as_of'] or not _covers(entry['bundle'],proof,target):
                raise ValueError('SUSPENSION_EXISTING_LINEAGE_DATE_INVALID')
        all_bundles.append(entry['bundle'])
    verified=[];identities={}
    for bundle in all_bundles:
        proof=_verified(bundle,code,asof);key=bundle['source_key']
        if key in identities and identities[key]!=bundle['row_sha256']:
            raise ValueError('SUSPENSION_DATED_SOURCE_CONFLICT')
        identities[key]=bundle['row_sha256'];verified.append((bundle,proof))
    # This production calendar only certifies 2026. Never invent older sessions
    # to satisfy the lookback bound or extend a halt across an unsupported year.
    start=max(min(observed),(day(asof)-timedelta(days=MAX_DAYS)).isoformat())
    start=max(start,f'{day(asof).year}-01-01')
    official=sessions(start,asof)
    supported={target:[] for target in official}
    for bundle,proof in verified:
        for target in official:
            if _covers(bundle,proof,target):supported[target].append(bundle)
    # A later source's explicit resumption can refute an earlier open interval.
    resumptions={record['fpkssj'][:10] for _,proof in verified if proof['provider']=='szse'
        for record in proof['queryRecords'] if record.get('fpkssj')}
    if any(supported.get(target) for target in resumptions):raise ValueError('SUSPENSION_RESUMPTION_CONFLICT')
    moved=value.get('unconfirmed_current_session_history_lines',[])
    if not isinstance(moved,list):raise ValueError('SUSPENSION_UNCONFIRMED_LINES_INVALID')
    for line in [*lines,*moved]:
        if not isinstance(line,str):raise ValueError('SUSPENSION_HISTORY_ROW_INVALID')
        target=line.split(',')[0]
        if supported.get(target) and _positive_line(line):raise ValueError('SUSPENSION_POSITIVE_TRADE_CONFLICT')
    quote=value.get('current_session_quote_overlay')
    if quote is not None:
        if not isinstance(quote,dict):raise ValueError('SUSPENSION_QUOTE_OVERLAY_INVALID')
        if supported.get(quote.get('date')) and quote.get('observed_positive_volume_trade') is True:
            raise ValueError('SUSPENSION_POSITIVE_QUOTE_CONFLICT')
    added=sorted(target for target,proofs in supported.items() if proofs and target not in observed and target not in original)
    if not added:return raw,receipt
    lineage=deepcopy(existing)
    for bundle,_ in verified:
        dates=[target for target in added if bundle in supported[target]]
        if dates and not any(entry['bundle']['bundle_sha256']==bundle['bundle_sha256'] and set(dates)<=set(entry['derived_confirmed_dates']) for entry in lineage):
            lineage.append({'schema':LINEAGE,'code':code,'as_of':asof,'derived_confirmed_dates':dates,
                'derivation':'OFFICIAL_INTERVAL_REPARSED_PER_SESSION; ORIGINAL_QUERY_IDENTITY_RETAINED',
                'bundle':deepcopy(bundle),'historical_PIT_authenticated':False})
    value['confirmed_no_trade_sessions']=[*original,*added]
    value['official_no_trade_evidence']=lineage
    merged=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
    if len(merged)>MAX_MODEL_RAW:raise ValueError('SUSPENSION_DERIVED_RAW_BOUND')
    result=deepcopy(receipt)
    result.setdefault('official_no_trade_original_bundle_sha256',receipt['sha256'])
    result.update(sha256=hashlib.sha256(merged).hexdigest(),bytes=len(merged),
        official_no_trade_evidence_sha256=digest(lineage),
        official_no_trade_added_dates=sorted(set(receipt.get('official_no_trade_added_dates',[]))|set(added)))
    return merged,result
