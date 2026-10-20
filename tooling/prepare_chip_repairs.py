"""Read active failures and prepare verified Windows source bundles locally.

No private cloud object is written and no running writer lease is touched.
Prepared inputs are evidence, not a published generation.
"""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import sys

import boto3
from botocore.config import Config

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from backend.chips.runner import frame,implementation_identity
from backend.chips.source import Source
from backend.chips.listing import verified_current


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--limit',type=int,default=50)
    args=parser.parse_args()
    if not 1<=args.limit<=100:parser.error('limit must be 1..100')
    client=boto3.Session(profile_name='stock-dev',region_name='us-east-1').client('s3',
        config=Config(connect_timeout=7,read_timeout=25,retries={'total_max_attempts':2}))
    response=client.get_object(Bucket='stock-private-access-databucket-bmzud610gtex',Key='collector/chips/state.json')
    raw=response['Body'].read(20000001)
    if len(raw)>20000000:raise RuntimeError('State read bound exceeded')
    state=json.loads(gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw)
    if state['dataset_id']!=args.dataset:raise RuntimeError('Active generation changed; inspect before preparing inputs')
    output=root/'var'/'chip-repair-inputs'/args.dataset
    output.mkdir(parents=True,exist_ok=True)
    stocks={stock['code']:stock for stock in state['stocks']}
    membership=root/'var/chip-repair-inputs'/('official-current-universe-'+state['as_of']+'.json')
    if membership.exists():
        verified=verified_current(json.loads(membership.read_text(encoding='utf-8')),list(stocks.values()),state['as_of'])
        stocks={stock['code']:stock for stock in verified[0]}
    source=Source();summary=[];attempted=0
    try:
        for code,row in state['rows'].items():
            if row['chip_status']=='READY' or (output/(code+'.json')).exists():continue
            if attempted>=args.limit:break
            attempted+=1
            try:
                _,receipt,source_raw=source.history(stocks[code],state['as_of'])
                checked,value=frame(stocks[code],source_raw,receipt,state['days'],state['pools'],
                    state['dataset_id'],state.get('actual_limits'))
                if checked['chip_status']!='READY':
                    summary.append({'code':code,'prepared':False,'reasons':value['reasons']});continue
                if hashlib.sha256(source_raw).hexdigest()!=receipt['sha256']:raise RuntimeError('Source hash mismatch')
                bundle={'schema':'stock.chips.prepared-input.v1','security_id':code,'as_of':state['as_of'],
                    'based_on_failed_generation':args.dataset,'receipt':receipt,
                    'raw_base64':base64.b64encode(source_raw).decode(),'execution_identity':implementation_identity(),
                    'checked_screening_row':checked,'published':False}
                (output/(code+'.json')).write_text(json.dumps(bundle,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
                summary.append({'code':code,'prepared':True,'provider':receipt['provider']})
            except Exception as exc:
                summary.append({'code':code,'prepared':False,'error':type(exc).__name__,'detail':str(exc)[:200]})
    finally:source.close()
    print(json.dumps({'dataset':args.dataset,'attempted':attempted,'results':summary,'cloud_writes':0,'published':False}))


if __name__=='__main__':main()
