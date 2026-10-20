"""Install source evidence under the normal lease; published outputs stay immutable.

Default is a read-only preview. Only the normal reviewed runner may publish the
next generation. Input hashes, dated membership, quote references and both
publication/staging pointers are checked before writing chip-specific caches.
"""
import argparse,hashlib,json,sys,time,uuid
from decimal import Decimal as D
from pathlib import Path
import boto3

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tooling')]
from aws_stock import verify_account
from github_stock import request
from backend.chips.runner import Storage,encoded
from backend.chips.calendar import last_ten
from backend.chips.limit_reference import prepared,closing_pool,corroborate_quantity
from backend.chips.screen import prefilter

def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True);p.add_argument('--as-of',required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--corroboration-only',action='store_true')
    a=p.parse_args();days=last_ten(a.as_of)
    directory=ROOT/'var/chip-limit-evidence'/a.as_of
    bundles={f.stem:json.loads(f.read_text(encoding='utf-8')) for f in directory.glob('??????.json')}
    if not 2000<=len(bundles)<=2500:raise ValueError('Prepared source population bound exceeded')
    refs={c:prepared(b,c,days) for c,b in bundles.items()}
    pool_bundles={d:json.loads((ROOT/'var/chip-limit-evidence/closing-pools'/(d+'.json')).read_text(encoding='utf-8')) for d in days}
    pools={d:closing_pool(b,d) for d,b in pool_bundles.items()}
    verify_account();boto3.setup_default_session(profile_name='stock-dev',region_name='us-east-1')
    store=Storage('stock-private-access-databucket-bmzud610gtex','us-east-1')
    current,_=store.get('data/chips/daily/manifest.json');state,_=store.get('collector/chips/state.json')
    def valid(c,s):
        return (c['dataset_id']==a.dataset==s['dataset_id'] and c['as_of']==a.as_of==s['as_of']
            and len(s['rows'])==len(s['stocks']) and c['model_ready_count']==c['universe_total'])
    if not valid(current,state):raise RuntimeError('Expected fully processed committed generation is required')
    low={c for c,r in state['rows'].items() if r.get('close_cny') and D(r['close_cny'])<10}
    if not low<=set(bundles) or not set(bundles)<=set(state['rows']):raise ValueError('Reference population does not cover every low-price stock')
    checked=0
    for code,ref in refs.items():
        r=ref['rows'].get(a.as_of);quote=state.get('actual_limits',{}).get(a.as_of,{}).get(code)
        if not r or not quote:raise ValueError('Current-session reference/quote missing: '+code)
        if r['tradestatus']=='0':continue
        if D(r['close'])!=D(quote['close']) or D(r['preclose'])!=D(quote['reference_close']):
            raise ValueError('Dated quote/reference conflict: '+code)
        checked+=1
    batch={'schema':'stock.chips.limit.reference.batch.v1','as_of':a.as_of,'securities':bundles,
        'based_on_dataset':a.dataset,'unchanged_threshold':None,'coefficient':2}
    batch_sha=hashlib.sha256(encoded(batch)).hexdigest()
    result={'based_on_dataset':a.dataset,'as_of':a.as_of,'reference_security_count':len(refs),
        'all_low_price_count':len(low),'quote_reference_checks':checked,'batch_sha256':batch_sha,
        'closing_pool_counts':{d:len(v) for d,v in pools.items()},'published_outputs_changed':False,'applied':False}
    supplements={}
    if a.corroboration_only:
        installed,_=store.get(f'collector/chips/references/{a.as_of}.json')
        if not installed or installed['securities']!=bundles:raise ValueError('Installed primary evidence differs from local frozen inputs')
        result['batch_sha256']=hashlib.sha256(encoded(installed)).hexdigest()
        files=sorted((ROOT/'var/chip-limit-evidence/corroboration'/a.as_of).glob('??????.json'))
        if not 1<=len(files)<=100:raise ValueError('Corroboration repair input bound exceeded')
        stocks={s['code']:s for s in state['stocks']};checks={}
        for file in files:
            code=file.stem;bundle=json.loads(file.read_text(encoding='utf-8'))
            secondary=prepared(bundle,code,days)
            if secondary['provider']!='SOHU_REPORTED_DAILY_CHANGE' or code not in refs:raise ValueError('Unexpected corroboration source')
            cache,_=store.get(f'collector/chips/model/{code}.json')
            published,_=store.get(f'data/chips/daily/{a.dataset}/{code}.json')
            # Earlier valid model caches have last_date, before the explicit
            # calculated-session field was introduced. Never override an
            # existing calculated cutoff or accept a different actual last day.
            cache_day=cache.get('_calculated_as_of',cache.get('last_date')) if cache else None
            if (not cache or cache_day!=a.as_of or not cache.get('_source_history')
                or not published or published.get('dataset_id')!=a.dataset or published.get('chip_status')!='READY'
                or published['as_of']!=a.as_of or published['security_id']!=code
                or published.get('execution_identity')!=state['execution_identity']
                or cache.get('_source_bundle_sha256')!=published.get('source',{}).get('sha256')
                or cache.get('_history_adapter_sha256')!=state['execution_identity']['history_adapter_sha256']
                or cache.get('_model_code_sha256')!=state['execution_identity']['files']['model.py']):
                raise ValueError('Matching published source, model and adapter identities are required')
            reference=corroborate_quantity(refs[code],secondary,cache['_source_history'])
            row=prefilter({**stocks[code],'reference_close_verified':cache['_source_provider']!='THS_UNADJUSTED',
                'confirmed_no_trade_sessions':cache.get('_confirmed_no_trade_sessions',[])},cache['_source_history'],days,state['pools'],state.get('actual_limits'),reference,pools)
            if row['prefilter_status']=='UNKNOWN':raise ValueError('Supplement does not resolve reference qualification: '+code)
            if D(row['close_cny'])!=D(published['screening_row']['close_cny']):raise ValueError('Supplement model close conflicts with publication')
            supplements[code]=bundle;checks[code]={'resolved_status':row['prefilter_status'],'changes':reference.get('quantity_representation_differences',[]),
                'source_sha256':secondary['source_sha256'],
                'cache_cutoff_basis':'_calculated_as_of' if '_calculated_as_of' in cache else 'last_date'}
        result.update(corroboration_only=True,corroboration_checks=checks)
    if not a.apply:print(json.dumps(result));return
    runs=request('/actions/workflows/chips.yml/runs?per_page=20')['workflow_runs']
    if any(r['status']!='completed' for r in runs):raise RuntimeError('A chip workflow is active/queued')
    lease,etag=store.get('collector/chips/lease.json')
    if lease and lease.get('expires',0)>time.time():raise RuntimeError('Chip writer holds lease')
    owner=uuid.uuid4().hex
    store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+600},match=etag,new=etag is None)
    def own():
        v,_=store.get('collector/chips/lease.json')
        if not v or v['owner']!=owner or v['expires']<=time.time():raise RuntimeError('Reference input writer lost lease')
    def immutable(key,value):
        old,_=store.get(key)
        if old is None:own();store.put(key,value,new=True,compressed=True)
        elif old!=value:raise RuntimeError('Existing reference input conflicts: '+key)
    try:
        c,_=store.get('data/chips/daily/manifest.json');s,_=store.get('collector/chips/state.json')
        if not valid(c,s) or s['execution_identity']!=state['execution_identity']:
            raise RuntimeError('Publication/staging pointers advanced while acquiring lease')
        if a.corroboration_only:
            for code,bundle in supplements.items():
                key=f'collector/chips/reference-corroboration/{a.as_of}/{code}.json';immutable(key,bundle)
                reread,_=store.get(key)
                if reread!=bundle:raise RuntimeError('Corroboration readback mismatch')
        else:
            immutable(f'collector/chips/reference-inputs/{batch_sha}.json',batch)
            for day,bundle in pool_bundles.items():immutable(f'collector/chips/closing-pools/{day}.json',bundle)
            immutable(f'collector/chips/references/{a.as_of}.json',batch)
            reread,_=store.get(f'collector/chips/references/{a.as_of}.json')
            if hashlib.sha256(encoded(reread)).hexdigest()!=batch_sha:raise RuntimeError('Reference input readback mismatch')
        result['applied']=True
    finally:
        l,e=store.get('collector/chips/lease.json')
        if l and l['owner']==owner:store.put('collector/chips/lease.json',{'owner':owner,'expires':0},match=e)
    output=ROOT/'outputs/chips-coverage-20261018'/(f'limit-corroboration-application-{a.dataset}.json' if a.corroboration_only else 'limit-evidence-application.json')
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result))

if __name__=='__main__':main()
