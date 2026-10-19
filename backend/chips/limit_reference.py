"""Dated, lossless historical reference-price evidence, separate from chip input.

BaoStock's preclose is the exchange reference on ex-rights days. An observed
previous unadjusted close is never substituted. Evidence is checked against each
actual model-source candle before it can eliminate a historical session.
"""
import base64
import hashlib
import json
import urllib.parse
from datetime import datetime,timezone,timedelta
from decimal import Decimal as D
from .baostock_history import FIELDS


def prepared(bundle, code, days):
    if bundle.get('schema')=='stock.chips.limit.sohu.prepared.v1':
        return sohu_prepared(bundle,code,days)
    if (bundle.get('schema')!='stock.chips.limit.reference.prepared.v1'
        or bundle.get('security_id')!=code or bundle.get('official_sessions')!=days
        or bundle.get('as_of')!=days[-1] or bundle.get('published') is not False
        or code.startswith(('4','8','9'))):
        raise ValueError('REFERENCE_BUNDLE_IDENTITY_INVALID')
    receipt=bundle['receipt'];raw=base64.b64decode(bundle['raw_base64'],validate=True)
    if (len(raw)>200000 or len(raw)!=receipt['bytes']
        or hashlib.sha256(raw).hexdigest()!=receipt['sha256']
        or receipt.get('provider')!='BAOSTOCK_0.9.3' or receipt.get('adjustflag')!='3'):
        raise ValueError('REFERENCE_RAW_HASH_OR_PROVIDER_INVALID')
    value=json.loads(raw);original=value['original_sdk_response']
    symbol=('sh.' if code.startswith('6') else 'sz.')+code
    if (original['fields']!=FIELDS.split(',') or original['symbol']!=symbol
        or original['adjustflag']!='3' or original['start_date']!=days[0]
        or original['end_date']!=days[-1] or value['data']['code']!=code):
        raise ValueError('REFERENCE_SOURCE_IDENTITY_INVALID')
    rows={};last=''
    for row in original['rows']:
        if len(row)!=14:raise ValueError('REFERENCE_FIELD_COUNT_INVALID')
        r=dict(zip(original['fields'],row));day=r['date']
        if (day not in days or day<=last or r['code']!=symbol or r['adjustflag']!='3'
            or r['tradestatus'] not in ('0','1') or r['isST'] not in ('0','1')):
            raise ValueError('REFERENCE_DATE_OR_STATUS_INVALID')
        datetime.strptime(day,'%Y-%m-%d');last=day
        if r['tradestatus']=='0':
            if any(r[k].strip() and D(r[k])!=0 for k in ('volume','amount')):
                raise ValueError('REFERENCE_NO_TRADE_VOLUME_CONFLICT')
        else:
            numbers={k:D(r[k]) for k in ('open','high','low','close','preclose','volume','amount','pctChg')}
            if any(not n.is_finite() for n in numbers.values()) or any(numbers[k]<=0 for k in ('open','high','low','close','preclose','volume','amount')):
                raise ValueError('REFERENCE_NUMERIC_INVALID')
            if not numbers['low']<=min(numbers['open'],numbers['close'])<=max(numbers['open'],numbers['close'])<=numbers['high']:
                raise ValueError('REFERENCE_OHLC_INVALID')
            pct=(numbers['close']/numbers['preclose']-1)*100
            if abs(pct-numbers['pctChg'])>D('.0002'):
                raise ValueError('REFERENCE_PERCENT_CONFLICT')
        rows[day]=r
    if not rows:raise ValueError('REFERENCE_ROWS_EMPTY')
    return {'provider':receipt['provider'],'source_sha256':receipt['sha256'],
        'source_url':receipt['url'],'ingested_at':receipt['ingested_at'],
        'historical_PIT_authenticated':False,'rows':rows}


