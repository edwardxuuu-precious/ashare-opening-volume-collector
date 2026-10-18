"""Independent, resumable daily whole-market descriptive calculation.

No opening-volume collector state is read or changed. Real data stays in private
S3. The currently committed slot remains readable until manifest-last commit.
"""
from __future__ import annotations
import argparse,base64,concurrent.futures,gzip,hashlib,json,os,time,uuid
from collections import Counter
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from itertools import zip_longest
from .calendar import SOURCES,last_ten,sessions
from .model import MODEL,compare_snapshots,estimate_snapshots,prepare_eastmoney
from .screen import board,prefilter
from .source import Source

def now(): return datetime.now(timezone.utc).isoformat()
def encoded(v): return json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()

def shared_universe(store,as_of):
    """Reuse the site's published current-list catalog read-only if EM fails."""
    catalog,etag=store.get('data/stocks.json')
    if not catalog or catalog.get('historicalMembership') is not False: raise ValueError('SHARED_UNIVERSE_UNAVAILABLE')
    age=(datetime.fromisoformat(as_of).date()-datetime.fromisoformat(catalog['asOf']).date()).days
    rows=catalog.get('rows')
    if not -2<=age<=7 or not isinstance(rows,list) or not 1000<len(rows)<15000 or len(rows)!=catalog.get('total'):
        raise ValueError('SHARED_UNIVERSE_STALE_OR_INCOMPLETE')
    seen=set();stocks=[];markets=set()
    for r in rows:
        code=r['code'];name=r['name'];market,_=board(code)
        if len(code)!=6 or not code.isdigit() or code in seen or not isinstance(name,str) or not name.strip():raise ValueError('SHARED_UNIVERSE_INVALID')
        seen.add(code);markets.add(market);stocks.append({'code':code,'name':name,'secid':('1' if code.startswith('6') else '0')+'.'+code,'listed':None})
    if markets!={'MAIN','STAR','CHINEXT','BSE'}:raise ValueError('SHARED_UNIVERSE_MARKET_MISSING')
    return stocks,[{'provider':'SITE_PUBLISHED_CURRENT_UNIVERSE','key':'data/stocks.json','etag':etag,
        'as_of':catalog['asOf'],'canonical_sha256':hashlib.sha256(encoded(catalog)).hexdigest(),'cache_age_days':age,'historical_membership':False}]

class Storage:
    def __init__(self,bucket,region):
        import boto3
        self.s3=boto3.client('s3',region_name=region);self.bucket=bucket
    def get(self,key):
        from botocore.exceptions import ClientError
        try:
            r=self.s3.get_object(Bucket=self.bucket,Key=key);raw=r['Body'].read()
            return json.loads(gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw),r['ETag']
        except ClientError as e:
            if e.response['Error']['Code'] in ('NoSuchKey','404'): return None,None
            raise
    def put(self,key,value,*,match=None,new=False,compressed=False):
        raw=encoded(value);args={'Bucket':self.bucket,'Key':key,'Body':gzip.compress(raw,mtime=0) if compressed else raw,
            'ContentType':'application/json; charset=utf-8','CacheControl':'no-store, private','ServerSideEncryption':'AES256'}
        if compressed: args['ContentEncoding']='gzip'
        if match: args['IfMatch']=match
        if new: args['IfNoneMatch']='*'
        return self.s3.put_object(**args)['ETag']

