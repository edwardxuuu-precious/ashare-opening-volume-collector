"""Real saved inputs: browser arithmetic must match original server bit for bit."""
import gzip,json,hashlib,subprocess,sys,time
from pathlib import Path
import boto3
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.chips.history import load_history,ImmutableHistoryStore,CALENDARS
from backend.chips.eastmoney_model import VERSION
OUT=ROOT/'outputs/chips-local-preview-20261020';OUT.mkdir(exist_ok=True,parents=True)
s=boto3.Session(profile_name='stock-dev',region_name='us-east-1');assert s.client('sts').get_caller_identity()['Account']=='838775535952'
client=s.client('s3');bucket='stock-private-access-databucket-bmzud610gtex'
manifest=json.loads(client.get_object(Bucket=bucket,Key='data/chips/daily/manifest.json')['Body'].read());dataset=manifest['dataset_id']
assert json.loads((ROOT/'src/lib/chip-history-calendar.json').read_text())==json.loads(json.dumps(CALENDARS))
store=ImmutableHistoryStore(client);cases=[]
for code in ['000573','600812','000725','000001','000576','920196']:
    raw=client.get_object(Bucket=bucket,Key=f'data/chips/daily/{dataset}/{code}.json')['Body'].read();frame=json.loads(gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw)
    days=[r['date'] for r in frame['history'][209:] if r['date']<frame['as_of']]
    expected={}
    for day in days:expected[day]=load_history(store,bucket,dataset,code,day)
    cases.append({'stock':frame,'expected':expected})
(OUT/'real-parity-input.json').write_text(json.dumps(cases,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
print(json.dumps({'dataset':dataset,'stocks':len(cases),'dates':sum(len(c['expected']) for c in cases),'model_version':VERSION}),flush=True)
result=subprocess.run([r'C:\Program Files\nodejs\node.exe','--experimental-strip-types','tooling/verify_chip_local_history.mjs'],cwd=ROOT)
raise SystemExit(result.returncode)
