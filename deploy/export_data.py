"""Copy only checked public aggregates. Never export raw probes or checkpoints."""
import argparse,json,os,sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/scripts'))
from retention import six_month_start,retain_six_months
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--destination',default='public/data');p.add_argument('--require-full',action='store_true');a=p.parse_args()
src=Path(a.source);dst=Path(a.destination);manifest=json.loads((src/'manifest.json').read_text());dates=manifest.get('dates',[])
today=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat();dates=[d for d in dates if d['date']>=six_month_start(today)];manifest['dates']=dates
if not dates:raise SystemExit('No saved dates in retention window')
latest=json.loads((src/dates[0]['file']).read_text())
if a.require_full:
    if latest.get('scope')!='full' or latest.get('pendingCount',0)>0:raise SystemExit('Full-universe traversal has not finished; preserve existing site')
    if sum(r.get('status')=='ok' for r in latest['rows'])/max(1,len(latest['rows']))<.90:raise SystemExit('Latest-date valid coverage below 90%; preserve existing site and inspect collector')
validated=[]
for d in dates:
    if Path(d['file']).name!=d['file'] or d['file']!=d['date']+'.json':raise SystemExit('Invalid public filename')
    obj=json.loads((src/d['file']).read_text())
    if obj['date']!=d['date'] or len({r['code'] for r in obj['rows']})!=len(obj['rows']):raise SystemExit('Date mismatch or duplicate stock')
    for r in obj['rows']:
        ratio=r.get('ratio')
        if ratio is not None:
            if r.get('quality')=='source_difference' or r.get('dayVolumeDifference',0)!=0:raise SystemExit('Unreconciled volume cannot have a published ratio')
            if r['status']!='ok' or not 0<=ratio<=100 or not r.get('dailyVolume',0)>0:raise SystemExit('Invalid published ratio')
            if abs(ratio-r['first15Volume']/r['dailyVolume']*100)>1e-5:raise SystemExit('Ratio arithmetic mismatch')
    validated.append((d['file'],obj))
catalog=None
if (src/'universe.json').exists():
    raw=json.loads((src/'universe.json').read_text())
    catalog={'asOf':raw.get('asOf'),'historicalMembership':False,'total':raw['total'],'rows':[{'code':r['code'],'name':r['name']} for r in raw['rows']]}
    if len(catalog['rows'])!=catalog['total'] or len({r['code'] for r in catalog['rows']})!=catalog['total']:raise SystemExit('Invalid stock catalog')
    if any(len(r['code'])!=6 or not r['code'].isdigit() or not isinstance(r['name'],str) for r in catalog['rows']):raise SystemExit('Invalid stock code/name')
elif a.require_full:raise SystemExit('Full export requires current universe catalog')
dst.mkdir(parents=True,exist_ok=True)
if catalog:(dst/'stocks.json').write_text(json.dumps(catalog,ensure_ascii=False,allow_nan=False))
for filename,obj in validated:(dst/filename).write_text(json.dumps(obj,ensure_ascii=False,allow_nan=False))
manifest['automationStatus']='active' if a.require_full and os.environ.get('DAILY_UPDATE_ENABLED')=='true' else 'not_configured'
(dst/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,allow_nan=False))
retain_six_months(dst,today)
print(f'Exported {len(validated)} dates; latest {latest["date"]}, {len(latest["rows"])} stocks, scope={latest["scope"]}')
