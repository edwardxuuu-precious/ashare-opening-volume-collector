"""Bounded unadjusted OHLC reads for the fixed review samples, not chips.

Exact source strings and raw responses are retained. Missing/stale rows are
errors unless the provider explicitly confirms the target was a no-trade day.
"""
from datetime import datetime,timedelta
import re
from decimal import Decimal
from .anchors import validate
from .daily import index_history,sha
from .watchlist import WATCHLIST
from backend.chips.calendar import sessions


def parse(value,code,as_of):
    expected='000001' if code=='sh000001' else code
    data=value.get('data',{})
    if value.get('rc')!=0 or data.get('code')!=expected: raise ValueError('SOURCE_IDENTITY')
    if code=='sh000001' and data.get('name')!='上证指数': raise ValueError('INDEX_NAME_OR_MARKET')
    lines=data.get('klines')
    if not isinstance(lines,list) or not 0<len(lines)<=400: raise ValueError('SOURCE_ROW_COUNT')
    rows=[]
    for line in lines:
        f=line.split(',')
        if len(f)!=11 or f[0]>as_of: raise ValueError('SOURCE_FIELDS_OR_FUTURE_DATE')
        datetime.strptime(f[0],'%Y-%m-%d')
        volume,amount=Decimal(f[5])*100,Decimal(f[6])
        if not volume.is_finite() or not amount.is_finite() or min(volume,amount)<0: raise ValueError('SOURCE_VOLUME')
        rows.append(dict(date=f[0],open=f[1],high=f[3],low=f[4],close=f[2],
            volume_shares=str(volume),amount_cny=f[6],turnover_percent=f[10]))
    validate(rows)
    no_trade=value.get('confirmed_no_trade_sessions',[])
    if len(rows)<60 or (rows[-1]['date']!=as_of and as_of not in no_trade): raise ValueError('TARGET_SESSION_NOT_READY')
    return rows,no_trade


