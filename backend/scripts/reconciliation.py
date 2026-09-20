"""Bounded same-provider reconciliation; conflicting observations never become valid ratios."""
from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math
import pandas as pd

VERIFICATION_VERSION = 3
QUOTE_FIELDS = ('pctChange', 'amplitude')
PRICE_FIELDS = ('open', 'high', 'low', 'close')
PRICE_SCHEMA_VERSION = 1
PRICE_REQUIRED_FROM = '2026-09-21'
STATUS_EXPLANATION_REQUIRED_FROM = '2026-09-18'
QUOTE_METHOD = ('涨跌幅与振幅取自同一日线来源：涨跌幅=(当日收盘-上一交易日收盘)/上一交易日收盘×100%；'
                '振幅=(当日最高-当日最低)/上一交易日收盘×100%。日线窗口缺少前收或最高最低时两字段留空，不影响开盘占比。')


def _finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def missing_prices(status='missing'):
    """Return an atomic price state. Partial OHLC values are never exposed."""
    if status not in ('missing', 'not_traded'):
        raise ValueError('Invalid price status')
    return {**{field: None for field in PRICE_FIELDS}, 'priceStatus': status}


def normalize_ohlc(open_price, high, low, close):
    """Validate one unadjusted daily bar as an indivisible observation."""
    values = tuple(_finite_number(value) for value in (open_price, high, low, close))
    if any(value is None or value <= 0 for value in values):
        return missing_prices()
    open_price, high, low, close = values
    if not (low <= open_price <= high and low <= close <= high):
        return missing_prices()
    return dict(open=open_price, high=high, low=low, close=close, priceStatus='available')


def validate_price_row(row, allow_legacy=True):
    """Validate the persisted atomic OHLC contract and return its state."""
    if 'priceStatus' not in row and allow_legacy:
        return None
    present = {field for field in (*PRICE_FIELDS, 'priceStatus') if field in row}
    if present != set((*PRICE_FIELDS, 'priceStatus')):
        raise ValueError('Partial OHLC price record')
    status = row.get('priceStatus')
    if status not in ('available', 'not_traded', 'missing'):
        raise ValueError('Invalid price status')
    if status == 'available':
        normalized = normalize_ohlc(*(row[field] for field in PRICE_FIELDS))
        if normalized['priceStatus'] != 'available':
            raise ValueError('Invalid OHLC values')
        if row.get('priceSourceProvider') not in ('sina', 'tencent'):
            raise ValueError('Available OHLC requires a supported source')
    else:
        if any(row[field] is not None for field in PRICE_FIELDS):
            raise ValueError('Unavailable OHLC must be entirely null')
        if status == 'not_traded' and row.get('status') != 'suspended':
            raise ValueError('Only confirmed no-trade rows may use not_traded')
        if status == 'not_traded' and (not isinstance(row.get('noTradeEvidence'), dict)
                                       or not row['noTradeEvidence']):
            raise ValueError('No-trade OHLC requires evidence')
    return status


def price_counts(rows):
    states = [validate_price_row(row) or 'missing' for row in rows]
    return dict(
        priceAvailable=states.count('available'),
        priceNoTrade=states.count('not_traded'),
        priceMissing=states.count('missing'),
        priceDataComplete='missing' not in states,
    )


def special_status_explained(row):
    """Return whether a no-trade row has a concrete user-facing explanation."""
    if row.get('status') != 'suspended':
        return True
    status = row.get('specialStatus')
    required = ('type', 'label', 'description', 'startedAt', 'source')
    if not isinstance(status, dict) or any(not isinstance(status.get(key), str) or not status[key].strip()
                                           for key in required):
        return False
    try:
        return datetime.strptime(status['startedAt'], '%Y-%m-%d').date().isoformat() == status['startedAt']
    except ValueError:
        return False


def special_status_counts(rows):
    no_trade = [row for row in rows if row.get('status') == 'suspended']
    explained = sum(special_status_explained(row) for row in no_trade)
    return dict(specialStatusExplained=explained,
                specialStatusUnexplained=len(no_trade)-explained,
                statusExplanationComplete=explained == len(no_trade))


def window_start(dates, lookback_days=10):
    """Start a daily fetch a few calendar days earlier so the first requested
    trading day still has a previous close for pctChange/amplitude."""
    return (datetime.strptime(min(dates), '%Y-%m-%d') - timedelta(days=lookback_days)).strftime('%Y%m%d')


