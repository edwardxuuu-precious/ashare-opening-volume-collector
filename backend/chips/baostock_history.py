"""Explicit SH/SZ fallback using the existing bounded anonymous transport.

Official units: volume=shares, amount=CNY, turn=percent, adjustflag=3.
Source: https://www.baostock.com/helpDocsHome?file=stockKData.md
Original SDK field strings and trade states are retained; no bars are invented.
"""
import hashlib,json,time
from datetime import datetime,timezone
from decimal import Decimal as D
from backend.scripts.baostock_source import BaoStockSession

FIELDS='date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST'
def normalize(stock,rows,as_of,start_date='1990-12-19'):
    symbol=('sh.' if stock['code'].startswith('6') else 'sz.')+stock['code'];lines=[];no_trade=[];last=''
    if not rows or len(rows)>10000:raise ValueError('BAOSTOCK_FULL_HISTORY_MISSING_OR_CAPPED')
    for values in rows:
        if len(values)!=14:raise ValueError('BAOSTOCK_FIELD_COUNT')
        r=dict(zip(FIELDS.split(','),values));day=r['date']
        if r['code']!=symbol or r['adjustflag']!='3' or day<=last or day>as_of:raise ValueError('BAOSTOCK_IDENTITY_DATE_OR_ADJUSTMENT')
        datetime.strptime(day,'%Y-%m-%d');last=day
        if r['tradestatus']=='0':
            if D(r['volume'])!=0 or D(r['amount'])!=0:raise ValueError('BAOSTOCK_SUSPENSION_VOLUME_CONFLICT')
            no_trade.append(day);continue
        if r['tradestatus']!='1':raise ValueError('BAOSTOCK_TRADE_STATUS_UNKNOWN')
        # Only a declared unit conversion. Decimal hand field is re-expanded by
        # the shared parser, exactly recovering the provider's share count.
        volume=str(D(r['volume'])/100)
        change=str(D(r['close'])-D(r['preclose'])) if r['preclose'] else ''
        lines.append(','.join([day,r['open'],r['close'],r['high'],r['low'],volume,r['amount'],'',r['pctChg'],change,r['turn']]))
    value={'rc':0,'data':{'code':stock['code'],'name':stock['name'],'klines':lines},
        'adapter':'BAOSTOCK_FIELD_STRINGS_TO_DECLARED_DAILY_SCHEMA_V1',
        'original_sdk_response':{'fields':FIELDS.split(','),'rows':rows,'symbol':symbol,'adjustflag':'3','start_date':start_date,'end_date':as_of},
        'confirmed_no_trade_sessions':no_trade}
    return value,json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()

class BaoHistory:
    def __init__(self):self.session=BaoStockSession(timeout=18,interval=.8)
    def history(self,stock,as_of,start_date='1990-12-19'):
        if stock['code'].startswith(('4','8','9')):raise ValueError('BAOSTOCK_BSE_NOT_SUPPORTED')
        s=self.session;symbol=('sh.' if stock['code'].startswith('6') else 'sz.')+stock['code']
        with s._operation_lock:
            try:
                s._ensure_connected();r=s._call(s._sdk.query_history_k_data_plus,symbol,FIELDS,
                    start_date=start_date,end_date=as_of,frequency='d',adjustflag='3')
                if r.error_code!='0' or r.fields!=FIELDS.split(','):raise ValueError('BAOSTOCK_FIELDS_OR_STATUS')
                rows=[];pages=set()
                while s._call(r.next):
                    if len(rows)>=10000:raise ValueError('BAOSTOCK_HISTORY_BOUND_EXCEEDED')
                    pages.add(str(r.cur_page_num))
                    if len(pages)>20:raise ValueError('BAOSTOCK_PAGE_BOUND_EXCEEDED')
                    rows.append(list(r.get_row_data()))
                if r.error_code!='0':raise ValueError('BAOSTOCK_PAGINATION_INCOMPLETE')
            except Exception:
                s.close();raise
        value,raw=normalize(stock,rows,as_of,start_date)
        receipt={'provider':'BAOSTOCK_0.9.3','url':'https://www.baostock.com/helpDocsHome?file=stockKData.md',
            'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'ingested_at':datetime.now(timezone.utc).isoformat(),
            'raw_representation':'Lossless SDK field strings plus explicit normalized rows; not original transport bytes',
            'source_volume_unit':'SHARES','source_amount_unit':'CNY','source_turnover_unit':'PERCENT','adjustflag':'3'}
        return value,receipt,raw
    def close(self):self.session.close()
