"""Publish only 20 daily automatic drawing examples; no model or screen changes.

Nineteen stocks reuse the committed private daily frames. Shanghai Composite
uses its explicit sh.000001 identity (never the 000001 Ping An equity).
Immutable data first, latest pointer last with ETag CAS. Reruns of the same
source batch/implementation do no work. Failures remain per-example UNKNOWN.
"""
import argparse,gzip,hashlib,json,os
from pathlib import Path
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from .watchlist import WATCHLIST
from .automatic import evaluate, POLICY, policy_sha
from .anchors import validate

PREFIX='data/golden-workbench/'
VERSION='golden-workbench.auto20.v1'


def encoded(v): return json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()
def sha(v): return hashlib.sha256(v).hexdigest()
def implementation():
    return sha(b''.join((Path(__file__).parent/n).read_text(encoding='utf-8').replace('\r\n','\n').encode() for n in ('daily.py','watchlist.py','anchors.py','automatic.py','sources.py','update.py')))


def read(s3,bucket,key):
    from botocore.exceptions import ClientError
    try:
        obj=s3.get_object(Bucket=bucket,Key=key);raw=obj['Body'].read()
        return json.loads(gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw),obj['ETag']
    except ClientError as exc:
        if exc.response['Error']['Code']=='NoSuchKey': return None,None
        raise


def put(s3,bucket,key,value,**conditional):
    return s3.put_object(Bucket=bucket,Key=key,Body=gzip.compress(encoded(value),mtime=0),
        ContentType='application/json; charset=utf-8',ContentEncoding='gzip',**conditional)


def index_history(as_of):
    from backend.scripts.baostock_source import BaoStockSession
    fields='date,code,open,high,low,close,volume,amount'
    start=(datetime.fromisoformat(as_of)-timedelta(days=450)).date().isoformat()
    with BaoStockSession(timeout=18,interval=.8) as session:
        session._ensure_connected()
        response=session._call(session._sdk.query_history_k_data_plus,'sh.000001',fields,
            start_date=start,end_date=as_of,frequency='d')
        if response.error_code!='0' or response.fields!=fields.split(','): raise ValueError('INDEX_FIELDS')
        raw=[]
        while session._call(response.next):
            if len(raw)>=400: raise ValueError('INDEX_ROWS_BOUND')
            raw.append(list(response.get_row_data()))
        if response.error_code!='0': raise ValueError('INDEX_INCOMPLETE')
    rows=[]
    for values in raw:
        r=dict(zip(fields.split(','),values))
        if r['code']!='sh.000001' or r['date']>as_of: raise ValueError('INDEX_IDENTITY')
        rows.append(dict(date=r['date'],**{k:r[k] for k in ('open','high','low','close')},
            volume_shares=r['volume'],amount_cny=r['amount'],turnover_percent='0'))
    validate(rows)
    return rows,dict(provider='BAOSTOCK_INDEX',symbol='sh.000001',price_unit='INDEX_POINTS',
        volume_unit='PROVIDER_REPORTED_SHARES',turnover_available=False,adjustment='INDEX_NOT_ADJUSTED',
        source_fields=fields,source_sha256=sha(encoded(raw)),historical_known_at=None,
        original_sdk_response=dict(fields=fields.split(','),rows=raw,start_date=start,end_date=as_of,symbol='sh.000001'))


def make_case(code,name,history,as_of,source):
    history=[r for r in history if r['date']<=as_of][-120:]
    validate(history)
    for row in history:
        volume=Decimal(str(row['volume_shares']))
        if not volume.is_finite() or volume<0: raise ValueError('VOLUME')
    result=evaluate(history[-60:]) if len(history)>=60 else None
    if result:
        # These are local-computation counters in the reference wrapper, not
        # publication claims. Omit them from the website's data contract.
        for k in ('production_changes','full_market_runs','performance_runs'): result.pop(k,None)
    return dict(symbol=code,name=name,kind='INDEX' if code=='sh000001' else 'EQUITY',
        as_of=as_of,last_bar_date=history[-1]['date'] if history else None,
        freshness='CURRENT' if history and history[-1]['date']==as_of else 'STALE_OR_NO_TRADE',
        calculation_rows=min(60,len(history)),window_start=history[-60]['date'] if len(history)>=60 else None,
        history=history,result=result,error=None if result else 'INSUFFICIENT_60_ROWS',source=source)


