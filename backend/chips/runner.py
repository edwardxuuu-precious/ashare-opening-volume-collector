"""Independent, resumable daily whole-market descriptive calculation.

No opening-volume collector state is read or changed. Real data stays in private
S3. The currently committed slot remains readable until manifest-last commit.
"""
from __future__ import annotations
import argparse,base64,concurrent.futures,gzip,hashlib,json,os,re,time,uuid
from copy import deepcopy
from collections import Counter
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from itertools import zip_longest
from pathlib import Path
from .calendar import SOURCES,last_ten,sessions
from .eastmoney_model import MODEL,compare_snapshots,estimate_snapshots,prepare_eastmoney
from .screen import board,prefilter,apply_price_position,apply_stability,PRICE_POSITION_RULE,PRICE_FILTER_RULE,CHIP_UNCHANGED_RULE
from .stability import classify as classify_stability,calculation_complete as stability_calculated,THRESHOLDS as STABILITY_THRESHOLDS,POLICY_METADATA as STABILITY_POLICY
from .source import Source
from .golden import VERSION as GOLDEN_VERSION,grid_row as golden_row,document as golden_document
from .current_quote import collect as collect_final_quotes,overlay as overlay_final_quote,renormalize as renormalize_final_quotes
from .listing import trusted_listing
from .display import display_frame,response_upper_bound,MAX_BROWSER_PROFILE_BINS
from .history import archive_keys
from .limit_reference import prepared as reference_prepared,closing_pool,fetch_closing_pool,fetch_reference,corroborate_quantity,archived_corroboration

def listing_context(stock):
    return {'listed':stock.get('listed'),'evidence':stock.get('listing_evidence')}

class _ArchiveConflict(RuntimeError):
    pass

def now(): return datetime.now(timezone.utc).isoformat()
def encoded(v): return json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()

def implementation_identity():
    """Bind resumed batches and exact model caches to their actual source."""
    root=Path(__file__).parent
    hashes={name:hashlib.sha256((root/name).read_text(encoding='utf-8').replace('\r\n','\n').encode()).hexdigest()
        for name in ('runner.py','source.py','baostock_history.py','ths_history.py','listing.py','display.py','model.py','eastmoney_model.py','ohlc4_input.py','current_quote.py','screen.py','calendar.py','limit_reference.py','golden.py','stability.py')}
    transport=root.parent/'scripts'/'baostock_source.py'
    hashes['baostock_source.py']=hashlib.sha256(transport.read_text(encoding='utf-8').replace('\r\n','\n').encode()).hexdigest()
    adapter_hash=hashlib.sha256(encoded({k:hashes[k] for k in ('source.py','baostock_history.py','ths_history.py','baostock_source.py')})).hexdigest()
    model_hash=hashlib.sha256(encoded({k:hashes[k] for k in ('model.py','eastmoney_model.py','ohlc4_input.py')})).hexdigest()
    return {'sha256':hashlib.sha256(encoded(hashes)).hexdigest(),'files':hashes,'model_implementation_sha256':model_hash,
        'history_adapter_sha256':adapter_hash,'current_quote_sha256':hashes['current_quote.py'],'baostock_version':'0.9.3'}

def compatible_state(state,identity):
    return (state.get('execution_identity')==identity and state.get('source_version')==Source.VERSION
        and state.get('active_slot')==state.get('dataset_id') and len(state.get('dataset_id',''))==16)

def compatible_ready_model(previous,identity):
    """One exact source migration, replayed against every stored snapshot.

    The new source only repairs proven negative final nodes. An older READY
    result has no negative final nodes; verify that the new full replay is
    identical before reusing its original calculation identity.
    """
    old=previous.get('execution_identity',{});old_files=old.get('files',{});new_files=identity.get('files',{})
    if old.get('model_implementation_sha256')==identity.get('model_implementation_sha256'):return True
    if not (old.get('model_implementation_sha256')=='3f4e2515c261ea219b187c9803f73a3460c933c813de889c820cd82111956091'
        and old_files.get('eastmoney_model.py')=='41f391357dc7b6f52136e137b561c4c795596abb58b8552eb8c7140f58064743'
        and new_files.get('eastmoney_model.py')=='f95add1f60ec5fe4fdd5c9e9d6ffc8844b91283307b9525c61e8ba18a3529896'
        and all(old_files.get(name)==new_files.get(name) for name in ('model.py','ohlc4_input.py'))):return False
    try:
        from .model import Bar
        bars=[Bar(h['date'],*(Decimal(h[k]) for k in ('open','high','low','close','volume_shares','amount_cny','turnover_percent'))) for h in previous['history']]
        targets=sorted(previous['snapshots']);no_trade={d for s in previous['snapshots'].values() for d in s.get('no_trade_confirmation_dates',[])}
        replay,_=session_snapshots(bars,targets,security_id=previous['security_id'],no_trade=no_trade)
        for day,snapshot in previous['snapshots'].items():
            masses=[Decimal(v) for v in snapshot['raw_node_masses']]
            if len(masses)!=150 or any(not v.is_finite() or v<0 for v in masses):return False
            if replay[day]['roundoff_repairs']:return False
            if any(snapshot.get(k)!=v for k,v in replay[day].items() if k!='roundoff_repairs'):return False
        previous.setdefault('calculation_execution_identity',old)
        previous['roundoff_compatibility']={'from_model_source_sha256':old_files['eastmoney_model.py'],
            'to_model_source_sha256':new_files['eastmoney_model.py'],'all_snapshots_fully_replayed':True,
            'raw_profile_peak_and_input_hash_exact_match':True}
        for snapshot in previous['snapshots'].values():snapshot['roundoff_repairs']=[]
        return True
    except (ValueError,ArithmeticError,KeyError,TypeError):return False

def shared_universe(store,as_of,source=None):
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
    receipts=[{'provider':'SITE_PUBLISHED_CURRENT_UNIVERSE','key':'data/stocks.json','etag':etag,
        'as_of':catalog['asOf'],'canonical_sha256':hashlib.sha256(encoded(catalog)).hexdigest(),'cache_age_days':age,'historical_membership':False}]
    if source:
        try:
            from .listing import sh_sz
            official,official_receipts=sh_sz(source,as_of)
            stocks=sorted(official+[s for s in stocks if board(s['code'])[0]=='BSE'],key=lambda s:s['code'])
            receipts+=official_receipts
            receipts[0]['reconciled_markets']=['SSE','SZSE']
            receipts[0]['unreconciled_markets']=['BSE']
        except Exception as exc:
            receipts[0]['official_reconciliation_error']={'error':type(exc).__name__,'detail':str(exc)[:200]}
    return stocks,receipts

class Storage:
    def __init__(self,bucket,region):
        import boto3
        self.s3=boto3.client('s3',region_name=region);self.bucket=bucket;self.region=region
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


