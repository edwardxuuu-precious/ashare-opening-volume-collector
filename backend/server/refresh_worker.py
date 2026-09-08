"""One leased writer, separate target-day queues, private opening cache and bounded retries."""
import multiprocessing as mp
import signal
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import collect, refresh, sina_spot
from scripts.daily_collector import SharedHTTPBudget, deadline_for, publish_changes
from scripts.daily_state import load_calendar, read_json, merge_checkpoint
from scripts.universe_cache import load_universe, validate_universe

ZONE = ZoneInfo('Asia/Shanghai')


def now():
    return datetime.now(ZONE)


def validate_cache(value):
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('targets'), dict):
        raise ValueError('Invalid refresh cache')
    for day, target in value['targets'].items():
        datetime.strptime(day, '%Y-%m-%d')
        if not isinstance(target, dict): raise ValueError('Invalid target cache')
        if target.get('universe'):
            validate_universe(dict(asOf=day, total=len(target['universe']), rows=target['universe'],
                                   historicalMembership=False), minimum_count=1)
        for code, amount in target.get('openings', {}).items():
            if len(code) != 6 or not code.isdigit() or not refresh.volume(amount):
                raise ValueError('Invalid opening cache identity/volume')
        for phase in ('openingAttempts', 'attempts'):
            for code, attempt in target.get(phase, {}).items():
                if len(code) != 6 or not code.isdigit() or not isinstance(attempt, dict):
                    raise ValueError('Invalid refresh attempt')
                if attempt.get('nextRetryAt'): datetime.fromisoformat(attempt['nextRetryAt'])
    return value


def metrics(items, rows, target, phase):
    codes = {item['code'] for item in items}
    ok = {code for code in codes if refresh.complete(rows.get(code, {}), target.get('date'))}
    observed = {code for code in codes if code in ok or target.get('attempts', {}).get(code, {}).get('committed')
                or (code in rows and rows[code].get('reason') != '尚未采集')}
    # Valid downloaded rows from the old collector count as observed, not just new attempts.
    observed |= {code for code in codes if rows.get(code, {}).get('status') in ('ok','suspended')}
    unprocessed = len(codes - observed)
    missing = len(codes - ok)
    attempts = target.get('openingAttempts' if phase == 'opening' else 'attempts', {})
    due = [a['nextRetryAt'] for code, a in attempts.items() if a.get('nextRetryAt') and
           (code not in target.get('openings', {}) if phase == 'opening' else code not in ok)]
    return dict(totalStocks=len(codes), completedStocks=len(observed), unprocessedCount=unprocessed,
        calculableCount=sum(rows.get(code, {}).get('status') == 'ok' for code in ok),
        noTradeCount=sum(rows.get(code, {}).get('status') == 'suspended' for code in ok),
        retryableCount=missing-unprocessed, pendingStockDates=missing, pendingCount=missing,
        firstPassComplete=unprocessed == 0, dataComplete=missing == 0,
        openingCachedCount=len(codes & set(target.get('openings', {}))),
        openingComplete=codes <= set(target.get('openings', {})), nextRetryAt=min(due) if due else None)


