"""Keep published history; six months limits only the mutable collection workspace."""
import calendar,json,re
from datetime import date
from pathlib import Path

def six_month_start(today):
    current=date.fromisoformat(str(today));month=current.year*12+current.month-1-6
    year,offset=divmod(month,12);return date(year,offset+1,min(current.day,calendar.monthrange(year,offset+1)[1])).isoformat()

def validate_dates(dates,today,trading_dates):
    values=sorted(set(dates));start=six_month_start(today);allowed=set(trading_dates)
    if not values or len(values)>186:raise ValueError('请选择最近6个月内的交易日')
    for value in values:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('日期格式无效')
        date.fromisoformat(value)
        if not start<=value<=str(today):raise ValueError('仅保留最近6个月的数据')
        if value not in allowed:raise ValueError(f'{value} 不是已收盘的交易日')
    return values

def accumulated_manifest(manifest):
    """Expose every saved date, independently of the rolling collection calendar."""
    result=dict(manifest)
    saved=[entry['date'] for entry in result.get('dates',[])]
    result['tradingDates']=sorted(set(result.get('tradingDates',[])) | set(saved))
    result.pop('retentionMonths',None)
    result.update(retentionPolicy='accumulate',collectionWindowMonths=6)
    starts=saved+([result['retentionStart']] if result.get('retentionStart') else [])
    if starts:result['retentionStart']=min(starts)
    return result

def retain_six_months(out,today):
    """Compatibility entrypoint: retain date files; trim only disposable working checkpoints."""
    out=Path(out);start=six_month_start(today);path=out/'manifest.json'
    if path.exists():
        manifest=accumulated_manifest(json.loads(path.read_text()))
        tmp=path.with_suffix('.json.tmp');tmp.write_text(json.dumps(manifest,ensure_ascii=False));tmp.replace(path)
    for path in (out/'checkpoint').glob('*.json'):
        try:record=json.loads(path.read_text())
        except (ValueError,OSError):continue
        if isinstance(record.get('days'),dict):
            record['days']={d:r for d,r in record['days'].items() if d>=start}
            tmp=path.with_suffix('.json.tmp');tmp.write_text(json.dumps(record,ensure_ascii=False));tmp.replace(path)
