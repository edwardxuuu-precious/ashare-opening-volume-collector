"""One leased writer, separate target-day queues, private opening cache and bounded retries."""
import multiprocessing as mp
import re
import signal
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import collect, refresh, sina_spot
from scripts.exchange_status import (load_sse_suspensions, load_szse_suspensions,
                                    valid_historical_no_trade, SZSE_MAX_CODES)
from scripts.daily_collector import SharedHTTPBudget, deadline_for, publish_changes
from scripts.daily_state import load_calendar, read_json, merge_checkpoint
from scripts.universe_cache import load_universe, validate_universe
from scripts.no_trade_evidence import evidence as reviewed_no_trade_evidence, valid_notice
from scripts.reconciliation import (PRICE_FIELDS, PRICE_REQUIRED_FROM,
                                    STATUS_EXPLANATION_REQUIRED_FROM,
                                    price_counts, special_status_counts,
                                    special_status_explained,
                                    validate_price_row)

ZONE = ZoneInfo('Asia/Shanghai')
SOURCE_HTTP_FAILURE_LIMIT = 20
SOURCE_COOLDOWN_SECONDS = (5, 15, 30, 60, 120)
QUOTE_FRAME_FIELDS = ('pctChange', 'amplitude', *PRICE_FIELDS, 'priceStatus',
                      'priceSourceProvider', 'dailyAdapter', 'quoteTime')


def now():
    return datetime.now(ZONE)


def valid_opening_no_trade(proof, code, day):
    """Only exact-day formal proof resolves an opening without a volume."""
    return bool(isinstance(proof, dict) and proof.get('date') == day and
                (valid_notice(proof, code) or valid_historical_no_trade(proof, code, day)))


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
        no_trade = target.get('openingNoTrade', {})
        if not isinstance(no_trade, dict):
            raise ValueError('Invalid opening no-trade cache')
        for code, proof in no_trade.items():
            if (not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code) or
                    not valid_opening_no_trade(proof, code, day) or
                    code in target.get('openings', {})):
                raise ValueError('Invalid or conflicting opening no-trade proof')
        for phase in ('openingAttempts', 'attempts'):
            for code, attempt in target.get(phase, {}).items():
                if len(code) != 6 or not code.isdigit() or not isinstance(attempt, dict):
                    raise ValueError('Invalid refresh attempt')
                if attempt.get('nextRetryAt'): datetime.fromisoformat(attempt['nextRetryAt'])
                if attempt.get('sourceHold') and not refresh.source_held(attempt, code, day):
                    # An interrupted dispatch retains the last committed observation,
                    # but cannot invent a new held result.
                    if attempt.get('committed') is not False or not refresh.source_held(dict(attempt, committed=True), code, day):
                        raise ValueError('Invalid source hold identity or observation')
    return value


def preserve_published_quote_frame(checkpoint_row, published_row):
    """Keep one already-published atomic quote when the core checkpoint predates it."""
    try:
        checkpoint_price = validate_price_row(checkpoint_row)
        published_price = validate_price_row(published_row)
    except ValueError:
        raise
    if published_price not in ('available', 'not_traded') or checkpoint_price in ('available', 'not_traded'):
        return checkpoint_row
    # Core volume completion and independent OHLC availability are separate.
    # Only a newly confirmed no-trade result supersedes an available frame;
    # missing opening/daily volume must never erase an already published quote.
    if ((published_price == 'available' and checkpoint_row.get('status') == 'suspended'
         and refresh.complete(checkpoint_row)) or
            (published_price == 'not_traded' and checkpoint_row.get('status') == 'ok')):
        return checkpoint_row
    merged = dict(checkpoint_row)
    for key in QUOTE_FRAME_FIELDS:
        if key in published_row:
            merged[key] = published_row[key]
        else:
            merged.pop(key, None)
    if published_price == 'not_traded':
        merged['noTradeEvidence'] = published_row['noTradeEvidence']
    else:
        merged.pop('noTradeEvidence', None)
    validate_price_row(merged, allow_legacy=False)
    return merged


