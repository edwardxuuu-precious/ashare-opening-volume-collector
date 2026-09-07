"""Durable stock/date backlog and trading-cycle idempotence for the daily collector."""
from __future__ import annotations
import json
import math
import os
import re
import hashlib
from datetime import datetime
from pathlib import Path
from .retention import six_month_start
from .collect import VERIFICATION_VERSION, safe_saved_row, write_json
from .download_only import POLICY, volumes_present


def successful(row):
    if not isinstance(row, dict) or row.get('verificationVersion') != VERIFICATION_VERSION:
        return False
    if row.get('status') == 'suspended':
        return row.get('ratio') is None
    if row.get('calculationPolicy') == POLICY:
        ratio=row.get('ratio')
        return (row.get('status')=='ok' and volumes_present(row) and type(ratio) in (int,float)
                and math.isfinite(ratio) and abs(ratio-row['first15Volume']/row['dailyVolume']*100)<=1e-5)
    if row.get('status') != 'ok' or row.get('quality') != 'matched' or row.get('dayVolumeDifference') != 0:
        return False
    values = [row.get(k) for k in ('first15Volume', 'dailyVolume', 'minuteDayVolume', 'ratio')]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return False
    first, daily, minute, ratio = values
    return daily > 0 and 0 <= first <= daily and minute == daily and 0 <= ratio <= 100 and abs(ratio-first/daily*100) <= 1e-5


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {} if default is None else default


def checked_rows(payload, day):
    if payload.get('date') != day or not isinstance(payload.get('rows'), list):
        raise ValueError('Cached date/rows mismatch')
    seen = set()
    for raw in payload['rows']:
        row = safe_saved_row(raw)
        code = row.get('code')
        if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code) or code in seen:
            raise ValueError('Invalid or duplicate cached stock identity')
        seen.add(code)
        if row.get('ratio') is not None and not successful(row):
            raise ValueError('Invalid cached verified ratio')
        yield row


def load_calendar(out, now, fetcher):
    """Refresh once per calendar day, keeping the full calendar separate from closed dates."""
    path = Path(out) / 'calendar.json'
    saved = read_json(path)
    today = now.date().isoformat()
    if saved.get('calendarAsOf') != today:
        raw = fetcher()
        values = raw.trade_date if hasattr(raw, 'trade_date') else raw
        dates = sorted({str(d)[:10] for d in values})
        for day in dates:
            datetime.strptime(day, '%Y-%m-%d')
        saved = dict(calendarAsOf=today, calendarDates=dates)
    else:
        dates = saved.get('calendarDates', saved.get('tradingDates', []))
    start = six_month_start(today)
    closed = [d for d in dates if start <= d < today or (d == today and now.strftime('%H:%M') >= '17:00')]
    result = dict(saved, tradingDates=closed, retentionMonths=6, retentionStart=start)
    if result != read_json(path):
        write_json(path, result)
    return result


def row_digest(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).digest()


