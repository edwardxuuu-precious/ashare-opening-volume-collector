"""Dated positive-trade quote overlay for lagging historical placeholders.

Primary mapping: github.com/shidenggui/easyquotation/blob/master/easyquotation/tencent.py
Fields6/36 and field35 volume use shares for 688/689, hands for other A shares;
field35 precise amount CNY, field37
rounded ten-thousand CNY, and field38 supplied turnover percent. No turnover is
inferred from share capital. Original quote and history bytes remain traceable.
"""
import hashlib
import json
import re
from datetime import datetime,timezone
from decimal import Decimal
from .model import Bar


def daily_line(code,as_of,body):
    f=body.split('~')
    if len(f)<49 or f[2]!=code or len(f[30])!=14 or f[30][:8]!=as_of.replace('-','') or f[30][8:12]<'1500':
        raise ValueError('FINAL_QUOTE_CODE_DATE_OR_CLOSE_TIME_INVALID')
    try:datetime.strptime(f[30],'%Y%m%d%H%M%S')
    except ValueError as exc:raise ValueError('FINAL_QUOTE_DATE_TIME_INVALID') from exc
    triple=f[35].split('/')
    if len(triple)!=3 or Decimal(triple[0])!=Decimal(f[3]) or Decimal(triple[1])!=Decimal(f[6]) or Decimal(f[36])!=Decimal(f[6]):
        raise ValueError('FINAL_QUOTE_TRADE_TRIPLE_CONFLICT')
    op,close,high,low,volume,amount,turn=(Decimal(x) for x in (f[5],f[3],f[33],f[34],f[6],triple[2],f[38]))
    if volume==0 and amount==0:return None
    if volume<=0 or amount<=0:raise ValueError('FINAL_QUOTE_VOLUME_AMOUNT_CONFLICT')
    volume_shares=volume if code.startswith(('688','689')) else volume*100
    Bar(as_of,op,high,low,close,volume_shares,amount,turn).mode()
    if abs(amount-Decimal(f[37])*10000)>Decimal('10000'):
        raise ValueError('FINAL_QUOTE_PRECISE_AND_ROUNDED_AMOUNT_CONFLICT')
    if Decimal(f[4])<=0:raise ValueError('FINAL_QUOTE_REFERENCE_CLOSE_MISSING')
    return ','.join([as_of,f[5],f[3],f[33],f[34],format(volume_shares/100,'f'),triple[2],f[43],f[32],f[31],f[38]])


