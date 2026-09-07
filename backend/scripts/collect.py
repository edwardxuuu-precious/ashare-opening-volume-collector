#!/usr/bin/env python3
"""AKShare collector. Missing or inconsistent data never becomes zero or a valid ratio."""
from __future__ import annotations
import argparse,json,math,time,threading
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
try:
    from .sina_unadjusted import minute_unadjusted
    from .universe_cache import load_universe
    from .retention import six_month_start,validate_dates,retain_six_months
    from .reconciliation import VERIFICATION_VERSION,FallbackBudget,eastmoney_pair,reconcile_stock,reconcile_baostock,needs_baostock_checkpoint,sina_observations
    from .download_only import POLICY, METHOD as DOWNLOAD_METHOD, evaluate_downloaded
except ImportError:
    from sina_unadjusted import minute_unadjusted
    from universe_cache import load_universe
    from retention import six_month_start,validate_dates,retain_six_months
    from reconciliation import VERIFICATION_VERSION,FallbackBudget,eastmoney_pair,reconcile_stock,reconcile_baostock,needs_baostock_checkpoint,sina_observations
    from download_only import POLICY, METHOD as DOWNLOAD_METHOD, evaluate_downloaded

TIMES = [f'{h:02}:{m:02}:00' for h,m in [(9,45),(10,0),(10,15),(10,30),(10,45),(11,0),(11,15),(11,30),(13,15),(13,30),(13,45),(14,0),(14,15),(14,30),(14,45),(15,0)]]
STOP_REQUESTED=threading.Event()
SOURCE='AKShare · 新浪优先，东方财富整套备用核验'
METHOD='比例 = 同一来源09:45首根15分钟K线成交量 ÷ 同日日线成交量 × 100%，统一为股。新浪优先；差异时清缓存重取一次，再尝试东方财富不复权15分钟和日线整套备用（原始单位均为手，乘100转股）。全天16根齐全、日期与收盘一致、成交量合计精确对齐才纳入排名；不设置股数容差。待核验记录仅保留参考比例，不纳入排名和统计；首根沿用所选来源的开盘成交归属，不保证跨来源首根完全等价，不自行补减集合竞价。历史股票名单是采集当日的在市A股快照。'

def market(code):
    if code.startswith(('60','68')):return 'SH'
    if code.startswith(('00','30')):return 'SZ'
    if code.startswith(('43','83','87','88','92')):return 'BJ'
    raise ValueError(f'Unsupported A-share code: {code}')

def finite_number(value):
    try:number=float(value)
    except (TypeError,ValueError):return None
    return number if math.isfinite(number) else None


def evaluate(code,name,day,minute,daily,daily_lot=False):
    row={'code':str(code),'name':name,'market':market(str(code)),'first15Volume':None,'dailyVolume':None,'ratio':None,'status':'missing','sourceProvider':'sina','verificationVersion':VERIFICATION_VERSION}
    m=minute.copy();d=daily.copy()
    if 'day' not in m or 'volume' not in m:
        row['reason']='分钟数据缺失';return row
    x=m[m['day'].astype(str).str[:10]==day].copy()
    date_col='日期' if '日期' in d else 'date';vol_col='成交量' if '成交量' in d else 'volume'
    z=d[d[date_col].astype(str).str[:10]==day] if date_col in d else pd.DataFrame()
    if len(z)!=1 or vol_col not in z:
        row['reason']='该日的日线数据缺失或重复';return row
    dv=finite_number(z.iloc[0][vol_col])
    if dv is not None:dv*=100 if daily_lot else 1
    if dv is None or dv<0 or not dv.is_integer():
        row['reason']='日线成交量异常';return row
    row['dailyVolume']=dv
    if dv==0:
        row['reason']='日线成交量为零；未获得停牌确认';return row
    times=x['day'].astype(str).str[11:19]
    if len(x)!=16 or sorted(times.tolist())!=TIMES:
        row['reason']='全天15分钟K线不完整或含重复时间';return row
    vols=pd.to_numeric(x['volume'],errors='coerce')
    if vols.isna().any() or not vols.map(math.isfinite).all() or (vols<0).any() or ((vols % 1)!=0).any():
        row['reason']='分钟成交量异常';return row
    first=float(vols[times=='09:45:00'].iloc[0]);total=float(vols.sum())
    row.update(first15Volume=first,minuteDayVolume=total)
    if first>dv or first<0:
        row.update(status='unverified',reason='首15分钟量超出全天量范围');return row
    if 'close' in x and 'close' in z:
        mc=finite_number(x.loc[times=='15:00:00','close'].iloc[0]);dc=finite_number(z.iloc[0]['close'])
        if mc is None or dc is None or abs(mc-dc)>1e-8:
            row.update(status='unverified',reason='分钟收盘与日线收盘不一致，待排查日期或源数据');return row
    difference=total-dv
    row.update(dayVolumeDifference=difference,dayVolumeDifferencePct=round(difference/dv*100,6),quality='source_difference' if difference!=0 else 'matched')
    if difference!=0:
        row.update(status='unverified',verificationState='unverified',referenceRatio=round(first/dv*100,6),reason='成交量未完全核对一致',qualityNote=f'分钟全天合计与日线相差{difference:+,.0f}股（{difference/dv*100:+.2f}%）；参考比例不纳入排名和统计')
        return row
    row.update(status='ok',verificationState='matched',ratio=round(first/dv*100,6));return row