def metrics(items, rows, target, phase):
    codes = {item['code'] for item in items}
    day = target.get('date')
    ok = {code for code in codes if refresh.complete(rows.get(code, {}), day)}
    observed = {code for code in codes if code in ok or target.get('attempts', {}).get(code, {}).get('committed')
                or (code in rows and rows[code].get('reason') != '尚未采集')}
    # Valid downloaded rows from the old collector count as observed, not just new attempts.
    observed |= {code for code in codes if rows.get(code, {}).get('status') in ('ok','suspended')}
    unprocessed = len(codes - observed)
    missing = len(codes - ok)
    opening_cached = codes & set(target.get('openings', {}))
    opening_no_trade = {code for code, proof in target.get('openingNoTrade', {}).items()
                       if code in codes-opening_cached and valid_opening_no_trade(proof, code, day)}
    opening_resolved = opening_cached | opening_no_trade
    opening_observed = opening_resolved | {code for code, attempt in
        target.get('openingAttempts', {}).items() if code in codes and attempt.get('committed')}
    opening_unprocessed = len(codes-opening_observed)
    opening_missing = len(codes-opening_resolved)
    attempts = target.get('openingAttempts' if phase == 'opening' else 'attempts', {})
    checked_at = now()
    quote_pending = {code for code in codes if day and day >= PRICE_REQUIRED_FROM
                     and not refresh.quotes_present(rows.get(code, {}), day)}
    blocked = {code for code in codes-ok if phase != 'opening' and
               refresh.source_held(attempts.get(code, {}), code, day)}
    due = [(datetime.fromisoformat(a['nextRetryAt']), a['nextRetryAt'])
           for code, a in attempts.items() if a.get('nextRetryAt') and
           (code not in opening_resolved if phase == 'opening' else
            code not in ok or code in quote_pending)
           and datetime.fromisoformat(a['nextRetryAt']) > checked_at]
    result_rows = [rows.get(code, {}) for code in codes]
    coverage = price_counts(result_rows)
    explanation_coverage = special_status_counts(result_rows)
    explanation_required = bool(day and day >= STATUS_EXPLANATION_REQUIRED_FROM)
    explanation_pending = (explanation_coverage['specialStatusUnexplained']
                           if explanation_required else 0)
    pending = missing + explanation_pending
    return dict(totalStocks=len(codes), completedStocks=len(observed), unprocessedCount=unprocessed,
        calculableCount=sum(rows.get(code, {}).get('status') == 'ok' for code in ok),
        noTradeCount=sum(rows.get(code, {}).get('status') == 'suspended' for code in ok),
        retryableCount=pending-unprocessed-len(blocked), sourceBlockedCount=len(blocked),
        sourceBlockedCodes=sorted(blocked),
        automaticActionableCount=pending-len(blocked)+len(quote_pending),
        pendingStockDates=pending, pendingCount=pending,
        firstPassComplete=unprocessed == 0, dataComplete=pending == 0,
        openingCachedCount=len(opening_cached), openingNoTradeCount=len(opening_no_trade),
        openingMissingCount=opening_missing, openingUnprocessedCount=opening_unprocessed,
        openingRetryableCount=opening_missing-opening_unprocessed,
        openingFirstPassComplete=opening_unprocessed == 0,
        openingComplete=codes <= opening_resolved,
        nextRetryAt=min(due, key=lambda value: value[0])[1] if due else None,
        **coverage, **explanation_coverage)


def should_finish_after_stop(stopped, active):
    """Stop once an interruption has drained the already-dispatched work."""
    return stopped and not active


def target_complete(summary, phase, day):
    if phase == 'opening':
        return summary.get('openingComplete') is True
    return (summary.get('dataComplete') is True and
            (day < PRICE_REQUIRED_FROM or summary.get('priceDataComplete') is True))