def build_state(out, items, dates, previous=None, recovery=None, review_cycle=None):
    """Published verified history remains usable even if legacy daily CP discarded old days."""
    out = Path(out)
    codes = {item['code'] for item in items}
    allowed = set(dates)
    successes = {code: set() for code in codes}
    published_verified = {code: set() for code in codes}
    def relevant_cycle(value):
        return value == review_cycle if review_cycle else not str(value).startswith('review:')
    observed = {code: set() for code in codes}
    digests = {code:{} for code in codes} if recovery else None
    finished = {}
    for day in dates:
        payload = read_json(out / (day + '.json'))
        for row in checked_rows(payload, day) if payload else []:
            code = row.get('code')
            if code not in codes:
                continue
            if recovery:
                digests[code][day] = (row_digest(row), row.get('status'))
            if successful(row):
                successes[code].add(day)
                published_verified[code].add(day)
            if row.get('verificationVersion') == VERIFICATION_VERSION and row.get('reason') != '尚未采集':
                observed[code].add(day)
    for code in codes:
        record = read_json(out / 'checkpoint' / (code + '.json'))
        if record and record.get('code') != code:
            raise ValueError('Checkpoint code mismatch')
        for day, row in record.get('days', {}).items():
            if day not in allowed:
                continue
            row = next(checked_rows({'date':day, 'rows':[row]}, day))
            if row['code'] != code:
                raise ValueError('Checkpoint row code mismatch')
            # A targeted review must never downgrade an already published verified row.
            if review_cycle and day in published_verified[code]:
                continue
            if recovery and row.get('verificationVersion') == VERIFICATION_VERSION:
                published_digest, published_status = digests[code].get(day, (None, None))
                actual_missing = (row.get('status') == 'missing' and row.get('reason') != '尚未采集'
                                  and record.get('fetchStatus') != 'pending')
                eligible = row.get('status') != 'missing' or (actual_missing and published_status in (None, 'missing'))
                if eligible and row_digest(row) != published_digest:
                    recovery(code, day, row)
            for provider, cycle in row.get('dailyAttempts', {}).items():
                if provider in ('primary', 'bao') and relevant_cycle(cycle):
                    finished.setdefault(code, {}).setdefault(day, {}).update({provider:cycle, provider+'Done':cycle})
            if successful(row):
                successes[code].add(day)
            elif row.get('status') == 'unverified':
                successes[code].discard(day)
            if record.get('fetchStatus') != 'pending' and row.get('verificationVersion') == VERIFICATION_VERSION:
                observed[code].add(day)
    previous = previous or {}
    pending = {code: sorted(allowed - successes[code], reverse=True) for code in sorted(codes)}
    pending = {code: days for code, days in pending.items() if days}
    attempts = {}
    for code, days in previous.get('attempts', {}).items():
        selected = {day: {provider: cycle for provider, cycle in value.items() if relevant_cycle(cycle)}
                    for day, value in days.items() if day in pending.get(code, [])}
        if selected:
            attempts[code] = selected
    for code, days in finished.items():
        for day, markers in days.items():
            if day in pending.get(code, []):
                existing = attempts.setdefault(code, {}).setdefault(day, {})
                for provider, cycle in markers.items():
                    existing[provider] = max(cycle, existing.get(provider, ''))
    latest = max(dates or [''])
    return dict(version=1, dates=sorted(dates), pending=pending, attempts=attempts, latestDate=latest,
                latestObservedCount=sum(latest in days for days in observed.values()),
                observed={code: sorted(days) for code, days in observed.items()},
                lastSuccessfulUpdate=previous.get('lastSuccessfulUpdate'))


def mark_attempt(state, code, dates, provider, cycle):
    for day in dates:
        markers = state['attempts'].setdefault(code, {}).setdefault(day, {})
        old = markers.get(provider, '')
        markers[provider] = cycle if str(cycle).startswith('review:') or str(old).startswith('review:') else max(cycle, old)


def journal_attempt(out, state, code, dates, provider, cycle):
    """Small fsynced append before dispatch; no multi-megabyte state rewrite per stock."""
    event = dict(code=code, dates=dates, provider=provider, cycle=cycle)
    with (Path(out)/'daily-attempts.jsonl').open('a') as stream:
        stream.write(json.dumps(event, separators=(',', ':'))+'\n')
        stream.flush()
        os.fsync(stream.fileno())
    mark_attempt(state, code, dates, provider, cycle)


def replay_attempts(out, state, review_cycle=None):
    path = Path(out)/'daily-attempts.jsonl'
    if not path.exists():
        return
    with path.open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                # An incomplete final append never claims that a request was completed.
                continue
            if review_cycle:
                if event['cycle'] != review_cycle:
                    continue
            elif str(event['cycle']).startswith('review:'):
                continue
            code = event['code']
            dates = [day for day in event['dates'] if day in state['pending'].get(code, [])]
            mark_attempt(state, code, dates, event['provider'], event['cycle'])