def safe_saved_row(row):
    """Quarantine old conflicts before merging or publishing checkpoint snapshots."""
    row=dict(row)
    if row.get('calculationPolicy')==POLICY:return row
    difference=finite_number(row.get('dayVolumeDifference'))
    if row.get('quality')=='source_difference' or (difference is not None and difference!=0):
        if row.get('ratio') is not None:row['referenceRatio']=row['ratio']
        row.update(status='unverified',ratio=None,quality='source_difference',verificationState='unverified',
                   reason=row.get('reason') or '成交量未完全核对一致，等待自动重取及备用核验')
    return row


def write_json(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False));tmp.replace(path)

def publish(results,dates,out,scope,universe_total,attempted_count=None,reconcile_latest_first=False,baostock_fallback=False,apply_retention=True,single_source=False,require_no_trade_evidence=False):
    attempted_count=len(results) if attempted_count is None else attempted_count
    policy='latest_first' if reconcile_latest_first else 'all_dates'
    methodology=METHOD+(' 本轮首次回填采用最新日优先：仅最新请求交易日的差异自动重取与尝试备用；历史差异仅完成初次核对，保留初次来源证据并标记等待后续核验，尚未自动排队重核验。' if reconcile_latest_first else '')
    source=SOURCE
    source_policy=['sina','eastmoney']
    if baostock_fallback:
        source+=' + BaoStock整套备用核验';source_policy.append('baostock')
        methodology+=' 本轮另启用BaoStock：沪深仍有量差、历史待核验或缺失的请求日期，使用一次覆盖整个请求窗口的不复权15分钟与日线整套数据复核，股为单位；整套严格匹配才替换。前述最新日优先仅限制新浪/东方财富，BaoStock会尝试当前请求窗口内的历史缺口。北交所不请求BaoStock。未调用/被熔断跳过不等于已核验，按逐行审计标记为准。'
    if single_source:
        source='AKShare · 新浪（新采集）；历史记录保留原来源'
        source_policy=['sina']
        policy='none'
        methodology=DOWNLOAD_METHOD
    now=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds');manifest={'generatedAt':now,'source':source,'sourcePolicy':source_policy,'scope':scope,'universeTotal':universe_total,'attemptedCount':attempted_count,'pendingCount':len(results)-attempted_count,'methodology':methodology,'reconciliationPolicy':policy,'dates':[]}
    observed_sources=set()
    for day in sorted(dates,reverse=True):
        rows=[safe_saved_row(r['days'][day]) for r in results if day in r['days']];valid=sum(r['status']=='ok' for r in rows);missing=sum(r['status']=='missing' for r in rows);suspended=sum(r['status']=='suspended' for r in rows);unverified=sum(r['status']=='unverified' for r in rows)
        file=f'{day}.json';payload={'date':day,'generatedAt':now,'source':source,'sourcePolicy':source_policy,'scope':scope,'total':len(rows),'universeTotal':universe_total,'attemptedCount':attempted_count,'pendingCount':len(results)-attempted_count,'rows':rows,'methodology':methodology,'reconciliationPolicy':policy}
        # Preserve each previously usable row when a refresh cannot retrieve it,
        # while retaining new successes for other stocks. A new explicit conflict
        # remains visible rather than being replaced by a stale successful value.
        target=out/file;previous=None;retained=0
        if target.exists():
            previous=json.loads(target.read_text())
        if previous:
            old_rows={r['code']:safe_saved_row(r) for r in previous.get('rows',[])};merged=[]
            for row in rows:
                old=old_rows.pop(row['code'],None)
                unproven_no_trade = bool(require_no_trade_evidence and old and old.get('status') == 'suspended'
                    and old.get('noTradeEvidence', {}).get('kind') != 'explicit_zero_daily_volume')
                if row['status']=='missing' and old and old['status'] in ('ok','suspended','unverified') and not unproven_no_trade:
                    preserved=dict(old,retainedFromPrevious=True,dataObservedAt=old.get('dataObservedAt',previous['generatedAt']))
                    if row.get('reason')=='尚未采集':
                        # A placeholder is not a new observation or a completed attempt.
                        preserved.update(pendingRefresh=True,pendingRefreshReason='本轮尚未采集，保留上次结果')
                    else:
                        preserved.pop('pendingRefresh',None);preserved.pop('pendingRefreshReason',None)
                        preserved.update(lastAttemptAt=now,lastAttemptReason=row.get('reason','数据缺失'))
                    merged.append(preserved);retained+=1
                else:
                    if row.get('reconciliationDeferred') and old and old.get('status')=='ok':
                        row=dict(row,previousVerifiedObservation=old)
                    merged.append(row)
            # A smaller ad-hoc refresh must not erase previously collected stocks.
            merged.extend(old_rows.values());rows=merged
            if old_rows:
                payload.update(scope=previous['scope'],universeTotal=previous['universeTotal'],attemptedCount=max(attempted_count,previous.get('attemptedCount',0)))
        payload.update(rows=rows,total=len(rows),retainedCount=retained)
        if single_source:
            payload['collectionSourcePolicy']=['sina']
            payload['calculationPolicy']=POLICY
            payload['sourcePolicy']=sorted({'sina'} | {r.get('sourceProvider','unknown') for r in rows})
            observed_sources.update(payload['sourcePolicy'])
        valid=sum(r['status']=='ok' for r in rows);missing=sum(r['status']=='missing' for r in rows);suspended=sum(r['status']=='suspended' for r in rows);unverified=sum(r['status']=='unverified' for r in rows)
        write_json(target,payload);manifest['dates'].append({'date':day,'status':'complete' if payload['scope']=='full' and valid+suspended==len(rows) else 'partial','total':len(rows),'valid':valid,'missing':missing,'unverified':unverified,'suspended':suspended,'file':file,'generatedAt':payload['generatedAt'],'retainedCount':retained,'scope':payload['scope']})
    previous_manifest=out/'manifest.json'
    if previous_manifest.exists():
        requested=set(dates)
        manifest['dates'].extend(d for d in json.loads(previous_manifest.read_text()).get('dates',[]) if d['date'] not in requested and (out/d['file']).exists())
        manifest['dates'].sort(key=lambda d:d['date'],reverse=True)
    calendar_file=out/'calendar.json'
    if calendar_file.exists():manifest.update(json.loads(calendar_file.read_text()))
    if single_source:
        manifest['collectionSourcePolicy']=['sina']
        manifest['calculationPolicy']=POLICY
        previous_sources=json.loads(previous_manifest.read_text()).get('sourcePolicy',[]) if previous_manifest.exists() else []
        manifest['sourcePolicy']=sorted(set(previous_sources) | observed_sources | {'sina'})
    write_json(out/'manifest.json',manifest)
    if apply_retention:retain_six_months(out,now[:10])