def session_snapshots(bars,targets,*,security_id,no_trade,state=None):
    """Carry model mass only across explicitly confirmed no-trade sessions.

    No candle is inserted. The distribution and last traded close are carried
    with an explicit status; absent source sessions are never assumed halted.
    """
    observed={bar.source_date for bar in bars}
    anchors={}
    no_trade=set(no_trade)
    for day in targets:
        if day in observed:anchors[day]=day;continue
        prior=max((d for d in observed if d<day),default=None)
        if day not in no_trade or not prior:
            raise ValueError('REQUESTED_SOURCE_DATES_MISSING:'+day)
        required=set(sessions(prior,day))-observed
        if not required<=no_trade:raise ValueError('UNCONFIRMED_NO_TRADE_GAP:'+day)
        anchors[day]=prior
    calculated,next_state=estimate_snapshots(bars,sorted(set(anchors.values())),security_id=security_id,state=state,export_state=True)
    snapshots={}
    for day,anchor in anchors.items():
        snapshot=deepcopy(calculated[anchor])
        if day!=anchor:
            snapshot.update(source_date=day,last_trade_date=anchor,turnover_percent='0',effective_replacement='0',
                market_session_status='SOURCE_CONFIRMED_NO_TRADE',close_status='LAST_OBSERVED_TRADE_CLOSE',
                no_trade_confirmation_dates=sorted(d for d in no_trade if anchor<d<=day))
        snapshots[day]=snapshot
    return snapshots,next_state

def frame(stock,raw,receipt,days,pools,dataset,actual_limits=None,model_state=None,reference_evidence=None,closing_pools=None):
    decoded=json.loads(raw)
    lines=(decoded.get('data') or {}).get('klines') or []
    no_trade=decoded.get('confirmed_no_trade_sessions',[])
    screen_stock={**stock,'reference_close_verified':receipt.get('provider')!='THS_UNADJUSTED',
        'confirmed_no_trade_sessions':no_trade}
    row={'security_id':stock['code'],'name':stock['name'],'market':board(stock['code'])[0],
         **prefilter(screen_stock,lines,days,pools,actual_limits,reference_evidence,closing_pools),'chip_status':'MISSING','peak_prices_cny':[],
         'previous_peak_prices_cny':[],'original_peak_changes':[]}
    result={'schema':'stock.chips.market.stock.v1','dataset_id':dataset,'as_of':days[-1],
        'security_id':stock['code'],'name':stock['name'],'model':MODEL,'unchanged_threshold':None,
        'listing_context':listing_context(stock),
        'chip_status':'MISSING','snapshots':{},'history':[],'source':receipt,'known_at':None,
        'recorded_at':now(),'historical_PIT_authenticated':False,'reasons':[]}
    if reference_evidence:result['historical_limit_reference']=reference_evidence
    try:
        model_raw=raw
        if receipt.get('provider') in ('BAOSTOCK_0.9.3','THS_UNADJUSTED'):
            decoded=json.loads(raw);model_raw=encoded({'rc':decoded['rc'],'data':decoded['data']})
        bars,audit=prepare_eastmoney(model_raw,expected_code=stock['code'],cutoff=days[-1],allow_valid_suffix=True)
        if receipt.get('provider')=='BAOSTOCK_0.9.3':
            audit.update(source_provider='BAOSTOCK_0.9.3',source_volume_unit='SHARES',
                volume_conversion='SDK shares / 100 into common hand field, then *100 exactly',
                amount_unit='CNY_AS_DOCUMENTED_BY_BAOSTOCK',confirmed_no_trade_sessions=json.loads(raw).get('confirmed_no_trade_sessions',[]),
                confirmed_no_trade_missing_fields=json.loads(raw).get('confirmed_no_trade_missing_fields',[]),
                original_raw_preserved=False,original_sdk_field_strings_preserved=True)
            audit['original_sdk_bundle_sha256']=hashlib.sha256(raw).hexdigest()
        result['source_audit']=audit
        if decoded.get('current_session_quote_overlay'):
            audit['current_session_quote_overlay']=decoded['current_session_quote_overlay']
            row['reasons'].append('CURRENT_SESSION_REAL_FINAL_QUOTE_USED')
        if receipt.get('provider')=='THS_UNADJUSTED':
            audit.update(source_provider='THS_UNADJUSTED',source_volume_unit='SHARES',
                volume_conversion='Original THS shares / 100 into common hand field, then *100 exactly',
                amount_unit='CNY',adjustment='00_UNADJUSTED',original_source_preserved=True,
                original_sdk_bundle_sha256=hashlib.sha256(raw).hexdigest(),
                reference_close_method=json.loads(raw).get('reference_close_method'))
        result['history']=[{'date':b.source_date,'open':str(b.open),'high':str(b.high),'low':str(b.low),
            'close':str(b.close),'volume_shares':str(b.volume_shares),'amount_cny':str(b.amount_cny),
            'turnover_percent':str(b.turnover_pct)} for b in bars[-500:]]
        if model_state and model_state.get('last_date','9999')>days[-2]:model_state=None
        first_listed_session=trusted_listing(stock,days[-1])==days[-1]
        if first_listed_session and bars[0].source_date!=days[-1]:
            raise ValueError('VERIFIED_FIRST_LISTING_CONFLICTS_WITH_SOURCE_HISTORY')
        targets=days[-1:] if first_listed_session else days[-2:]
        try:
            snapshots,next_state=session_snapshots(bars,targets,security_id=stock['code'],state=model_state,no_trade=no_trade)
        except ValueError as e:
            if not model_state:raise
            snapshots,next_state=session_snapshots(bars,targets,security_id=stock['code'],no_trade=no_trade)
            row['reasons'].append('MODEL_CACHE_REBUILT:'+str(e)[:100])
        result['_model_state']=next_state
        result.update(snapshots=snapshots,chip_status='READY')
        if first_listed_session:
            result.update(comparison_status='NO_PREVIOUS_LISTED_SESSION',reported_listing_date=stock['listed'])
            row.update(chip_status='READY',peak_prices_cny=snapshots[days[-1]]['peak_prices_cny'],
                comparison_status='NO_PREVIOUS_LISTED_SESSION')
            row['reasons'].append('FIRST_LISTED_SESSION_NO_PREVIOUS_CHIP_COMPARISON')
        else:
            comparison=compare_snapshots(snapshots[days[-2]],snapshots[days[-1]])
            for day,field in ((days[-2],'previous_share_percent'),(days[-1],'current_same_price_share_percent')):
                snapshots[day]['comparison_anchor_shares_percent']={r['anchor_price_cny']:r[field] for r in comparison['anchors']}
                snapshots[day]['comparison_anchor_definition']='Yesterday raw peak +/- half yesterday step; fixed price interval'
            row.update(chip_status='READY',peak_prices_cny=comparison['current_peak_prices_cny'],
                previous_peak_prices_cny=comparison['previous_peak_prices_cny'],
                original_peak_changes=[{'price_cny':r['anchor_price_cny'],'previous_share_percent':r['previous_share_percent'],
                    'current_share_percent':r['current_same_price_share_percent'],'change_percentage_points':r['change_percentage_points']} for r in comparison['anchors']])
        if audit['invalid_source_rows']: row['reasons'].append('MODEL_RESEEDED_AFTER_INVALID_SOURCE_HISTORY')
    except (ValueError,ArithmeticError) as e:
        result['reasons'].append(str(e)[:200]);row['reasons'].append('CHIP_MODEL_INPUT_INCOMPLETE')
    result['reasons']+=row['reasons']
    return row,result