def sohu_prepared(bundle,code,days):
    receipt=bundle['receipt'];raw=base64.b64decode(bundle['raw_base64'],validate=True)
    if (bundle.get('security_id')!=code or len(code)!=6 or not code.isdigit()
        or bundle.get('as_of')!=days[-1] or bundle.get('official_sessions')!=days
        or bundle.get('published') is not False or receipt.get('provider')!='SOHU_REPORTED_DAILY_CHANGE'
        or len(raw)>500000 or len(raw)!=receipt['bytes'] or hashlib.sha256(raw).hexdigest()!=receipt['sha256']):
        raise ValueError('SOHU_REFERENCE_IDENTITY_OR_HASH_INVALID')
    value=json.loads(raw.decode('gb18030'))
    if not isinstance(value,list) or len(value)!=1 or value[0].get('status')!=0:
        raise ValueError('SOHU_REFERENCE_STATUS_INVALID')
    # Source response has no echoed security id. Bind its request URL exactly.
    query=urllib.parse.parse_qs(urllib.parse.urlsplit(receipt['url']).query)
    if (value[0].get('code')!='cn_'+code or urllib.parse.urlsplit(receipt['url']).hostname!='q.stock.sohu.com'
        or query.get('code')!=['cn_'+code] or query.get('end')!=[days[-1].replace('-','')]):
        raise ValueError('SOHU_REFERENCE_REQUEST_IDENTITY_INVALID')
    rows={}
    for r in value[0]['hq']:
        # SH/SZ responses can append an eleventh column. It stays in the raw
        # response and is not interpreted as one of the ten displayed fields.
        if len(r) not in (10,11) or r[0] not in days or r[0] in rows:raise ValueError('SOHU_REFERENCE_DATE_INVALID')
        o,c,delta,lo,hi=[D(r[i]) for i in (1,2,3,5,6)];ref=c-delta;pct=D(r[4].rstrip('%'))
        if (any(not x.is_finite() or x<=0 for x in (o,c,lo,hi,ref))
            or not lo<=min(o,c)<=max(o,c)<=hi or abs((c/ref-1)*100-pct)>D('.011')):
            raise ValueError('SOHU_REFERENCE_OHLC_OR_CHANGE_INVALID')
        volume,amount=D(r[7]),D(r[8])
        if not volume.is_finite() or not amount.is_finite() or min(volume,amount)<=0:
            raise ValueError('SOHU_REFERENCE_TRADE_VOLUME_INVALID')
        symbol=('bj.' if code.startswith(('4','8','9')) else 'sh.' if code.startswith('6') else 'sz.')+code
        rows[r[0]]={'date':r[0],'code':symbol,'open':str(o),'close':str(c),'low':str(lo),'high':str(hi),
            'preclose':str(ref),'pctChg':str(pct),'tradestatus':'1',
            'volume':str(volume*100),'amount':str(amount*10000),
            'reported_change':r[3],'volume_tolerance_shares':'100',
            'amount_tolerance_cny':str(D(10)**(amount.as_tuple().exponent+4)),
            'reference_basis':'CLOSE_MINUS_VENDOR_REPORTED_CHANGE;NOT_PREVIOUS_OBSERVED_CLOSE'}
    if not rows:raise ValueError('SOHU_REFERENCE_ROWS_EMPTY')
    return {'provider':receipt['provider'],'source_sha256':receipt['sha256'],'source_url':receipt['url'],
        'ingested_at':receipt['ingested_at'],'historical_PIT_authenticated':False,'rows':rows}


def closing_pool(bundle,day):
    receipt=bundle['receipt'];raw=base64.b64decode(bundle['raw_base64'],validate=True)
    if (bundle.get('date')!=day or receipt.get('provider')!='THS_INCLUSIVE_CLOSING_LIMIT_POOL'
        or len(raw)>2000000 or hashlib.sha256(raw).hexdigest()!=receipt['sha256']):
        raise ValueError('THS_CLOSING_POOL_IDENTITY_OR_HASH_INVALID')
    query=urllib.parse.parse_qs(urllib.parse.urlsplit(receipt['url']).query)
    if (urllib.parse.urlsplit(receipt['url']).hostname!='data.10jqka.com.cn'
        or urllib.parse.urlsplit(receipt['url']).path!='/dataapi/limit_up/limit_up_pool'
        or query.get('date')!=[day.replace('-','')] or query.get('filter')!=['HS,GEM2STAR,ST']):
        raise ValueError('THS_CLOSING_POOL_REQUEST_IDENTITY_INVALID')
    value=json.loads(raw);data=value['data'];page=data['page'];rows=data['info']
    if value.get('status_code')!=0 or data['date']!=day.replace('-','') or page['total']!=len(rows) or len(rows)>1000:
        raise ValueError('THS_CLOSING_POOL_DATE_OR_TOTAL_INVALID')
    result={};cn=timezone(timedelta(hours=8))
    for row in rows:
        code=row['code'];tag=row['change_tag']
        if len(code)!=6 or not code.isdigit() or code in result or tag not in ('FIRST_LIMIT','LIMIT_BACK'):
            raise ValueError('THS_CLOSING_POOL_SECURITY_OR_TAG_INVALID')
        for k in ('first_limit_up_time','last_limit_up_time'):
            stamp=datetime.fromtimestamp(int(row[k]),cn)
            if stamp.date().isoformat()!=day or not '09:15'<=stamp.strftime('%H:%M')<='15:00':
                raise ValueError('THS_CLOSING_POOL_SESSION_TIMESTAMP_INVALID')
        # time_preview is a coarse intraday series; its final sample can precede
        # the closing auction. It is not substituted for the source latest close.
        result[code]={'close':str(row['latest']),'pct':str(row['change_rate']),
            'turnover_percent':str(row['turnover_rate']),'tag':tag,'date':day,
            'source_sha256':receipt['sha256'],'provider':receipt['provider']}
    return result