def resume_review_attempts(state, cycle):
    """A dispatch journal proves ownership, not a committed source result."""
    if not str(cycle).startswith('review:'):
        raise ValueError('Interrupted-attempt recovery is limited to explicit reviews')
    for days in state.get('attempts', {}).values():
        for markers in days.values():
            for provider in ('primary', 'bao'):
                if markers.get(provider) == cycle and markers.get(provider+'Done') != cycle:
                    markers.pop(provider)


def persist_state(out, state, compact=False):
    out = Path(out)
    path = out/'daily-state.json'
    write_json(path, state)
    if compact:
        # Commit the checkpoint before removing its redo journal, including rename durability.
        with path.open('rb') as stream:os.fsync(stream.fileno())
        directory = os.open(str(out), os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        journal = out/'daily-attempts.jsonl'
        if journal.exists():
            with journal.open('w') as stream:
                stream.flush();os.fsync(stream.fileno())


def attempted(state, code, day, provider, cycle):
    return state['attempts'].get(code, {}).get(day, {}).get(provider) == cycle


def primary_tasks(state, cycle):
    tasks = [(code, [day for day in days if not attempted(state, code, day, 'primary', cycle)])
             for code, days in state['pending'].items()]
    return sorted([(code, days) for code, days in tasks if days],
                  key=lambda task: (cycle not in task[1], task[0]))


def bao_tasks(state, cycle, now, after):
    from .reconciliation import baostock_supported
    today = now.date().isoformat()
    result = []
    for code, days in state['pending'].items():
        if not baostock_supported(code):
            continue
        selected = [day for day in days if attempted(state, code, day, 'primary', cycle)
                    and not attempted(state, code, day, 'bao', cycle)
                    and (day < today or now.strftime('%H:%M') >= after)]
        if selected:
            result.append((code, selected))
    return sorted(result, key=lambda task: (cycle not in task[1], task[0]))


def merge_checkpoint(previous, code, rows, today, preserve_history=False):
    if previous and previous.get('code') != code:
        raise ValueError('Previous checkpoint code mismatch')
    start = six_month_start(today)
    days = {day: safe_saved_row(row) for day, row in previous.get('days', {}).items() if preserve_history or day >= start}
    for day, row in rows.items():
        row = safe_saved_row(row)
        if row.get('code') != code:
            raise ValueError('New checkpoint row code mismatch')
        next(checked_rows({'date':day, 'rows':[row]}, day))
        if successful(days.get(day)) and not successful(row):
            if row.get('status') == 'missing':
                continue
            row = dict(row, previousVerifiedObservation=days[day])
        days[day] = row
    return dict(previous, code=code, days=days, fetchStatus='ok', fetchedOn=today)


def accept_rows(state, code, rows):
    seen = set(state['observed'].get(code, []))
    had_latest = state.get('latestDate') in seen
    pending = set(state['pending'].get(code, []))
    for day, row in rows.items():
        seen.add(day)
        if successful(row):
            pending.discard(day)
            state['attempts'].get(code, {}).pop(day, None)
    state['observed'][code] = sorted(seen)
    if 'latestObservedCount' in state and not had_latest and state.get('latestDate') in seen:
        state['latestObservedCount'] += 1
    if pending:
        state['pending'][code] = sorted(pending, reverse=True)
    else:
        state['pending'].pop(code, None)


def counters(state, total_stocks, cycle, attempt_cycle=None):
    pending = sum(len(days) for days in state['pending'].values())
    total = total_stocks * len(state['dates'])
    observed = sum(len(days) for days in state['observed'].values())
    latest_observed = state.get('latestObservedCount') if cycle == state.get('latestDate') else None
    if latest_observed is None:
        latest_observed = sum(cycle in days for days in state['observed'].values())
    return dict(completedStocks=latest_observed, totalStocks=total_stocks,
                completedStockDates=total-pending, totalStockDates=total, pendingStockDates=pending,
                attemptedStockDates=observed, latestTraversalCompleted=latest_observed == total_stocks,
                traversalCompleted=not any(not attempted(state, code, day, 'primaryDone', attempt_cycle or cycle)
                    for code, days in state['pending'].items() for day in days))