def _calculate(store,source,stock,ctx):
    slot,dataset,days,as_of=ctx['slot'],ctx['dataset'],ctx['days'],ctx['as_of']
    state=ctx
    references=state.get('historical_references',{}).get(stock['code'])
    def own():
        lease,_=store.get('collector/chips/lease.json')
        if not lease or lease.get('owner')!=ctx['owner'] or lease.get('expires',0)<=time.time():
            raise RuntimeError('CHIP_WRITER_LEASE_LOST')
    def preserve_rejected(entries):
        links=[]
        for entry in entries[:4]:
            rejected_key=f'collector/chips/rejected/{dataset}/{stock["code"]}/{entry["sha256"]}.json'
            original,_=store.get(rejected_key)
            if original is None:
                own();store.put(rejected_key,entry,new=True,compressed=True)
            links.append({k:v for k,v in entry.items() if k!='raw_base64'}|{'private_raw_key':rejected_key})
        return links
    def evaluate(screen_stock,lines):
        nonlocal references
        row=prefilter(screen_stock,lines,days,state['pools'],state.get('actual_limits'),references,state.get('closing_pools'))
        if (isinstance(store,Storage) and row['prefilter_status']=='UNKNOWN' and row.get('close_cny') is not None
            and any(e['reason']=='HISTORICAL_REFERENCE_CLOSE_NOT_VERIFIED' for e in row['limit_evidence'])):
            try:
                ref_key=f'collector/chips/reference-daily/{as_of}/{stock["code"]}.json'
                bundle,_=store.get(ref_key)
                if bundle is None:
                    bundle=fetch_reference(source,stock,days)
                    own();store.put(ref_key,bundle,new=True,compressed=True)
                references=reference_prepared(bundle,stock['code'],days)
                row=prefilter(screen_stock,lines,days,state['pools'],state.get('actual_limits'),references,state.get('closing_pools'))
            except Exception as exc:
                row['reasons'].append('HISTORICAL_REFERENCE_FETCH_ERROR:'+type(exc).__name__)
        if (isinstance(store,Storage) and references and references.get('provider')=='BAOSTOCK_0.9.3'
            and row['prefilter_status']=='UNKNOWN' and row.get('close_cny') is not None
            and any(e['reason']=='REFERENCE_DAILY_VOLUME_CONFLICT' for e in row['limit_evidence'])):
            try:
                corroboration_key=f'collector/chips/reference-corroboration/{as_of}/{stock["code"]}.json'
                primary_references=references
                bundle,_=store.get(corroboration_key)
                if bundle is None:
                    # Revalidate dated raw archives; never relabel an old URL
                    # or fill a newer source session from an older request.
                    for prior_day in reversed(days[:-1]):
                        prior_key=f'collector/chips/reference-corroboration/{prior_day}/{stock["code"]}.json'
                        prior,_=store.get(prior_key)
                        if prior is None:continue
                        secondary=archived_corroboration(prior,stock['code'],days,expected_as_of=prior_day)
                        secondary['private_archive_key']=prior_key
                        references=corroborate_quantity(references,secondary,lines)
                        row=prefilter(screen_stock,lines,days,state['pools'],state.get('actual_limits'),references,state.get('closing_pools'))
                        break
                if row['prefilter_status']=='UNKNOWN' and any(e['reason']=='REFERENCE_DAILY_VOLUME_CONFLICT' for e in row['limit_evidence']):
                    if bundle is None:
                        bundle=fetch_reference(source,stock,days,provider='SOHU_REPORTED_DAILY_CHANGE')
                        own();store.put(corroboration_key,bundle,new=True,compressed=True)
                    secondary=reference_prepared(bundle,stock['code'],days)
                    references=corroborate_quantity(primary_references,secondary,lines)
                    row=prefilter(screen_stock,lines,days,state['pools'],state.get('actual_limits'),references,state.get('closing_pools'))
            except Exception as exc:
                row['reasons'].append('REFERENCE_QUANTITY_CORROBORATION_ERROR:'+type(exc).__name__)
        return row
    def preserve_archive(value,cached=None):
        """Bind reused frames to verified final bytes before their lineage changes."""
        expected=value.get('source',{}).get('sha256')
        if (not re.fullmatch('[0-9]{6}',stock['code']) or value.get('security_id')!=stock['code']):
            raise RuntimeError('SOURCE_ARCHIVE_IDENTITY_MISMATCH')
        if not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected):
            raise RuntimeError('SOURCE_ARCHIVE_HASH_REQUIRED')
        pattern=rf'collector/chips/raw/(?:[a-f0-9]{{16}}|repair-\d{{4}}-\d{{2}}-\d{{2}})/{stock["code"]}(?:-current-quote|-[a-f0-9]{{16}})?\.json'
        checked={}
        def verified(key):
            if not isinstance(key,str) or not re.fullmatch(pattern,key):return False
            if key in checked:return checked[key]
            saved,_=store.get(key)
            checked[key]=False
            if not saved or saved.get('receipt',{}).get('sha256')!=expected:return False
            try:
                text=saved['raw_base64']
                if not isinstance(text,str) or len(text)>3*1024*1024:
                    raise ValueError('ARCHIVE_BOUND')
                raw=base64.b64decode(text,validate=True)
                decoded=json.loads(raw)
            except (KeyError,TypeError,ValueError) as exc:
                raise RuntimeError('SOURCE_ARCHIVE_BYTES_INVALID') from exc
            if hashlib.sha256(raw).hexdigest()!=expected:
                raise RuntimeError('SOURCE_ARCHIVE_RAW_HASH_MISMATCH')
            if (not isinstance(decoded,dict) or not isinstance(decoded.get('data'),dict)
                or decoded['data'].get('code')!=stock['code']
                or saved['receipt'].get('provider','EASTMONEY')!=value['source'].get('provider','EASTMONEY')):
                raise RuntimeError('SOURCE_ARCHIVE_IDENTITY_MISMATCH')
            checked[key]=True
            return True
        key=value.get('source',{}).get('same_session_cached_raw_key')
        if verified(key):return cached
        if cached is None:cached,_=store.get(f'collector/chips/model/{stock["code"]}.json')
        identity=ctx.get('execution_identity',{})
        if (not cached or cached.get('security_id')!=stock['code']
            or cached.get('_calculated_as_of')!=as_of
            or cached.get('_source_bundle_sha256')!=expected
            or not identity.get('history_adapter_sha256') or not identity.get('model_implementation_sha256')
            or cached.get('_history_adapter_sha256')!=identity['history_adapter_sha256']
            or cached.get('_model_code_sha256')!=identity['model_implementation_sha256']
            or not verified(cached.get('_source_raw_key'))):
            raise RuntimeError('SOURCE_ARCHIVE_BINDING_UNAVAILABLE')
        value['source']={**value['source'],'same_session_cached_raw_key':cached['_source_raw_key']}
        return cached
    def publish(value):
        apply_price_position(value['screening_row'],value.get('snapshots',{}).get(as_of))
        no_trade=set(stock.get('confirmed_no_trade_sessions',[]))
        no_trade.update(value.get('source_audit',{}).get('confirmed_no_trade_sessions',[]))
        no_trade.update(value.get('snapshots',{}).get(as_of,{}).get('no_trade_confirmation_dates',[]))
        stability,proof=classify_stability(value['screening_row'],value.get('history',[]),days,
            security_id=stock['code'],no_trade=no_trade,
            reference_evidence=references or value.get('historical_limit_reference'),
            actual_limits=state.get('actual_limits'),expected_current=value.get('snapshots',{}).get(as_of),
            incomparable=bool(value.get('price_basis_incomparable')))
        apply_stability(value['screening_row'],stability)
        value.pop('chip_stability_evidence',None)
        if proof:value['chip_stability_evidence']=proof
        value['screening_row']['golden_grid']=golden_row(value)
        display=display_frame(value)
        exceeds_browser_limit=any(len(s.get('profile',[]))>MAX_BROWSER_PROFILE_BINS for s in display.get('snapshots',{}).values())
        if (display is not value or exceeds_browser_limit) and not display.get('full_precision_frame_private_key'):
            full_key=f'collector/chips/full-frames/{dataset}/{stock["code"]}.json'
            hashes={day:s.get('original_profile_sha256') or hashlib.sha256(encoded(s['profile'])).hexdigest()
                for day,s in display['snapshots'].items()}
            original,_=store.get(full_key)
            if original is None:
                own();store.put(full_key,{**value,'archived_display_profile_sha256':hashes},new=True,compressed=True)
            elif (original.get('dataset_id')!=dataset or original.get('security_id')!=stock['code']
                or original.get('source',{}).get('sha256')!=value.get('source',{}).get('sha256')
                or original.get('archived_display_profile_sha256')!=hashes):
                raise RuntimeError('IMMUTABLE_FULL_PRECISION_FRAME_CONFLICT')
            display['full_precision_frame_private_key']=full_key
        if exceeds_browser_limit:raise ValueError('STOCK_DISPLAY_EXCEEDS_BROWSER_PROFILE_LIMIT')
        bound=response_upper_bound(display)
        if bound>=5900000:raise ValueError('STOCK_DISPLAY_EXCEEDS_AUTHENTICATED_API_LIMIT')
        display['screening_row']['display_response_upper_bound_bytes']=bound
        own();store.put(key,display,new=True,compressed=True)
    key=f'data/chips/daily/{slot}/{stock["code"]}.json'
    existing,_=store.get(key)
    if existing:
        if existing.get('dataset_id')!=dataset or existing.get('security_id')!=stock['code']:
            raise RuntimeError('IMMUTABLE_FRAME_CONFLICT')
        return existing['screening_row']
    previous_slot=ctx.get('previous_slot')
    if previous_slot:
        previous,_=store.get(f'data/chips/daily/{previous_slot}/{stock["code"]}.json')
        cached=None
        listing_changed=previous is not None and previous.get('listing_context',{'listed':None,'evidence':None})!=listing_context(stock)
        if previous and listing_changed:
            cached,_=store.get(f'collector/chips/model/{stock["code"]}.json')
            valid_screen_cache=(cached and cached.get('_source_history')
                and cached.get('_source_bundle_sha256')==previous.get('source',{}).get('sha256')
                and cached.get('_history_adapter_sha256')==ctx.get('execution_identity',{}).get('history_adapter_sha256'))
            if (not valid_screen_cache or trusted_listing(stock,as_of)==as_of
                or previous.get('comparison_status')=='NO_PREVIOUS_LISTED_SESSION'):
                previous=None
        if (previous and previous.get('as_of')==as_of and previous.get('chip_status')=='READY'
            and previous.get('model')==MODEL
            and previous.get('execution_identity',{}).get('history_adapter_sha256')
                ==ctx.get('execution_identity',{}).get('history_adapter_sha256')
            and previous.get('execution_identity',{}).get('current_quote_sha256')
                ==ctx.get('execution_identity',{}).get('current_quote_sha256')
            and previous.get('source',{}).get('current_quote_sha256')
                ==state.get('actual_limits',{}).get(as_of,{}).get(stock['code'],{}).get('raw_quote_sha256')
            and (not ctx.get('require_current_final_quote') or previous.get('source',{}).get('provider')!='THS_UNADJUSTED'
                or previous.get('source',{}).get('current_quote_sha256'))
            and compatible_ready_model(previous,ctx.get('execution_identity',{}))):
            cached=preserve_archive(previous,cached)
            if cached is None:cached,_=store.get(f'collector/chips/model/{stock["code"]}.json')
            if (cached and cached.get('_source_history')
                and cached.get('_history_adapter_sha256')==ctx.get('execution_identity',{}).get('history_adapter_sha256')
                and cached.get('_source_bundle_sha256')==previous.get('source',{}).get('sha256')):
                row=previous['screening_row']
                screen_stock={**stock,'reference_close_verified':cached.get('_source_provider')!='THS_UNADJUSTED',
                    'confirmed_no_trade_sessions':cached.get('_confirmed_no_trade_sessions',[])}
                row.update(evaluate(screen_stock,cached['_source_history']))
                if references:previous['historical_limit_reference']=references
                previous.update(dataset_id=dataset,recorded_at=now(),reused_from_generation=previous_slot,
                    calculation_execution_identity=previous.get('calculation_execution_identity',previous['execution_identity']),
                    execution_identity=ctx.get('execution_identity'),screening_row=row,listing_context=listing_context(stock))
                publish(previous)
                return row
    try:
        cache_key=f'collector/chips/model/{stock["code"]}.json'
        cache,cache_etag=store.get(cache_key)
        adapter_hash=ctx.get('execution_identity',{}).get('history_adapter_sha256')
        valid_history_cache=cache if cache and adapter_hash and cache.get('_history_adapter_sha256')==adapter_hash else None
        raw_key=f'collector/chips/raw/{dataset}/{stock["code"]}.json'
        saved,_=store.get(raw_key)
        same_session_cache=False
        final_quote=state.get('actual_limits',{}).get(as_of,{}).get(stock['code'],{})
        previous_session_cache=(valid_history_cache and final_quote.get('daily_line')
            and valid_history_cache.get('last_date')==days[-2])
        if (saved is None and valid_history_cache
            and (valid_history_cache.get('_calculated_as_of')==as_of or valid_history_cache.get('last_date')==as_of or previous_session_cache)):
            cached_key=valid_history_cache.get('_source_raw_key','')
            if cached_key.startswith('collector/chips/raw/'):
                cached_raw,_=store.get(cached_key)
                if cached_raw:
                    if cached_raw['receipt']['sha256']!=valid_history_cache.get('_source_bundle_sha256'):
                        raise ValueError('SAME_SESSION_CACHE_SOURCE_HASH_MISMATCH')
                    saved=deepcopy(cached_raw);saved['receipt']['same_session_cached_raw_key']=cached_key
                    if previous_session_cache:saved['receipt']['previous_session_cached_prefix_date']=days[-2]
                    same_session_cache=True
        if saved:
            receipt=saved['receipt'];raw=base64.b64decode(saved['raw_base64'],validate=True)
            if hashlib.sha256(raw).hexdigest()!=receipt['sha256']: raise ValueError('RAW_HASH_MISMATCH')
            original_raw=raw
            original_raw_key=cached_key if same_session_cache else raw_key
            raw,receipt=overlay_final_quote(raw,receipt,stock,as_of,state.get('actual_limits',{}).get(as_of,{}).get(stock['code']),require_final_quote=bool(ctx.get('require_current_final_quote')))
            if raw!=original_raw:
                receipt['original_history_raw_key']=original_raw_key
                raw_key=f'collector/chips/raw/{dataset}/{stock["code"]}-current-quote.json'
                existing,_=store.get(raw_key)
                if existing and existing['receipt']['sha256']!=receipt['sha256']:raise ValueError('IMMUTABLE_QUOTE_OVERLAY_CONFLICT')
                if existing is None:
                    own();store.put(raw_key,{'receipt':receipt,'raw_base64':base64.b64encode(raw).decode()},new=True,compressed=True)
            elif same_session_cache:
                own();store.put(raw_key,saved,new=True,compressed=True)
        else:
            _,receipt,raw=source.history(stock,as_of,cached=valid_history_cache) if valid_history_cache and valid_history_cache.get('_source_history') else source.history(stock,as_of)
            rejected=preserve_rejected(receipt.pop('_rejected_sources',[]))
            if rejected:receipt['rejected_sources']=rejected
            own();store.put(raw_key,{'receipt':receipt,'raw_base64':base64.b64encode(raw).decode()},new=True,compressed=True)
            original_raw=raw;original_raw_key=raw_key
            raw,receipt=overlay_final_quote(raw,receipt,stock,as_of,state.get('actual_limits',{}).get(as_of,{}).get(stock['code']),require_final_quote=bool(ctx.get('require_current_final_quote')))
            if raw!=original_raw:
                receipt['original_history_raw_key']=original_raw_key
                raw_key=f'collector/chips/raw/{dataset}/{stock["code"]}-current-quote.json'
                own();store.put(raw_key,{'receipt':receipt,'raw_base64':base64.b64encode(raw).decode()},new=True,compressed=True)
        model_hash=ctx.get('execution_identity',{}).get('model_implementation_sha256')
        model_cache=valid_history_cache if valid_history_cache and model_hash and valid_history_cache.get('_model_code_sha256')==model_hash else None
        if hashlib.sha256(raw).hexdigest()!=receipt['sha256'] or json.loads(raw)['data']['code']!=stock['code']:
            raise ValueError('RAW_HASH_OR_IDENTITY_MISMATCH')
        row,value=frame(stock,raw,receipt,days,state['pools'],dataset,state.get('actual_limits'),model_cache,references,state.get('closing_pools'))
        decoded_screen=json.loads(raw)
        row.update(evaluate({**stock,'reference_close_verified':receipt.get('provider')!='THS_UNADJUSTED',
            'confirmed_no_trade_sessions':decoded_screen.get('confirmed_no_trade_sessions',[])},decoded_screen['data']['klines']))
        if references:value['historical_limit_reference']=references
        final_wrapper=None
        if (row['chip_status']=='MISSING' and receipt.get('provider')=='THS_UNADJUSTED'
            and board(stock['code'])[0]!='BSE' and stock.get('listed')!=as_of
            and any(reason.startswith('REQUESTED_SOURCE_DATES_MISSING:') for reason in value['reasons'])):
            try:
                alternate_key=f'collector/chips/raw/{dataset}/{stock["code"]}-trade-status.json'
                alternate,_=store.get(alternate_key)
                if alternate:
                    alternate_receipt=alternate['receipt'];alternate_raw=base64.b64decode(alternate['raw_base64'],validate=True)
                    if hashlib.sha256(alternate_raw).hexdigest()!=alternate_receipt['sha256']:raise ValueError('ALTERNATE_RAW_HASH_MISMATCH')
                else:
                    _,alternate_receipt,alternate_raw=source.confirmed_history(stock,as_of)
                    own();store.put(alternate_key,{'receipt':alternate_receipt,'raw_base64':base64.b64encode(alternate_raw).decode()},new=True,compressed=True)
                original_rows={r.split(',')[0]:r.split(',') for r in json.loads(raw)['data']['klines']}
                alternate_rows={r.split(',')[0]:r.split(',') for r in json.loads(alternate_raw)['data']['klines']}
                common=sorted(original_rows.keys()&alternate_rows.keys())[-10:]
                if not common or any(Decimal(original_rows[d][i])!=Decimal(alternate_rows[d][i]) for d in common for i in (1,2,3,4)):
                    raise ValueError('TRADE_STATUS_FALLBACK_PRICE_CONFLICT')
                alternate_row,alternate_value=frame(stock,alternate_raw,alternate_receipt,days,state['pools'],dataset,state.get('actual_limits'),reference_evidence=references,closing_pools=state.get('closing_pools'))
                if alternate_row['chip_status']=='READY':
                    final_wrapper=deepcopy(alternate) if alternate else {
                        'receipt':deepcopy(alternate_receipt),'raw_base64':base64.b64encode(alternate_raw).decode()}
                    alternate_receipt['prior_source_raw_key']=raw_key
                    alternate_receipt['fallback_reason']='PRIOR_PROVIDER_MISSING_REQUESTED_SESSIONS;EXPLICIT_TRADE_STATUS_REQUIRED'
                    row,value,receipt,raw,raw_key=alternate_row,alternate_value,alternate_receipt,alternate_raw,alternate_key
                else:value['trade_status_fallback_reasons']=alternate_value['reasons']
            except Exception as exc:
                value['trade_status_fallback_error']={'error':type(exc).__name__,'detail':str(exc)[:200]}
        if hashlib.sha256(raw).hexdigest()!=receipt['sha256'] or json.loads(raw)['data']['code']!=stock['code']:
            raise ValueError('RAW_HASH_OR_IDENTITY_MISMATCH')
        if final_wrapper is not None and row['chip_status']=='READY' and raw_key not in archive_keys({
            'security_id':stock['code'],'source':{'same_session_cached_raw_key':raw_key}}):
            alias=f'collector/chips/raw/{dataset}/{stock["code"]}-{receipt["sha256"][:16]}.json'
            existing,_=store.get(alias)
            if existing is None:
                own();store.put(alias,final_wrapper,new=True,compressed=True)
            else:
                try:
                    saved_raw=base64.b64decode(existing['raw_base64'],validate=True)
                    valid=(existing==final_wrapper and existing['receipt']['sha256']==receipt['sha256']
                        and hashlib.sha256(saved_raw).hexdigest()==receipt['sha256'])
                except (KeyError,TypeError,ValueError):valid=False
                if not valid:raise _ArchiveConflict('IMMUTABLE_SOURCE_ARCHIVE_ALIAS_CONFLICT')
            raw_key=alias
        receipt={**receipt,'same_session_cached_raw_key':
            original_raw_key if same_session_cache and raw==original_raw else raw_key}
        value['source']=receipt
        next_state=value.pop('_model_state',None)
        if next_state:
            next_state.update(_source_history=json.loads(raw)['data']['klines'],_source_provider=receipt.get('provider','EASTMONEY'),
                _source_bundle_sha256=receipt['sha256'],_source_raw_key=raw_key,_model_code_sha256=model_hash,_history_adapter_sha256=adapter_hash,
                _confirmed_no_trade_sessions=json.loads(raw).get('confirmed_no_trade_sessions',[]),_calculated_as_of=as_of)
        if next_state and (not cache or next_state['last_date']>=cache['last_date']):
            own()
            store.put(cache_key,next_state,match=cache_etag,new=cache_etag is None,compressed=True)
    except _ArchiveConflict:
        raise
    except Exception as e:
        row={'security_id':stock['code'],'name':stock['name'],'market':board(stock['code'])[0],
            'close_cny':None,'prefilter_status':'UNKNOWN','recent_limit_up_dates':[],
            'chip_status':'MISSING','peak_prices_cny':[],'previous_peak_prices_cny':[],'original_peak_changes':[],
            'reasons':['SOURCE_OR_MODEL_ERROR:'+type(e).__name__]}
        if stock['code'].startswith(('4','8','9')):row['reasons'].append('BSE_HISTORY_UNAVAILABLE')
        quote=state.get('actual_limits',{}).get(as_of,{}).get(stock['code'])
        if quote:
            try:
                close,upper=Decimal(quote['close']),Decimal(quote['upper_limit'])
                if close.is_finite() and upper.is_finite() and close>0:
                    row['close_cny']=str(close)
                    if upper>0 and close==upper:
                        row['prefilter_status']='PASS';row['recent_limit_up_dates']=[as_of]
                    row['reasons'].append('LATEST_CLOSE_FROM_VERIFIED_DATED_TENCENT_QUOTE;HISTORICAL_OHLC_MISSING')
            except (ArithmeticError,ValueError):pass
        value={'schema':'stock.chips.market.stock.v1','dataset_id':dataset,'as_of':as_of,
            'security_id':stock['code'],'name':stock['name'],'model':MODEL,'unchanged_threshold':None,
            'chip_status':'MISSING','snapshots':{},'history':[],'reasons':row['reasons']}
        if getattr(e,'chip_source_attempts',None):value['source_failures']=e.chip_source_attempts
        value['error_detail']=str(e)[:300]
        rejected=preserve_rejected(getattr(e,'chip_rejected_sources',[]))
        if rejected:value['rejected_sources']=rejected
        if getattr(e,'chip_source_errors',None):value['source_errors']=e.chip_source_errors
    value['execution_identity']=ctx.get('execution_identity')
    value['screening_row']=row
    publish(value)
    return row