class DailySources:
    def __init__(self):
        from backend.chips.source import Source
        self.source=Source(interval=.8);self.bao=None;self.bao_failures=0;self.quotes=None;self.quote_receipts=[];self.quote_date=None
    def close(self):
        self.source.close()
        if self.bao:self.bao.close()
    def from_final_quote(self,code,name,as_of,previous):
        from backend.chips.current_quote import collect,daily_line
        from .daily import encoded
        if not previous or previous.get('symbol')!=code:raise ValueError('PRIOR_CANDLE_IDENTITY')
        prior=[r for r in previous.get('history',[]) if r['date']<as_of]
        validate(prior)
        if len(prior)<60:raise ValueError('PRIOR_CANDLES_INSUFFICIENT')
        first=(datetime.fromisoformat(prior[-1]['date'])+timedelta(days=1)).date().isoformat()
        if sessions(first,as_of)!=[as_of]:raise ValueError('PRIOR_CANDLES_HAVE_SESSION_GAP')
        if code=='sh000001':
            url='https://qt.gtimg.cn/q=sh000001';raw=self.source.read_bytes(url)
            match=re.search(r'v_sh000001="([^"]*)"',raw.decode('gb18030'))
            if not match or match.group(1).split('~')[1]!='上证指数':raise ValueError('FINAL_INDEX_IDENTITY')
            body=match.group(1);line=daily_line('000001',as_of,body)
            raw_receipt=dict(url=url,sha256=sha(raw),bytes=len(raw))
        else:
            if self.quotes is None or self.quote_date!=as_of:
                self.quotes,self.quote_receipts=collect(self.source,[dict(code=c,name=n) for c,n in WATCHLIST if c!='sh000001'],as_of);self.quote_date=as_of
            quote=self.quotes.get(code,{})
            body=quote.get('raw_quote_body','');line=daily_line(code,as_of,body)
            raw_receipt=dict(receipts=self.quote_receipts,quote_body_sha256=sha(body.encode('gb18030')))
        if not line:raise ValueError('NO_POSITIVE_FINAL_TRADE')
        f=line.split(',')
        # daily_line already validates target date, >=15:00 provider timestamp,
        # identity, precise amount and 688/689 share-vs-hand volume units.
        today=dict(date=f[0],open=f[1],high=f[3],low=f[4],close=f[2],volume_shares=str(Decimal(f[5])*100),amount_cny=f[6],turnover_percent=f[10])
        rows=prior+[today];validate(rows)
        bundle=encoded(dict(prior_history=prior,final_quote_body=body,final_quote_receipt=raw_receipt,normalized_target_line=line))
        receipt=dict(provider='PUBLISHED_HISTORY_PLUS_TENCENT_CLOSE',adjustment='UNADJUSTED',symbol=code,
            price_unit='INDEX_POINTS' if code=='sh000001' else 'CNY',source_sha256=sha(bundle),
            prior_dataset_id=previous.get('dataset_id'),prior_history_sha256=sha(encoded(prior)),
            prior_provider=(previous.get('source') or {}).get('provider'),quote_time=body.split('~')[30],
            raw_representation='Lossless committed candle strings plus dated final quote body; not original HTTP bytes',
            target_volume_contract='688/689 source shares; other equity/index source hands *100',historical_known_at=None)
        return rows,receipt,bundle,[]

    def history(self,code,name,as_of,previous=None):
        from backend.chips.baostock_history import BaoHistory
        from backend.chips.ths_history import history as ths
        from .daily import encoded
        start=(datetime.fromisoformat(as_of)-timedelta(days=450)).date().isoformat()
        stock=dict(code='000001' if code=='sh000001' else code,name=name,
            secid='1.000001' if code=='sh000001' else ('1.' if code.startswith('6') else '0.')+code)
        failures=[]
        try:return self.from_final_quote(code,name,as_of,previous)
        except Exception as exc:failures.append(dict(provider='DATED_FINAL_QUOTE_WITH_PRIOR_HISTORY',error=type(exc).__name__,detail=str(exc)[:120]))
        for provider in ('EASTMONEY','BAOSTOCK','THS'):
            try:
                if provider=='EASTMONEY':
                    value,receipt,raw=self.source.em_history(stock,as_of,start)
                elif provider=='BAOSTOCK':
                    if self.bao_failures>=2: continue
                    if code=='sh000001':
                        if self.bao:self.bao.close();self.bao=None
                        rows,receipt=index_history(as_of)
                        if len(rows)<60 or rows[-1]['date']!=as_of: raise ValueError('TARGET_SESSION_NOT_READY')
                        raw=encoded(receipt['original_sdk_response'])
                        return rows,dict(receipt,source_failures=failures),raw,[]
                    if self.bao is None:self.bao=BaoHistory()
                    value,receipt,raw=self.bao.history(stock,as_of,start_date=start)
                else:
                    if code=='sh000001':continue
                    value,receipt,raw=ths(self.source,stock,as_of,start_date=start)
                    # THS derives this list from zero quantity placeholders,
                    # not an explicit trade-status field. It cannot establish
                    # that today's stock was suspended when final quote failed.
                    value=dict(value,confirmed_no_trade_sessions=[d for d in value.get('confirmed_no_trade_sessions',[]) if d!=as_of])
                rows,no_trade=parse(value,code,as_of)
                return rows,dict(receipt,adjustment='UNADJUSTED',symbol=code,
                    price_unit='INDEX_POINTS' if code=='sh000001' else 'CNY',
                    normalized_volume_unit='HAND',volume_conversion='HAND*100',source_sha256=sha(raw),
                    source_failures=failures,historical_known_at=None),raw,no_trade
            except Exception as exc:
                if provider=='BAOSTOCK': self.bao_failures+=1
                failures.append(dict(provider=provider,error=type(exc).__name__,detail=str(exc)[:120]))
        raise ValueError('SOURCE_NOT_READY:'+str(failures))