class RequestBudget:
    """Rate limit every actual HTTP request, including hidden AKShare requests."""
    def __init__(self,interval=0.75):
        self.lock=threading.Lock();self.last=0.;self.interval=max(interval,0.5);self.cache={};self.request_count=0;self.cache_hits=0
    def clear_cache(self):self.cache.clear()
    def install(self):
        import requests
        original=requests.sessions.Session.request
        def bounded(session,method,url,**kwargs):
            # AKShare minute() internally loads daily data even for adjust=''. Reuse
            # only identical public daily-history/share-count responses within one stock.
            cacheable=method.upper()=='GET' and ('/hisdata_klc2/klc_kl.js' in url or 'StockService.getAmountBySymbol' in url)
            key=(url,json.dumps(kwargs.get('params'),sort_keys=True,default=str))
            cached=self.cache.get(key) if cacheable else None
            if cached and time.monotonic()-cached[0]<60:
                self.cache_hits+=1;return cached[1]
            with self.lock:
                time.sleep(max(0,self.interval-(time.monotonic()-self.last)));self.last=time.monotonic()
            kwargs['timeout']=20
            self.request_count+=1
            response=original(session,method,url,**kwargs)
            response.raise_for_status()
            if cacheable and len(self.cache)<8:self.cache[key]=(time.monotonic(),response)
            return response
        requests.sessions.Session.request=bounded