_WORKER=None

def _initialize_worker(bucket,region,ctx):
    global _WORKER
    import atexit
    source=Source();_WORKER=(Storage(bucket,region),source,ctx);atexit.register(source.close)

def _worker_stock(stock):
    return _calculate(_WORKER[0],_WORKER[1],stock,_WORKER[2])

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
        identity=implementation_identity()
        if (current and current['as_of']==as_of and (current.get('market_complete') or current.get('calculation_complete'))
            and current.get('execution_identity')==identity):
            return {'outcome':'ALREADY_COMMITTED','as_of':as_of,'market_complete':current['market_complete']}
        state,state_etag=store.get('collector/chips/state.json');source=Source()
        if current and state and current['dataset_id']==state.get('dataset_id'):
            # A committed but incomplete generation is immutable. Repair into a
            # new generation, keeping the existing publication readable.
            state=None
        if state and not compatible_state(state,identity):
            archive=f'collector/chips/recovery/{state["dataset_id"]}-{hashlib.sha256(encoded(state)).hexdigest()[:12]}.json'
            archived,_=store.get(archive)
            if not archived:store.put(archive,state,new=True,compressed=True)
            state=None
        if state and state['as_of']<as_of and len(state['rows'])<len(state['stocks']):
            # Finish the first bootstrap rather than discarding its progress
            # every afternoon. Subsequent sessions reuse its exact model states.
            as_of=state['as_of']
        if state and state['as_of']>as_of: raise ValueError('WOULD_REPLACE_NEWER_BATCH')
        if state and state.get('retry_failed'):
            old_slot=state['active_slot']
            dataset=hashlib.sha256(encoded([old_slot,identity,now()])).hexdigest()[:16]
            state.update(dataset_id=dataset,active_slot=dataset,previous_slot=old_slot,rows={},retry_failed=False)
            state_etag=store.put('collector/chips/state.json',state,match=state_etag,compressed=True)
        if not state or state['as_of']!=as_of:
            from .listing import verified_current
            reconciliation,_=store.get(f'collector/chips/universe-verified/{as_of}.json')
            if reconciliation:
                # The dated official reconciliation is the primary current
                # list, including real additions/removals since a site catalog.
                verified=verified_current(reconciliation,reconciliation.get('stocks',[]),as_of)
                stocks,receipt=verified
                universe_receipts=[receipt]
            else:
                try:stocks,universe_receipts=source.universe()
                except Exception as exc:
                    stocks,universe_receipts=shared_universe(store,as_of,source)
                    universe_receipts[0]['em_refresh_error']=type(exc).__name__
            days=last_ten(as_of);pools,pool_receipts=source.pools(days)
            for day in days:
                pool_key=f'collector/chips/pools/{day}.json'
                cached_pool,pool_etag=store.get(pool_key)
                if pools.get(day) is not None:
                    store.put(pool_key,{'date':day,'pool':pools[day],
                        'receipts':[r for r in pool_receipts if r.get('requested_session')==day]},match=pool_etag,new=pool_etag is None,compressed=True)
                elif cached_pool and cached_pool.get('date')==day:
                    pools[day]=cached_pool['pool'];pool_receipts+=cached_pool['receipts']
            actual_limits={};limit_receipts=[];historical_references={};reference_receipts=[];closing_pools={};closing_pool_receipts=[]
            for day in days:
                value,_=store.get(f'collector/chips/limits/{day}.json')
                if value: actual_limits[day]=value['quotes']
                reference_batch,_=store.get(f'collector/chips/references/{day}.json')
                if reference_batch:
                    if reference_batch.get('as_of')!=day or reference_batch.get('schema')!='stock.chips.limit.reference.batch.v1':
                        raise ValueError('HISTORICAL_REFERENCE_BATCH_IDENTITY_INVALID')
                    for code,bundle in reference_batch['securities'].items():
                        reference=reference_prepared(bundle,code,last_ten(day))
                        prior=historical_references.get(code)
                        if prior:
                            for d,r in prior['rows'].items():
                                if d in reference['rows'] and r!=reference['rows'][d]:
                                    raise ValueError('HISTORICAL_REFERENCE_SOURCES_CONFLICT')
                            reference['rows']={**prior['rows'],**reference['rows']}
                        historical_references[code]=reference
                    reference_receipts.append({'key':f'collector/chips/references/{day}.json','as_of':day,
                        'security_count':len(reference_batch['securities']),'sha256':hashlib.sha256(encoded(reference_batch)).hexdigest()})
                closing_key=f'collector/chips/closing-pools/{day}.json'
                closing_bundle,_=store.get(closing_key)
                if closing_bundle is None and isinstance(store,Storage):
                    try:
                        closing_bundle=fetch_closing_pool(source,day)
                        store.put(closing_key,closing_bundle,new=True,compressed=True)
                    except Exception as exc:
                        closing_pool_receipts.append({'date':day,'error':type(exc).__name__,'detail':str(exc)[:150]})
                if closing_bundle:
                    closing_pools[day]=closing_pool(closing_bundle,day)
                    closing_pool_receipts.append(closing_bundle['receipt'])
            if as_of not in actual_limits or (isinstance(store,Storage) and not any(q.get('raw_quote_body') for q in actual_limits.get(as_of,{}).values())):
                quotes,limit_receipts=collect_final_quotes(source,stocks,as_of) if isinstance(store,Storage) else source.actual_limits(stocks,as_of)
                if quotes:
                    quote_key=f'collector/chips/limits/{as_of}.json'
                    previous_quotes,quote_etag=store.get(quote_key)
                    quotes={**(previous_quotes or {}).get('quotes',{}),**quotes}
                    store.put(quote_key,{'quotes':quotes,'receipts':limit_receipts},match=quote_etag,new=quote_etag is None,compressed=True)
                    actual_limits[as_of]=quotes
            if as_of in actual_limits:actual_limits[as_of]=renormalize_final_quotes(actual_limits[as_of],as_of)
            dataset=hashlib.sha256(encoded([as_of,MODEL,identity,stocks,now()])).hexdigest()[:16]
            state={'as_of':as_of,'dataset_id':dataset,'active_slot':dataset,
                'days':days,'stocks':stocks,'pools':pools,'universe_receipts':universe_receipts,'pool_receipts':pool_receipts,
                'source_version':source.VERSION,'execution_identity':identity,'actual_limits':actual_limits,'limit_receipts':limit_receipts,
                'require_current_final_quote':as_of==target_day(),
                'historical_references':historical_references,'reference_receipts':reference_receipts,
                'closing_pools':closing_pools,'closing_pool_receipts':closing_pool_receipts,'rows':{},'started_at':now()}
            if current and current['as_of']==as_of:state['previous_slot']=current['active_slot']
            state_etag=store.put('collector/chips/state.json',state,match=state_etag,new=state_etag is None,compressed=True)
        dataset=state['dataset_id'];slot=state['active_slot'];days=state['days'];done=state['rows']
        def assert_owned():
            lease_now,_=store.get('collector/chips/lease.json')
            if not lease_now or lease_now['owner']!=owner or lease_now['expires']<=time.time():
                raise RuntimeError('CHIP_WRITER_LEASE_LOST')
        saved_count=-1
        def checkpoint():
            nonlocal state_etag,lease_etag,lease_expires,saved_count
            assert_owned()
            lease_expires=time.time()+600
            lease_etag=store.put('collector/chips/lease.json',{'owner':owner,'expires':lease_expires},match=lease_etag)
            if saved_count!=len(done):
                state_etag=store.put('collector/chips/state.json',state,match=state_etag,compressed=True)
                saved_count=len(done)
        def save_status(status):
            assert_owned()
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
        # Submit only a bounded window so expiry cannot leave thousands of queued reads.
        ctx={'slot':slot,'dataset':dataset,'days':days,'as_of':as_of,'owner':owner,
             'pools':state['pools'],'actual_limits':state.get('actual_limits',{}),'historical_references':state.get('historical_references',{}),
             'closing_pools':state.get('closing_pools',{}),'execution_identity':identity,
             'require_current_final_quote':state.get('require_current_final_quote',as_of==target_day()),
             'previous_slot':state.get('previous_slot')}
        cloud=isinstance(store,Storage)
        executor=concurrent.futures.ProcessPoolExecutor(max_workers=workers,initializer=_initialize_worker,initargs=(store.bucket,store.region,ctx)) if cloud else concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        with executor as pool:
            active={};iterator=iter(pending);recent_source_failures=[]
            def fill():
                while len(active)<workers and time.monotonic()<deadline-90:
                    try: stock=next(iterator)
                    except StopIteration: break
                    # Same-session repair mostly copies authenticated READY
                    # frames. Retain each worker's source rate limit, without
                    # sleeping for every private cached stock in the parent.
                    # Dated final quotes can extend authenticated prior-session
                    # raw prefixes without another public history request. Each
                    # worker still rate-limits any exceptional source fallback.
                    if cloud and not ctx.get('previous_slot') and not ctx['actual_limits'].get(as_of,{}).get(stock['code'],{}).get('daily_line'):time.sleep(.8)
                    future=pool.submit(_worker_stock,stock) if cloud else pool.submit(_calculate,store,source,stock,ctx)
                    active[future]=stock['code']
            fill();saved_at=time.monotonic()
            while active:
                finished,_=concurrent.futures.wait(active,timeout=30,return_when=concurrent.futures.FIRST_COMPLETED)
                for future in finished:
                    code=active.pop(future);done[code]=future.result()
                    transport_types=('BaoStockTransportError','RemoteDisconnected','URLError','TimeoutError',
                        'ConnectionError','ConnectionResetError','ConnectionAbortedError','ReadTimeout')
                    recent_source_failures.append((done[code]['market'],done[code]['chip_status']=='MISSING' and
                        any(x=='SOURCE_OR_MODEL_ERROR:'+kind for x in done[code]['reasons'] for kind in transport_types)))
                    recent_source_failures=recent_source_failures[-20:]
                if (finished and len(done)%20<workers) or time.monotonic()-saved_at>45:
                    checkpoint()
                    save_status('RUNNING');saved_at=time.monotonic()
                    print(json.dumps({'outcome':'PROGRESS','as_of':as_of,'scanned':len(done),'total':len(state['stocks'])}),flush=True)
                if (len(recent_source_failures)==20 and all(failed for _,failed in recent_source_failures)
                    and len({market for market,_ in recent_source_failures})>=2):
                    state['retry_failed']=True;saved_count=-1
                    checkpoint();save_status('SOURCE_UNAVAILABLE')
                    raise RuntimeError('SOURCE_UNAVAILABLE_STOPPED_AFTER_BOUNDED_PROBE')
                fill()
        checkpoint()
        if len(done)!=len(state['stocks']):
            save_status('PAUSED_BUDGET');return {'outcome':'CHECKPOINT_SAVED','as_of':as_of,'scanned':len(done),'total':len(state['stocks'])}
        rows=sorted(done.values(),key=lambda r:r['security_id']);counts=Counter(r['prefilter_status'] for r in rows)
        ready=sum(r['chip_status']=='READY' for r in rows)
        final_counts=Counter(r['screen_status'] for r in rows)
        reconciled=any(r.get('provider')=='OFFICIAL_CURRENT_LIST_RECONCILIATION' for r in state['universe_receipts'])
        catalog_only=any(r.get('provider')=='SITE_PUBLISHED_CURRENT_UNIVERSE' for r in state['universe_receipts']) and not reconciled
        manifest={'schema':'stock.chips.market.manifest.v1','dataset_id':dataset,'active_slot':slot,'as_of':as_of,
            'previous_date':days[-2],'official_sessions':days,'universe_total':len(rows),'scanned_count':len(rows),
            'model_ready_count':ready,'screen_counts':{k:counts[k] for k in ('PASS','FAIL','UNKNOWN')},
            'price_position_rule':PRICE_POSITION_RULE,'price_filter_rule':PRICE_FILTER_RULE,
            'chip_unchanged_rule':CHIP_UNCHANGED_RULE,
            'chip_stability_thresholds':STABILITY_THRESHOLDS,
            'chip_stability_policy':STABILITY_POLICY,
            'final_screen_counts':{k:final_counts[k] for k in ('PASS','FAIL','UNKNOWN')},
            'display_response_verified_count':sum(0<r.get('display_response_upper_bound_bytes',0)<5900000 for r in rows),
            'max_display_response_upper_bound_bytes':max((r.get('display_response_upper_bound_bytes',0) for r in rows),default=0),
            'market_complete':ready==len(rows) and counts['UNKNOWN']==0 and final_counts['UNKNOWN']==0 and not catalog_only,
            # Only actual unresolved input/comparison gaps require a repair.
            'calculation_complete':ready==len(rows) and counts['UNKNOWN']==0
                and all(r['price_position_status']!='UNKNOWN' for r in rows)
                and all(stability_calculated(r) for r in rows) and not catalog_only,
            'universe_coverage':'OFFICIAL_CURRENT_LIST_RECONCILED' if reconciled else ('SITE_CATALOG_UNVERIFIED' if catalog_only else 'VENDOR_REPORTED_TOTAL_VALIDATED'),
            'execution_identity':identity,'coefficient':2,'unchanged_threshold':None,
            'model_version':MODEL['version'],'model':MODEL,'history_window_bars':210,'golden_grid_version':GOLDEN_VERSION,
            'generated_at':now(),'calendar_source':SOURCES[0],'calendar_sources':SOURCES,
            'scope':'Current listed SH/SZ/BJ provider universe; historical delistings not included',
            'limit_evidence':'Vendor closing-limit pool matched to exact unadjusted daily close; uncertain candidates remain UNKNOWN',
            'universe_receipts':state['universe_receipts'],'pool_receipts':state['pool_receipts'],
            'reference_receipts':state.get('reference_receipts',[]),'inclusive_closing_pool_receipts':state.get('closing_pool_receipts',[])}
        assert_owned()
        screening={'schema':'stock.chips.market.screen.v1','dataset_id':dataset,'as_of':as_of,'rows':rows,'unchanged_threshold':None}
        manifest['screening_response_upper_bound_bytes']=response_upper_bound(screening)
        if manifest['screening_response_upper_bound_bytes']>=5900000:
            raise ValueError('MARKET_SCREEN_EXCEEDS_AUTHENTICATED_API_LIMIT')
        screen_key=f'data/chips/daily/{slot}/screening.json'
        existing_screen,_=store.get(screen_key)
        if existing_screen is None:
            assert_owned();store.put(screen_key,screening,new=True,compressed=True)
        elif existing_screen!=screening: raise RuntimeError('IMMUTABLE_SCREEN_CONFLICT')
        golden=golden_document(manifest,[r['golden_grid'] for r in rows])
        if response_upper_bound(golden)>=5900000:raise ValueError('GOLDEN_SCREEN_EXCEEDS_AUTHENTICATED_API_LIMIT')
        golden_key=f'data/chips/daily/{slot}/golden.json'
        existing_golden,_=store.get(golden_key)
        if existing_golden is None:
            assert_owned();store.put(golden_key,golden,new=True,compressed=True)
        elif existing_golden!=golden:raise RuntimeError('IMMUTABLE_GOLDEN_CONFLICT')
        assert_owned()
        store.put('data/chips/daily/manifest.json',manifest,match=current_etag,new=current_etag is None)
        save_status('COMMITTED')
        return {'outcome':'COMMITTED','as_of':as_of,'total':len(rows),'model_ready':ready,'screen_counts':manifest['screen_counts'],'final_screen_counts':manifest['final_screen_counts'],'market_complete':manifest['market_complete']}
    finally:
        if 'source' in locals():source.close()
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
    # Repair transient gaps in the same bounded invocation as time permits.
    # Never loop indefinitely over permanent missing securities or unknown rules.
    for _ in range(2):
        remaining=int((a.max_minutes*60-(time.monotonic()-started))/60)
        if value.get('outcome')!='COMMITTED' or value['model_ready']>=value['total'] or remaining<3:break
        before=value['model_ready']
        value=run(store,as_of=value['as_of'],max_minutes=remaining)
        if value.get('outcome')!='COMMITTED' or value['model_ready']<=before:break
    print(json.dumps(value,ensure_ascii=False),flush=True)
if __name__=='__main__': main()
