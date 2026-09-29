"""Target-date collection primitives; unchanged historical source holes are bounded."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

from . import collect
from .download_only import evaluate_downloaded, volumes_present
from .reconciliation import PRICE_FIELDS, PRICE_REQUIRED_FROM, missing_prices, validate_price_row

AK = None
SOURCE_OBSERVATION_LIMIT = 3


def observation_fingerprint(value):
    """Fingerprint the exact target result, not unrelated bars or a raw HTTP body."""
    if (not isinstance(value, dict) or value.get('version') != 1 or
            value.get('provider') != 'sina' or value.get('kind') != 'target_opening_absent' or
            not isinstance(value.get('code'), str) or len(value['code']) != 6 or not value['code'].isdigit() or
            not volume(value.get('dailyVolume')) or value['dailyVolume'] <= 0 or
            value.get('targetTime') != '09:45:00'):
        return None
    try:
        day = datetime.strptime(value['date'], '%Y-%m-%d').date().isoformat()
    except (KeyError, TypeError, ValueError):
        return None
    if day != value['date']:
        return None
    semantic = {key:value[key] for key in
                ('version','code','date','provider','kind','dailyVolume','targetTime')}
    return hashlib.sha256(json.dumps(semantic, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_held(attempt, code=None, day=None):
    hold = attempt.get('sourceHold')
    observation = attempt.get('sourceObservation')
    if not isinstance(hold, dict) or not isinstance(observation, dict):
        return False
    fingerprint = observation_fingerprint(observation)
    return bool(attempt.get('committed') is True and fingerprint and
                hold.get('fingerprint') == observation.get('fingerprint') == fingerprint and
                type(hold.get('observations')) is int and hold['observations'] >= SOURCE_OBSERVATION_LIMIT and
                hold['observations'] == observation.get('observations') and
                (code is None or observation['code'] == code) and
                (day is None or observation['date'] == day))


def all_sources_held(summary):
    """Only a fully observed, price/description-safe target can leave automatic work."""
    pending = summary.get('pendingCount')
    blocked = summary.get('sourceBlockedCount')
    return (type(pending) is int and pending > 0 and type(blocked) is int and blocked == pending and
            summary.get('unprocessedCount') == 0 and summary.get('retryableCount') == 0 and
            summary.get('automaticActionableCount') == 0)


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
        from .exchange_status import valid_historical_no_trade
        return ((evidence.get('kind') == 'explicit_zero_daily_volume' and evidence.get('volume') == 0)
                or valid_notice(evidence, row.get('code'))
                or valid_historical_no_trade(evidence, row.get('code'), day or evidence.get('date')))
    return row.get('status') == 'ok' and volumes_present(row) and type(row.get('ratio')) in (int, float) and abs(
        row['ratio'] - row['first15Volume'] / row['dailyVolume'] * 100) <= 1e-5


def quotes_present(row, day=None):
    """A quote snapshot was applied, including an explicitly unavailable value."""
    if row.get('status') == 'suspended':
        metrics = ('pctChange' in row and 'amplitude' in row and
                   row.get('pctChange') is None and row.get('amplitude') is None)
    else:
        metrics = all(key in row and (row[key] is None or
               (type(row[key]) in (int, float) and math.isfinite(row[key])))
               for key in ('pctChange', 'amplitude'))
    if not metrics or not day or day < PRICE_REQUIRED_FROM:
        return metrics
    return validate_price_row(row) in ('available', 'not_traded')


def retry_due(attempt, now):
    if source_held(attempt):
        return False
    # A dispatch without a committed result is immediately reclaimable, even tomorrow.
    if not attempt.get('committed'):
        return True
    return not attempt.get('nextRetryAt') or datetime.fromisoformat(attempt['nextRetryAt']) <= now


def ready(code, day, phase, attempt, now, status_evidence=None):
    from .no_trade_evidence import evidence
    from .exchange_status import valid_exchange_evidence
    confirmed = (evidence(code, day) is not None or
                 valid_exchange_evidence(status_evidence, code))
    return (phase != 'opening' and confirmed) or retry_due(attempt, now)


def commit_attempt(previous, now, success, source_observation=None):
    failures = 0 if success else previous.get('failures', 0) + 1
    delay = (5, 15, 30)[min(max(0, failures - 1), 2)]
    result = dict(previous, committed=True, attemptedAt=now.isoformat(), failures=failures,
                  nextRetryAt=None if success else (now + timedelta(minutes=delay)).isoformat())
    result.pop('sourceHold', None)
    old = result.pop('sourceObservation', {})
    fingerprint = observation_fingerprint(source_observation)
    if success or not fingerprint or source_observation['date'] >= now.date().isoformat():
        return result
    same = old.get('fingerprint') == fingerprint and observation_fingerprint(old) == fingerprint
    count = old.get('observations', 0) if same and type(old.get('observations')) is int else 0
    observation = dict(source_observation, fingerprint=fingerprint, observations=count+1,
                       firstObservedAt=old['firstObservedAt'] if count else now.isoformat(),
                       lastObservedAt=now.isoformat())
    result['sourceObservation'] = observation
    if observation['observations'] >= SOURCE_OBSERVATION_LIMIT:
        result.update(nextRetryAt=None, sourceHold=dict(fingerprint=fingerprint,
            observations=observation['observations'], blockedAt=now.isoformat(),
            reason='unchanged_target_opening_absent',
            reopenCondition='verified exact-date opening data or valid exact-date no-trade proof'))
    return result


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
    selected_summary = targets.get(selected, {}).get('summary', {})
    def incomplete(day):
        summary = targets.get(day, {}).get('summary', {})
        return (summary.get('dataComplete') is not True or
                day >= PRICE_REQUIRED_FROM and summary.get('priceDataComplete') is not True)

    def due(day):
        if all_sources_held(targets.get(day, {}).get('summary', {})):
            return False
        retry_at = targets.get(day, {}).get('summary', {}).get('nextRetryAt')
        return not retry_at or datetime.fromisoformat(retry_at) <= now

    first_pass_complete = (selected_summary.get('firstPassComplete') is True or
                           selected_summary.get('dataComplete') is True or
                           type(selected_summary.get('unprocessedCount')) is int and
                           selected_summary['unprocessedCount'] == 0)
    if incomplete(selected) and due(selected) and not first_pass_complete:
        return selected
    pending = {day for values in legacy.get('pending', {}).values() for day in values}
    pending |= {day for day in targets if incomplete(day)}
    if incomplete(selected):
        pending.add(selected)
    eligible = sorted((day for day in pending if day <= selected and day in calendar
                       and incomplete(day) and due(day)), reverse=True)
    def last_attempt(day):
        value = targets.get(day, {}).get('summary', {}).get('lastAttemptedAt')
        return datetime.fromisoformat(value) if value else datetime.min.replace(tzinfo=now.tzinfo)
    return min(eligible, key=last_attempt) if eligible else None


def fetch(task):
    """Keep each successful half even if the other request fails; reuse exact-day cache."""
    import pandas as pd
    from .sina_daily import daily_unadjusted
    item, day, opening, previous, phase = task[:5]
    # Keep the established 5/6-item task contract. A seventh item is used only
    # when the parent has exact-day exchange evidence for this stock.
    snapshot = task[5] if len(task) >= 6 else None
    status_evidence = task[6] if len(task) >= 7 else None
    snapshot_supplied = len(task) == 6 or (len(task) >= 7 and snapshot is not None)
    code = item['code']; symbol = collect.market(code).lower() + code
    from .no_trade_evidence import evidence, explanation
    from .exchange_status import valid_exchange_evidence, valid_historical_no_trade
    notice = evidence(code, day)
    # Price backfill already stores exact stock/date listing and suspension
    # evidence. Reuse that proof after a stale core checkpoint marked it missing;
    # never turn positive core observations into no-trade from an old proof.
    saved_proof = previous.get('noTradeEvidence')
    if (not notice and previous.get('status') in ('missing', 'suspended') and
            not (volume(previous.get('dailyVolume')) and previous['dailyVolume'] > 0) and
            not (volume(opening) and opening > 0) and
            not (volume(previous.get('first15Volume')) and previous['first15Volume'] > 0) and
            valid_historical_no_trade(saved_proof, code, day)):
        notice = saved_proof
    if not notice and valid_exchange_evidence(status_evidence, code):
        notice = status_evidence
    if phase != 'opening' and notice:
        row = (dict(previous) if notice is saved_proof else
               collect.pending_record(item, [day])['days'][day])
        row.update(status='suspended', calculationPolicy='download_only', ratio=None,
                   reason=('交易所上市记录确认目标日尚未上市，无交易' if notice.get('kind') == 'not_yet_listed'
                           else '已核验目标日期停牌证据，无交易'), noTradeEvidence=notice,
                   pctChange=None, amplitude=None)
        row.update(missing_prices('not_traded'))
        special_status = notice.get('specialStatus') or explanation(code, day)
        if special_status:
            row['specialStatus'] = special_status
        return dict(code=code,row=row,opening=None,errors=[],speedDegraded=False,firstDataRequestAt=None)
    errors = []; degraded = False; request_started = None
    retained_quote = {}
    saved_daily = volume(previous.get('dailyVolume')) and previous['dailyVolume'] > 0
    # Preserve an independently captured OHLC frame for every date, even when
    # either core volume is missing. The rollout date gates new price requests,
    # never retention of already published prices.
    if validate_price_row(previous) == 'available':
        retained_quote = {key: previous[key] for key in
            (*PRICE_FIELDS, 'priceStatus', 'priceSourceProvider', 'pctChange', 'amplitude',
             'dailyAdapter', 'quoteTime') if key in previous}
    # Fetch exact-day prices independently of a saved daily volume. Even when
    # the opening is still missing, a provider's revised volume cannot replace
    # the saved core observation during this price repair.
    if (phase != 'opening' and day >= PRICE_REQUIRED_FROM and not snapshot_supplied and
            (saved_daily or complete(previous, day)) and not quotes_present(previous, day)):
        row = dict(previous)
        if previous.get('status') == 'suspended':
            row.update(missing_prices('not_traded'), pctChange=None, amplitude=None)
        else:
            from .reconciliation import quote_observation
            try:
                request_started = datetime.now(timezone.utc).isoformat()
                daily = daily_unadjusted(AK, symbol, day.replace('-', ''), day.replace('-', ''))
                degraded = bool(daily.attrs.get('speedDegraded'))
                quote = quote_observation(daily, day)
                if quote['priceStatus'] == 'available':
                    row.update(quote, priceSourceProvider='sina',
                               dailyAdapter=daily.attrs.get('dailyAdapter', 'sina_daily_price_repair'))
                    row.pop('quoteTime', None)
                    retained_quote = dict(quote, priceSourceProvider='sina',
                        dailyAdapter=row['dailyAdapter'])
                else:
                    errors.append('price:missing')
            except Exception as exc:
                errors.append('price:'+type(exc).__name__)
        if complete(previous, day):
            return dict(code=code, row=row, opening=row.get('first15Volume'), errors=errors,
                        speedDegraded=degraded, firstDataRequestAt=request_started)
    first = opening if volume(opening) else previous.get('first15Volume')
    minute = pd.DataFrame()
    if volume(first):
        minute = pd.DataFrame([dict(day=day+' 09:45:00', volume=first)])
    else:
        try:
            request_started = request_started or datetime.now(timezone.utc).isoformat()
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
    if retained_quote:
        row.update(retained_quote)
    if snapshot:
        for key in ('pctChange', 'amplitude', *PRICE_FIELDS, 'priceStatus',
                    'priceSourceProvider', 'dailyAdapter', 'quoteTime'):
            if key in snapshot:
                row[key] = snapshot[key]
    if row.get('dailyVolume') == 0:
        row.update(status='suspended', ratio=None, noTradeEvidence=dict(kind='explicit_zero_daily_volume',
            provider='sina', date=day, symbol=symbol, volume=0))
        row.update(missing_prices('not_traded'))
    elif not complete(row):
        row.update(status='missing', ratio=None, reason='；'.join(errors) or '新浪未提供目标日期的开盘量或日成交量；等待补采')
        if row.get('priceStatus') == 'not_traded':
            row.update(missing_prices())
    result = dict(code=code, row=row, opening=row.get('first15Volume'), errors=errors, speedDegraded=degraded,
                  firstDataRequestAt=request_started)
    # Only an error-free source call with a retained positive daily volume and
    # available prices can qualify. HTTP/parse failures and current-day prefetch
    # never become source absence. Existing failure counts do not seed this log.
    if (not errors and request_started and row.get('status') == 'missing' and
            not volume(row.get('first15Volume')) and volume(row.get('dailyVolume')) and
            row['dailyVolume'] > 0 and validate_price_row(row) == 'available'):
        result['sourceObservation'] = dict(version=1, code=code, date=day, provider='sina',
            kind='target_opening_absent', targetTime='09:45:00', dailyVolume=row['dailyVolume'],
            responseRows=len(minute), responseEmpty=minute.empty)
    return result