def run(args, publisher):
    root = Path(args.root); out = root/'data'
    phase = args.phase
    began = now(); started = time.monotonic()
    previous = read_json(root/'worker-state.json')
    cache = validate_cache(read_json(out/'refresh-state.json', dict(version=1, targets={})))
    def no_work(reason):
        value = dict(previous, status='completed', state='completed', phase=phase, noOp=True,
                     exitCode=0, exitReason='no_work', updatedAt=began.isoformat(),
                     message=reason)
        collect.write_json(root/'worker-state.json', value)
        publisher.publish_status(value)
        return 0
    clock = began.strftime('%H:%M')
    if clock < '07:00' or clock >= '23:55' or (phase == 'catchup' and
            ('09:40' <= clock < '09:50' or '15:20' <= clock < '15:30')):
        return no_work('当前处于暂停或让位窗口；断点保留')
    if phase == 'opening' and not '09:50' <= clock < '15:20':
        return no_work('当前不在上午预采窗口；未发布未收盘指标')
    ctx = mp.get_context('spawn')
    cutoff = refresh.yield_at(phase, began)
    deadline = min(deadline_for(began, cutoff), started+max(.1, args.max_minutes-10)*60)
    budget = SharedHTTPBudget(ctx.Lock(), ctx.Value('d',0), ctx.Value('q',0), ctx.Value('q',0), .75, deadline)
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    # The parent only makes bounded calendar/universe requests before children start.
    budget.install()
    import akshare as ak
    calendar = load_calendar(out, began, ak.tool_trade_date_hist_sina)
    days = calendar.get('calendarDates', calendar.get('tradingDates', []))
    today = began.date().isoformat()
    old_state = read_json(out/'daily-state.json')
    legacy_backlog = {}
    for pending_dates in old_state.get('pending', {}).values():
        for historical_day in pending_dates:
            if historical_day < today:
                legacy_backlog[historical_day] = legacy_backlog.get(historical_day, 0) + 1

    # The legacy pending queue can omit an entire partial day. Rebuild the
    # historical queue from the current published manifest, and inspect every
    # nominal suspension because an empty response is not no-trade evidence.
    manifest_complete = set()
    for entry in read_json(out/'manifest.json').get('dates', []):
        historical_day = entry.get('date')
        if historical_day not in days or historical_day >= today:
            continue
        total = entry.get('total', 0)
        universe_total = entry.get('universeTotal', total)
        if type(total) is not int or total < 0 or type(universe_total) is not int or universe_total < total:
            raise ValueError('Invalid historical manifest counts')
        missing = entry.get('missing', 0)
        unverified = entry.get('unverified', 0)
        suspended = entry.get('suspended', 0)
        if any(type(value) is not int or value < 0 for value in (missing, unverified, suspended)):
            raise ValueError('Invalid historical manifest counts')
        unproven = 0
        if suspended:
            expected_file = historical_day + '.json'
            if entry.get('file') != expected_file or not (out/expected_file).is_file():
                raise ValueError('Historical suspension file is unavailable')
            payload = read_json(out/expected_file)
            unproven = sum(row.get('status') == 'suspended' and not refresh.complete(row, historical_day)
                           for row in payload.get('rows', []))
        gap = missing + unverified + (universe_total - total) + unproven
        summary = cache['targets'].setdefault(historical_day, {}).setdefault('summary', {})
        if gap:
            summary.update(pendingCount=gap, retryableCount=gap, dataComplete=False)
        elif entry.get('valid', 0) + suspended == universe_total:
            summary.update(pendingCount=0, retryableCount=0, unprocessedCount=0, dataComplete=True)
            manifest_complete.add(historical_day)
    for historical_day, gap in legacy_backlog.items():
        if historical_day in days and historical_day not in manifest_complete:
            summary = cache['targets'].setdefault(historical_day, {}).setdefault('summary', {})
            known = max(gap, summary.get('pendingCount', 0))
            summary.update(pendingCount=known, retryableCount=max(known, summary.get('retryableCount', 0)),
                           dataComplete=False)
    day = refresh.select_target(phase, getattr(args, 'target_date', None), days, cache,
                               old_state, began)
    if not day:
        return no_work('今天休市或当前没有到期缺口，已有数据保持不变')
    catalog = read_json(out/'universe.json')
    if day == began.date().isoformat() or not catalog:
        catalog = load_universe(out/'universe.json', began.date().isoformat(), ak.stock_info_a_code_name)
    validate_universe(catalog)
    items = catalog['rows']; names = {item['code']:item for item in items}
    target = cache['targets'].setdefault(day, {})
    target['date'] = day
    saved_payload = read_json(out/(day+'.json'))
    saved_rows = saved_payload.get('rows', [])
    if target.get('universe'):
        items = target['universe']
    elif saved_rows and saved_payload.get('universeTotal') == len(saved_rows) and day < began.date().isoformat():
        items = [dict(code=row['code'], name=row['name']) for row in saved_rows]
    target.setdefault('universe', items)
    names = {item['code']:item for item in items}
    openings = target.setdefault('openings', {})
    attempts = target.setdefault('openingAttempts' if phase == 'opening' else 'attempts', {})
    rows = {row['code']:row for row in read_json(out/(day+'.json')).get('rows', [])}
    dirty = {}
    for item in items:
        code = item['code']
        checkpoint_path = out/'checkpoint'/(code+'.json')
        saved_record = read_json(checkpoint_path)
        row = saved_record.get('days', {}).get(day)
        if row and (not refresh.complete(rows.get(code, {}), day) or refresh.complete(row, day)):
            rows[code] = row
        if refresh.volume(rows.get(code, {}).get('first15Volume')):
            openings.setdefault(code, rows[code]['first15Volume'])
        if phase != 'opening' and code in rows:
            if rows[code].get('status') == 'suspended' and not refresh.complete(rows[code], day):
                rows[code] = dict(rows[code], status='missing', ratio=None, reason='旧空响应缺少无交易依据，重新补采')
            # A hosted runner may die after the target snapshot/cache is durable
            # but before the large archive upload. Rebuild only this exact day
            # from its already published valid rows, without another source call.
            if refresh.complete(rows[code], day) and not refresh.complete(row or {}, day):
                if not saved_record:
                    saved_record = dict(code=code, days={})
                saved_record = dict(saved_record, days=dict(saved_record.get('days', {}), **{day:rows[code]}))
                collect.write_json(checkpoint_path, merge_checkpoint(saved_record, code, {day:rows[code]},
                    began.date().isoformat(), preserve_history=True))
            dirty[code] = rows[code]
    historical_pending = sum(saved.get('summary', {}).get('pendingCount', 0)
        for historical_day, saved in cache['targets'].items()
        if historical_day < today and historical_day != day
        and saved.get('summary', {}).get('dataComplete') is not True)
    status = dict(previous, id='refresh-'+day+'-'+began.strftime('%H%M%S'), phase=phase,
        targetDate=day, dates=[day], startedAt=began.isoformat(), status='running', state='running',
        historicalPendingCount=historical_pending, collectionSourcePolicy=['sina'], calculationPolicy='download_only',
        calendarDates=days, calendarValidThrough=max(days), speedDegraded=False, attemptedThisRun=0,
        publicationCommitted=False, message='上午预采开盘量，未发布未收盘指标' if phase=='opening' else '正在补齐目标交易日数据')
    # Do not inherit a previous run's terminal/error flags.
    for key in ('error','exitCode','exitReason','fullyPublishedAt','firstPassCompletedAt'):
        status.pop(key, None)
    active = {}; pool = None; changed_count = 0; published = False; last_sync = 0
    close_snapshot = None

    def persist():
        threshold = (began.date()-timedelta(days=7)).isoformat()
        for cached_day, saved in cache['targets'].items():
            if cached_day < threshold and saved.get('summary', {}).get('dataComplete'):
                for field in ('openings', 'openingAttempts', 'attempts', 'universe'):
                    saved.pop(field, None)
        cache['updatedAt'] = now().isoformat()
        collect.write_json(out/'refresh-state.json', cache)

    def sync(final=False):
        nonlocal published, last_sync
        persist()
        if dirty and phase != 'opening':
            publish_changes(out, items, {day:dict(dirty)}, universe_total=len(items), single_source=True, require_no_trade_evidence=True)
            if publisher.publish(all_dates=True) == 0:
                raise RuntimeError('Target-date publication was not committed')
            dirty.clear(); published = True
        summary = metrics(items, rows, target, phase)
        if phase != 'opening' and summary['firstPassComplete']:
            target.setdefault('firstPassCompletedAt', now().isoformat())
        if phase != 'opening' and summary['dataComplete'] and published:
            target.setdefault('fullyPublishedAt', now().isoformat())
        status.update(summary, updatedAt=now().isoformat(), httpRequests=budget.count.value,
            httpResponseBytes=budget.byte_count.value, firstPassCompletedAt=target.get('firstPassCompletedAt'),
            fullyPublishedAt=target.get('fullyPublishedAt'), publicationCommitted=published,
            elapsedSeconds=round(time.monotonic()-started, 2))
        status['firstDataRequestAt'] = target.get(phase+'FirstDataRequestAt')
        if target.get('fullyPublishedAt'): status['lastSuccessfulUpdate'] = target['fullyPublishedAt']
        target['summary'] = dict(summary, firstPassCompletedAt=target.get('firstPassCompletedAt'), fullyPublishedAt=target.get('fullyPublishedAt'))
        persist()
        # The small cache is durable every 50 completions without uploading the full archive.
        publisher.put_json('collector/refresh-state.json', cache)
        publisher.put_json('collector/refresh-status.json', dict(status,
            targets={d:dict(t.get('summary', {})) for d,t in cache['targets'].items()}))
        publisher.publish_status(status)
        collect.write_json(root/'worker-state.json', status)
        collect.write_json(out/'run-status.json', status)
        if final: publisher.save_checkpoint()
        last_sync = time.monotonic()

    try:
        if phase != 'opening' and day == today:
            close_snapshot = sina_spot.load_snapshot(ak, items, day, moment=began)
            status.update(closeSnapshotAvailableCount=close_snapshot['availableCount'],
                          closeSnapshotMissingCount=len(close_snapshot['missingCodes']),
                          closeSnapshotSource=close_snapshot['source'])
        sync()
        while not stopped and time.monotonic() < deadline:
            for code, future in list(active.items()):
                if not future.ready(): continue
                result = future.get(); del active[code]
                if result.get('firstDataRequestAt'):
                    key = phase+'FirstDataRequestAt'
                    target[key] = min(target.get(key, result['firstDataRequestAt']), result['firstDataRequestAt'])
                success = refresh.volume(result.get('opening')) if phase == 'opening' else refresh.complete(result['row'], day)
                if refresh.volume(result.get('opening')): openings[code] = result['opening']
                if phase != 'opening':
                    row = result['row']
                    # Preserve independently captured quote fields and exact history.
                    row = dict(rows.get(code, {}), **{key:value for key,value in row.items() if value is not None or key in ('ratio','reason')})
                    record_path = out/'checkpoint'/(code+'.json')
                    saved_record = read_json(record_path)
                    saved_day = saved_record.get('days', {}).get(day, {})
                    if saved_day.get('status') == 'suspended' and not refresh.complete(saved_day, day):
                        saved_record = dict(saved_record, days=dict(saved_record['days'], **{day:rows[code]}))
                    record = merge_checkpoint(saved_record, code, {day:row}, began.date().isoformat(), preserve_history=True)
                    collect.write_json(record_path, record)
                    rows[code] = record['days'][day]; dirty[code] = rows[code]
                attempts[code] = refresh.commit_attempt(attempts.get(code, {}), now(), success)
                status['attemptedThisRun'] += 1
                status['speedDegraded'] |= result['speedDegraded']
                changed_count += 1
                if changed_count % 50 == 0: sync()
            unresolved = [code for code in names if (code not in openings if phase=='opening' else not refresh.complete(rows.get(code, {}), day))]
            if not unresolved and not active: break
            pending = [code for code in unresolved if code not in active and refresh.ready(code, day, phase, attempts.get(code, {}), now())]
            pending.sort(key=lambda code: (bool(attempts.get(code, {}).get('committed')), code))
            for code in pending[:max(0, 4-len(active))]:
                if stopped or time.monotonic() >= deadline: break
                attempts[code] = dict(attempts.get(code, {}), committed=False, dispatchedAt=now().isoformat())
                persist()
                if pool is None: pool = ctx.Pool(4, initializer=refresh.init_worker, initargs=(budget,))
                task = (names[code], day, openings.get(code), rows.get(code, {}), phase)
                if close_snapshot is not None:
                    task += (close_snapshot['rows'].get(code),)
                active[code] = pool.apply_async(refresh.fetch, (task,))
            # Commit the last <50 first-pass rows before waiting on retry timers.
            if not active and not pending:
                if dirty or time.monotonic()-last_sync >= 60: sync()
                time.sleep(min(5, max(0, deadline-time.monotonic())))
            else:
                time.sleep(.1)
        finished = metrics(items, rows, target, phase)
        complete = finished['openingComplete'] if phase=='opening' else finished['dataComplete']
        status.update(status='completed' if complete else 'paused', state='completed' if complete else 'paused',
            exitCode=0, exitReason='completed' if complete else ('interrupted' if stopped else 'cutoff'),
            message=('开盘量预采完成；15:30 开始下载全天量' if phase=='opening' else '目标交易日数据已补齐') if complete else '保存断点，等待下一轮到期补采')
        return 0
    except Exception as exc:
        status.update(status='paused', state='paused', exitCode=1, exitReason='failed',
                      error=type(exc).__name__, errorDetail=str(exc))
        raise
    finally:
        if pool is not None:
            pool.terminate() if active else pool.close()
            pool.join()
        sync(final=True)