def frame(stock,raw,receipt,days,pools,dataset,actual_limits=None,model_state=None):
    lines=(json.loads(raw).get('data') or {}).get('klines') or []
    row={'security_id':stock['code'],'name':stock['name'],'market':board(stock['code'])[0],
         **prefilter(stock,lines,days,pools,actual_limits),'chip_status':'MISSING','peak_prices_cny':[],
         'previous_peak_prices_cny':[],'original_peak_changes':[]}
    result={'schema':'stock.chips.market.stock.v1','dataset_id':dataset,'as_of':days[-1],
        'security_id':stock['code'],'name':stock['name'],'model':MODEL,'unchanged_threshold':None,
        'chip_status':'MISSING','snapshots':{},'history':[],'source':receipt,'known_at':None,
        'recorded_at':now(),'historical_PIT_authenticated':False,'reasons':[]}
    try:
        bars,audit=prepare_eastmoney(raw,expected_code=stock['code'],cutoff=days[-1],allow_valid_suffix=True)
        result['source_audit']=audit
        result['history']=[{'date':b.source_date,'open':str(b.open),'high':str(b.high),'low':str(b.low),
            'close':str(b.close),'volume_shares':str(b.volume_shares),'amount_cny':str(b.amount_cny),
            'turnover_percent':str(b.turnover_pct)} for b in bars[-500:]]
        if model_state and model_state.get('last_date','9999')>days[-2]:model_state=None
        try:
            snapshots,next_state=estimate_snapshots(bars,days[-2:],security_id=stock['code'],state=model_state,export_state=True)
        except ValueError as e:
            if not model_state:raise
            snapshots,next_state=estimate_snapshots(bars,days[-2:],security_id=stock['code'],export_state=True)
            row['reasons'].append('MODEL_CACHE_REBUILT:'+str(e)[:100])
        result['_model_state']=next_state
        result.update(snapshots=snapshots,chip_status='READY')
        comparison=compare_snapshots(snapshots[days[-2]],snapshots[days[-1]])
        row.update(chip_status='READY',peak_prices_cny=comparison['current_peak_prices_cny'],
            previous_peak_prices_cny=comparison['previous_peak_prices_cny'],
            original_peak_changes=[{'price_cny':r['anchor_price_cny'],'previous_share_percent':r['previous_share_percent'],
                'current_share_percent':r['current_same_price_share_percent'],'change_percentage_points':r['change_percentage_points']} for r in comparison['anchors']])
        if audit['invalid_source_rows']: row['reasons'].append('MODEL_RESEEDED_AFTER_INVALID_SOURCE_HISTORY')
    except (ValueError,ArithmeticError) as e:
        result['reasons'].append(str(e)[:200]);row['reasons'].append('CHIP_MODEL_INPUT_INCOMPLETE')
    result['reasons']+=row['reasons']
    return row,result

def target_day(explicit=None):
    cn=datetime.now(timezone(timedelta(hours=8)))
    eligible=cn.date() if (cn.hour,cn.minute)>=(16,10) else cn.date()-timedelta(days=1)
    if explicit:
        if explicit>eligible.isoformat(): raise ValueError('SESSION_NOT_COMPLETED')
        last_ten(explicit);return explicit
    return sessions('2026-01-01',eligible.isoformat())[-1]