def publish(s3,bucket,index_reader=index_history):
    source,_=read(s3,bucket,'data/chips/daily/manifest.json')
    if not source: raise ValueError('CHIP_BATCH_MISSING')
    latest,etag=read(s3,bucket,PREFIX+'manifest.json')
    identity=implementation()
    if latest and latest.get('source_dataset_id')==source['dataset_id'] and latest.get('implementation_sha256')==identity and all(not r.get('error') and (r['symbol']!='sh000001' or r['freshness']=='CURRENT') for r in latest['rows']):
        return dict(outcome='ALREADY_CURRENT',as_of=latest['as_of'],dataset_id=latest['dataset_id'])
    as_of=source['as_of'];cases=[]
    if latest and latest['as_of']>as_of:
        return dict(outcome='NEWER_INDEPENDENT_BATCH_EXISTS',as_of=latest['as_of'],dataset_id=latest['dataset_id'])
    for code,name in WATCHLIST:
        try:
            if code=='sh000001':
                cache_key=PREFIX+'source/index/'+as_of+'.json'
                cached,_=read(s3,bucket,cache_key)
                if cached:
                    if cached['symbol']!='sh.000001' or cached['as_of']!=as_of: raise ValueError('INDEX_CACHE_IDENTITY')
                    rows,receipt=cached['history'],cached['source']
                    validate(rows)
                    if not rows or rows[-1]['date']!=as_of or sha(encoded(rows))!=cached['history_sha256']: raise ValueError('INDEX_CACHE_DATA')
                else:
                    rows,receipt=index_reader(as_of)
                    validate(rows)
                    if rows and rows[-1]['date']==as_of:
                        put(s3,bucket,cache_key,dict(symbol='sh.000001',as_of=as_of,history=rows,
                            source=receipt,history_sha256=sha(encoded(rows))),IfNoneMatch='*')
            else:
                frame,_=read(s3,bucket,f'data/chips/daily/{source["dataset_id"]}/{code}.json')
                if not frame or frame.get('security_id')!=code or frame.get('as_of')!=as_of: raise ValueError('FRAME_IDENTITY')
                rows=frame['history'];receipt=dict(provider='COMMITTED_CHIP_DAILY_FRAME',
                    dataset_id=source['dataset_id'],adjustment='UNADJUSTED',source_sha256=sha(encoded(rows)),historical_known_at=None)
            value=make_case(code,name,rows,as_of,receipt)
        except Exception as exc:
            value=dict(symbol=code,name=name,kind='INDEX' if code=='sh000001' else 'EQUITY',
                as_of=as_of,last_bar_date=None,freshness='UNKNOWN',calculation_rows=0,window_start=None,
                history=[],result=None,error=type(exc).__name__+':'+str(exc)[:160],source=None)
        cases.append(value)
    # The legacy chip-dependent entry point is retained only for old callers.
    # New scheduled publication goes through the independent 20-example runner.
    current,_=read(s3,bucket,'data/chips/daily/manifest.json')
    if current['dataset_id']!=source['dataset_id']: raise ValueError('SOURCE_ADVANCED_RETRY_NEXT_RUN')
    return publish_cases(s3,bucket,source,cases)


