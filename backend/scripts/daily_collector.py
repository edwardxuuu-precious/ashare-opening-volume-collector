#!/usr/bin/env python3
"""Four isolated primary workers, one serial backup queue, and a single durable writer."""
from __future__ import annotations
import argparse
import fcntl
import json
import multiprocessing as mp
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import collect
from scripts.daily_state import (accept_rows, bao_tasks, build_state, counters, load_calendar,
                                journal_attempt, mark_attempt, merge_checkpoint, primary_tasks, read_json, replay_attempts, persist_state, resume_review_attempts)
from scripts.reconciliation import (FallbackBudget, eastmoney_pair, reconcile_stock,
                                    reconcile_baostock, baostock_supported)
from scripts.universe_cache import load_universe, validate_universe
from scripts.retention import accumulated_manifest

ZONE = ZoneInfo('Asia/Shanghai')
STOP = False
PRIMARY = None
BAO = None


def local_now():
    return datetime.now(ZONE)


def before_cutoff(now, cutoff):
    return now.strftime('%H:%M') < cutoff


def deadline_for(now, cutoff):
    hour, minute = map(int, cutoff.split(':'))
    return time.monotonic() + max(0, (now.replace(hour=hour, minute=minute, second=0, microsecond=0)-now).total_seconds())


class RequestWindowClosed(TimeoutError):
    pass


class SharedHTTPBudget:
    """A permit is shared across *all* spawned processes, including hidden AKShare calls."""
    def __init__(self, lock, last, count, byte_count, interval, deadline):
        self.lock, self.last, self.count = lock, last, count
        self.byte_count, self.interval, self.deadline = byte_count, interval, deadline

    def reserve(self):
        with self.lock:
            delay = max(0, self.interval - (time.monotonic()-self.last.value))
            if time.monotonic()+delay >= self.deadline:
                raise RequestWindowClosed('Daily request cutoff reached')
            time.sleep(delay)
            self.last.value = time.monotonic()
            self.count.value += 1
            return self.last.value

    def install(self):
        import requests
        original = requests.sessions.Session.request
        def bounded(session, method, url, **kwargs):
            self.reserve()
            kwargs['timeout'] = max(.1, min(20, self.deadline-time.monotonic()))
            response = original(session, method, url, **kwargs)
            response.raise_for_status()
            with self.lock:
                self.byte_count.value += len(response.content)
            return response
        requests.sessions.Session.request = bounded


class SharedFallbackBudget:
    def __init__(self, lock, attempted, failures, maximum=50):
        self.lock, self.attempted, self.failures, self.maximum = lock, attempted, failures, maximum

    def reserve(self):
        with self.lock:
            if self.failures.value >= 3:
                return '备用接口连续失败，已暂停本轮备用请求'
            if self.attempted.value >= self.maximum:
                return '已达本轮备用核验上限，等待后续重试'
            self.attempted.value += 1

    def result(self, success):
        with self.lock:
            self.failures.value = 0 if success else self.failures.value+1


def init_primary(http, fallback):
    global PRIMARY
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    http.install()
    import akshare as ak
    PRIMARY = (ak, fallback)


def fetch_primary(task):
    """No filesystem writes in children. One initial pair and at most one conflict refresh."""
    code, name, dates = task
    ak, fallback = PRIMARY
    def pair():
        minute = collect.minute_unadjusted(ak, collect.market(code).lower()+code)
        daily = ak.stock_zh_a_daily(symbol=collect.market(code).lower()+code,
            start_date=min(dates).replace('-', ''), end_date=max(dates).replace('-', ''), adjust='')
        return minute, daily
    try:
        minute, daily = pair()
        initial = {day: collect.evaluate(code, name, day, minute, daily) for day in dates}
        rows = reconcile_stock(code, name, dates, initial, pair,
            lambda selected: eastmoney_pair(ak, code, selected), lambda: None,
            collect.evaluate, fallback)
        return code, rows
    except Exception as exc:
        rows = collect.pending_record({'code':code, 'name':name}, dates)['days']
        for row in rows.values():
            row['reason'] = '采集失败：'+type(exc).__name__
        return code, rows


def init_bao(interval):
    global BAO
    from multiprocessing.util import Finalize
    from scripts.baostock_source import BaoStockSession
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    session = BaoStockSession(timeout=20, interval=interval)
    Finalize(session, session.close, exitpriority=10)
    BAO = (session, FallbackBudget(max_stocks=5556))


def fetch_bao(task):
    code, name, rows = task
    session, budget = BAO
    return code, reconcile_baostock(code, name, list(rows), rows,
        lambda dates: session.pair(code, dates), collect.evaluate, budget)


