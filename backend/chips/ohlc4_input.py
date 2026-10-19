"""Input validation for OHLC4 triangles; retain unused VWAP observations.

Volume and amount may include off-book trades outside the continuous-session
OHLC range. Their VWAP is not the new triangle mode. No original value changes.
The archived VWAP-mode model and its validation remain unchanged.
"""
import hashlib
import json
from decimal import localcontext
from .model import Bar, PRECISION, iso_day, number


def validate_bar(bar):
    try:
        bar.mode()  # Reuse all finite, tick, OHLC, quantity and zero checks.
    except ValueError as exc:
        if str(exc)!='AMOUNT_VOLUME_VWAP_OUTSIDE_OHLC':raise
        return {'source_date':bar.source_date,'raw_vwap':str(bar.amount_cny/bar.volume_shares),
            'reason':'VWAP_OUTSIDE_OHLC_NOT_USED_BY_OHLC4',
            'original_volume_shares':str(bar.volume_shares),'original_amount_cny':str(bar.amount_cny),
            'ohlc4_mode':str((bar.open+bar.close+bar.high+bar.low)/4),
            'off_book_cause_authenticated':False}
    return None


def prepare_eastmoney(raw,*,expected_code,cutoff,allow_valid_suffix=False):
    iso_day(cutoff)
    if type(raw) is not bytes or not 0<len(raw)<=2*1024*1024:raise ValueError('BOUNDED_SOURCE_BYTES_REQUIRED')
    value=json.loads(raw);data=value.get('data') if isinstance(value,dict) else None
    if not isinstance(value,dict) or value.get('rc')!=0 or not isinstance(data,dict) or data.get('code')!=expected_code:
        raise ValueError('SUCCESSFUL_SAME_SECURITY_RESPONSE_REQUIRED')
    lines=data.get('klines')
    if not isinstance(lines,list) or not 0<len(lines)<=10000:raise ValueError('BOUNDED_NONEMPTY_HISTORY_REQUIRED')
    bars=[];issues=[];observations=[];previous=None;last_invalid=-1;eligible=0
    with localcontext() as context:
        context.prec=PRECISION
        for index,line in enumerate(lines):
            if not isinstance(line,str):raise ValueError('ORIGINAL_SOURCE_ROW_STRING_REQUIRED')
            fields=line.split(',');day=iso_day(fields[0])
            if previous is not None and day<=previous:raise ValueError('UNIQUE_INCREASING_SOURCE_DATES_REQUIRED')
            previous=day
            if day>cutoff:continue
            eligible+=1
            try:
                if len(fields)!=11:raise ValueError('EXACT_DAILY_FIELD_COUNT_REQUIRED')
                nums={i:number(fields[i]) for i in (1,2,3,4,5,6,10)}
                bar=Bar(day,nums[1],nums[3],nums[4],nums[2],nums[5]*100,nums[6],nums[10])
                observation=validate_bar(bar)
                if observation:observations.append({**observation,'source_row_index':index,'original_row':line})
                bars.append((index,bar))
            except ValueError as exc:
                issues.append({'source_row_index':index,'source_date':day,'reason':str(exc),'original_row':line});last_invalid=index
    if issues and not allow_valid_suffix:raise ValueError('INVALID_SOURCE_HISTORY:'+issues[-1]['source_date']+':'+issues[-1]['reason'])
    kept=[bar for index,bar in bars if index>last_invalid]
    if not kept:raise ValueError('NO_CONTIGUOUS_VALID_SOURCE_SUFFIX')
    return kept,{'source_sha256':hashlib.sha256(raw).hexdigest(),'source_bytes':len(raw),
        'security_id':expected_code,'source_name':data.get('name'),'source_volume_unit':'HAND_AS_DOCUMENTED_BY_AKSHARE',
        'volume_conversion':'source_volume*100','amount_unit':'CNY_AS_DOCUMENTED_BY_AKSHARE','source_rows':len(lines),
        'source_rows_at_or_before_cutoff':eligible,'cutoff':cutoff,'used_rows':len(kept),
        'used_first_source_date':kept[0].source_date,'used_last_source_date':kept[-1].source_date,
        'invalid_source_rows':issues,'excluded_prefix_rows':eligible-len(kept),
        'reseed_after_last_invalid_explicitly_selected':bool(issues and allow_valid_suffix),
        'non_blocking_vwap_observations':observations,'triangle_mode':'OHLC4; VWAP does not select or reject a valid OHLC4 row',
        'original_raw_preserved':True,'historical_PIT_authenticated':False,
        'official_calendar_authenticated':False,'corporate_action_coverage_authenticated':False}