def fetch_closing_pool(source,day):
    query=urllib.parse.urlencode({'page':1,'limit':200,
        'field':'199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004',
        'filter':'HS,GEM2STAR,ST','order_field':'330324','order_type':'0','date':day.replace('-','')})
    url='https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?'+query
    raw=source.read_bytes(url,referer='https://data.10jqka.com.cn/')
    result={'date':day,'receipt':{'provider':'THS_INCLUSIVE_CLOSING_LIMIT_POOL','url':url,
        'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'ingested_at':datetime.now(timezone.utc).isoformat()},
        'raw_base64':base64.b64encode(raw).decode()}
    closing_pool(result,day)
    return result


def fetch_reference(source,stock,days,provider=None):
    """Bounded fallback for newly-low-price stocks without dated reference input."""
    code=stock['code'];as_of=days[-1]
    if code.startswith(('4','8','9')) or provider=='SOHU_REPORTED_DAILY_CHANGE':
        query=urllib.parse.urlencode({'code':'cn_'+code,'start':days[0].replace('-',''),
            'end':as_of.replace('-',''),'stat':1,'order':'D','period':'d','rt':'json'})
        url='https://q.stock.sohu.com/hisHq?'+query
        raw=source.read_bytes(url,referer=f'https://q.stock.sohu.com/cn/{code}/lshq.shtml')
        receipt={'provider':'SOHU_REPORTED_DAILY_CHANGE','url':url,'bytes':len(raw),
            'sha256':hashlib.sha256(raw).hexdigest(),'ingested_at':datetime.now(timezone.utc).isoformat()}
        schema='stock.chips.limit.sohu.prepared.v1'
    else:
        if source.bao is None:
            from .baostock_history import BaoHistory
            source.bao=BaoHistory()
        _,receipt,raw=source.bao.history(stock,as_of,start_date=days[0])
        schema='stock.chips.limit.reference.prepared.v1'
    bundle={'schema':schema,'security_id':code,'as_of':as_of,'official_sessions':days,
        'receipt':receipt,'raw_base64':base64.b64encode(raw).decode(),'published':False}
    prepared(bundle,code,days)
    return bundle


def corroborate_quantity(primary,secondary,lines):
    """Resolve quantity representation conflicts with a second price reference.

    No tolerance is added to the original provider. The separately reported
    Sohu hand/10k-CNY units have explicit quantization. Price OHLC and reference
    must agree exactly between both reference sources and the model candle.
    Original model prices/volume/amount and primary evidence remain preserved.
    """
    model={s.split(',')[0]:s.split(',') for s in lines};replacements={};differences=[]
    if primary.get('provider')!='BAOSTOCK_0.9.3' or secondary.get('provider')!='SOHU_REPORTED_DAILY_CHANGE':
        return primary
    for day,row in primary['rows'].items():
        fields=model.get(day);other=secondary['rows'].get(day)
        if matched(row,fields)!='REFERENCE_DAILY_VOLUME_CONFLICT' or not other:continue
        if any(D(row[k])!=D(other[k]) for k in ('open','high','low','close','preclose')):continue
        if matched(other,fields) is not None:continue
        replacements[day]=other
        differences.append({'date':day,'primary_volume_shares':row['volume'],
            'model_volume_shares':str(D(fields[5])*100),'corroborating_reported_hand_volume_shares':other['volume'],
            'resolution':'EXACT_TWO_REFERENCE_SOURCE_PRICE_AGREEMENT;DECLARED_HAND_UNIT_QUANTIZATION'})
    if not replacements:return primary
    return {**primary,'rows':{**primary['rows'],**replacements},'primary_rows':primary['rows'],
        'corroborating_source':{k:v for k,v in secondary.items() if k!='rows'},
        'quantity_representation_differences':differences}


def matched(reference, fields):
    """Return a conflict, explicit no-trade status, or a matched reference."""
    if reference['tradestatus']=='0':
        # A source trade candle with positive volume cannot be silently removed.
        if fields and (D(fields[5])!=0 or D(fields[6])!=0):
            return 'REFERENCE_TRADE_STATUS_CONFLICT'
        return None
    if not fields or len(fields)!=11:return 'REFERENCE_DAILY_BAR_MISSING'
    for key,index in (('open',1),('close',2),('high',3),('low',4)):
        if D(reference[key])!=D(fields[index]):return 'REFERENCE_DAILY_OHLC_CONFLICT'
    if abs(D(reference['volume'])-D(fields[5])*100)>D(reference.get('volume_tolerance_shares','0')):return 'REFERENCE_DAILY_VOLUME_CONFLICT'
    amount=D(fields[6])
    if abs(D(reference['amount'])-amount)>max(D(reference.get('amount_tolerance_cny','1')),abs(amount)*D('.000001')):
        return 'REFERENCE_DAILY_AMOUNT_CONFLICT'
    return None