def quote_observation(d, day):
    """Return atomic OHLC plus derived quote metrics from one daily frame."""
    date_col = 'date' if 'date' in d else ('日期' if '日期' in d else None)
    open_col = 'open' if 'open' in d else '开盘'
    close_col = 'close' if 'close' in d else '收盘'
    high_col = 'high' if 'high' in d else '最高'
    low_col = 'low' if 'low' in d else '最低'
    result = dict(missing_prices(), pctChange=None, amplitude=None)
    required = {open_col, close_col, high_col, low_col}
    if date_col is None or not required <= set(d.columns):
        return result
    frame = d[[date_col, open_col, close_col, high_col, low_col]].copy()
    frame[date_col] = frame[date_col].astype(str).str[:10]
    frame = frame.drop_duplicates(subset=date_col, keep='last').sort_values(date_col).reset_index(drop=True)
    hit = frame.index[frame[date_col] == day]
    if len(hit) != 1:
        return result
    i = int(hit[0])
    prices = normalize_ohlc(frame.iloc[i][open_col], frame.iloc[i][high_col],
                            frame.iloc[i][low_col], frame.iloc[i][close_col])
    result.update(prices)
    if prices['priceStatus'] != 'available' or i == 0:
        return result
    prev = _finite_number(frame.iloc[i-1][close_col])
    if prev is None or prev <= 0:
        return result
    result.update(
        pctChange=round((prices['close'] - prev) / prev * 100, 4),
        amplitude=round((prices['high'] - prices['low']) / prev * 100, 4),
    )
    return result


def quote_metrics(d, day):
    """Backward-compatible derived-metric interface."""
    quote = quote_observation(d, day)
    return quote['pctChange'], quote['amplitude']


def sina_observations(initial):
    """Keep one Sina observation per day without retries or another provider."""
    return {day: dict(row, sourceProvider='sina', verificationVersion=VERIFICATION_VERSION,
                      verificationState='matched' if row['status'] == 'ok' else 'unverified',
                      reconciliationAttempts=[observation(row, 'sina', 'initial')])
            for day, row in initial.items()}


def observation(row, source, stage):
    keys = ('status', 'reason', 'first15Volume', 'dailyVolume', 'minuteDayVolume',
            'dayVolumeDifference', 'dayVolumeDifferencePct', 'ratio', 'referenceRatio', 'quality')
    return dict(source=source, stage=stage, **{k: row[k] for k in keys if k in row})


class FallbackBudget:
    """A single run permits at most 50 stock requests, stopping after 3 endpoint failures."""
    def __init__(self, max_stocks=50, failure_limit=3):
        self.max_stocks = max_stocks
        self.failure_limit = failure_limit
        self.attempted = 0
        self.consecutive_failures = 0

    def reserve(self):
        if self.consecutive_failures >= self.failure_limit:
            return '备用接口连续失败，已暂停本轮备用请求'
        if self.attempted >= self.max_stocks:
            return '已达本轮备用核验上限，等待后续重试'
        self.attempted += 1
        return None

    def result(self, success):
        self.consecutive_failures = 0 if success else self.consecutive_failures + 1


def eastmoney_pair(ak, code, dates):
    """Both unadjusted frames come from Eastmoney; both documented volume units are lots."""
    start, end = min(dates), max(dates)
    minute = ak.stock_zh_a_hist_min_em(symbol=code, period='15',
        start_date=start+' 09:30:00', end_date=end+' 15:00:00', adjust='')
    daily = ak.stock_zh_a_hist(symbol=code, period='daily',
        start_date=window_start(dates), end_date=end.replace('-', ''), adjust='', timeout=20)
    minute = minute.rename(columns={'时间':'day', '成交量':'volume', '收盘':'close'}).copy()
    daily = daily.rename(columns={'日期':'date', '成交量':'volume', '收盘':'close'}).copy()
    # Never substitute missing fields or infer a unit from numeric magnitude.
    for frame in (minute, daily):
        if 'volume' not in frame or 'close' not in frame:
            raise ValueError('Eastmoney required fields missing')
        frame['volume'] = pd.to_numeric(frame['volume'], errors='coerce') * 100
    return minute, daily