def publish_changes(out, items, dirty, sample=False, universe_total=None):
    """Only changed dates; each date is constructed separately to bound memory."""
    for day, updates in sorted(dirty.items(), reverse=True):
        previous = read_json(out / (day+'.json'))
        rows = {row['code']: row for row in previous.get('rows', [])}
        rows.update(updates)
        results = []
        for item in items:
            row = rows.get(item['code']) or collect.pending_record(item, [day])['days'][day]
            pending = row.get('reason') == '尚未采集'
            results.append(dict(code=item['code'], fetchStatus='pending' if pending else 'ok', days={day:row}))
        attempted = sum(record['fetchStatus'] != 'pending' for record in results)
        scope = 'sample' if sample else ('full' if attempted == len(items) else 'partial')
        collect.publish(results, [day], out, scope,
                        universe_total or len(items), attempted_count=attempted, baostock_fallback=True, apply_retention=False)


def review_dates(out, args, today):
    """Freeze an explicit review to existing saved dates strictly before the as-of date."""
    review_id = getattr(args, 'review_id', None)
    as_of = getattr(args, 'review_as_of', None)
    if not review_id and not as_of:
        return None
    if not review_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', review_id):
        raise ValueError('Review ID must be 1..64 safe characters')
    if not as_of or datetime.strptime(as_of, '%Y-%m-%d').date().isoformat() != as_of or as_of > today:
        raise ValueError('Review as-of must be a valid date no later than today')
    selected = getattr(args, 'dates', None)
    if not selected:
        raise ValueError('Review requires explicit saved dates')
    dates = sorted(set(selected.split(',')))
    saved = {entry['date']: entry for entry in read_json(out/'manifest.json').get('dates', [])}
    for day in dates:
        if datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day or day >= as_of:
            raise ValueError('Review dates must strictly precede the review as-of date')
        entry = saved.get(day)
        if not entry or entry.get('file') != day+'.json' or not (out/(day+'.json')).is_file():
            raise ValueError('Review only accepts existing saved manifest dates')
    return dates


