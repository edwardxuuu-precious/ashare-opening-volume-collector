"""Freeze bounded BSE reference inputs and inclusive closing pools locally."""
import argparse,base64,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.chips.calendar import last_ten
from backend.chips.limit_reference import prepared,closing_pool,fetch_closing_pool
from backend.chips.source import Source

def main():
    p=argparse.ArgumentParser();p.add_argument('--as-of',required=True);a=p.parse_args();days=last_ten(a.as_of)
    source_dir=ROOT/'var/chip-limit-evidence/sohu'/a.as_of
    target=ROOT/'var/chip-limit-evidence'/a.as_of;target.mkdir(exist_ok=True)
    count=0
    for file in source_dir.glob('??????.raw'):
        code=file.stem;raw=file.read_bytes();receipt=json.loads(file.with_suffix('.receipt.json').read_text(encoding='utf-8'))
        assert len(raw)<500000 and receipt['sha256']==hashlib.sha256(raw).hexdigest()
        receipt={k:receipt[k] for k in ('url','sha256','bytes','ingested_at')}
        receipt['provider']='SOHU_REPORTED_DAILY_CHANGE'
        bundle={'schema':'stock.chips.limit.sohu.prepared.v1','security_id':code,'as_of':a.as_of,
            'official_sessions':days,'receipt':receipt,'raw_base64':base64.b64encode(raw).decode(),'published':False}
        prepared(bundle,code,days)
        (target/(code+'.json')).write_text(json.dumps(bundle,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8');count+=1
    if count!=94:raise ValueError('Expected 94 frozen BSE inputs')
    pool_dir=ROOT/'var/chip-limit-evidence/closing-pools';pool_dir.mkdir(exist_ok=True)
    pools={};source=Source()
    try:
        for day in days:
            file=pool_dir/(day+'.json')
            bundle=json.loads(file.read_text(encoding='utf-8')) if file.exists() else fetch_closing_pool(source,day)
            pools[day]=closing_pool(bundle,day)
            if not file.exists():file.write_text(json.dumps(bundle,separators=(',',':'))+'\n',encoding='utf-8')
            print(json.dumps({'date':day,'inclusive_closing_limit_pool_count':len(pools[day]),'st_included':True}),flush=True)
    finally:source.close()
    print(json.dumps({'bse_reference_inputs':count,'closing_pool_days':len(pools),'cloud_writes':0}))

if __name__=='__main__':main()
