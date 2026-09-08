"""Target-date collection primitives. Empty source responses are always retryable."""
from datetime import datetime, timedelta, timezone
import math

from . import collect
from .download_only import evaluate_downloaded, volumes_present

AK = None


def init_worker(budget):
    import signal
    global AK
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    budget.install()
    import akshare
    AK = akshare


def volume(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0 and value == int(value)


def complete(row, day=None):
    if row.get('status') == 'suspended':
        evidence = row.get('noTradeEvidence', {})
        if not isinstance(evidence, dict) or (day and evidence.get('date') != day):
            return False
        from .no_trade_evidence import valid_notice
        return ((evidence.get('kind') == 'explicit_zero_daily_volume' and evidence.get('volume') == 0)
                or valid_notice(evidence, row.get('code')))
    return row.get('status') == 'ok' and volumes_present(row) and type(row.get('ratio')) in (int, float) and abs(
        row['ratio'] - row['first15Volume'] / row['dailyVolume'] * 100) <= 1e-5


def quotes_present(row):
    """A quote snapshot was applied, including an explicitly unavailable value."""
    return all(key in row and (row[key] is None or
               (type(row[key]) in (int, float) and math.isfinite(row[key])))
               for key in ('pctChange', 'amplitude'))


def retry_due(attempt, now):
    # A dispatch without a committed result is immediately reclaimable, even tomorrow.
    if not attempt.get('committed'):
        return True
    return not attempt.get('nextRetryAt') or datetime.fromisoformat(attempt['nextRetryAt']) <= now


def ready(code, day, phase, attempt, now):
    from .no_trade_evidence import evidence
    return (phase != 'opening' and evidence(code, day) is not None) or retry_due(attempt, now)


def commit_attempt(previous, now, success):
    failures = 0 if success else previous.get('failures', 0) + 1
    delay = (5, 15, 30)[min(max(0, failures - 1), 2)]
    return dict(previous, committed=True, attemptedAt=now.isoformat(), failures=failures,
                nextRetryAt=None if success else (now + timedelta(minutes=delay)).isoformat())


def target_date(phase, explicit, calendar, now):
    dates = sorted(set(calendar))
    today = now.date().isoformat()
    if explicit:
        if explicit not in dates or explicit > today:
            raise ValueError('Target must be a known trading date, not a future date')
        selected = explicit
    elif phase == 'opening':
        selected = today if today in dates else None
    else:
        closed = [d for d in dates if d < today or (d == today and now.strftime('%H:%M') >= '15:30')]
        selected = max(closed) if closed else None
    if selected == today and now.strftime('%H:%M') < ('09:50' if phase == 'opening' else '15:30'):
        raise ValueError('The target collection window has not opened')
    if phase == 'opening' and selected and selected != today:
        raise ValueError('Opening prefetch is only for today')
    return selected


def yield_at(phase, now):
    clock = now.strftime('%H:%M')
    if phase == 'opening':
        return '15:20'
    if clock < '09:40':
        return '09:40'
    if clock < '15:20':
        return '15:20'
    return '23:55'


def select_target(phase, explicit, calendar, cache, legacy, now):
    selected = target_date(phase, explicit, calendar, now)
    if explicit or phase != 'catchup' or not selected:
        return selected
    targets = cache.get('targets', {})
    if targets.get(selected, {}).get('summary', {}).get('dataComplete') is not True:
        return selected
    pending = {day for values in legacy.get('pending', {}).values() for day in values}
    pending |= {day for day, value in targets.items() if value.get('summary', {}).get('pendingCount', 0)}
    eligible = [day for day in pending if day <= selected and day in calendar
                and targets.get(day, {}).get('summary', {}).get('dataComplete') is not True]
    return max(eligible) if eligible else None


def fetch(task):
    """Keep each successful half even if the other request fails; reuse exact-day cache."""
    import pandas as pd
    from .sina_daily import daily_unadjusted
    item, day, opening, previous, phase = task[:5]
    snapshot_supplied = len(task) == 6
    snapshot = task[5] if snapshot_supplied else None
    code = item['code']; symbol = collect.market(code).lower() + code
    from .no_trade_evidence import evidence
    notice = evidence(code, day)
    if phase != 'opening' and notice:
        row = collect.pending_record(item, [day])['days'][day]
        row.update(status='suspended', calculationPolicy='download_only', ratio=None,
                   reason='公司公告确认目标日停牌，无交易', noTradeEvidence=notice)
        return dict(code=code,row=row,opening=None,errors=[],speedDegraded=False,firstDataRequestAt=None)
    errors = []; degraded = False; request_started = None
    first = opening if volume(opening) else previous.get('first15Volume')
    minute = pd.DataFrame()
    if volume(first):
        minute = pd.DataFrame([dict(day=day+' 09:45:00', volume=first)])
    else:
        try:
            request_started = datetime.now(timezone.utc).isoformat()
            minute = collect.minute_unadjusted(AK, symbol)
        except Exception as exc:
            errors.append('opening:'+type(exc).__name__)
    if phase == 'opening':
        row = evaluate_downloaded(code, item['name'], day, minute, pd.DataFrame(), collect.market)
        return dict(code=code, opening=row.get('first15Volume'), errors=errors, speedDegraded=False,
                    firstDataRequestAt=request_started)
    daily = pd.DataFrame()
    if snapshot_supplied and snapshot:
        snapshot_volume = snapshot.get('dailyVolume')
        if not volume(snapshot_volume):
            snapshot_volume = previous.get('dailyVolume')
        daily = pd.DataFrame([dict(date=day, volume=snapshot_volume)])
    elif snapshot_supplied:
        errors.append('snapshot:missing')
    elif volume(previous.get('dailyVolume')) and previous['dailyVolume'] > 0:
        daily = pd.DataFrame([dict(date=day, volume=previous['dailyVolume'])])
    else:
        try:
            request_started = request_started or datetime.now(timezone.utc).isoformat()
            daily = daily_unadjusted(AK, symbol, day.replace('-', ''), day.replace('-', ''))
            degraded = bool(daily.attrs.get('speedDegraded'))
        except Exception as exc:
            errors.append('daily:'+type(exc).__name__)
    row = evaluate_downloaded(code, item['name'], day, minute, daily, collect.market)
    if snapshot:
        for key in ('pctChange', 'amplitude', 'dailyAdapter', 'quoteTime'):
            if key in snapshot:
                row[key] = snapshot[key]
    if row.get('dailyVolume') == 0:
        row.update(status='suspended', ratio=None, noTradeEvidence=dict(kind='explicit_zero_daily_volume',
            provider='sina', date=day, symbol=symbol, volume=0))
    elif not complete(row):
        row.update(status='missing', ratio=None, reason='；'.join(errors) or '新浪未提供目标日期的开盘量或日成交量；等待补采')
    return dict(code=code, row=row, opening=row.get('first15Volume'), errors=errors, speedDegraded=degraded,
                firstDataRequestAt=request_started)
