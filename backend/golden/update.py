"""Independent after-close update for the fixed twenty drawing examples.

One lease, bounded source reads and resumable per-symbol caches. Does not read
or modify chip manifests, chip state or shared annotations. All timestamps UTC;
session selection and advertised schedule use Asia/Shanghai (UTC+8).
"""
import argparse,json,os,time,uuid
from datetime import datetime,timedelta,timezone
from .daily import PREFIX,encoded,implementation,make_case,publish_cases,read,put,sha
from .watchlist import WATCHLIST
from .anchors import validate
from .sources import DailySources
from backend.chips.calendar import sessions

TZ=timezone(timedelta(hours=8))
TIMES=((15,45),(16,15),(17,15),(20,15))
STATUS_KEY=PREFIX+'status.json'
LEASE_KEY=PREFIX+'update-lease.json'


def target_session(now,requested=None):
    local=now.astimezone(TZ);today=local.date()
    end=today if (local.hour,local.minute)>=(15,30) else today-timedelta(days=1)
    days=sessions(f'{end.year}-01-01',end.isoformat())
    if not days:raise ValueError('NO_COMPLETED_SESSION')
    target=requested or days[-1]
    if target not in days:raise ValueError('TARGET_NOT_A_COMPLETED_SESSION')
    return target


def next_run(now):
    local=now.astimezone(TZ)
    for i in range(14):
        day=local.date()+timedelta(days=i)
        try:open_day=bool(sessions(day.isoformat(),day.isoformat()))
        except ValueError:return None
        if open_day:
            for h,m in TIMES:
                candidate=datetime(day.year,day.month,day.day,h,m,tzinfo=TZ)
                if candidate>local:return candidate.isoformat()
    return None


