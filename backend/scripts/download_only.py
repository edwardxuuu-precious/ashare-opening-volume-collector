"""Calculate from downloaded source volumes without cross-series reconciliation."""
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

POLICY = 'download_only'
METHOD = '新采集固定使用AKShare新浪不复权15分钟线和日线。开盘占比=09:45首根15分钟成交量÷同日日线成交量×100%，单位为股。不比对全天分钟合计和日线，不进行备用源核对。历史记录保留原始来源。'


def volumes_present(row):
    first, daily = row.get('first15Volume'), row.get('dailyVolume')
    return (all(type(v) in (int,float) and math.isfinite(v) and v == int(v) for v in (first,daily))
            and daily > 0 and 0 <= first <= daily)


def downloaded_row(original):
    row = dict(original)
    # Existing successful observations retain their exact identity and source.
    if row.get('status') in ('ok','suspended'):
        return row
    row.update(calculationPolicy=POLICY, verificationVersion=2)
    if volumes_present(row):
        row.update(status='ok', ratio=round(row['first15Volume']/row['dailyVolume']*100,6),
                   quality='downloaded', verificationState='downloaded')
        row.pop('reason',None)
    else:
        row.update(status='missing',ratio=None)
    return row


def evaluate_downloaded(code, name, day, minute, daily, market):
    row=dict(code=code,name=name,market=market(code),sourceProvider='sina',verificationVersion=2,
             calculationPolicy=POLICY,status='missing',first15Volume=None,dailyVolume=None,ratio=None)
    opening_rows = 0
    daily_rows = 0
    if 'day' in minute and 'volume' in minute:
        opening=minute[minute['day'].astype(str)==day+' 09:45:00']
        minute_on_day=minute[minute['day'].astype(str).str[:10]==day]
        opening_rows=len(minute_on_day)
        if len(opening)==1:
            row['first15Volume']=_number(opening.iloc[0]['volume'])
    date_col='date' if 'date' in daily else '日期'
    volume_col='volume' if 'volume' in daily else '成交量'
    if date_col in daily and volume_col in daily:
        selected=daily[daily[date_col].astype(str).str[:10]==day]
        daily_rows=len(selected)
        if len(selected)==1:row['dailyVolume']=_number(selected.iloc[0][volume_col])
    # A completed historical request with no minute or daily row means there was
    # no trading session for this listed security (for example, a suspension).
    # Treat it as terminal so the backlog does not retry it forever. Keep the
    # current date retryable in case the provider has not published it yet.
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    if day < today and opening_rows == 0 and daily_rows == 0:
        row['status']='suspended'
        return row
    return downloaded_row(row)


def _number(value):
    try:
        value=float(value)
        return value if math.isfinite(value) and value >= 0 and value.is_integer() else None
    except (TypeError,ValueError):return None


def prepare_saved_downloads(out, dates):
    """Reclassify existing volumes locally; never overwrite source volume values."""
    from .collect import write_json
    out=Path(out); selected=set(dates)
    timestamp=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    manifest=json.loads((out/'manifest.json').read_text())
    original_manifest=json.dumps(manifest,sort_keys=True)
    entries={entry['date']:entry for entry in manifest.get('dates',[])}
    promoted=0; changed=False
    for day in dates:
        path=out/(day+'.json')
        if not path.exists():continue
        payload=json.loads(path.read_text()); rows=[]
        for original in payload['rows']:
            row=downloaded_row(original)
            promoted+=original.get('status')!='ok' and row['status']=='ok'
            rows.append(row)
        payload.update(rows=rows,calculationPolicy=POLICY,collectionSourcePolicy=['sina'],
                       methodology=METHOD,source='AKShare · 新浪（新采集）；历史记录保留原来源',
                       reconciliationPolicy='none')
        if payload!=json.loads(path.read_text()):
            payload['generatedAt']=timestamp
            write_json(path,payload)
            changed=True
        if day in entries:
            counts={s:sum(row['status']==s for row in rows) for s in ('ok','missing','suspended')}
            entries[day].update(valid=counts['ok'],missing=counts['missing'],unverified=0,
                                suspended=counts['suspended'],generatedAt=payload['generatedAt'],
                                status='complete' if counts['missing']==0 else 'partial')
    for path in (out/'checkpoint').glob('*.json'):
        original=json.loads(path.read_text()); record=dict(original,days=dict(original.get('days',{})))
        for day,row in record['days'].items():
            if day in selected:record['days'][day]=downloaded_row(row)
        if record!=original:
            write_json(path,record); changed=True
    manifest.update(calculationPolicy=POLICY,collectionSourcePolicy=['sina'],methodology=METHOD,
                    source='AKShare · 新浪（新采集）；历史记录保留原来源',reconciliationPolicy='none',
                    )
    if json.dumps(manifest,sort_keys=True)!=original_manifest:
        manifest['generatedAt']=timestamp
        write_json(out/'manifest.json',manifest); changed=True
    return dict(promotedRows=promoted,changed=changed)