def run(store,*,as_of,max_minutes=285,workers=4):
    started=time.monotonic();deadline=started+max_minutes*60;owner=uuid.uuid4().hex
    lease,etag=store.get('collector/chips/lease.json')
    if lease and lease.get('expires',0)>time.time(): return {'outcome':'LEASE_HELD','as_of':as_of}
    lease_expires=time.time()+max_minutes*60+600
    lease_etag=store.put('collector/chips/lease.json',{'owner':owner,'expires':lease_expires},match=etag,new=etag is None)
    try:
        current,current_etag=store.get('data/chips/daily/manifest.json')
        if current and current['as_of']==as_of:
            return {'outcome':'ALREADY_COMMITTED','as_of':as_of,'market_complete':current['market_complete']}
        state,state_etag=store.get('collector/chips/state.json');source=Source()
        if state and state['as_of']<as_of and len(state['rows'])<len(state['stocks']):
            # Finish the first bootstrap rather than discarding its progress
            # every afternoon. Subsequent sessions reuse its exact model states.
            as_of=state['as_of']
        if state and state['as_of']>as_of: raise ValueError('WOULD_REPLACE_NEWER_BATCH')
        if not state or state['as_of']!=as_of:
            try:stocks,universe_receipts=source.universe()
            except Exception as exc:
                stocks,universe_receipts=shared_universe(store,as_of)
                universe_receipts[0]['em_refresh_error']=type(exc).__name__
            days=last_ten(as_of);pools,pool_receipts=source.pools(days)
            actual_limits={};limit_receipts=[]
            for day in days:
                value,_=store.get(f'collector/chips/limits/{day}.json')
                if value: actual_limits[day]=value['quotes']
            if as_of not in actual_limits:
                quotes,limit_receipts=source.actual_limits(stocks,as_of)
                if quotes:
                    store.put(f'collector/chips/limits/{as_of}.json',{'quotes':quotes,'receipts':limit_receipts},new=True,compressed=True)
                    actual_limits[as_of]=quotes
            dataset=hashlib.sha256(encoded([as_of,MODEL,stocks,now()])).hexdigest()[:16]
            state={'as_of':as_of,'dataset_id':dataset,'active_slot':dataset,
                'days':days,'stocks':stocks,'pools':pools,'universe_receipts':universe_receipts,'pool_receipts':pool_receipts,
                'actual_limits':actual_limits,'limit_receipts':limit_receipts,'rows':{},'started_at':now()}
            state_etag=store.put('collector/chips/state.json',state,match=state_etag,new=state_etag is None,compressed=True)
        dataset=state['dataset_id'];slot=state['active_slot'];days=state['days'];done=state['rows']
        def assert_owned():
            lease_now,_=store.get('collector/chips/lease.json')
            if not lease_now or lease_now['owner']!=owner or lease_now['expires']<=time.time():
                raise RuntimeError('CHIP_WRITER_LEASE_LOST')
        def checkpoint():
            nonlocal state_etag,lease_etag,lease_expires
            assert_owned()
            lease_expires=time.time()+600
            lease_etag=store.put('collector/chips/lease.json',{'owner':owner,'expires':lease_expires},match=lease_etag)
            state_etag=store.put('collector/chips/state.json',state,match=state_etag,compressed=True)
        def save_status(status):
            store.put('data/chips/daily/status.json',{'schema':'stock.chips.market.status.v1','state':status,
                'as_of':as_of,'dataset_id':dataset,'universe_total':len(state['stocks']),'scanned_count':len(done),
                'model_ready_count':sum(r['chip_status']=='READY' for r in done.values()),'updated_at':now(),
                'last_committed_date':current['as_of'] if current else None,'unchanged_threshold':None})
        save_status('RUNNING')
        # Stratify initial reads so one failing exchange cannot trigger a global
        # provider circuit-breaker before any other exchange has been sampled.
        groups={m:[] for m in ('MAIN','CHINEXT','STAR','BSE')}
        for stock in state['stocks']:
            if stock['code'] not in done:groups[board(stock['code'])[0]].append(stock)
        pending=[s for group in zip_longest(*groups.values()) for s in group if s is not None]
        def calculate(stock):
            key=f'data/chips/daily/{slot}/{stock["code"]}.json'
            existing,_=store.get(key)
            if existing:
                if existing.get('dataset_id')!=dataset or existing.get('security_id')!=stock['code']:
                    raise RuntimeError('IMMUTABLE_FRAME_CONFLICT')
                return existing['screening_row']
            try:
                raw_key=f'collector/chips/raw/{dataset}/{stock["code"]}.json'
                saved,_=store.get(raw_key)
                if saved:
                    receipt=saved['receipt'];raw=base64.b64decode(saved['raw_base64'],validate=True)
                    if hashlib.sha256(raw).hexdigest()!=receipt['sha256']: raise ValueError('RAW_HASH_MISMATCH')
                else:
                    _,receipt,raw=source.history(stock,as_of)
                    if time.time()>=lease_expires: raise RuntimeError('CHIP_WRITER_LEASE_EXPIRED')
                    store.put(raw_key,{'receipt':receipt,'raw_base64':base64.b64encode(raw).decode()},new=True,compressed=True)
                cache_key=f'collector/chips/model/{stock["code"]}.json'
                cache,cache_etag=store.get(cache_key)
                row,value=frame(stock,raw,receipt,days,state['pools'],dataset,state.get('actual_limits'),cache)
                next_state=value.pop('_model_state',None)
                if next_state and (not cache or next_state['last_date']>=cache['last_date']):
                    if time.time()>=lease_expires:raise RuntimeError('CHIP_WRITER_LEASE_EXPIRED')
                    store.put(cache_key,next_state,match=cache_etag,new=cache_etag is None,compressed=True)
            except Exception as e:
                row={'security_id':stock['code'],'name':stock['name'],'market':board(stock['code'])[0],
                    'close_cny':None,'prefilter_status':'UNKNOWN','recent_limit_up_dates':[],
                    'chip_status':'MISSING','peak_prices_cny':[],'previous_peak_prices_cny':[],'original_peak_changes':[],
                    'reasons':['SOURCE_OR_MODEL_ERROR:'+type(e).__name__]}
                value={'schema':'stock.chips.market.stock.v1','dataset_id':dataset,'as_of':as_of,
                    'security_id':stock['code'],'name':stock['name'],'model':MODEL,'unchanged_threshold':None,
                    'chip_status':'MISSING','snapshots':{},'history':[],'reasons':row['reasons']}
            if time.time()>=lease_expires: raise RuntimeError('CHIP_WRITER_LEASE_EXPIRED')
            value['screening_row']=row
            store.put(key,value,new=True,compressed=True)
            return row
        # Submit only a bounded window so expiry cannot leave thousands of queued reads.
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            active={};iterator=iter(pending)
            def fill():
                while len(active)<workers and time.monotonic()<deadline-90:
                    try: stock=next(iterator)
                    except StopIteration: break
                    active[pool.submit(calculate,stock)]=stock['code']
            fill();saved_at=time.monotonic()
            while active:
                finished,_=concurrent.futures.wait(active,return_when=concurrent.futures.FIRST_COMPLETED)
                for future in finished:
                    code=active.pop(future);done[code]=future.result()
                if len(done)%20<workers or time.monotonic()-saved_at>45:
                    checkpoint()
                    save_status('RUNNING');saved_at=time.monotonic()
                    print(json.dumps({'outcome':'PROGRESS','as_of':as_of,'scanned':len(done),'total':len(state['stocks'])}),flush=True)
                if len(done)>=20 and all(r['chip_status']=='MISSING' and r['close_cny'] is None for r in done.values()):
                    checkpoint();save_status('SOURCE_UNAVAILABLE')
                    raise RuntimeError('SOURCE_UNAVAILABLE_STOPPED_AFTER_BOUNDED_PROBE')
                fill()
        checkpoint()
        if len(done)!=len(state['stocks']):
            save_status('PAUSED_BUDGET');return {'outcome':'CHECKPOINT_SAVED','as_of':as_of,'scanned':len(done),'total':len(state['stocks'])}
        rows=sorted(done.values(),key=lambda r:r['security_id']);counts=Counter(r['prefilter_status'] for r in rows)
        ready=sum(r['chip_status']=='READY' for r in rows)
        manifest={'schema':'stock.chips.market.manifest.v1','dataset_id':dataset,'active_slot':slot,'as_of':as_of,
            'previous_date':days[-2],'official_sessions':days,'universe_total':len(rows),'scanned_count':len(rows),
            'model_ready_count':ready,'screen_counts':{k:counts[k] for k in ('PASS','FAIL','UNKNOWN')},
            'market_complete':ready==len(rows) and counts['UNKNOWN']==0 and all(r.get('cache_age_days',0)==0 for r in state['universe_receipts']),'coefficient':2,'unchanged_threshold':None,
            'generated_at':now(),'calendar_source':SOURCES[0],'calendar_sources':SOURCES,
            'scope':'Current listed SH/SZ/BJ provider universe; historical delistings not included',
            'limit_evidence':'Vendor closing-limit pool matched to exact unadjusted daily close; uncertain candidates remain UNKNOWN',
            'universe_receipts':state['universe_receipts'],'pool_receipts':state['pool_receipts']}
        assert_owned()
        screening={'schema':'stock.chips.market.screen.v1','dataset_id':dataset,'as_of':as_of,'rows':rows,'unchanged_threshold':None}
        screen_key=f'data/chips/daily/{slot}/screening.json'
        existing_screen,_=store.get(screen_key)
        if existing_screen is None: store.put(screen_key,screening,new=True,compressed=True)
        elif existing_screen!=screening: raise RuntimeError('IMMUTABLE_SCREEN_CONFLICT')
        store.put('data/chips/daily/manifest.json',manifest,match=current_etag,new=current_etag is None)
        save_status('COMMITTED')
        return {'outcome':'COMMITTED','as_of':as_of,'total':len(rows),'model_ready':ready,'screen_counts':manifest['screen_counts'],'market_complete':manifest['market_complete']}
    finally:
        latest,latest_etag=store.get('collector/chips/lease.json')
        if latest and latest.get('owner')==owner:
            store.put('collector/chips/lease.json',{'owner':owner,'expires':0},match=latest_etag)

def main():
    p=argparse.ArgumentParser();p.add_argument('--bucket',required=True);p.add_argument('--region',default='us-east-1')
    p.add_argument('--as-of');p.add_argument('--max-minutes',type=int,default=285);a=p.parse_args()
    if not 2<=a.max_minutes<=285: p.error('max-minutes must be 2..285')
    store=Storage(a.bucket,a.region);target=target_day(a.as_of);started=time.monotonic()
    value=run(store,as_of=target,max_minutes=a.max_minutes)
    remaining=int((a.max_minutes*60-(time.monotonic()-started))/60)
    if value.get('outcome')=='COMMITTED' and value['as_of']<target and remaining>=3:
        value=run(store,as_of=target,max_minutes=remaining)
    print(json.dumps(value,ensure_ascii=False),flush=True)
if __name__=='__main__': main()