def reusable_checkpoint(record,dates,today):
    if not record or record.get('fetchedOn')!=today or not all(d in record.get('days',{}) for d in dates):
        return False
    if record.get('fetchStatus')=='error':return False
    if any(record['days'][d].get('verificationVersion')!=VERIFICATION_VERSION for d in dates):return False
    if any(record['days'][d].get('calculationPolicy')!=POLICY and (record['days'][d].get('quality')=='source_difference' or record['days'][d].get('dayVolumeDifference',0)!=0) for d in dates):return False
    if any(record['days'][d].get('status') not in ('ok','suspended') for d in dates):return False
    return not any(str(record['days'][d].get('reason','')).startswith('采集失败') for d in dates)


def attempted_checkpoint(record, dates):
    """Resume an explicitly requested traversal without retrying unresolved rows forever."""
    return bool(record and record.get('fetchStatus') in ('ok','error') and
                all(d in record.get('days', {}) and
                    record['days'][d].get('verificationVersion') == VERIFICATION_VERSION
                    for d in dates))


def pending_record(item,dates):
    return {'code':item['code'],'fetchStatus':'pending','days':{
        day:{'code':item['code'],'name':item['name'],'market':market(item['code']),
             'first15Volume':None,'dailyVolume':None,'ratio':None,'status':'missing','reason':'尚未采集','verificationVersion':VERIFICATION_VERSION}
        for day in dates}}


def make_snapshot(items,records,dates,full_requested):
    results=[records.get(item['code']) or pending_record(item,dates) for item in items]
    attempted=sum(r.get('fetchStatus')!='pending' and all(day in r['days'] for day in dates) for r in results)
    scope=('full' if attempted==len(items) else 'partial') if full_requested else 'sample'
    return results,scope,attempted


def load_checkpoint(path):
    try:
        data=json.loads(path.read_text())
        return data if isinstance(data,dict) and isinstance(data.get('days'),dict) else None
    except (OSError,ValueError,TypeError):return None


