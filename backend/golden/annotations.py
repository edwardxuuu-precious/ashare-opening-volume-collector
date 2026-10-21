"""Authenticated shared drawings: bounded documents, owner writes and CAS.

No deletion API. Every edit retains the prior version. Authenticated usernames
come only from the existing Cognito gateway, never from request payloads.
"""
import json, re
from datetime import datetime, timezone, date
from decimal import Decimal
from .watchlist import SYMBOLS

SCHEMA = 'stock.golden.annotations.v1'
MAX_BYTES = 1500000


def empty(symbol):
    if symbol not in SYMBOLS: raise ValueError('SYMBOL')
    return dict(schema=SCHEMA, symbol=symbol, revision=0, drawings=[], reviews=[])


def day(v):
    if not isinstance(v,str) or date.fromisoformat(v).isoformat()!=v: raise ValueError('DATE')
    return v


def text(v,n):
    if not isinstance(v,str) or len(v)>n: raise ValueError('TEXT')
    return v


def point(v):
    if not isinstance(v,dict) or set(v)!={'date','field','price'}: raise ValueError('POINT')
    day(v['date'])
    if v['field'] not in ('open','high','low','close','custom'): raise ValueError('FIELD')
    raw=v['price']
    if not isinstance(raw,str) or not re.fullmatch(r'\d{1,8}(?:\.\d{1,8})?',raw): raise ValueError('PRICE')
    if not Decimal(0)<Decimal(raw)<Decimal('100000000'): raise ValueError('PRICE')
    return dict(v)


def calibration_context(example, mode, axis_id):
    """Trusted context derived from the immutable published example, not UI claims."""
    if mode not in ('HL','HM') or not isinstance(axis_id,str): raise ValueError('AUTO_CONTEXT')
    result=example.get('result') or {}
    axis=next((a for g in result.get('price_groups',[]) for a in g['records'] if a['id']==axis_id),None)
    if axis_id and axis is None: raise ValueError('AXIS_NOT_IN_SAVED_EXAMPLE')
    return dict(symbol=example['symbol'],dataset_id=example['dataset_id'],as_of=example['as_of'],
        policy_sha256=example['policy_sha256'],auto_mode=mode,axis_id=axis_id,
        H=result.get('H'),L=result.get('L'),
        M={k:axis[k] for k in ('id','price','B_conditional')} if mode=='HM' and axis else None)


