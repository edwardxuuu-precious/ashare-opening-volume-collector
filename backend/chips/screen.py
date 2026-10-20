"""Dated price/limit evidence and explicit chip-price position, without stability."""
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING
from .calendar import sessions
from .listing import trusted_listing
from .limit_reference import matched

PRICE_POSITION_RULE = 'CLOSE_GT_ANY_PEAK_OR_MEAN_COST_V1'

def apply_price_position(row, snapshot):
    """Project the new condition from the sealed current snapshot, without replay.

    Equality fails. Any tied maximum-mass peak may satisfy the peak branch.
    Missing/invalid operands stay unknown; an established false conjunct still
    excludes a stock. Original two-condition evidence is retained separately.
    """
    def positive(value):
        try:
            number = Decimal(value)
            return number if number.is_finite() and number > 0 else None
        except (ValueError, TypeError, ArithmeticError):
            return None
    close = positive(row.get('close_cny'))
    valid = bool(snapshot and row.get('chip_status') == 'READY'
        and positive(snapshot.get('close_cny')) == close and close is not None)
    mean = positive(snapshot.get('model_mean_cost_cny')) if valid else None
    raw_peaks = snapshot.get('peak_prices_cny', []) if valid else []
    peaks = [positive(price) for price in raw_peaks]
    known_peaks = [price for price in peaks if price is not None]
    peak_state = ('PASS' if close is not None and any(close > price for price in known_peaks)
        else 'FAIL' if peaks and len(known_peaks) == len(peaks) else 'UNKNOWN')
    mean_state = ('PASS' if close > mean else 'FAIL') if mean is not None else 'UNKNOWN'
    state = ('PASS' if 'PASS' in (peak_state, mean_state)
        else 'FAIL' if peak_state == mean_state == 'FAIL' else 'UNKNOWN')
    base = row['prefilter_status']
    row.update(model_mean_cost_cny=str(mean) if mean is not None else None,
        above_peak_status=peak_state, above_mean_cost_status=mean_state,
        price_position_status=state,
        screen_status='FAIL' if 'FAIL' in (base, state) else 'PASS' if base == state == 'PASS' else 'UNKNOWN')
    return row

def board(code):
    if code.startswith(('4','8','9')): return 'BSE',Decimal('.30')
    if code.startswith(('688','689')): return 'STAR',Decimal('.20')
    if code.startswith('30'): return 'CHINEXT',Decimal('.20')
    if code.startswith(('60','00')): return 'MAIN',Decimal('.10')
    raise ValueError('UNSUPPORTED_SECURITY_BOARD')

def closing_limit_state(stock, fields, pool, actual=None, closing=None):
    """Positive evidence: vendor closing-limit pool AND exact daily-close match.

    Negative evidence can exclude a day whose close cannot equal its normal
    limit. Equality without actual vendor confirmation stays UNKNOWN (special
    no-limit events and pool omissions are not invented). Current names are
    never used as historical ST metadata. Supported dates start July 6, 2026.
    """
    code=stock['code']; market,rate=board(code); day=fields[0]
    if day<'2026-07-06': return 'UNKNOWN','RULE_VERSION_UNSUPPORTED'
    try:
        close=Decimal(fields[2]);reference_verified=stock.get('reference_close_verified',True)
        reference=close-Decimal(fields[9]) if reference_verified else None
        if not close.is_finite() or close<=0 or (reference_verified and (not reference.is_finite() or reference<=0)):
            return 'UNKNOWN','REFERENCE_CLOSE_INVALID'
        listed=trusted_listing(stock)
        if listed and listed>day: return 'UNKNOWN','BEFORE_REPORTED_LISTING'
        if listed and listed>='2026-01-01':
            ordinal=len(sessions(listed,day))
            if ordinal<=(1 if market=='BSE' else 5): return 'FAIL','IPO_NO_LIMIT_SESSION'
        if actual and actual.get('date')==day:
            upper=Decimal(actual['upper_limit'])
            if (reference_verified and Decimal(actual['close'])==close
                and Decimal(actual['reference_close'])>0 and Decimal(actual['reference_close'])!=reference):
                return 'UNKNOWN','VENDOR_ACTUAL_REFERENCE_CLOSE_CONFLICT'
            if (upper.is_finite() and upper>0 and Decimal(actual['close'])==close
                and Decimal(actual['reference_close'])>0 and (not reference_verified or Decimal(actual['reference_close'])==reference)):
                return ('PASS' if close==upper else 'FAIL'),'VENDOR_ACTUAL_UPPER_LIMIT_MATCHED_DAILY_BAR'
        if closing and closing.get('date')==day:
            if (Decimal(closing['close'])==close and reference_verified
                and abs(Decimal(closing['pct'])-Decimal(fields[8]))<=Decimal('.015')
                and abs(Decimal(closing['turnover_percent'])-Decimal(fields[10]))<=Decimal('.02')):
                return 'PASS','INCLUSIVE_CLOSING_LIMIT_POOL_MATCHED_CLOSE_REFERENCE_PERCENT_AND_TURNOVER'
            return 'UNKNOWN','INCLUSIVE_LIMIT_POOL_DAILY_FIELDS_CONFLICT'
        hit=(pool or {}).get(code)
        if hit:
            # qdate is the upstream latest quote date, not the requested pool
            # session (the official AKShare adapter also keys pools by date).
            # Cross-check price, percentage and amount against that exact bar.
            price_ok=Decimal(str(hit['p']))/1000==close
            pct_ok=not reference_verified or abs(Decimal(str(hit['zdp']))-Decimal(fields[8]))<=Decimal('.015')
            amount=Decimal(fields[6]);pool_amount=Decimal(str(hit['amount']))
            amount_ok=abs(amount-pool_amount)<=max(Decimal('1'),amount*Decimal('.000001'))
            if price_ok and pct_ok and amount_ok:
                return 'PASS','VENDOR_POOL_MATCHED_DAILY_CLOSE_PERCENT_AND_AMOUNT' if reference_verified else 'VENDOR_POOL_MATCHED_DAILY_CLOSE_AND_AMOUNT;REFERENCE_CLOSE_NOT_INFERRED'
            return 'UNKNOWN','LIMIT_POOL_DAILY_FIELDS_CONFLICT'
        if not reference_verified:
            return 'UNKNOWN','HISTORICAL_REFERENCE_CLOSE_NOT_VERIFIED'
        raw=reference*(1+rate)
        if market=='BSE':
            possible={raw.quantize(Decimal('.01'),rounding=r) for r in (ROUND_FLOOR,ROUND_CEILING)}
        else:
            possible={max(reference+Decimal('.01'),raw.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))}
        if close not in possible: return 'FAIL','CLOSE_DIFFERS_FROM_NORMAL_BOARD_LIMIT'
        return 'UNKNOWN','LIMIT_CANDIDATE_NEEDS_ACTUAL_LIMIT_CONFIRMATION'
    except (ValueError,KeyError,ArithmeticError): return 'UNKNOWN','LIMIT_EVIDENCE_INVALID'