def run_collection(args):
    import akshare as ak
    single_source=getattr(args,'source_policy','sina')=='sina'
    started=time.monotonic();budget=RequestBudget(args.interval);budget.install()
    out=Path(args.out);cache=out/'checkpoint';cache.mkdir(parents=True,exist_ok=True)
    now=datetime.now(ZoneInfo('Asia/Shanghai'));today=now.date().isoformat()
    universe_payload=load_universe(out/'universe.json',today,ak.stock_info_a_code_name)
    universe_items=universe_payload['rows'];universe=universe_items
    calendar_file=out/'calendar.json'
    saved_calendar=json.loads(calendar_file.read_text()) if calendar_file.exists() else {}
    if saved_calendar.get('calendarAsOf')==today:
        trading=saved_calendar['tradingDates']
    else:
        calendar=ak.tool_trade_date_hist_sina()
        trading=[str(d) for d in calendar.trade_date if six_month_start(today)<=str(d)<today or (str(d)==today and now.strftime('%H:%M')>='15:00')]
        write_json(calendar_file,{'calendarAsOf':today,'tradingDates':trading,'retentionMonths':6,'retentionStart':six_month_start(today)})
    explicit=getattr(args,'dates',None)
    dates=validate_dates(explicit.split(','),today,trading) if explicit else sorted(trading)[-(186 if getattr(args,'months',None)==6 else args.days):]
    if not dates:raise RuntimeError('No completed trading dates available')
    selected=getattr(args,'symbols',None)
    if selected:
        symbols=set(selected.split(','));known={item['code'] for item in universe_items}
        if not symbols<=known:raise ValueError('Unknown A-share stock code')
        items=[item for item in universe_items if item['code'] in symbols]
    else:items=universe_items if args.full else universe_items[:args.limit or 3]
    get_minute=(lambda symbol:minute_unadjusted(ak,symbol)) if getattr(args,'efficient_sina',False) else (lambda symbol:ak.stock_zh_a_minute(symbol=symbol,period='15',adjust=''))
    bao_enabled=not single_source and getattr(args,'baostock_fallback',False)
    reference=None if bao_enabled else get_minute('sz000001')
    fallback_budget=FallbackBudget();bao_budget=FallbackBudget(max_stocks=5556)
    bao_session=None
    if bao_enabled:
        try:from .baostock_source import BaoStockSession
        except ImportError:from baostock_source import BaoStockSession
        bao_session=BaoStockSession(timeout=20,interval=args.interval)
    records={};queue=[];errors=0;attempted_this_run=0;publish_count=0;last_published_attempt=-1
    defer_retries=getattr(args,'resume_attempted',False)
    retries=[]
    for item in items:
        previous=load_checkpoint(cache/f"{item['code']}.json") if args.resume else None
        if previous and all(d in previous.get('days',{}) for d in dates):records[item['code']]=previous
        if defer_retries and attempted_checkpoint(previous,dates) and not (bao_enabled and needs_baostock_checkpoint(previous,dates)):continue
        if not reusable_checkpoint(previous,dates,today):
            (retries if previous else queue).append(item)
    queue.extend(retries)
    attempted_codes={code for code,record in records.items() if attempted_checkpoint(record,dates)}
    print(json.dumps({'stage':'preparing','totalStocks':len(items),'completedStocks':len(attempted_codes),'dates':dates}),flush=True)
    limit=args.max_stocks if args.max_stocks is not None else (args.limit if args.full else None)
    if limit is not None:queue=queue[:limit]
    exit_reason='completed'
    def flush():
        nonlocal publish_count,last_published_attempt
        results,scope,attempted=make_snapshot(items,records,dates,args.full)
        if attempted_this_run!=last_published_attempt:
            publish(results,dates,out,scope,len(universe),attempted_count=attempted,
                    reconcile_latest_first=getattr(args,'reconcile_latest_first',False),baostock_fallback=bao_enabled,
                    single_source=single_source)
            publish_count+=1;last_published_attempt=attempted_this_run
        write_json(out/'run-status.json',{'startedAt':now.isoformat(timespec='seconds'),'updatedAt':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),'elapsedSeconds':round(time.monotonic()-started,2),'scope':scope,'targetCount':len(items),'attemptedCount':attempted,'pendingCount':len(items)-attempted,'attemptedThisRun':attempted_this_run,'httpRequests':budget.request_count,'cacheHits':budget.cache_hits,'publishCount':publish_count,'exitReason':exit_reason,'dates':dates})
    try:
        for item in queue:
            if STOP_REQUESTED.is_set():
                exit_reason='interrupted';raise KeyboardInterrupt
            code=item['code'];symbol=market(code).lower()+code
            budget.clear_cache();minute=pd.DataFrame();daily=pd.DataFrame();failure=None
            previous=records.get(code)
            repair_only=bao_enabled and attempted_checkpoint(previous,dates) and needs_baostock_checkpoint(previous,dates)
            if repair_only:
                by_day={day:safe_saved_row(previous['days'][day]) for day in dates}
                failure='PreviousSourceFailure' if previous.get('fetchStatus')=='error' else None
            else:
                for attempt in range(1 if single_source else 2):
                    try:
                        minute=reference if code=='000001' and reference is not None else get_minute(symbol)
                        daily=ak.stock_zh_a_daily(symbol=symbol,start_date=min(dates).replace('-',''),end_date=max(dates).replace('-',''),adjust='')
                        by_day={d:(evaluate_downloaded(code,item['name'],d,minute,daily,market) if single_source
                                   else evaluate(code,item['name'],d,minute,daily)) for d in dates}
                        def fresh_primary():
                            fresh_minute=get_minute(symbol)
                            fresh_daily=ak.stock_zh_a_daily(symbol=symbol,start_date=min(dates).replace('-',''),end_date=max(dates).replace('-',''),adjust='')
                            return fresh_minute,fresh_daily
                        by_day=by_day if single_source else reconcile_stock(code,item['name'],dates,by_day,fresh_primary,
                            lambda selected_dates:eastmoney_pair(ak,code,selected_dates),budget.clear_cache,evaluate,fallback_budget,
                            latest_first=getattr(args,'reconcile_latest_first',False))
                        failure=None;break
                    except Exception as exc:
                        failure=type(exc).__name__
                        if attempt==0 and not single_source:
                            budget.clear_cache();time.sleep(3)
            if failure and not repair_only:
                by_day=pending_record(item,dates)['days']
                for row in by_day.values():row['reason']=f'采集失败：{failure}'
            if bao_session is not None:
                by_day=reconcile_baostock(code,item['name'],dates,by_day,
                    lambda selected_dates:bao_session.pair(code,selected_dates),evaluate,bao_budget)
                if any(row.get('baostockOutcome') in ('matched','unverified','missing','suspended') for row in by_day.values()):failure=None
            errors=errors+1 if failure else 0
            record=dict(previous if repair_only else {},code=code,fetchedOn=today,fetchStatus='error' if failure else 'ok',days=by_day)
            write_json(cache/f'{code}.json',record);records[code]=record;attempted_this_run+=1
            attempted_codes.add(code)
            print(json.dumps({'completedStocks':len(attempted_codes),'totalStocks':len(items),'stage':'collecting','completed':attempted_this_run,'batchTarget':len(queue),'code':code,'error':failure,'httpRequests':budget.request_count,'cacheHits':budget.cache_hits,'elapsedSeconds':round(time.monotonic()-started,2)},ensure_ascii=False),flush=True)
            if attempted_this_run%args.publish_every==0:flush()
            if errors>=5:
                exit_reason='circuit_breaker';raise RuntimeError('5 consecutive stock failures; collection stopped. Check connectivity, then resume.')
    except KeyboardInterrupt:
        exit_reason='interrupted';raise
    except Exception:
        if exit_reason=='completed':exit_reason='failed'
        raise
    finally:
        try:flush()
        finally:
            if bao_session is not None:bao_session.close()


