"""Two price conditions only. No stability threshold or strategy decision."""
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING
from .calendar import sessions

def board(code):
    if code.startswith(('4','8','9')): return 'BSE',Decimal('.30')
    if code.startswith(('688','689')): return 'STAR',Decimal('.20')
    if code.startswith('30'): return 'CHINEXT',Decimal('.20')
    if code.startswith(('60','00')): return 'MAIN',Decimal('.10')
    raise ValueError('UNSUPPORTED_SECURITY_BOARD')

def closing_limit_state(stock, fields, pool, actual=None):
    """Positive evidence: vendor closing-limit pool AND exact daily-close match.

    Negative evidence can exclude a day whose close cannot equal its normal
    limit. Equality without actual vendor confirmation stays UNKNOWN (special
    no-limit events and pool omissions are not invented). Current names are
    never used as historical ST metadata. Supported dates start July 6, 2026.
    """
    code=stock['code']; market,rate=board(code); day=fields[0]
    if day<'2026-07-06': return 'UNKNOWN','RULE_VERSION_UNSUPPORTED'
    try:
        close,change=Decimal(fields[2]),Decimal(fields[9]); reference=close-change
        if not close.is_finite() or not reference.is_finite() or reference<=0 or close<=0:
            return 'UNKNOWN','REFERENCE_CLOSE_INVALID'
        listed=stock.get('listed')
        if listed and listed>day: return 'UNKNOWN','BEFORE_REPORTED_LISTING'
        if listed and listed>='2026-01-01':
            ordinal=len(sessions(listed,day))
            if ordinal<=(1 if market=='BSE' else 5): return 'FAIL','IPO_NO_LIMIT_SESSION'
        if actual and actual.get('date')==day:
            upper=Decimal(actual['upper_limit'])
            if upper.is_finite() and upper>0 and Decimal(actual['close'])==close and Decimal(actual['reference_close'])==reference:
                return ('PASS' if close==upper else 'FAIL'),'VENDOR_ACTUAL_UPPER_LIMIT_MATCHED_DAILY_BAR'
        hit=(pool or {}).get(code)
        if hit:
            # qdate is the upstream latest quote date, not the requested pool
            # session (the official AKShare adapter also keys pools by date).
            # Cross-check price, percentage and amount against that exact bar.
            price_ok=Decimal(str(hit['p']))/1000==close
            pct_ok=abs(Decimal(str(hit['zdp']))-Decimal(fields[8]))<=Decimal('.015')
            amount=Decimal(fields[6]);pool_amount=Decimal(str(hit['amount']))
            amount_ok=abs(amount-pool_amount)<=max(Decimal('1'),amount*Decimal('.000001'))
            if price_ok and pct_ok and amount_ok:
                return 'PASS','VENDOR_POOL_MATCHED_DAILY_CLOSE_PERCENT_AND_AMOUNT'
            return 'UNKNOWN','LIMIT_POOL_DAILY_FIELDS_CONFLICT'
        raw=reference*(1+rate)
        if market=='BSE':
            possible={raw.quantize(Decimal('.01'),rounding=r) for r in (ROUND_FLOOR,ROUND_CEILING)}
        else:
            possible={max(reference+Decimal('.01'),raw.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))}
        if close not in possible: return 'FAIL','CLOSE_DIFFERS_FROM_NORMAL_BOARD_LIMIT'
        return 'UNKNOWN','LIMIT_CANDIDATE_NEEDS_ACTUAL_LIMIT_CONFIRMATION'
    except (ValueError,KeyError,ArithmeticError): return 'UNKNOWN','LIMIT_EVIDENCE_INVALID'

def prefilter(stock, lines, days, pools, actual_limits=None):
    rows={line.split(',')[0]:line.split(',') for line in lines}
    today=rows.get(days[-1]); close=None; hits=[]; unknown=[]; evidence=[]
    try:
        close=Decimal(today[2]) if today else None
        if close is not None and (not close.is_finite() or close<=0): close=None
    except (ValueError,ArithmeticError,IndexError): close=None
    for day in days:
        row=rows.get(day)
        actual=(actual_limits or {}).get(day,{}).get(stock['code'])
        state,reason=closing_limit_state(stock,row,pools.get(day),actual) if row and len(row)==11 else ('UNKNOWN','DAILY_BAR_MISSING')
        evidence.append({'date':day,'status':state,'reason':reason})
        if state=='PASS': hits.append(day)
        if state=='UNKNOWN': unknown.append(day)
    status='FAIL' if close is not None and close>=10 else ('PASS' if close is not None and hits else ('FAIL' if close is not None and not unknown else 'UNKNOWN'))
    return {'close_cny':str(close) if close is not None else None,'prefilter_status':status,
            'recent_limit_up_dates':hits,'limit_evidence':evidence,
            'reasons':(['LATEST_CLOSE_MISSING'] if close is None else [])+(['LIMIT_HISTORY_INCOMPLETE'] if not hits and unknown else [])}
