"""Bounded Windows read-only inspection of the active private chip batch."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path

import boto3
from botocore.config import Config


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--samples',type=int,default=4)
    parser.add_argument('--save',action='store_true')
    args=parser.parse_args()
    if not 0<=args.samples<=8:parser.error('samples must be 0..8')
    client=boto3.Session(profile_name='stock-dev',region_name='us-east-1').client('s3',
        config=Config(connect_timeout=7,read_timeout=25,retries={'total_max_attempts':2}))
    def read(key):
        response=client.get_object(Bucket='stock-private-access-databucket-bmzud610gtex',Key=key)
        raw=response['Body'].read(20000001)
        if len(raw)>20000000:raise RuntimeError('Inspection read bound exceeded')
        return json.loads(gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw)
    status=read('data/chips/daily/status.json')
    manifest=read('data/chips/daily/manifest.json')
    state=read('collector/chips/state.json')
    rows=list(state.get('rows',{}).values())
    result={'observed_at':datetime.now(timezone.utc).isoformat(),'status':status,
        'committed':{k:manifest.get(k) for k in ('dataset_id','as_of','model_ready_count','universe_total','screen_counts','market_complete',
            'universe_coverage','display_response_verified_count','max_display_response_upper_bound_bytes','screening_response_upper_bound_bytes')},
        'staged_rows':len(rows),'staged_by_market':{market:dict(Counter(r['chip_status'] for r in rows if r['market']==market)) for market in sorted({r['market'] for r in rows})},
        'missing_reasons':dict(Counter(reason for r in rows if r['chip_status']!='READY' for reason in r.get('reasons',[]))),
        'universe_receipts':state.get('universe_receipts',[]),
        'pool_failures':[r for r in state.get('pool_receipts',[]) if 'error' in r]}
    examples=[];seen=set()
    for row in rows:
        category=(row['market'],tuple(row.get('reasons',[])))
        if row['chip_status']=='READY' or category in seen:continue
        seen.add(category)
        if len(examples)>=args.samples:break
        value=read(f'data/chips/daily/{state["dataset_id"]}/{row["security_id"]}.json')
        examples.append({k:value.get(k) for k in ('security_id','reasons','error_detail','source_errors')})
    result['missing_examples']=examples
    if args.save:
        output=Path(__file__).resolve().parents[1]/'outputs/chips-coverage-20261018'
        output.mkdir(parents=True,exist_ok=True)
        (output/'coverage-latest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