def main():
    import fcntl,signal
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-policy',choices=['sina'],default='sina')
    parser.add_argument('--full',action='store_true',help='Target the entire current A-share universe; uncollected rows remain explicitly missing')
    parser.add_argument('--limit',type=int,help='Sample size (default 3), or per-run maximum when combined with --full')
    parser.add_argument('--max-stocks',type=int,help='Maximum number of new/retried stocks in this invocation, useful with --full --resume')
    parser.add_argument('--months',type=int,choices=[6]);parser.add_argument('--dates',help='Explicit comma-separated completed trading dates');parser.add_argument('--symbols',help='Explicit comma-separated A-share codes');parser.add_argument('--days',type=int,default=5);parser.add_argument('--out',default='var/data');parser.add_argument('--resume',action='store_true');parser.add_argument('--interval',type=float,default=.75)
    parser.add_argument('--baostock-fallback',action='store_true',help='Enable independent same-provider BaoStock 15-minute/daily repair for SH/SZ')
    parser.add_argument('--efficient-sina',action='store_true',help='Skip only the fingerprint-verified unused qfq lookup for unadjusted minutes')
    parser.add_argument('--reconcile-latest-first',action='store_true',help='During initial backfill, retry conflicts only for the latest requested day; retain older conflicts and initial evidence as unverified')
    parser.add_argument('--resume-attempted',action='store_true',help='Resume a full traversal across restarts without repeatedly retrying already attempted versioned stock-days; does not mark failures as verified')
    parser.add_argument('--publish-every',type=int,default=50,help='Publish every N completed stocks; each stock is always checkpointed')
    args=parser.parse_args()
    if not 1<=args.days<=186:parser.error('--days must be 1..186')
    if args.resume_attempted and not args.resume:parser.error('--resume-attempted requires --resume')
    if args.symbols and args.full:parser.error('--symbols and --full are mutually exclusive')
    for name in ('limit','max_stocks','publish_every'):
        value=getattr(args,name)
        if value is not None and value<1:parser.error(f'--{name.replace("_","-")} must be positive')
    if args.interval<.5:parser.error('--interval must be at least 0.5 seconds')
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    def interrupted(signum,frame):STOP_REQUESTED.set()
    signal.signal(signal.SIGTERM,interrupted)
    with (out/'.collector.lock').open('a') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:parser.exit(2,'Another collector is already writing this output directory.\n')
        try:
            run_collection(args)
            if STOP_REQUESTED.is_set():parser.exit(130,'Collection stopped; checkpoints retained.\n')
        except KeyboardInterrupt:parser.exit(130,'Collection interrupted; completed stock checkpoints and snapshot retained.\n')

if __name__=='__main__':main()