def collect(source,stocks,as_of):
    quotes={};receipts=[]
    for offset in range(0,len(stocks),60):
        part=stocks[offset:offset+60]
        symbols=[('bj' if s['code'].startswith(('4','8','9')) else 'sh' if s['code'].startswith('6') else 'sz')+s['code'] for s in part]
        url='https://qt.gtimg.cn/q='+','.join(symbols)
        try:
            raw=source.read_bytes(url)
            for symbol,body in re.findall(r'v_([a-z]{2}\d{6})="([^"]*)"',raw.decode('gb18030')):
                f=body.split('~')
                if symbol not in symbols or len(f)<49 or f[2]!=symbol[2:] or len(f[30])!=14 or f[30][:8]!=as_of.replace('-','') or f[30][8:12]<'1500':continue
                q={'date':as_of,'close':f[3],'reference_close':f[4],'upper_limit':f[47],
                    'provider':'TENCENT_ACTUAL_DAILY_QUOTE','quote_time':f[30],
                    'raw_quote_body':body,'raw_quote_sha256':hashlib.sha256(body.encode('gb18030')).hexdigest()}
                try:q['daily_line']=daily_line(symbol[2:],as_of,body)
                except (ValueError,ArithmeticError) as exc:q['daily_line_error']=str(exc)[:160]
                quotes[symbol[2:]]=q
            receipts.append({'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                'provider':'TENCENT_FINAL_QUOTE_WITH_ORIGINAL_BODY','ingested_at':datetime.now(timezone.utc).isoformat()})
        except Exception as exc:receipts.append({'offset':offset,'provider':'TENCENT_FINAL_QUOTE','error':type(exc).__name__})
    return quotes,receipts


def overlay(raw,receipt,stock,as_of,quote,*,require_final_quote=False):
    def without_unconfirmed_placeholder():
        value=json.loads(raw)
        if receipt.get('provider')!='THS_UNADJUSTED':return raw,receipt
        current_rows=[s for s in value['data']['klines'] if s.split(',')[0]==as_of]
        strip_current=require_final_quote and bool(current_rows)
        if as_of not in value.get('confirmed_no_trade_sessions',[]) and not strip_current:return raw,receipt
        value['confirmed_no_trade_sessions']=[d for d in value.get('confirmed_no_trade_sessions',[]) if d!=as_of]
        value['unconfirmed_no_trade_placeholder_dates']=sorted(set(value.get('unconfirmed_no_trade_placeholder_dates',[])+[as_of]))
        if strip_current:
            value['unconfirmed_current_session_history_lines']=current_rows
            value['data']['klines']=[s for s in value['data']['klines'] if s.split(',')[0]!=as_of]
        merged=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
        return merged,{**receipt,'sha256':hashlib.sha256(merged).hexdigest(),'bytes':len(merged),
            'original_history_bundle_sha256':hashlib.sha256(raw).hexdigest(),'unconfirmed_no_trade_placeholder_date':as_of}
    if not quote or not quote.get('raw_quote_body'):return without_unconfirmed_placeholder()
    body=quote.get('raw_quote_body','')
    if hashlib.sha256(body.encode('gb18030')).hexdigest()!=quote.get('raw_quote_sha256'):
        raise ValueError('FINAL_QUOTE_BODY_HASH_CONFLICT')
    line=daily_line(stock['code'],as_of,body)
    # A positive trade with invalid turnover/OHLC must fail the calculation,
    # rather than leaving a contradictory history no-trade claim in place.
    # A real zero quote is only ignored; it does not establish suspension.
    if line is None:return without_unconfirmed_placeholder()
    if line!=quote.get('daily_line'):raise ValueError('FINAL_QUOTE_LINE_CONFLICT')
    value=json.loads(raw);lines=value['data']['klines']
    if value['data']['code']!=stock['code']:raise ValueError('FINAL_QUOTE_HISTORY_IDENTITY_CONFLICT')
    if any(s.split(',')[0]>as_of for s in lines):raise ValueError('FINAL_QUOTE_HISTORY_FUTURE_CONFLICT')
    current=[s for s in lines if s.split(',')[0]==as_of]
    if len(current)>1:raise ValueError('FINAL_QUOTE_HISTORY_DUPLICATE_SESSION')
    if current==[line]:return raw,receipt
    previous_hash=hashlib.sha256(raw).hexdigest()
    # Even a nonzero history row can be an intraday cached candle. The dated
    # final quote takes precedence for today's full OHLCV/amount/turnover row.
    value['data']['klines']=[s for s in lines if s.split(',')[0]!=as_of]+[line]
    zero_claim=as_of in value.get('confirmed_no_trade_sessions',[])
    value['confirmed_no_trade_sessions']=[d for d in value.get('confirmed_no_trade_sessions',[]) if d!=as_of]
    value['current_session_quote_overlay']={'provider':'TENCENT_ACTUAL_DAILY_QUOTE','date':as_of,
        'original_history_bundle_sha256':previous_hash,'raw_quote_body':body,'raw_quote_sha256':quote['raw_quote_sha256'],
        'observed_positive_volume_trade':True,'historical_zero_volume_placeholder_overridden':zero_claim,
        'replaced_current_history_line':current[0] if current else None,
        'unit_contract':'688/689 volume shares; other A-share volume hands *100; precise field35 amount CNY; supplied field38 turnover percent'}
    merged=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
    return merged,{**receipt,'sha256':hashlib.sha256(merged).hexdigest(),'bytes':len(merged),
        'current_session_overlay_provider':'TENCENT_ACTUAL_DAILY_QUOTE','current_session_overlay_date':as_of,
        'original_history_bundle_sha256':previous_hash,'current_quote_sha256':quote['raw_quote_sha256'],
        'historical_zero_volume_placeholder_overridden':zero_claim}