def should_finish_for_deferred_retry(unresolved, active, pending, next_retry_at, checked_at):
    """Return the lease when every remaining item is deliberately backoff-gated."""
    return bool(unresolved) and not active and not pending and bool(next_retry_at) and \
        datetime.fromisoformat(next_retry_at) > checked_at


def source_cooldown_seconds(failure_streak):
    """Back off the whole source after stock-level HTTP failures."""
    index = min(max(1, failure_streak)-1, len(SOURCE_COOLDOWN_SECONDS)-1)
    return SOURCE_COOLDOWN_SECONDS[index]


def source_dispatch_capacity(failure_streak):
    """Probe recovery serially before returning to four concurrent workers."""
    return 1 if failure_streak else 4


def source_recovery_confirmed(first_data_request_at, current, cooldown_until):
    """Ignore successes already in flight when the source entered cooldown."""
    return bool(first_data_request_at) and current >= cooldown_until


def validate_membership_repair(value, day, catalog, today):
    """Validate a reviewed one-code listing-day grant; never fetch its source."""
    fields = {'date', 'code', 'listingDate', 'sourceUrl', 'sourceSha256'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Membership repair requires date/code/listingDate/sourceUrl/sourceSha256')
    code = value['code']
    if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
        raise ValueError('Invalid membership repair code')
    if value['date'] != day or value['listingDate'] != day or day > today:
        raise ValueError('Membership repair must match the exact nonfuture listing day')
    if not day <= catalog['asOf'] <= today:
        raise ValueError('Membership repair catalog does not cover the target day')
    source = value['sourceUrl']
    if not isinstance(source, str) or source != source.strip():
        raise ValueError('Invalid membership repair source URL')
    url = urlsplit(source)
    hosts = {'static.cninfo.com.cn', 'www.sse.com.cn', 'www.szse.cn', 'www.bse.cn'}
    if url.scheme != 'https' or url.netloc not in hosts or not url.path or url.fragment:
        raise ValueError('Membership repair requires an official disclosure source URL')
    digest = value['sourceSha256']
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Invalid membership repair source SHA256')
    member = next((item for item in catalog['rows'] if item['code'] == code), None)
    if member is None:
        raise ValueError('Membership repair code is absent from the validated catalog')
    return member


def ensure_target(cache, day, catalog, saved_payload, today, repair_target=None, phase='catchup'):
    """Preserve frozen members; historical additions require an explicit grant."""
    target = cache['targets'].get(day, {})
    saved_rows = saved_payload.get('rows', [])
    saved_complete = bool(saved_rows and saved_payload.get('universeTotal') == len(saved_rows))
    repair_member = None
    if repair_target is not None:
        repair_member = validate_membership_repair(repair_target, day, catalog, today)
        if not target.get('universe') and not saved_complete:
            raise ValueError('Membership repair requires a frozen or fully saved target scope')
    target = cache['targets'].setdefault(day, target)
    target['date'] = day
    items = target.get('universe')
    if not items:
        if saved_complete and day < today:
            items = [dict(code=row['code'], name=row['name']) for row in saved_rows]
        else:
            items = catalog['rows']
        target['universe'] = items
    known = {item['code'] for item in items}
    eligible = ([repair_member] if repair_member is not None else
                catalog['rows'] if day == today and catalog['asOf'] == day else [])
    additions = [item for item in eligible if item['code'] not in known]
    if additions:
        correction = dict(repair_target or {},
                          mode='explicit_membership_repair' if repair_target else 'same_day_catalog',
                          date=day, catalogAsOf=catalog['asOf'],
                          codes=[item['code'] for item in additions], updatedAt=now().isoformat())
        for field in ('firstPassCompletedAt', 'fullyPublishedAt'):
            if field in target:
                correction[field] = target.pop(field)
        target.setdefault('membershipCorrections', []).append(correction)
        items = [*items, *additions]
        target['universe'] = items
        summary = {key:value for key,value in target.get('summary', {}).items()
                   if key not in ('firstPassCompletedAt', 'fullyPublishedAt', 'publishedAt')}
        target['summary'] = dict(summary, **metrics(
            items, {row['code']:row for row in saved_rows}, target, phase))
    return target, items


def run(args, publisher):
    root = Path(args.root); out = root/'data'
    phase = args.phase
    repair_target = getattr(args, 'repair_target', None)
    if repair_target is not None and (not isinstance(repair_target, dict) or
            repair_target.get('date') != getattr(args, 'target_date', None) or
            phase not in ('close', 'catchup')):
        raise ValueError('Membership repair requires its exact explicit target date and close/catchup phase')
    began = now(); started = time.monotonic()
    previous = read_json(root/'worker-state.json')
    cache = validate_cache(read_json(out/'refresh-state.json', dict(version=1, targets={})))
    def no_work(reason):
        value = dict(previous, status='completed', state='completed', outcome='no_work', phase=phase, noOp=True,
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
    budget = SharedHTTPBudget(ctx.Lock(), ctx.Value('d',0), ctx.Value('q',0),
                              ctx.Value('q',0), .75, deadline,
                              ctx.Value('d',0), ctx.Value('q',0))
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
            explanation_required = historical_day >= STATUS_EXPLANATION_REQUIRED_FROM
            unproven = sum(row.get('status') == 'suspended' and (
                not refresh.complete(row, historical_day) or
                explanation_required and not special_status_explained(row))
                for row in payload.get('rows', []))
        gap = missing + unverified + (universe_total - total) + unproven
        summary = cache['targets'].setdefault(historical_day, {}).setdefault('summary', {})
        if gap:
            # A manifest gap is still missing; it must not erase a committed
            # source hold or make every later current-day run reopen that hole.
            held = summary.get('sourceBlockedCount', 0)
            held = held if type(held) is int and 0 <= held <= gap else 0
            summary.update(pendingCount=gap, retryableCount=gap-held, dataComplete=False,
                           automaticActionableCount=gap-held+summary.get('priceMissing', 0))
        elif entry.get('valid', 0) + suspended == universe_total:
            summary.update(pendingCount=0, retryableCount=0, unprocessedCount=0, dataComplete=True,
                           sourceBlockedCount=0, sourceBlockedCodes=[],
                           automaticActionableCount=summary.get('priceMissing', 0))
            manifest_complete.add(historical_day)
    for historical_day, gap in legacy_backlog.items():
        if historical_day in days and historical_day not in manifest_complete:
            summary = cache['targets'].setdefault(historical_day, {}).setdefault('summary', {})
            # A selective restore omits other completed snapshots. Their current
            # summaries outrank a stale legacy retry; real manifest gaps above
            # have already set dataComplete=False and still take precedence.
            if summary.get('dataComplete') is True:
                continue
            known = max(gap, summary.get('pendingCount', 0))
            held = summary.get('sourceBlockedCount', 0)
            held = held if type(held) is int and 0 <= held <= known else 0
            summary.update(pendingCount=known, retryableCount=max(known-held, summary.get('retryableCount', 0)),
                           dataComplete=False,
                           automaticActionableCount=known-held+summary.get('priceMissing', 0))
    # A completed current-day summary can otherwise hide a later catalog addition
    # before target selection reaches the frozen scope. Only inspect a validated
    # local exact-day catalog here; stale catalogs and history remain frozen.
    if phase == 'catchup' and not getattr(args, 'target_date', None) and today in days and clock >= '15:30':
        current = cache['targets'].get(today, {})
        catalog = read_json(out/'universe.json')
        if current.get('universe') and catalog and catalog.get('asOf') == today:
            validate_universe(catalog)
            ensure_target(cache, today, catalog, read_json(out/(today+'.json')), today)
    day = refresh.select_target(phase, getattr(args, 'target_date', None), days, cache,
                               old_state, began)
    if not day:
        return no_work('今天休市或当前没有到期缺口，已有数据保持不变')
    catalog = read_json(out/'universe.json')
    if day == began.date().isoformat() or not catalog:
        catalog = load_universe(out/'universe.json', began.date().isoformat(), ak.stock_info_a_code_name)
    validate_universe(catalog)
    saved_payload = read_json(out/(day+'.json'))
    target, items = ensure_target(cache, day, catalog, saved_payload, today, repair_target, phase)
    names = {item['code']:item for item in items
             if repair_target is None or item['code'] == repair_target['code']}
    openings = target.setdefault('openings', {})
    opening_no_trade = target.setdefault('openingNoTrade', {})
    attempts = target.setdefault('openingAttempts' if phase == 'opening' else 'attempts', {})
    rows = {row['code']:row for row in read_json(out/(day+'.json')).get('rows', [])}
    dirty = {}
    for item in items:
        code = item['code']
        checkpoint_path = out/'checkpoint'/(code+'.json')
        saved_record = read_json(checkpoint_path)
        row = saved_record.get('days', {}).get(day)
        if row and (not refresh.complete(rows.get(code, {}), day) or refresh.complete(row, day)):
            rows[code] = preserve_published_quote_frame(row, rows.get(code, {}))
        if refresh.volume(rows.get(code, {}).get('first15Volume')):
            openings.setdefault(code, rows[code]['first15Volume'])
        if phase != 'opening' and code in rows:
            if rows[code].get('status') == 'suspended' and not refresh.complete(rows[code], day):
                rows[code] = dict(rows[code], status='missing', ratio=None, reason='旧空响应缺少无交易依据，重新补采')
            # A hosted runner may die after the target snapshot/cache is durable
            # but before the large archive upload. Rebuild only this exact day
            # from its already published valid rows, without another source call.
            if refresh.complete(rows[code], day) and (
                    not refresh.complete(row or {}, day) or rows[code] != row):
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
        targetDate=day, dates=[day], startedAt=began.isoformat(), status='running', state='running', outcome='running',
        historicalPendingCount=historical_pending, collectionSourcePolicy=['sina'], calculationPolicy='download_only',
        calendarDates=days, calendarValidThrough=max(days), speedDegraded=False, attemptedThisRun=0,
        publicationCommitted=False, sourceThrottled=False, sourceHTTPFailureStreak=0,
        message='上午预采开盘量，未发布未收盘指标' if phase=='opening' else '正在补齐目标交易日数据')
    # Do not inherit a previous run's terminal/error flags. In particular,
    # ``noOp`` only describes this invocation's explicit no-work paths.
    for key in ('error','errorDetail','exchangeStatusError','exchangeStatusAvailable',
                'exchangeStatusSource','exchangeStatusMatchCount','exchangeStatusSources',
                'exchangeStatusErrors','exitCode','exitReason',
                'noOp','fullyPublishedAt','firstPassCompletedAt','publishedAt'):
        status.pop(key, None)
    exchange_status_evidence = {}
    unresolved_sh = [code for code in names if code.startswith('6') and (
                     code not in openings and code not in opening_no_trade if phase == 'opening' else
                     not refresh.complete(rows.get(code, {}), day) or
                     rows.get(code, {}).get('status') == 'suspended' and
                     not special_status_explained(rows[code]))]
    if unresolved_sh:
        try:
            exchange_status_evidence = load_sse_suspensions(day)
            exchange_status_evidence = {
                code: value for code, value in exchange_status_evidence.items()
                if code in names
            }
            status.update(exchangeStatusSource='sse',
                          exchangeStatusAvailable=True,
                          exchangeStatusMatchCount=len(exchange_status_evidence))
        except Exception as exc:
            # A status-source outage must not convert an empty quote response into
            # a suspension. The ordinary retry path remains authoritative.
            status.update(exchangeStatusSource='sse', exchangeStatusAvailable=False,
                          exchangeStatusMatchCount=0,
                          exchangeStatusError=type(exc).__name__)
    # Query known no-trade rows and already-attempted rows with neither volume.
    # Untouched history and partial volume observations are not suspension
    # evidence; only an exact-day official record may repair a missing row.
    unexplained_sz = []
    for code in sorted(names):
        if not code.startswith(('00', '30')):
            continue
        row = rows.get(code, {})
        attempt = attempts.get(code, {})
        attempted_missing = (row.get('status') == 'missing'
            and not refresh.volume(row.get('first15Volume'))
            and not refresh.volume(openings.get(code))
            and not refresh.volume(row.get('dailyVolume'))
            and (attempt.get('committed') is True or
                 type(attempt.get('failures')) is int and attempt['failures'] > 0))
        opening_gap = (phase == 'opening' and code not in openings and
                       code not in opening_no_trade and attempt.get('committed') is True)
        if opening_gap or attempted_missing or (row.get('status') == 'suspended' and not special_status_explained(row)):
            unexplained_sz.append(code)
    if unexplained_sz:
        try:
            szse_evidence = load_szse_suspensions(day, unexplained_sz[:SZSE_MAX_CODES])
            exchange_status_evidence.update(szse_evidence)
            status['exchangeStatusSources'] = ['sse', 'szse'] if unresolved_sh else ['szse']
            status['exchangeStatusMatchCount'] = len(exchange_status_evidence)
        except Exception as exc:
            status['exchangeStatusErrors'] = {'szse': type(exc).__name__}
    if phase == 'opening':
        for code in names:
            if code in openings:
                continue
            proof = reviewed_no_trade_evidence(code, day) or exchange_status_evidence.get(code)
            if valid_opening_no_trade(proof, code, day):
                opening_no_trade[code] = proof
                # A no-trade conclusion is a committed observation, not a zero
                # opening. Never publish an unclosed daily row here.
                attempts[code] = refresh.commit_attempt(attempts.get(code, {}), now(), True)
    active = {}; pool = None; changed_count = 0; published = False; last_sync = 0
    source_http_failure_streak = 0; source_throttled = False
    source_cooldown_until = 0.0
    retry_deferred = False
    repair_attempts_exhausted = False
    quote_attempted = set()
    status_attempted = set()
    close_snapshot = None

    def persist():
        threshold = (began.date()-timedelta(days=7)).isoformat()
        for cached_day, saved in cache['targets'].items():
            if cached_day < threshold and saved.get('summary', {}).get('dataComplete'):
                for field in ('openings', 'openingNoTrade', 'openingAttempts', 'attempts', 'universe'):
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
        if phase != 'opening' and target_complete(summary, phase, day) and published:
            target.setdefault('fullyPublishedAt', now().isoformat())
        status.update(summary, updatedAt=now().isoformat(), httpRequests=budget.count.value,
            httpResponseBytes=budget.byte_count.value, firstPassCompletedAt=target.get('firstPassCompletedAt'),
            fullyPublishedAt=target.get('fullyPublishedAt'), publishedAt=target.get('fullyPublishedAt'),
            publicationCommitted=published,
            elapsedSeconds=round(time.monotonic()-started, 2))
        status['firstDataRequestAt'] = target.get(phase+'FirstDataRequestAt')
        if target.get('fullyPublishedAt'): status['lastSuccessfulUpdate'] = target['fullyPublishedAt']
        target['summary'] = dict(summary, firstPassCompletedAt=target.get('firstPassCompletedAt'),
            fullyPublishedAt=target.get('fullyPublishedAt'), publishedAt=target.get('fullyPublishedAt'),
            lastAttemptedAt=began.isoformat())
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
        daily_missing = sum(not refresh.volume(rows.get(code, {}).get('dailyVolume')) for code in names)
        needs_snapshot = (daily_missing / max(1, len(names)) > .01 or
                          any(not refresh.quotes_present(rows.get(code, {}), day) for code in names))
        if phase != 'opening' and day == today and needs_snapshot:
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
                if phase != 'opening' and day >= PRICE_REQUIRED_FROM and code in quote_attempted:
                    success = success and refresh.quotes_present(result['row'], day)
                if not success and any(error.endswith(':HTTPError') for error in result.get('errors', [])):
                    source_http_failure_streak += 1
                    status['sourceHTTPFailureStreak'] = source_http_failure_streak
                    source_cooldown_until = max(
                        source_cooldown_until,
                        time.monotonic()+source_cooldown_seconds(source_http_failure_streak))
                    if source_http_failure_streak >= SOURCE_HTTP_FAILURE_LIMIT:
                        source_throttled = True
                        stopped = True
                        status['sourceThrottled'] = True
                elif source_recovery_confirmed(
                        result.get('firstDataRequestAt'), time.monotonic(),
                        source_cooldown_until):
                    source_http_failure_streak = 0
                    status['sourceHTTPFailureStreak'] = 0
                if refresh.volume(result.get('opening')): openings[code] = result['opening']
                attempt = refresh.commit_attempt(attempts.get(code, {}), now(), success,
                                                  result.get('sourceObservation'))
                if phase != 'opening':
                    row = preserve_published_quote_frame(result['row'], rows.get(code, {}))
                    # Preserve independently captured quote fields and exact history.
                    row = dict(rows.get(code, {}), **{key:value for key,value in row.items()
                               if value is not None or key in ('ratio','reason','pctChange','amplitude',
                                                               *PRICE_FIELDS,'priceStatus')})
                    if result['row'].get('status') == 'ok':
                        row.pop('reason', None)
                    if row.get('priceStatus') != 'available':
                        row.pop('priceSourceProvider', None)
                    if refresh.source_held(attempt, code, day):
                        row['reason'] = ('新浪未提供目标日期09:45开盘量；连续相同观测后已停止自动补采；'
                                         '待精确原始开盘记录或合法无交易证据后复核')
                    record_path = out/'checkpoint'/(code+'.json')
                    saved_record = read_json(record_path)
                    saved_day = saved_record.get('days', {}).get(day, {})
                    if saved_day.get('status') == 'suspended' and not refresh.complete(saved_day, day):
                        saved_record = dict(saved_record, days=dict(saved_record['days'], **{day:rows[code]}))
                    record = merge_checkpoint(saved_record, code, {day:row}, began.date().isoformat(), preserve_history=True)
                    collect.write_json(record_path, record)
                    rows[code] = record['days'][day]; dirty[code] = rows[code]
                attempts[code] = attempt
                status['attemptedThisRun'] += 1
                status['speedDegraded'] |= result['speedDegraded']
                changed_count += 1
                if changed_count % 50 == 0: sync()
            # Once a throttle has stopped new dispatches, drain only the work
            # already in flight and release the runner for a later retry.
            if should_finish_after_stop(stopped, active):
                break
            def quote_repairable(code):
                row = rows.get(code, {})
                return (phase != 'opening' and
                        not refresh.quotes_present(row, day) and
                        ((day == today and (row.get('status') == 'suspended' or
                          close_snapshot is not None and code in close_snapshot['rows'])) or
                         day >= PRICE_REQUIRED_FROM and refresh.complete(row, day)))

            def status_repairable(code):
                row = rows.get(code, {})
                return (phase != 'opening' and row.get('status') == 'suspended' and
                        not special_status_explained(row) and
                        (reviewed_no_trade_evidence(code, day) is not None or
                         code in exchange_status_evidence))

            unresolved = [code for code in names if (
                code not in openings and code not in opening_no_trade if phase == 'opening' else
                not refresh.complete(rows.get(code, {}), day) or
                quote_repairable(code) or status_repairable(code)
            )]
            if not unresolved and not active: break
            source_cooling = not stopped and time.monotonic() < source_cooldown_until
            status['sourceCooldownSeconds'] = round(
                max(0, source_cooldown_until-time.monotonic()), 2)
            pending = [] if source_cooling else [code for code in unresolved if code not in active and (
                status_repairable(code) and code not in status_attempted or
                quote_repairable(code) and code not in quote_attempted and
                refresh.retry_due(attempts.get(code, {}), now()) or
                not refresh.complete(rows.get(code, {}), day) and
                refresh.ready(code, day, phase, attempts.get(code, {}), now(),
                              exchange_status_evidence.get(code))
            )]
            pending.sort(key=lambda code: (bool(attempts.get(code, {}).get('committed')), code))
            capacity = source_dispatch_capacity(source_http_failure_streak)
            for code in pending[:max(0, capacity-len(active))]:
                if stopped or time.monotonic() >= deadline: break
                attempts[code] = dict(attempts.get(code, {}), committed=False, dispatchedAt=now().isoformat())
                persist()
                if pool is None: pool = ctx.Pool(4, initializer=refresh.init_worker, initargs=(budget,))
                task = (names[code], day, openings.get(code), rows.get(code, {}), phase)
                if close_snapshot is not None:
                    task += (close_snapshot['rows'].get(code),)
                if exchange_status_evidence.get(code):
                    if len(task) == 5:
                        task += (None,)
                    task += (exchange_status_evidence[code],)
                if quote_repairable(code):
                    quote_attempted.add(code)
                if status_repairable(code):
                    status_attempted.add(code)
                active[code] = pool.apply_async(refresh.fetch, (task,))
            # Commit the last <50 first-pass rows before waiting on retry timers.
            if not active and not pending:
                if source_cooling:
                    if dirty or time.monotonic()-last_sync >= 60: sync()
                    time.sleep(min(5, max(0, source_cooldown_until-time.monotonic()),
                                   max(0, deadline-time.monotonic())))
                    continue
                next_retry = metrics(items, rows, target, phase).get('nextRetryAt')
                if should_finish_for_deferred_retry(unresolved, active, pending, next_retry, now()):
                    retry_deferred = True
                else:
                    # A reviewed status/quote can remain unresolved after its
                    # one bounded attempt with no future retry timestamp.
                    repair_attempts_exhausted = True
                break
            else:
                time.sleep(.1)
        finished = metrics(items, rows, target, phase)
        complete = target_complete(finished, phase, day)
        source_held = not complete and refresh.all_sources_held(finished)
        status.update(status='completed' if complete else 'paused', state='completed' if complete else 'paused',
            outcome='completed' if complete else 'incomplete_checkpointed',
            exitCode=0, exitReason='completed' if complete else (
                'source_evidence_required' if source_held else
                'source_throttled' if source_throttled else 'source_retry_not_due' if retry_deferred else
                'repair_attempts_exhausted' if repair_attempts_exhausted else
                'interrupted' if stopped else 'cutoff'),
            message=('开盘量预采完成；15:30 开始下载全天量' if phase=='opening' else '目标交易日数据已补齐') if complete else (
                '目标开盘量连续相同观测后仍缺失，已保存断点并停止该项自动补采；待精确新证据复核' if source_held else
                '上游连续拒绝请求，已保存断点并等待新 runner 接力' if source_throttled else
                '所有剩余项尚未到期，已保存断点并等待下一轮到期补采' if retry_deferred else
                '本轮证据核查已尝试且无其他可执行项，断点已保存' if repair_attempts_exhausted else
                '保存断点，等待下一轮到期补采'))
        return 0
    except Exception as exc:
        status.update(status='paused', state='paused', outcome='failed', exitCode=1, exitReason='failed',
                      error=type(exc).__name__, errorDetail=str(exc))
        raise
    finally:
        if pool is not None:
            pool.terminate() if active else pool.close()
            pool.join()
        sync(final=True)