def run(s3,bucket,*,as_of=None,source_factory=DailySources,clock=lambda:datetime.now(timezone.utc),max_seconds=900):
    from botocore.exceptions import ClientError
    began=clock();target=target_session(began,as_of);monotonic=time.monotonic()
    latest,_=read(s3,bucket,PREFIX+'manifest.json')
    if latest and latest['as_of']>target:raise ValueError('WOULD_REPLACE_NEWER_GOLDEN_DATE')
    lease,etag=read(s3,bucket,LEASE_KEY);owner=uuid.uuid4().hex
    if lease and lease.get('expires',0)>began.timestamp():return dict(outcome='LEASE_HELD',as_of=target)
    try:
        put(s3,bucket,LEASE_KEY,dict(owner=owner,expires=began.timestamp()+1200),**({'IfMatch':etag} if etag else {'IfNoneMatch':'*'}))
    except ClientError as exc:
        if exc.response['Error']['Code'] in ('PreconditionFailed','ConditionalRequestConflict'):return dict(outcome='LEASE_HELD',as_of=target)
        raise
    prior,status_etag=read(s3,bucket,STATUS_KEY);source=None;cases=[];ready=0;status=None

    def owned():
        current,_=read(s3,bucket,LEASE_KEY)
        if not current or current.get('owner')!=owner or current.get('expires',0)<=clock().timestamp():raise RuntimeError('LEASE_LOST')

    def state(value,processed,error=None,publication=None):
        nonlocal status,status_etag
        owned();stamp=clock()
        good=[c for c in cases if not c.get('error') and c.get('result') and c['freshness'] in ('CURRENT','CONFIRMED_NO_TRADE')]
        published=publication or latest
        if publication and 'generated_at' not in published:
            published,_=read(s3,bucket,PREFIX+'manifest.json')
        status=dict(schema='stock.golden.update.v1',run_id=owner,state=value,target_date=target,
            data_as_of=published['as_of'] if published else None,dataset_id=published['dataset_id'] if published else None,
            data_generated_at=published.get('generated_at') if published else None,
            total=20,processed=processed,ready=len(good),started_at=began.isoformat(),updated_at=stamp.isoformat(),
            lease_expires_at=datetime.fromtimestamp(began.timestamp()+1200,timezone.utc).isoformat(),
            finished_at=stamp.isoformat() if value!='RUNNING' else None,
            last_success_at=stamp.isoformat() if value=='COMPLETE' else (prior or {}).get('last_success_at'),
            error=error,missing=[dict(symbol=c['symbol'],name=c['name'],reason=c.get('error') or c['freshness']) for c in cases if c not in good],
            schedule=dict(timezone='Asia/Shanghai',first='15:45',retries=['16:15','17:15','20:15'],next_run_at=next_run(stamp)),
            run_url=f'https://github.com/edwardxuuu-precious/ashare-opening-volume-collector/actions/runs/{os.environ["GITHUB_RUN_ID"]}' if os.environ.get('GITHUB_RUN_ID','').isdigit() else None)
        try:
            response=put(s3,bucket,STATUS_KEY,status,**({'IfMatch':status_etag} if status_etag else {'IfNoneMatch':'*'}))
        except ClientError as exc:
            if exc.response['Error']['Code'] not in ('PreconditionFailed','ConditionalRequestConflict'):raise
            # A new owner can take over after lease expiry. A stale writer
            # must not overwrite its status even if the prior lease read raced.
            current,current_etag=read(s3,bucket,STATUS_KEY);owned()
            if current and current.get('run_id')!=owner and current.get('started_at','')>=began.isoformat():raise RuntimeError('STATUS_REPLACED_BY_NEW_RUN')
            response=put(s3,bucket,STATUS_KEY,status,**({'IfMatch':current_etag} if current_etag else {'IfNoneMatch':'*'}))
        status_etag=response['ETag']
    try:
        state('RUNNING',0)
        source=source_factory()
        for code,name in WATCHLIST:
            try:
                owned()
                if time.monotonic()-monotonic>max_seconds:raise TimeoutError('UPDATE_TIME_BUDGET')
                key=PREFIX+f'source/{target}/{code}.json'
                cached,cached_etag=read(s3,bucket,key)
                if cached:
                    try:
                        if cached['symbol']!=code or cached['as_of']!=target:raise ValueError('CACHED_SOURCE_IDENTITY')
                        rows,receipt,no_trade=cached['history'],cached['source'],cached.get('confirmed_no_trade_sessions',[])
                        if receipt.get('provider')!='BAOSTOCK_0.9.3':no_trade=[d for d in no_trade if d!=target]
                        if sha(encoded(rows))!=cached['history_sha256']:raise ValueError('CACHED_HISTORY_HASH')
                        validate(rows)
                        if len(rows)<60 or (rows[-1]['date']!=target and target not in no_trade):raise ValueError('TARGET_SESSION_NOT_READY')
                    except (ValueError,KeyError,TypeError,IndexError):
                        # An old placeholder or invalid cache is a missing
                        # sample, not a permanent reason to skip fresh reads.
                        # Replace only its pointer with CAS; original raw
                        # objects and published datasets remain immutable.
                        cached=None
                if not cached:
                    old=None
                    if latest:old,_=read(s3,bucket,PREFIX+latest['dataset_id']+'/'+code+'.json')
                    rows,receipt,raw,no_trade=source.history(code,name,target,previous=old)
                    if receipt.get('provider')!='BAOSTOCK_0.9.3':no_trade=[d for d in no_trade if d!=target]
                    validate(rows)
                    if len(rows)<60 or (rows[-1]['date']!=target and target not in no_trade):raise ValueError('TARGET_SESSION_NOT_READY')
                    raw_key=PREFIX+f'source/{target}/{code}-'+sha(raw)[:16]+'.raw.json'
                    owned()
                    try:s3.put_object(Bucket=bucket,Key=raw_key,Body=raw,ContentType='application/json; charset=utf-8',IfNoneMatch='*')
                    except ClientError as exc:
                        if exc.response['Error']['Code']!='PreconditionFailed':raise
                    receipt=dict(receipt,raw_key=raw_key,raw_sha256=sha(raw),captured_at=clock().isoformat())
                    cached=dict(symbol=code,as_of=target,history=rows,source=receipt,history_sha256=sha(encoded(rows)),confirmed_no_trade_sessions=no_trade)
                    put(s3,bucket,key,cached,**({'IfMatch':cached_etag} if cached_etag else {'IfNoneMatch':'*'}))
                validate(rows)
                if len(rows)<60 or (rows[-1]['date']!=target and target not in no_trade):raise ValueError('TARGET_SESSION_NOT_READY')
                value=make_case(code,name,rows,target,receipt)
                if target in no_trade and rows[-1]['date']!=target:value['freshness']='CONFIRMED_NO_TRADE'
            except Exception as exc:
                # Keep the last observed candles available with an explicit
                # stale/error label, rather than fabricate a target-day row.
                old=None
                if latest:old,_=read(s3,bucket,PREFIX+latest['dataset_id']+'/'+code+'.json')
                value=make_case(code,name,(old or {}).get('history',[]),target,(old or {}).get('source'))
                value['error']=type(exc).__name__+':'+str(exc)[:220]
            cases.append(value);state('RUNNING',len(cases))
        ready=sum(not c.get('error') and bool(c.get('result')) and c['freshness'] in ('CURRENT','CONFIRMED_NO_TRADE') for c in cases)
        if not ready:
            state('FAILED',20,error='所有样本的目标日行情均未就绪，保留上次已发布数据')
            return dict(outcome='FAILED',as_of=target,ready=0,total=20)
        owned()
        source_id=sha(encoded(dict(as_of=target,inputs=[(c['symbol'],(c.get('source') or {}).get('raw_sha256'),c['error']) for c in cases])))[:16]
        result=publish_cases(s3,bucket,dict(as_of=target,dataset_id=source_id),cases)
        state('COMPLETE' if ready==20 else 'PARTIAL',20,publication=result)
        return dict(result,ready=ready,total=20,state=status['state'])
    except Exception as exc:
        try:state('FAILED',len(cases),error=type(exc).__name__+':'+str(exc)[:220])
        except Exception:pass
        raise
    finally:
        if source:source.close()
        current,current_etag=read(s3,bucket,LEASE_KEY)
        if current and current.get('owner')==owner:put(s3,bucket,LEASE_KEY,dict(owner=owner,expires=0),IfMatch=current_etag)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bucket',default=os.environ.get('STOCK_BUCKET'));p.add_argument('--profile');p.add_argument('--as-of');args=p.parse_args()
    if not args.bucket:p.error('bucket required')
    import boto3
    from botocore.config import Config
    s3=boto3.Session(profile_name=args.profile,region_name='us-east-1').client('s3',config=Config(connect_timeout=7,read_timeout=25,retries={'total_max_attempts':1}))
    outcome=run(s3,args.bucket,as_of=args.as_of);print(json.dumps(outcome,ensure_ascii=False))
    if outcome.get('state')=='PARTIAL' or outcome.get('outcome')=='FAILED':raise SystemExit(2)