def reconcile_stock(code, name, dates, initial, fetch_primary, fetch_fallback,
                    clear_cache, evaluate, fallback_budget, latest_first=False):
    """Refresh Sina once per stock, then replace each unresolved day with a whole verified pair."""
    result = {d: dict(initial[d], sourceProvider='sina', verificationVersion=VERIFICATION_VERSION,
                      verificationState='matched' if initial[d]['status']=='ok' else 'unverified') for d in dates}
    targets = [d for d in dates if initial[d].get('quality') == 'source_difference']
    if latest_first and dates:
        latest = max(dates)
        deferred = [d for d in targets if d != latest]
        for day in deferred:
            result[day].update(status='unverified', ratio=None, verificationState='unverified',
                               reconciliationDeferred=True, reason='历史差异等待后续核验',
                               reconciliationAttempts=[observation(initial[day], 'sina', 'initial')])
        targets = [d for d in targets if d == latest]
    if not targets:
        return result
    history = {d: [observation(initial[d], 'sina', 'initial')] for d in targets}
    clear_cache()
    try:
        minute, daily = fetch_primary()
        for day in targets:
            refreshed = evaluate(code, name, day, minute, daily)
            history[day].append(observation(refreshed, 'sina', 'refresh'))
            # A failed refresh must not erase the original conflicting observation.
            if refreshed['status'] == 'ok' or refreshed.get('quality') == 'source_difference':
                result[day] = dict(refreshed, sourceProvider='sina', verificationVersion=VERIFICATION_VERSION,
                                  verificationState='matched' if refreshed['status']=='ok' else 'unverified')
    except Exception as exc:
        for day in targets:
            history[day].append(dict(source='sina', stage='refresh', status='error', reason=type(exc).__name__))
    unresolved = [d for d in targets if result[d]['status'] != 'ok']
    if unresolved:
        blocked = fallback_budget.reserve()
        if blocked:
            for day in unresolved:
                history[day].append(dict(source='eastmoney', stage='fallback', status='skipped', reason=blocked))
        else:
            try:
                minute, daily = fetch_fallback(unresolved)
                fallback_budget.result(True)
                for day in unresolved:
                    alternate = evaluate(code, name, day, minute, daily)
                    history[day].append(observation(alternate, 'eastmoney', 'fallback'))
                    if alternate['status'] == 'ok' and alternate.get('quality') == 'matched':
                        result[day] = dict(alternate, sourceProvider='eastmoney', verificationVersion=VERIFICATION_VERSION,
                                          verificationState='fallback_matched')
            except Exception as exc:
                fallback_budget.result(False)
                for day in unresolved:
                    history[day].append(dict(source='eastmoney', stage='fallback', status='error', reason=type(exc).__name__))
    checked = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    for day in targets:
        result[day]['reconciliationAttempts'] = history[day]
        result[day]['verifiedAt'] = checked
        if result[day]['status'] != 'ok':
            result[day].update(status='unverified', ratio=None, verificationState='unverified',
                               reason='成交量未完全核对一致，自动重取及备用核验未通过')
    return result


def baostock_needed(row):
    return row.get('status') not in ('ok', 'suspended') and (
        row.get('status') == 'missing' or row.get('quality') == 'source_difference'
        or row.get('reconciliationDeferred', False))


def baostock_supported(code):
    return code.startswith(('60', '68', '00', '30'))


def needs_baostock_checkpoint(record, dates):
    if not record or not baostock_supported(record.get('code', '')):
        return False
    return any(baostock_needed(record.get('days', {}).get(day, {}))
               and not record.get('days', {}).get(day, {}).get('baostockAttempted', False)
               for day in dates)


def reconcile_baostock(code, name, dates, initial, fetch_pair, evaluate, budget):
    """One independent provider pair for the entire date window; never mix volume sources."""
    result = {day: dict(initial[day]) for day in dates}
    targets = [day for day in dates if baostock_needed(result[day])]
    if not targets:
        return result
    histories = {day: list(result[day].get('reconciliationAttempts') or
                           [observation(result[day], result[day].get('sourceProvider', 'sina'), 'initial')])
                 for day in targets}
    blocked = None if baostock_supported(code) else 'BaoStock不支持北交所，未请求'
    if blocked is None:
        blocked = budget.reserve()
    if blocked:
        for day in targets:
            histories[day].append(dict(source='baostock',stage='fallback',status='unsupported' if not baostock_supported(code) else 'skipped',reason=blocked))
            result[day].update(reconciliationAttempts=histories[day],baostockAttempted=False,
                               baostockOutcome='unsupported' if not baostock_supported(code) else 'skipped')
        return result
    checked = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    try:
        # Fetch all requested dates once, even when only some of them need repair.
        minute, daily = fetch_pair(dates)
        budget.result(True)
    except Exception as exc:
        # Only transport failures trip this provider's independent connection breaker.
        budget.result(not isinstance(exc, (ConnectionError, TimeoutError)))
        for day in targets:
            histories[day].append(dict(source='baostock',stage='fallback',status='error',reason=type(exc).__name__))
            result[day].update(reconciliationAttempts=histories[day],baostockAttempted=True,
                               baostockOutcome='error',baostockCheckedAt=checked,ratio=None,
                               reason='BaoStock备用请求失败，原始数据仍待核验' if result[day].get('status')!='missing' else '原始数据缺失，BaoStock备用请求失败')
        return result
    for day in targets:
        alternate = evaluate(code, name, day, minute, daily)
        histories[day].append(observation(alternate, 'baostock', 'fallback'))
        if alternate.get('status') == 'ok' and alternate.get('quality') == 'matched':
            result[day] = dict(alternate, sourceProvider='baostock', verificationVersion=VERIFICATION_VERSION,
                               verificationState='fallback_matched')
            outcome = 'matched'
        else:
            outcome = alternate.get('status', 'unverified')
            # A complete but conflicting pair can replace a missing observation as reference only.
            if result[day].get('status') == 'missing' and alternate.get('status') == 'unverified':
                result[day] = dict(alternate, sourceProvider='baostock', verificationState='unverified')
            result[day].update(ratio=None,reason='BaoStock备用核验未通过，保留原始观测')
        result[day].pop('reconciliationDeferred', None)
        result[day].update(reconciliationAttempts=histories[day],baostockAttempted=True,
                           baostockOutcome=outcome,baostockCheckedAt=checked)
    return result
