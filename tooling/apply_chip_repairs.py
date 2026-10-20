"""Install verified same-session inputs after the cloud writer has finished.

This updates only private chip source/model caches under the regular lease.
It never rewrites published frames or manifests. The normal runner publishes
a new immutable generation afterwards. Default mode is a read-only preview.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

import boto3

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'tooling')]
from aws_stock import verify_account
from github_stock import request
from backend.chips.runner import Storage,frame,implementation_identity
from backend.chips.listing import verified_current


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--limit',type=int,default=100)
    args=parser.parse_args()
    if not 1<=args.limit<=100:parser.error('limit must be 1..100')
    verify_account()
    boto3.setup_default_session(profile_name='stock-dev',region_name='us-east-1')
    store=Storage('stock-private-access-databucket-bmzud610gtex','us-east-1')
    current,_=store.get('data/chips/daily/manifest.json')
    state,_=store.get('collector/chips/state.json')
    if (current['dataset_id']!=args.dataset or state['dataset_id']!=args.dataset
        or len(state['rows'])!=len(state['stocks'])):
        raise RuntimeError('Expected generation must be fully processed and committed before cache repair')
    identity=implementation_identity();stocks={s['code']:s for s in state['stocks']}
    membership_file=root/'var/chip-repair-inputs'/('official-current-universe-'+state['as_of']+'.json')
    membership=json.loads(membership_file.read_text(encoding='utf-8')) if membership_file.exists() else None
    verified=verified_current(membership,list(stocks.values()),state['as_of'])
    if verified:stocks={s['code']:s for s in verified[0]}
    prepared=root/'var'/'chip-repair-inputs'/args.dataset
    candidates=[]
    for file in sorted(prepared.glob('??????.json')):
        if len(candidates)>=args.limit:break
        code=file.stem
        if state['rows'].get(code,{}).get('chip_status')=='READY':continue
        if file.stat().st_size>25000000:raise RuntimeError('Prepared bundle exceeds file size bound')
        bundle=json.loads(file.read_text(encoding='utf-8'))
        if (bundle['security_id']!=code or bundle['as_of']!=state['as_of']
            or bundle['based_on_failed_generation']!=args.dataset or bundle['published'] is not False
            or bundle['execution_identity']['history_adapter_sha256']!=identity['history_adapter_sha256']
            or bundle['execution_identity']['files']['model.py']!=identity['files']['model.py']):
            raise RuntimeError('Prepared input identity mismatch: '+code)
        raw=base64.b64decode(bundle['raw_base64'],validate=True)
        if len(raw)>18000000 or hashlib.sha256(raw).hexdigest()!=bundle['receipt']['sha256']:
            raise RuntimeError('Prepared raw hash/size mismatch: '+code)
        candidates.append((code,bundle,raw))
    if not args.apply:
        print(json.dumps({'dataset':args.dataset,'prepared_candidates':len(candidates),'applied':False}));return
    runs=request('/actions/workflows/chips.yml/runs?per_page=20')['workflow_runs']
    if any(r['status'] not in ('completed',) for r in runs):
        raise RuntimeError('A chip workflow is active or queued; wait before applying input caches')
    lease,etag=store.get('collector/chips/lease.json')
    if lease and lease.get('expires',0)>time.time():raise RuntimeError('Another chip writer holds the lease')
    owner=uuid.uuid4().hex
    lease_etag=store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+600},match=etag,new=etag is None)
    applied=[]
    def own():
        observed,_=store.get('collector/chips/lease.json')
        if not observed or observed['owner']!=owner or observed['expires']<=time.time():
            raise RuntimeError('Repair writer lost its lease')
    try:
        # A writer may have staged another generation while we waited for the lease.
        # Validate both pointers inside the lease before trusting prepared inputs.
        locked_current,_=store.get('data/chips/daily/manifest.json')
        locked_state,_=store.get('collector/chips/state.json')
        if (locked_current['dataset_id']!=args.dataset
            or locked_state['dataset_id']!=args.dataset
            or locked_state['as_of']!=state['as_of']
            or locked_current['as_of']!=locked_state['as_of']
            or len(locked_state['rows'])!=len(locked_state['stocks'])
            or locked_state['execution_identity']!=state['execution_identity']):
            raise RuntimeError('Publication or staging state advanced before repair acquired the lease')
        state=locked_state
        stocks={s['code']:s for s in state['stocks']}
        verified=verified_current(membership,list(stocks.values()),state['as_of'])
        if verified:
            stocks={s['code']:s for s in verified[0]}
            membership_key=f'collector/chips/universe-verified/{state["as_of"]}.json'
            prior_membership,_=store.get(membership_key)
            if prior_membership is None:
                own();store.put(membership_key,membership,new=True,compressed=True)
            elif prior_membership['stocks_sha256']!=membership['stocks_sha256']:
                raise RuntimeError('Official membership input conflicts with existing reconciliation')
        for code,bundle,raw in candidates:
            own()
            row,value=frame(stocks[code],raw,bundle['receipt'],state['days'],state['pools'],args.dataset,state.get('actual_limits'))
            next_state=value.pop('_model_state',None)
            if row['chip_status']!='READY' or not next_state:raise RuntimeError('Prepared source no longer computes: '+code)
            cache_key=f'collector/chips/model/{code}.json'
            cached,cache_etag=store.get(cache_key)
            if cached and (cached['last_date']>state['as_of']
                or (cached.get('_calculated_as_of') or cached['last_date'])>state['as_of']):
                raise RuntimeError('Refusing to replace a newer model cache: '+code)
            raw_key=f'collector/chips/raw/repair-{state["as_of"]}/{code}-{bundle["receipt"]["sha256"][:16]}.json'
            saved,_=store.get(raw_key)
            if saved is None:
                own();store.put(raw_key,{'receipt':bundle['receipt'],'raw_base64':bundle['raw_base64']},new=True,compressed=True)
            elif saved['receipt']['sha256']!=bundle['receipt']['sha256']:
                raise RuntimeError('Prepared source key conflict: '+code)
            decoded=json.loads(raw)
            next_state.update(_source_history=decoded['data']['klines'],_source_provider=bundle['receipt']['provider'],
                _source_bundle_sha256=bundle['receipt']['sha256'],_source_raw_key=raw_key,
                _model_code_sha256=identity['files']['model.py'],_history_adapter_sha256=identity['history_adapter_sha256'],
                _confirmed_no_trade_sessions=decoded.get('confirmed_no_trade_sessions',[]),_calculated_as_of=state['as_of'])
            own();store.put(cache_key,next_state,match=cache_etag,new=cache_etag is None,compressed=True)
            applied.append(code)
            lease_etag=store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+600},match=lease_etag)
    finally:
        observed,observed_etag=store.get('collector/chips/lease.json')
        if observed and observed['owner']==owner:
            store.put('collector/chips/lease.json',{'owner':owner,'expires':0},match=observed_etag)
    receipt={'dataset':args.dataset,'as_of':state['as_of'],'applied_input_codes':applied,'published_frames_changed':False,
        'next_step':'Normal runner must calculate and publish a new generation; this is input cache repair only.'}
    output=root/'outputs'/'chips-coverage-20261018'/'prepared-input-application.json'
    output.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt))


if __name__=='__main__':main()