def apply(document, payload, user, symbol, now=None, automatic=None):
    # Pure validation and CAS revision check; the S3 ETag is checked separately.
    if document.get('schema')!=SCHEMA or document.get('symbol')!=symbol or symbol not in SYMBOLS: raise ValueError('DOCUMENT')
    if not isinstance(payload,dict) or type(payload.get('revision')) is not int: raise ValueError('REVISION')
    if payload['revision']!=document['revision']: raise Conflict('页面已有新保存，请刷新后重试')
    import copy
    result=copy.deepcopy(document)
    stamp=now or datetime.now(timezone.utc).isoformat()
    kind=payload.get('kind')
    if kind=='calibration':
        if set(payload)!={'kind','revision','drawing','review'}: raise ValueError('CALIBRATION_FIELDS')
        drawing,review=payload['drawing'],payload['review']
        if not isinstance(drawing,dict) or not isinstance(review,dict) or set(review)!={'status','note','axis_id','auto_mode'}: raise ValueError('CALIBRATION_FIELDS')
        if not automatic or automatic['symbol']!=symbol or automatic['dataset_id']!=drawing.get('dataset_id') or automatic['as_of']!=drawing.get('as_of') or automatic['axis_id']!=review['axis_id'] or automatic['auto_mode']!=review['auto_mode']: raise ValueError('CALIBRATION_SOURCE')
        # Validate both edits using the existing contracts. The gateway writes
        # the resulting document once, under its original S3 ETag.
        drawn=apply(document,dict(kind='drawing',revision=document['revision'],drawing=drawing),user,symbol,stamp)
        updated=apply(drawn,dict(kind='review',revision=drawn['revision'],as_of=drawing['as_of'],dataset_id=drawing['dataset_id'],**review),user,symbol,stamp)
        saved=next(d for d in updated['drawings'] if d['id']==drawing['id'])
        current=next(r for r in updated['reviews'] if r['owner']==user and r['dataset_id']==drawing['dataset_id'])
        current.update(round_id=f"{symbol}:{document['revision']+1}",drawing_snapshot=copy.deepcopy({k:v for k,v in saved.items() if k!='history'}),auto_context=copy.deepcopy(automatic))
        updated['revision']=document['revision']+1
        if len(json.dumps(updated,ensure_ascii=False).encode())>MAX_BYTES: raise ValueError('ANNOTATION_DOCUMENT_FULL')
        return updated
    if kind=='drawing':
        value=payload.get('drawing')
        if not isinstance(value,dict) or set(value)!={'id','title','mode','points','note','as_of','dataset_id'}: raise ValueError('DRAWING_FIELDS')
        identifier=value['id']
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-f0-9-]{36}',identifier): raise ValueError('ID')
        if value['mode'] not in ('HL','HM'): raise ValueError('MODE')
        if not isinstance(value['points'],list) or len(value['points'])!=2: raise ValueError('POINT_COUNT')
        points=[point(p) for p in value['points']]
        if Decimal(points[0]['price'])<=Decimal(points[1]['price']): raise ValueError('H_MUST_EXCEED_SECOND_POINT')
        title=text(value['title'],80).strip()
        if not title: raise ValueError('TITLE')
        text(value['note'],2000)
        day(value['as_of']); text(value['dataset_id'],64)
        record=dict(value, title=title, points=points, owner=user, updated_at=stamp)
        index=next((i for i,d in enumerate(result['drawings']) if d['id']==identifier),None)
        if index is not None:
            old=result['drawings'][index]
            if old['owner']!=user: raise Forbidden('只能修改自己保存的画法；可复制为新画法')
            history=old.get('history',[])+[{k:v for k,v in old.items() if k!='history'}]
            record.update(created_at=old['created_at'],history=history)
            result['drawings'][index]=record
        else:
            if len(result['drawings'])>=500: raise ValueError('DRAWING_DOCUMENT_FULL')
            record.update(created_at=stamp,history=[])
            result['drawings'].append(record)
    elif kind=='review':
        if set(payload)!={'revision','kind','as_of','dataset_id','status','note','axis_id','auto_mode'}: raise ValueError('REVIEW_FIELDS')
        day(payload['as_of']); text(payload['dataset_id'],64); text(payload['note'],2000)
        text(payload['axis_id'],80)
        if payload['auto_mode'] not in ('HL','HM'): raise ValueError('AUTO_MODE')
        if payload['status'] not in ('CORRECT','H_ISSUE','L_ISSUE','M_ISSUE','UNCERTAIN'): raise ValueError('REVIEW_STATUS')
        old=next((v for v in result['reviews'] if v['owner']==user and v['dataset_id']==payload['dataset_id']),None)
        value={k:payload[k] for k in ('as_of','dataset_id','status','note','axis_id','auto_mode')}
        value.update(owner=user,updated_at=stamp,history=(old.get('history',[])+[{k:v for k,v in old.items() if k!='history'}]) if old else [])
        result['reviews']=[v for v in result['reviews'] if not(v['owner']==user and v['dataset_id']==value['dataset_id'])]+[value]
    else: raise ValueError('KIND')
    result['revision']+=1
    if len(json.dumps(result,ensure_ascii=False).encode())>MAX_BYTES: raise ValueError('ANNOTATION_DOCUMENT_FULL')
    return result


class Conflict(ValueError): pass
class Forbidden(ValueError): pass
