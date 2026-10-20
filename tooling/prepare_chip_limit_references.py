"""Fetch bounded historical reference prices locally; never writes cloud data."""
import argparse,base64,concurrent.futures,hashlib,json,sys,time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.chips.baostock_history import BaoHistory
from backend.chips.calendar import last_ten
from decimal import Decimal

SOURCE=None
def initialize():
    global SOURCE
    import atexit
    SOURCE=BaoHistory();atexit.register(SOURCE.close)

def fetch(task):
    stock,as_of,days=task
    try:
        value,receipt,raw=SOURCE.history(stock,as_of,start_date=days[0])
        original=value['original_sdk_response']
        if any(row[0] not in days for row in original['rows']):raise ValueError('REFERENCE_DATE_OUTSIDE_REQUESTED_SESSIONS')
        if hashlib.sha256(raw).hexdigest()!=receipt['sha256']:raise ValueError('REFERENCE_SOURCE_HASH_MISMATCH')
        return stock['code'],{'schema':'stock.chips.limit.reference.prepared.v1','security_id':stock['code'],
            'as_of':as_of,'official_sessions':days,'receipt':receipt,'raw_base64':base64.b64encode(raw).decode(),
            'published':False},None
    except Exception as exc:
        return stock['code'],None,{'error':type(exc).__name__,'detail':str(exc)[:250]}

def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True);p.add_argument('--as-of',required=True)
    p.add_argument('--limit',type=int,default=2500);p.add_argument('--workers',type=int,default=2)
    p.add_argument('--max-minutes',type=int,default=45);a=p.parse_args()
    if not 1<=a.limit<=2500 or not 1<=a.workers<=2 or not 2<=a.max_minutes<=60:p.error('Bound exceeded')
    if len(a.dataset)!=16 or any(c not in '0123456789abcdef' for c in a.dataset):p.error('Invalid dataset')
    days=last_ten(a.as_of)
    screening=ROOT/'var/chip-publication-validation'/a.dataset/'screening.json'
    if screening.stat().st_size>30000000:raise ValueError('Screening read bound exceeded')
    screen=json.loads(screening.read_text(encoding='utf-8'))
    assert screen['dataset_id']==a.dataset and screen['as_of']==a.as_of
    membership=json.loads((ROOT/'var/chip-repair-inputs'/f'official-current-universe-{a.as_of}.json').read_text(encoding='utf-8'))
    stocks={s['code']:s for s in membership['stocks']}
    selected=[r['security_id'] for r in screen['rows'] if not r['security_id'].startswith(('4','8','9'))
        and (Decimal(r['close_cny'])<10 or any(e['reason']=='DAILY_BAR_MISSING' for e in r['limit_evidence']))]
    assert len(selected)==len(set(selected)) and set(selected)<=set(stocks)
    output=ROOT/'var/chip-limit-evidence'/a.as_of;output.mkdir(parents=True,exist_ok=True)
    skipped=[];pending=[]
    for code in selected:
        file=output/(code+'.json')
        if file.exists():
            if file.stat().st_size>200000:raise ValueError('Prepared reference read bound exceeded')
            existing=json.loads(file.read_text(encoding='utf-8'))
            raw=base64.b64decode(existing['raw_base64'],validate=True)
            if (existing['security_id']!=code or existing['as_of']!=a.as_of
                or existing['official_sessions']!=days or existing['receipt']['sha256']!=hashlib.sha256(raw).hexdigest()):
                raise ValueError('Existing reference bundle conflict: '+code)
            skipped.append(code)
        else:pending.append(code)
    pending=pending[:a.limit];deadline=time.monotonic()+a.max_minutes*60
    successes=[];errors={};iterator=iter(pending)
    with concurrent.futures.ProcessPoolExecutor(max_workers=a.workers,initializer=initialize) as pool:
        active={}
        def fill():
            while len(active)<a.workers and time.monotonic()<deadline-45:
                try:code=next(iterator)
                except StopIteration:break
                active[pool.submit(fetch,(stocks[code],a.as_of,days))]=code
        fill()
        while active:
            done,_=concurrent.futures.wait(active,timeout=30,return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                code=active.pop(future);returned,bundle,error=future.result();assert returned==code
                if error:errors[code]=error
                else:
                    file=output/(code+'.json');temporary=file.with_suffix('.tmp')
                    temporary.write_text(json.dumps(bundle,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
                    temporary.replace(file);successes.append(code)
            if done and (len(successes)+len(errors))%20<a.workers:
                print(json.dumps({'prepared_now':len(successes),'failed':len(errors),'cached':len(skipped),'target':len(selected)}),flush=True)
            fill()
    receipt={'schema':'stock.chips.limit.reference.local-batch.v1','based_on_dataset':a.dataset,'as_of':a.as_of,
        'official_sessions':days,'selected_codes':selected,'selected_codes_sha256':hashlib.sha256(json.dumps(selected,separators=(',',':')).encode()).hexdigest(),
        'prepared_codes':successes,'cached_codes':skipped,'errors':errors,'ingested_at':datetime.now(timezone.utc).isoformat(),
        'cloud_writes':0,'published':False,'scope':'SH/SZ below-10 price population plus all identified recent-session gaps; raw vendor preclose/trade status, no inferred previous close'}
    (output/'batch-manifest.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'target':len(selected),'prepared_now':len(successes),'cached':len(skipped),'failed':len(errors),'cloud_writes':0,'published':False}))

if __name__=='__main__':main()