def run(args):
    global STOP
    started = local_now()
    started_mono = time.monotonic()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    selected_review_dates = review_dates(out, args, started.date().isoformat())
    review_cycle = (f'review:{args.review_as_of}:{args.review_id}' if selected_review_dates is not None else None)
    context = mp.get_context('spawn')
    http = SharedHTTPBudget(context.Lock(), context.Value('d', 0), context.Value('q', 0),
                            context.Value('q', 0), args.interval, deadline_for(started, args.cutoff))
    state_path = out / 'daily-state.json'
    previous = read_json(state_path)
    state = previous
    items = read_json(out/'universe.json').get('rows', [])
    dirty = {}
    attempts_run = publishes = 0
    changed = False
    recovered_rows = 0
    source_failures = dict(sina=0, eastmoney=0, baostock=0)
    primary_pool = bao_pool = None
    active = {}
    backup = None
    exit_reason = 'completed'
    cycle = max(previous.get('dates') or [''])
    universe_total = len(items)

    def status(stage):
        metrics = counters(state, len(items), max(state['dates']), attempt_cycle=cycle) if state.get('dates') else dict(
            completedStocks=0,totalStocks=len(items),completedStockDates=0,totalStockDates=0,
            pendingStockDates=0,attemptedStockDates=0,latestTraversalCompleted=False,traversalCompleted=False)
        review_complete = bool(review_cycle) and exit_reason not in ('cutoff', 'interrupted', 'failed') and all(
            state.get('attempts', {}).get(code, {}).get(day, {}).get('primaryDone') == cycle
            and (not baostock_supported(code) or state.get('attempts', {}).get(code, {}).get(day, {}).get('baoDone') == cycle)
            for code, days in state.get('pending', {}).items() for day in days)
        report = dict(metrics, reviewCompleted=review_complete, stage=stage, startedAt=started.isoformat(timespec='seconds'),
            updatedAt=local_now().isoformat(timespec='seconds'), dates=state.get('dates', []),
            targetCount=metrics['totalStocks'], attemptedCount=metrics['completedStocks'],
            pendingCount=metrics['pendingStockDates'], attemptedThisRun=attempts_run,
            httpRequests=http.count.value, httpResponseBytes=http.byte_count.value, cacheHits=0,
            publishCount=publishes, exitReason=exit_reason,
            noOp=not changed and exit_reason in ('no_work', 'retry_next_trading_day'),
            recoveredRows=recovered_rows,
            lastSuccessfulUpdate=state.get('lastSuccessfulUpdate'),
            todayTargetCount=len(items) if cycle == local_now().date().isoformat() else 0,
            todayAttemptedCount=metrics['completedStocks'] if cycle == local_now().date().isoformat() else 0,
            latestDate=max(state.get('dates') or ['']), reviewId=getattr(args, 'review_id', None),
            reviewAsOf=getattr(args, 'review_as_of', None), sourceFailureCounts=source_failures,
            elapsedSeconds=round(time.monotonic()-started_mono, 2))
        collect.write_json(out / 'run-status.json', report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return report

    def save(compact=False):
        state['updatedAt'] = local_now().isoformat(timespec='seconds')
        persist_state(out, state, compact=compact)

    def flush():
        nonlocal publishes
        if dirty:
            publish_changes(out, items, dirty, sample=bool(getattr(args, 'symbols', None)), universe_total=universe_total)
            if review_cycle:
                collect.write_json(out/'manifest.json', accumulated_manifest(read_json(out/'manifest.json')))
            publishes += 1
            dirty.clear()

    def accept(code, rows, provider='primary'):
        nonlocal changed, attempts_run
        path = out / 'checkpoint' / (code+'.json')
        previous_record = read_json(path)
        if review_cycle and not set(rows) <= set(state['pending'].get(code, [])):
            raise ValueError('Review source returned a date outside the pending review scope')
        rows = {day:dict(row, dailyAttempts=dict(previous_record.get('days', {}).get(day, {}).get('dailyAttempts', {}),
                         **{provider:cycle})) for day,row in rows.items()}
        record = merge_checkpoint(previous_record, code, rows, local_now().date().isoformat(),
                                  preserve_history=bool(review_cycle))
        collect.write_json(path, record)
        actual = {day: record['days'][day] for day in rows}
        failures = {attempt.get('source') for row in rows.values()
                    for attempt in row.get('reconciliationAttempts', []) if attempt.get('status') == 'error'}
        if provider == 'primary' and any(str(row.get('reason', '')).startswith('采集失败') for row in rows.values()):
            failures.add('sina')
        for source in failures & ({'baostock'} if provider == 'bao' else {'sina', 'eastmoney'}):
            source_failures[source] += 1
        accept_rows(state, code, actual)
        mark_attempt(state, code, list(actual), provider+'Done', cycle)
        for day, row in actual.items():
            dirty.setdefault(day, {})[code] = row
        changed = True
        attempts_run += 1
        if attempts_run % args.publish_every == 0:
            flush()
        status('collecting')

    def recover(code, day, row):
        nonlocal changed, recovered_rows
        dirty.setdefault(day, {})[code] = row
        changed = True
        recovered_rows += 1
        if sum(len(rows) for rows in dirty.values()) >= 50000:
            flush()

    try:
        if not before_cutoff(started, args.cutoff):
            exit_reason = 'cutoff'
            status('paused')
            return 0
        http.install()
        import akshare as ak
        if selected_review_dates is not None:
            dates = selected_review_dates
        else:
            calendar = load_calendar(out, started, ak.tool_trade_date_hist_sina)
            dates = calendar['tradingDates']
            if getattr(args, 'dates', None):
                dates = collect.validate_dates(args.dates.split(','), started.date().isoformat(), dates)
        if not dates:
            state = dict(version=1, dates=[], pending={}, attempts={}, observed={})
            exit_reason = 'no_work'
            return 0
        cycle = review_cycle or max(dates)
        catalog = read_json(out / 'universe.json')
        # A holiday uses the last validated membership, without downloading stock metadata again.
        if not review_cycle and (cycle == started.date().isoformat() or not catalog):
            catalog = load_universe(out/'universe.json', started.date().isoformat(), ak.stock_info_a_code_name)
        else:
            validate_universe(catalog)
        items = catalog['rows']
        universe_total = len(items)
        if getattr(args, 'symbols', None):
            selected = set(args.symbols.split(','))
            if not selected or not selected <= {item['code'] for item in items}:
                raise ValueError('Unknown A-share stock code')
            items = [item for item in items if item['code'] in selected]
        names = {item['code']: item['name'] for item in items}
        if review_cycle:
            previous_review = previous.get('review', {})
            if previous_review.get('id') == args.review_id and (previous_review.get('dates') != dates or previous_review.get('asOf') != args.review_as_of):
                raise ValueError('An existing review ID cannot change its frozen date scope')
        state = build_state(out, items, dates, previous, recovery=recover, review_cycle=review_cycle)
        if review_cycle:
            state['review'] = dict(id=args.review_id, asOf=args.review_as_of, dates=dates, cycle=review_cycle)
        replay_attempts(out, state, review_cycle=review_cycle)
        if review_cycle:
            resume_review_attempts(state, review_cycle)
        save()
        flush()
        status('preparing')
        fallback = SharedFallbackBudget(context.Lock(), context.Value('i', 0), context.Value('i', 0))
        queued = primary_tasks(state, cycle)
        while True:
            now = local_now()
            if STOP or not before_cutoff(now, args.cutoff):
                exit_reason = 'interrupted' if STOP else 'cutoff'
                break
            for code, (future, selected) in list(active.items()):
                if future.ready():
                    result_code, rows = future.get()
                    accept(result_code, rows)
                    del active[code]
            if backup and backup[0].ready():
                result_code, rows = backup[0].get()
                accept(result_code, rows, provider='bao')
                backup = None
            while queued and len(active) < args.workers:
                if STOP or time.monotonic() >= http.deadline:
                    break
                code, selected = queued.pop(0)
                if primary_pool is None:
                    primary_pool = context.Pool(args.workers, initializer=init_primary, initargs=(http, fallback))
                journal_attempt(out, state, code, selected, 'primary', cycle)
                active[code] = (primary_pool.apply_async(fetch_primary, ((code, names[code], selected),)), selected)
            now = local_now()
            if backup is None and not STOP and time.monotonic() < http.deadline:
                ready = [(code, days) for code, days in bao_tasks(state, cycle, now, args.bao_after)
                         if code not in active]
                if ready:
                    code, selected = ready[0]
                    record = read_json(out/'checkpoint'/(code+'.json'))
                    initial = {day: record['days'][day] for day in selected if day in record.get('days', {})}
                    if review_cycle:
                        for day in selected:
                            if day not in initial:
                                initial[day] = next((row for row in read_json(out/(day+'.json')).get('rows', [])
                                                     if row.get('code') == code),
                                                    collect.pending_record({'code':code, 'name':names[code]}, [day])['days'][day])
                    journal_attempt(out, state, code, selected, 'bao', cycle)
                    if initial:
                        if bao_pool is None:
                            bao_pool = context.Pool(1, initializer=init_bao, initargs=(args.interval,))
                        backup = (bao_pool.apply_async(fetch_bao, ((code, names[code], initial),)), code)
            if not queued and not active and backup is None:
                waiting = any(now.date().isoformat() in days and baostock_supported(code)
                              and state['attempts'].get(code, {}).get(now.date().isoformat(), {}).get('bao') != cycle
                              for code, days in state['pending'].items()) and now.strftime('%H:%M') < args.bao_after
                if waiting:
                    flush()
                    status('waiting')
                    time.sleep(min(10, max(.1, http.deadline-time.monotonic())))
                    continue
                exit_reason = 'retry_next_trading_day' if state['pending'] else ('completed' if changed else 'no_work')
                break
            time.sleep(.1)
    except Exception:
        exit_reason = 'failed'
        raise
    finally:
        for pool in (primary_pool, bao_pool):
            if pool is not None:
                if STOP or exit_reason in ('cutoff', 'failed', 'interrupted'):
                    pool.terminate()
                else:
                    pool.close()
                pool.join()
        flush()
        if changed and not review_cycle:
            collect.retain_six_months(out, local_now().date().isoformat())
        if state.get('dates'):
            save(compact=True)
        status('paused' if exit_reason in ('cutoff', 'failed', 'interrupted') else 'completed')
    return 130 if STOP else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--interval', type=float, default=.75)
    parser.add_argument('--cutoff', default='21:55')
    parser.add_argument('--bao-after', default='20:05')
    parser.add_argument('--publish-every', type=int, default=50)
    parser.add_argument('--symbols', help='Explicit stock codes for an isolated sample/probe output directory')
    parser.add_argument('--dates', help='Explicit comma-separated completed trading dates; saved history allowed in review mode')
    parser.add_argument('--review-id', help='Unique bounded review ID; reuse it to resume without repeat attempts')
    parser.add_argument('--review-as-of', help='Exclude this date and all later dates from a historical review')
    args = parser.parse_args()
    if not 1 <= args.workers <= 4 or args.interval < .75 or args.publish_every < 1:
        parser.error('workers must be 1..4, interval >= .75, publish-every >= 1')
    for value in (args.cutoff, args.bao_after):
        try:
            datetime.strptime(value, '%H:%M')
            if len(value) != 5:
                raise ValueError()
        except ValueError:
            parser.error('Time must be HH:MM')
    if args.bao_after >= args.cutoff:
        parser.error('BaoStock start must precede cutoff')
    def stop(signum, frame):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out/'.collector.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(2, 'Another collector owns this output directory.\n')
        return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
