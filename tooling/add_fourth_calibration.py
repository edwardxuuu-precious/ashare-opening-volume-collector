"""Add one requested comparison stock from already saved, real SDK history."""
import copy,hashlib,itertools,json
from datetime import datetime,timezone
from pathlib import Path
from backend.chips.model import MODEL,prepare_eastmoney,estimate_snapshots,compare_snapshots

root=Path(__file__).resolve().parents[1];output=root/'outputs/chips-daily-20261018'
old=output/'calibration';new=output/'calibration-four'
new.mkdir(exist_ok=True)
def data(path):return json.loads(path.read_bytes())
def raw(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
manifest=data(old/'manifest.json');dates=manifest['dates']
source=output/'000001-baostock-full.raw.json';bundle=source.read_bytes();value=json.loads(bundle)
normalized=raw({'rc':value['rc'],'data':value['data']})
bars,audit=prepare_eastmoney(normalized,expected_code='000001',cutoff=dates[-1],allow_valid_suffix=True)
snapshots=estimate_snapshots(bars,dates,security_id='000001')
sample={'security_id':'000001','name':'\u5e73\u5b89\u94f6\u884c','model':MODEL,'snapshots':snapshots,
    'comparisons':[compare_snapshots(snapshots[a],snapshots[b]) for a,b in itertools.combinations(dates,2)],
    'source_sha256':hashlib.sha256(bundle).hexdigest(),'source_provider':'BAOSTOCK_0.9.3',
    'source_representation':'Lossless SDK strings with explicit normalized rows; not transport bytes',
    'source_audit':audit,'recorded_at':datetime.now(timezone.utc).isoformat(),'event_time':None,'bar_close_at':None,
    'known_at':None,'ingested_at':None,'vendor_calibration':'WAITING_SCREENSHOTS','unchanged_threshold':None,
    'history':[{'date':b.source_date,'open':str(b.open),'high':str(b.high),'low':str(b.low),'close':str(b.close),
        'volume_shares':str(b.volume_shares),'amount_cny':str(b.amount_cny),'turnover_percent':str(b.turnover_pct)} for b in bars[-500:]]}
payloads={s['code']+'.json':(old/(s['code']+'.json')).read_bytes() for s in manifest['securities']}
payloads['000001.json']=raw(sample)
screen=data(old/'screening.json');screen['covered_securities']=4
fourth=copy.deepcopy(screen['screenings'][0]);fourth.update(security_id='000001',name=sample['name'],
    close_cny=snapshots[dates[-1]]['close_cny'],peak_prices='/'.join(snapshots[dates[-1]]['peak_prices_cny']),
    previous_peak_prices='/'.join(snapshots[dates[-2]]['peak_prices_cny']))
fourth['conditions']['close_below_10']='FAIL';fourth['screen_status']='FAIL'
screen['screenings'].append(fourth)
screen['samples']=[{'code':name[:-5],'name':data(old/name)['name'] if name!='000001.json' else sample['name'],
    'file':name,'sha256':hashlib.sha256(body).hexdigest()} for name,body in payloads.items()]
payloads['screening.json']=raw(screen)
dataset=hashlib.sha256(raw({name:hashlib.sha256(body).hexdigest() for name,body in payloads.items()})).hexdigest()[:16]
manifest.update(dataset_id=dataset,covered_securities=4)
manifest['securities'].append({'code':'000001','name':sample['name']})
for name,body in payloads.items():(new/name).write_bytes(body)
(new/'manifest.json').write_bytes(raw(manifest))
print(json.dumps({'dataset_id':dataset,'stocks':4,'new_code':'000001','snapshots':dates,'history_rows':len(sample['history']),
    'coefficient':MODEL['historical_turnover_decay_coefficient'],'threshold':None,'source_sha256':sample['source_sha256']}))
