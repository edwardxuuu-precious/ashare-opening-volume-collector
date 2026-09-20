#!/usr/bin/env python3
"""Backfill atomic unadjusted OHLC and derived quote metrics into saved stock-day files.

Fetch: one AKShare unadjusted daily frame per stock covering its needed dates
(extended a few calendar days earlier for the previous close). Shanghai/Shenzhen use
the windowed Tencent endpoint; Beijing uses Sina (same unadjusted OHLC values). The two
fields never change volume/ratio/status; rows whose quote fields can be filled are
upgraded to the current VERIFICATION_VERSION so the next collector run does not
re-traverse them.

Cache: per-code quote tables persist across runs in --cache, so re-running after a
cutoff or failure only refetches the remaining codes. Fetching runs on a thread pool
with one shared request budget (I/O-bound work; no cross-process queues). A directory
flock on the output folder prevents racing a live collector.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import re
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.reconciliation import (PRICE_FIELDS, PRICE_SCHEMA_VERSION, QUOTE_METHOD,
                                    VERIFICATION_VERSION,
                                    missing_prices, price_counts, quote_observation,
                                    special_status_counts, validate_price_row,
                                    window_start)  # noqa: E402
from scripts.collect import market, write_json  # noqa: E402

ZONE = ZoneInfo('Asia/Shanghai')
DATE_FILE = re.compile(r'^\d{4}-\d{2}-\d{2}\.json$')
STOP = False
CACHE_VERSION = 2
DEFAULT_BACKFILL_ID = 'ohlc-v1-20260920'
DEFAULT_FROM = '2026-03-06'
DEFAULT_TO = '2026-09-18'


def local_now():
    return datetime.now(ZONE)


def deadline_for(now, cutoff):
    hour, minute = map(int, cutoff.split(':'))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return time.monotonic() + max(0, (target - now).total_seconds())


class RequestWindowClosed(TimeoutError):
    pass


class SharedHTTPBudget:
    """One permit shared across all fetcher threads, including hidden AKShare calls."""

    def __init__(self, interval, deadline):
        self.lock = threading.Lock()
        self.last = 0.0
        self.count = 0
        self.interval, self.deadline = interval, deadline

    def reserve(self):
        with self.lock:
            delay = max(0, self.interval - (time.monotonic() - self.last))
            if time.monotonic() + delay >= self.deadline:
                raise RequestWindowClosed('Daily request cutoff reached')
            time.sleep(delay)
            self.last = time.monotonic()
            self.count += 1

    def install(self):
        import requests
        original = requests.sessions.Session.request

        def bounded(session, method, url, **kwargs):
            self.reserve()
            kwargs['timeout'] = max(.1, min(20, self.deadline - time.monotonic()))
            response = original(session, method, url, **kwargs)
            response.raise_for_status()
            return response

        requests.sessions.Session.request = bounded


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def validate_cache(value):
    if not isinstance(value, dict) or value.get('version') != CACHE_VERSION or not isinstance(value.get('quotes'), dict):
        raise ValueError('Invalid price backfill cache')
    if len(value['quotes']) > 6000:
        raise ValueError('Price backfill cache exceeds stock bound')
    for code, entry in value['quotes'].items():
        if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code) or not isinstance(entry, dict):
            raise ValueError('Invalid price backfill cache identity')
        days = entry.get('days', {})
        if not isinstance(days, dict) or len(days) > 186:
            raise ValueError('Invalid price backfill cache dates')
        for day, quote in days.items():
            if datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day or not isinstance(quote, dict):
                raise ValueError('Invalid price backfill cache day')
            validate_price_row(dict(quote, status='ok' if quote.get('priceStatus') == 'available' else 'missing'),
                               allow_legacy=False)
    from scripts.exchange_status import valid_listing_record, valid_historical_no_trade
    listings = value.get('listingEvidence', {})
    statuses = value.get('statusEvidence', {})
    if not isinstance(listings, dict) or len(listings) > 6000:
        raise ValueError('Invalid price backfill listing evidence')
    if any(not valid_listing_record(record, code) for code, record in listings.items()):
        raise ValueError('Invalid price backfill listing evidence')
    if not isinstance(statuses, dict) or len(statuses) > 186:
        raise ValueError('Invalid price backfill status evidence')
    for day, records in statuses.items():
        if not isinstance(records, dict) or len(records) > 6000:
            raise ValueError('Invalid price backfill status evidence')
        if any(not valid_historical_no_trade(record, code, day)
               for code, record in records.items()):
            raise ValueError('Invalid price backfill status evidence')
    return value


def validate_state(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Invalid price backfill state')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value.get('backfillId', '')):
        raise ValueError('Invalid price backfill state identity')
    for key in ('fromDate', 'toDate'):
        if datetime.strptime(value.get(key, ''), '%Y-%m-%d').date().isoformat() != value[key]:
            raise ValueError('Invalid price backfill state date')
    if value['fromDate'] > value['toDate']:
        raise ValueError('Invalid price backfill state range')
    if type(value.get('batchSize')) is not int or not 1 <= value['batchSize'] <= 20:
        raise ValueError('Invalid price backfill state batch size')
    for key in ('selectedDates', 'remainingDates'):
        dates = value.get(key, [])
        if not isinstance(dates, list) or len(dates) > 186 or len(dates) != len(set(dates)):
            raise ValueError('Invalid price backfill state dates')
        for day in dates:
            if datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
                raise ValueError('Invalid price backfill state date')
            if not value['fromDate'] <= day <= value['toDate']:
                raise ValueError('Price backfill state date outside frozen range')
        if dates != sorted(dates, reverse=True):
            raise ValueError('Price backfill state dates must be newest first')
    if len(value['selectedDates']) > value['batchSize']:
        raise ValueError('Price backfill selected dates exceed batch size')
    if value.get('completed') != (not value['remainingDates']):
        raise ValueError('Invalid price backfill completion marker')
    if value.get('exitReason') not in ('completed', 'cutoff', 'interrupted', 'source_incomplete'):
        raise ValueError('Invalid price backfill exit reason')
    try:
        datetime.fromisoformat(value.get('updatedAt', ''))
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid price backfill update timestamp') from exc
    return value


def save_state(out, args, selected_dates, incomplete_dates, changed_at, exit_reason):
    state = dict(version=1, backfillId=args.backfill_id, fromDate=args.from_date,
                 toDate=args.to_date, batchSize=args.batch_size, selectedDates=selected_dates,
                 remainingDates=incomplete_dates, completed=not incomplete_dates,
                 updatedAt=changed_at, exitReason=exit_reason)
    validate_state(state)
    write_json(Path(out)/'price-backfill-state.json', state)
    return state


def completion_result(selected_dates, incomplete_dates, exit_reason):
    """Return a process result for this bounded batch, not the whole history range.

    Older dates remaining after a successful newest-first batch are expected.  A
    selected date that is still incomplete is not: it must keep the workflow red
    so an unavailable quote can never look like a completed publication.
    """
    selected_incomplete = sorted(set(selected_dates) & set(incomplete_dates), reverse=True)
    if exit_reason == 'completed' and selected_incomplete:
        exit_reason = 'source_incomplete'
    return exit_reason, selected_incomplete, 0 if exit_reason == 'completed' else 1


def confirmed_no_trade(row):
    evidence = row.get('noTradeEvidence')
    return row.get('status') == 'suspended' and isinstance(evidence, dict) and bool(evidence)


def complete_price(row):
    try:
        return validate_price_row(row) in ('available', 'not_traded')
    except ValueError:
        return False


def scan_needs(out, cache_quotes, force, start=DEFAULT_FROM, end=DEFAULT_TO, batch_size=20):
    """Return per-code dates for the newest bounded set of incomplete saved days."""
    needs = {}
    rows_seen = 0
    candidate_dates = []
    for path in sorted(Path(out).glob('*.json'), reverse=True):
        if not DATE_FILE.match(path.name):
            continue
        payload = load_json(path)
        day = payload.get('date')
        if day != path.stem or not start <= day <= end or not isinstance(payload.get('rows'), list):
            continue
        if any(not complete_price(row) for row in payload['rows']):
            candidate_dates.append(day)
    selected_dates = candidate_dates[:batch_size]
    # Fetch each stock's complete frozen need window once. Publication remains
    # bounded to selected_dates, while later 20-day batches reuse the v2 cache.
    for day in candidate_dates:
        payload = load_json(Path(out)/(day+'.json'))
        for row in payload['rows']:
            code = row.get('code')
            if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
                continue
            rows_seen += 1
            cached = cache_quotes.get(code, {}).get('days', {}).get(day)
            covered = (not force and isinstance(cached, dict) and
                       cached.get('priceStatus') == 'available')
            if not complete_price(row) and not confirmed_no_trade(row) and not covered:
                needs.setdefault(code, set()).add(day)
    return ({code: sorted(days) for code, days in needs.items()}, rows_seen,
            selected_dates, candidate_dates)


def patch_files(out, quotes, changed_generated_at, selected_dates=None, status_evidence=None):
    """Apply cached quote tables to saved rows; write only files that changed."""
    filled = unfilled = files_changed = 0
    selected_dates = set(selected_dates or [])
    status_evidence = status_evidence or {}
    # Fail the complete batch before the first write if a source says both traded
    # and no-trade for the same stock-day.
    for path in sorted(Path(out).glob('*.json')):
        if not DATE_FILE.match(path.name) or selected_dates and path.stem not in selected_dates:
            continue
        payload = load_json(path)
        day = payload.get('date')
        if day != path.stem or not isinstance(payload.get('rows'), list):
            continue
        for row in payload['rows']:
            code = row.get('code')
            quote = quotes.get(code, {}).get('days', {}).get(day) if code else None
            evidence = status_evidence.get(day, {}).get(code) if code else None
            if isinstance(quote, dict) and quote.get('priceStatus') == 'available' and (
                    evidence or confirmed_no_trade(row)):
                raise ValueError('Quote conflicts with no-trade evidence')
    for path in sorted(Path(out).glob('*.json')):
        if not DATE_FILE.match(path.name):
            continue
        if selected_dates and path.stem not in selected_dates:
            continue
        payload = load_json(path)
        day = payload.get('date')
        if day != path.stem or not isinstance(payload.get('rows'), list):
            continue
        original = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for row in payload['rows']:
            row['verificationVersion'] = VERIFICATION_VERSION
            code = row.get('code')
            table = quotes.get(code, {}).get('days', {}) if code else {}
            if confirmed_no_trade(row):
                row.update(missing_prices('not_traded'))
                row.pop('priceSourceProvider', None)
                filled += 1
                continue
            quote = table.get(day)
            evidence = status_evidence.get(day, {}).get(code) if code else None
            if evidence and (not isinstance(quote, dict) or quote.get('priceStatus') != 'available'):
                row.update(missing_prices('not_traded'))
                row['noTradeEvidence'] = evidence
                row.pop('priceSourceProvider', None)
                filled += 1
                continue
            if not isinstance(quote, dict) or quote.get('priceStatus') != 'available':
                row.update(missing_prices('missing'))
                row.pop('priceSourceProvider', None)
                unfilled += 1
                continue
            for key in (*PRICE_FIELDS, 'priceStatus', 'priceSourceProvider',
                        'dailyAdapter', 'pctChange', 'amplitude'):
                if key in quote:
                    row[key] = quote[key]
            filled += 1
        if json.dumps(payload, ensure_ascii=False, sort_keys=True) != original:
            payload['generatedAt'] = changed_generated_at
            if '涨跌幅' not in payload.get('methodology', ''):
                payload['methodology'] = (payload.get('methodology', '') + QUOTE_METHOD).strip()
            coverage = price_counts(payload['rows'])
            explanation_coverage = special_status_counts(payload['rows'])
            payload.update(priceSchemaVersion=PRICE_SCHEMA_VERSION,
                           priceFields=list(PRICE_FIELDS), **coverage,
                           **explanation_coverage)
            payload['quoteFields'] = dict(source='AKShare · 腾讯日线（沪深）/ 新浪日线（北交所），不复权', filledAt=changed_generated_at,
                                          adjustment='none', unit='CNY/share', priceFields=list(PRICE_FIELDS),
                                          formula='涨跌幅=(收盘-前收)/前收×100%；振幅=(最高-最低)/前收×100%')
            write_json(path, payload)
            files_changed += 1
        else:
            unfilled += sum(not complete_price(row) for row in payload['rows'])
    return dict(filledRows=filled, unfilledRows=unfilled, filesChanged=files_changed)


def historical_evidence(out, selected_dates, listing_catalog, status_catalog):
    """Resolve exact stock-day evidence without changing core or special status."""
    from scripts.exchange_status import listing_evidence
    from scripts.no_trade_evidence import evidence as reviewed_evidence
    result = {}
    for day in selected_dates:
        payload = load_json(Path(out)/(day+'.json'))
        records = {}
        for row in payload.get('rows', []):
            code = row.get('code')
            if not isinstance(code, str):
                continue
            proof = (reviewed_evidence(code, day) or
                     status_catalog.get(day, {}).get(code) or
                     listing_evidence(listing_catalog, code, day))
            if proof:
                records[code] = proof
        result[day] = records
    return result


def touch_manifest(out, changed_generated_at, selected_dates=None):
    manifest_path = Path(out) / 'manifest.json'
    manifest = load_json(manifest_path)
    if not manifest:
        return
    selected_dates = set(selected_dates or [])
    entries = manifest.get('dates') or []
    for entry in entries:
        file = entry.get('file')
        if selected_dates and entry.get('date') not in selected_dates:
            continue
        payload = load_json(out / file) if file and re.fullmatch(r'^\d{4}-\d{2}-\d{2}\.json$', file) else None
        if payload and payload.get('quoteFields'):
            entry['generatedAt'] = payload.get('generatedAt', changed_generated_at)
            for key in ('priceSchemaVersion','priceAvailable','priceNoTrade','priceMissing','priceDataComplete',
                        'specialStatusExplained','specialStatusUnexplained','statusExplanationComplete'):
                entry[key] = payload[key]
    if '涨跌幅' not in manifest.get('methodology', ''):
        manifest['methodology'] = (manifest.get('methodology', '') + QUOTE_METHOD).strip()
    manifest['generatedAt'] = changed_generated_at
    manifest['priceSchemaVersion'] = PRICE_SCHEMA_VERSION
    manifest['priceFields'] = list(PRICE_FIELDS)
    manifest['quoteFields'] = dict(source='AKShare · 腾讯日线（沪深）/ 新浪日线（北交所），不复权', filledAt=changed_generated_at,
                                   adjustment='none', unit='CNY/share', priceFields=list(PRICE_FIELDS))
    write_json(manifest_path, manifest)


def run(args):
    global STOP
    out = Path(args.out)
    if not out.is_dir():
        raise ValueError('Output directory does not exist: ' + str(out))
    cache_path = Path(args.cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    state_path = out/'price-backfill-state.json'
    if state_path.exists():
        previous_state = validate_state(load_json(state_path))
        identity = (previous_state['backfillId'], previous_state['fromDate'],
                    previous_state['toDate'], previous_state['batchSize'])
        requested = (args.backfill_id, args.from_date, args.to_date, args.batch_size)
        if identity != requested:
            raise ValueError('Existing price backfill state belongs to another frozen run')
    cache = load_json(cache_path, dict(version=CACHE_VERSION, quotes={}))
    if cache.get('version') != CACHE_VERSION or not isinstance(cache.get('quotes'), dict):
        cache = dict(version=CACHE_VERSION, quotes={})
    validate_cache(cache)
    cache.setdefault('quotes', {})
    needs, rows_seen, selected_dates, incomplete_dates = scan_needs(
        out, cache['quotes'], args.force, args.from_date, args.to_date, args.batch_size)
    total_codes = len(needs)
    print(json.dumps(dict(stage='scanning', savedRows=rows_seen, codesNeeding=total_codes,
                          daysNeeding=sum(len(d) for d in needs.values()), selectedDates=selected_dates,
                          remainingDates=len(incomplete_dates)), ensure_ascii=False), flush=True)
    if not needs:
        changed_at = local_now().isoformat(timespec='seconds')
        summary = patch_files(out, cache['quotes'], changed_at, selected_dates)
        touch_manifest(out, changed_at, selected_dates)
        remaining_after = scan_needs(out, cache['quotes'], False, args.from_date, args.to_date,
                                     max(1, len(incomplete_dates)))[3]
        exit_reason, selected_incomplete, result = completion_result(
            selected_dates, remaining_after, 'completed')
        save_state(out, args, selected_dates, remaining_after, changed_at, exit_reason)
        print(json.dumps(dict(stage='complete', exitReason=exit_reason,
                              selectedIncompleteDates=selected_incomplete, fetchedCodes=0,
                              refetchedCodes=0, failedCodes=0, **summary),
                          ensure_ascii=False), flush=True)
        return result
    started = local_now()
    budget = SharedHTTPBudget(args.interval, deadline_for(started, args.cutoff))
    budget.install()
    import akshare as ak
    from scripts.exchange_status import (load_listing_catalog, load_market_suspensions,
                                         load_sse_suspensions)
    cache.setdefault('listingEvidence', {})
    cache.setdefault('statusEvidence', {})
    if not cache['listingEvidence']:
        try:
            cache['listingEvidence'] = load_listing_catalog(ak)
        except (Exception, SystemExit):
            cache['listingEvidence'] = {}
    for day in selected_dates:
        if day in cache['statusEvidence']:
            continue
        records = {}; loaded = False
        try:
            records.update(load_market_suspensions(day, ak)); loaded = True
        except (Exception, SystemExit):
            pass
        try:
            official = load_sse_suspensions(day)
            records.update(official); loaded = True
        except (Exception, SystemExit):
            pass
        if loaded:
            cache['statusEvidence'][day] = records
    selected_evidence = historical_evidence(
        out, selected_dates, cache['listingEvidence'], cache['statusEvidence'])

    def fetch(task):
        code, needed = task
        if STOP:
            return code, 'interrupted'
        if time.monotonic() >= budget.deadline:
            return code, 'cutoff'
        try:
            symbol = market(code).lower() + code
            start = window_start(needed, args.lookback)
            end = max(needed).replace('-', '')
            if code.startswith(('60', '68', '00', '30')):
                daily = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end, adjust='')
            else:
                daily = ak.stock_zh_a_daily(symbol=symbol, start_date=start, end_date=end, adjust='')
            provider = 'tencent' if code.startswith(('60', '68', '00', '30')) else 'sina'
            adapter = 'tencent_daily_quote_backfill' if provider == 'tencent' else 'sina_daily_quote_backfill'
            table = {}
            for day in needed:
                quote = quote_observation(daily, day)
                if quote['priceStatus'] == 'available':
                    quote.update(priceSourceProvider=provider, dailyAdapter=adapter)
                table[day] = quote
            return code, table
        except (Exception, SystemExit) as exc:
            return code, type(exc).__name__ + ': ' + str(exc)[:120]

    completed = failed = refetched = 0
    attempts = {code: 0 for code in needs}
    exit_reason = 'completed'
    round_no = 0
    remaining = sorted(needs)
    pool = ThreadPoolExecutor(max_workers=args.workers)
    try:
        while remaining and not STOP and exit_reason == 'completed':
            round_no += 1
            retry_after = []
            for code, table in pool.map(fetch, [(c, needs[c]) for c in remaining]):
                attempts[code] += 1
                if isinstance(table, str):
                    if table in ('cutoff', 'interrupted'):
                        exit_reason = table
                        retry_after.append(code)
                        continue
                    if attempts[code] <= args.retries:
                        retry_after.append(code)
                        continue
                    cache['quotes'].setdefault(code, {}).setdefault('days', {})
                    cache['quotes'][code]['error'] = table
                    failed += 1
                else:
                    entry = cache['quotes'].setdefault(code, {})
                    old_days = entry.setdefault('days', {})
                    for day, pair in table.items():
                        old_days[day] = pair
                    entry.update(fetchedAt=local_now().isoformat(timespec='seconds'), error=None)
                    if attempts[code] > 1:
                        refetched += 1
                    completed += 1
                if (completed + failed) and (completed + failed) % args.save_every == 0:
                    cache['updatedAt'] = local_now().isoformat(timespec='seconds')
                    write_json(cache_path, cache)
                    print(json.dumps(dict(stage='fetching', round=round_no, completedCodes=completed,
                                          failedCodes=failed, remainingCodes=total_codes - completed - failed),
                                      ensure_ascii=False), flush=True)
            if exit_reason == 'completed' and retry_after and round_no <= args.retries:
                time.sleep(3)
                remaining = sorted(set(retry_after))
            else:
                remaining = []
            if exit_reason == 'cutoff':
                for code in retry_after:
                    cache['quotes'].setdefault(code, {})['error'] = 'cutoff：本轮未请求，请重新运行继续'
    except KeyboardInterrupt:
        exit_reason = 'interrupted'
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        cache['updatedAt'] = local_now().isoformat(timespec='seconds')
        write_json(cache_path, cache)
    changed_at = local_now().isoformat(timespec='seconds')
    summary = patch_files(out, cache['quotes'], changed_at, selected_dates, selected_evidence)
    touch_manifest(out, changed_at, selected_dates)
    remaining_after = scan_needs(out, cache['quotes'], False, args.from_date, args.to_date,
                                 max(1, len(incomplete_dates)))[3]
    exit_reason, selected_incomplete, result = completion_result(
        selected_dates, remaining_after, exit_reason)
    save_state(out, args, selected_dates, remaining_after, changed_at, exit_reason)
    print(json.dumps(dict(stage='complete', exitReason=exit_reason, fetchedCodes=completed,
                          refetchedCodes=refetched, failedCodes=failed,
                          selectedIncompleteDates=selected_incomplete, **summary),
                      ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--cache', default='var/work/quote-backfill-cache.json')
    parser.add_argument('--backfill-id', default=DEFAULT_BACKFILL_ID)
    parser.add_argument('--from-date', default=DEFAULT_FROM)
    parser.add_argument('--to-date', default=DEFAULT_TO)
    parser.add_argument('--batch-size', type=int, default=20)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--interval', type=float, default=.35)
    parser.add_argument('--cutoff', default='23:59')
    parser.add_argument('--lookback', type=int, default=14)
    parser.add_argument('--retries', type=int, default=2)
    parser.add_argument('--save-every', type=int, default=200)
    parser.add_argument('--force', action='store_true', help='Refetch codes even when the cache already covers them')
    args = parser.parse_args()
    if (not 1 <= args.workers <= 16 or args.interval < .25 or args.lookback < 1 or args.retries < 0
            or not 1 <= args.batch_size <= 20 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.backfill_id)):
        parser.error('workers must be 1..16, interval >= .25, lookback >= 1')
    for value in (args.from_date, args.to_date):
        if datetime.strptime(value, '%Y-%m-%d').date().isoformat() != value:
            parser.error('backfill dates must be YYYY-MM-DD')
    if args.from_date > args.to_date:
        parser.error('from-date must not be after to-date')
    datetime.strptime(args.cutoff, '%H:%M')

    def stop(signum, frame):
        global STOP
        STOP = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    out = Path(args.out)
    with (out / '.collector.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(2, 'Another collector or backfill owns this output directory.\n')
        return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