def prefilter(stock, lines, days, pools, actual_limits=None, reference_evidence=None, closing_pools=None):
    if not trusted_listing(stock,days[-1]):stock={**stock,'listing_evidence':None}
    rows={line.split(',')[0]:line.split(',') for line in lines}
    today=rows.get(days[-1]); close=None; hits=[]; unknown=[]; evidence=[]
    no_trade=set(stock.get('confirmed_no_trade_sessions',[]));carried_date=None
    if not today and days[-1] in no_trade:
        carried_date=max((d for d in rows if d<days[-1]),default=None)
        if carried_date:
            try:confirmed=(set(sessions(carried_date,days[-1]))-set(rows))<=no_trade
            except ValueError:confirmed=False
            if confirmed:today=rows[carried_date]
            else:carried_date=None
    try:
        close=Decimal(today[2]) if today else None
        if close is not None and (not close.is_finite() or close<=0): close=None
    except (ValueError,ArithmeticError,IndexError): close=None
    for day in days:
        row=rows.get(day)
        actual=(actual_limits or {}).get(day,{}).get(stock['code'])
        closing=(closing_pools or {}).get(day,{}).get(stock['code'])
        reference=(reference_evidence or {}).get('rows',{}).get(day)
        listed=trusted_listing(stock,days[-1])
        if listed and day<listed:
            state,reason='FAIL','NOT_YET_LISTED'
        else:
            conflict=matched(reference,row) if reference else None
            if conflict:
                state,reason='UNKNOWN',conflict
                # A quantity discrepancy cannot erase independent, dated price
                # evidence. This path accepts only an actual upper-limit quote,
                # never a formula/pool fallback or a conflicting reference.
                if (conflict=='REFERENCE_DAILY_VOLUME_CONFLICT' and reference['tradestatus']=='1'
                    and day not in no_trade and actual and actual.get('date')==day):
                    try:
                        if Decimal(actual['reference_close'])==Decimal(reference['preclose']):
                            verified=list(row);verified[8]=reference['pctChg']
                            verified[9]=str(Decimal(row[2])-Decimal(reference['preclose']))
                            witnessed,why=closing_limit_state({**stock,'reference_close_verified':True},verified,None,actual)
                            if why=='VENDOR_ACTUAL_UPPER_LIMIT_MATCHED_DAILY_BAR':
                                state,reason=witnessed,'ACTUAL_PRICE_WITNESS_WITH_SEPARATE_VOLUME_DIFFERENCE;'+why
                    except (KeyError,ValueError,ArithmeticError):pass
            elif reference and reference['tradestatus']=='0':state,reason='FAIL','REFERENCE_SOURCE_CONFIRMED_NO_TRADE'
            elif reference and day in no_trade:state,reason='UNKNOWN','REFERENCE_TRADE_STATUS_CONFLICT'
            elif reference:
                verified=list(row);verified[8]=reference['pctChg']
                verified[9]=str(Decimal(row[2])-Decimal(reference['preclose']))
                state,reason=closing_limit_state({**stock,'reference_close_verified':True},verified,pools.get(day),actual,closing)
                reason='HISTORICAL_REFERENCE_MATCHED_DAILY_OHLC;'+reason
            else:
                state,reason=('FAIL','SOURCE_CONFIRMED_NO_TRADE') if day in no_trade else (closing_limit_state(stock,row,pools.get(day),actual,closing) if row and len(row)==11 else ('UNKNOWN','DAILY_BAR_MISSING'))
        evidence.append({'date':day,'status':state,'reason':reason})
        if state=='PASS': hits.append(day)
        if state=='UNKNOWN': unknown.append(day)
    status='FAIL' if close is not None and close>=10 else ('PASS' if close is not None and hits else ('FAIL' if close is not None and not unknown else 'UNKNOWN'))
    return {'close_cny':str(close) if close is not None else None,'prefilter_status':status,
            'recent_limit_up_dates':hits,'limit_evidence':evidence,
            'last_trade_close_date':carried_date,
            'reasons':(['LATEST_CLOSE_MISSING'] if close is None else [])+(['LIMIT_HISTORY_INCOMPLETE'] if not hits and unknown else [])+
                (['LAST_TRADE_CLOSE_DURING_CONFIRMED_NO_TRADE_SESSION:'+carried_date] if carried_date else [])}