def publish_cases(s3,bucket,source,cases):
    as_of=source['as_of'];identity=implementation()
    latest,etag=read(s3,bucket,PREFIX+'manifest.json')
    if latest and latest['as_of']>as_of: raise ValueError('WOULD_REPLACE_NEWER_GOLDEN_DATE')
    if len(cases)!=20 or [v['symbol'] for v in cases]!=[v[0] for v in WATCHLIST]: raise ValueError('EXACT_TWENTY_CASES_REQUIRED')
    dataset=sha(encoded(dict(source=source['dataset_id'],implementation=identity,cases=cases)))[:16]
    if latest and latest['dataset_id']==dataset:
        return dict(outcome='ALREADY_CURRENT',as_of=as_of,dataset_id=dataset)
    summaries=[]
    for case in cases:
        case.update(schema='stock.golden.example.v1',dataset_id=dataset,version=VERSION,policy_sha256=policy_sha())
        key=PREFIX+dataset+'/'+case['symbol']+'.json'
        existing,_=read(s3,bucket,key)
        if existing is None: put(s3,bucket,key,case,IfNoneMatch='*')
        elif existing!=case: raise ValueError('IMMUTABLE_EXAMPLE_CONFLICT')
        r=case['result']
        summaries.append({k:case[k] for k in ('symbol','name','kind','as_of','last_bar_date','freshness','error','window_start')}|
            dict(history=case['history'][-60:],H=r['H'] if r else None,L=r['L'] if r else None,
                M_count=len(r['price_groups']) if r else 0,formation_count=r['retained_current_H_records'] if r else 0))
    archive=[a for a in (latest or {}).get('archives',[]) if a['dataset_id']!=dataset]
    stamp=datetime.now(timezone.utc).isoformat()
    archive.append(dict(as_of=as_of,dataset_id=dataset,generated_at=stamp))
    manifest=dict(schema='stock.golden.watchlist.v1',version=VERSION,dataset_id=dataset,source_dataset_id=source['dataset_id'],
        as_of=as_of,generated_at=stamp,implementation_sha256=identity,
        policy=POLICY,policy_sha256=policy_sha(),calculation_window=60,rows=summaries,archives=archive,
        owner_fidelity='NOT_VERIFIED',automatic_axis_winner=None,trading_rules=None)
    previous_manifest,_=read(s3,bucket,PREFIX+dataset+'/manifest.json')
    if previous_manifest is None: put(s3,bucket,PREFIX+dataset+'/manifest.json',manifest,IfNoneMatch='*')
    else:
        if previous_manifest['rows']!=manifest['rows'] or previous_manifest['implementation_sha256']!=identity: raise ValueError('IMMUTABLE_MANIFEST_CONFLICT')
        manifest=previous_manifest
    put(s3,bucket,PREFIX+'manifest.json',manifest,**({'IfMatch':etag} if etag else {'IfNoneMatch':'*'}))
    return dict(outcome='PUBLISHED',as_of=as_of,dataset_id=dataset,examples=len(cases),
        available=sum(c['result'] is not None for c in cases),H=sum(bool(c['H']) for c in summaries),HL=sum(bool(c['H'] and c['L']) for c in summaries))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bucket',default=os.environ.get('STOCK_BUCKET'));p.add_argument('--profile');p.add_argument('--prepare-dir',type=Path);p.add_argument('--index-input',type=Path);args=p.parse_args()
    if not args.bucket: p.error('bucket required')
    import boto3
    from botocore.config import Config
    client=boto3.Session(profile_name=args.profile,region_name='us-east-1').client('s3',config=Config(connect_timeout=7,read_timeout=25,retries={'total_max_attempts':1}))
    if args.prepare_dir:
        # Preparation is read-only to AWS; writes only a local, reviewable bundle.
        from io import BytesIO
        from botocore.exceptions import ClientError
        upstream=client;folder=args.prepare_dir
        class Prepare:
            def get_object(self,Bucket,Key):
                path=folder/Key
                if path.is_file():
                    raw=path.read_bytes();return dict(Body=BytesIO(raw),ETag='"'+sha(raw)+'"')
                if Key.startswith(PREFIX): raise ClientError({'Error':{'Code':'NoSuchKey'}},'GetObject')
                obj=upstream.get_object(Bucket=Bucket,Key=Key);raw=obj['Body'].read();path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
                return dict(Body=BytesIO(raw),ETag=obj['ETag'])
            def put_object(self,Bucket,Key,Body,**kwargs):
                path=folder/Key;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(Body);return dict(ETag='"'+sha(Body)+'"')
        client=Prepare()
    reader=index_history
    if args.index_input:
        if not args.prepare_dir: p.error('index-input is allowed only for local read-only preparation')
        saved=json.loads(args.index_input.read_text(encoding='utf-8'))
        def reader(as_of):
            if saved['symbol']!='sh000001' or saved['last_bar_date']!=as_of: raise ValueError('PINNED_INDEX_DATE')
            receipt=dict(saved['source'],preserved_from_case_sha256=sha(args.index_input.read_bytes()),
                cached_representation='Previously captured exact OHLC/volume strings; initial full SDK body not retained')
            return saved['history'],receipt
    print(json.dumps(publish(client,args.bucket,reader),ensure_ascii=False))
