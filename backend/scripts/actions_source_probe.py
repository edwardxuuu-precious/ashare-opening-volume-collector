#!/usr/bin/env python3
"""Read-only three-stock connectivity/verification probe; no AWS or market-data export."""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import multiprocessing as mp
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import collect, daily_collector as daily
from scripts.baostock_source import BaoStockSession

SAMPLES = (('000001', '平安银行'), ('600519', '贵州茅台'), ('920002', '万达轴承'))
ZONE = ZoneInfo('Asia/Shanghai')


def select_date(calendar, moment):
    """At 16:00 Shanghai time today becomes eligible; holidays use the latest prior date."""
    moment = moment.astimezone(ZONE)
    today = moment.date().isoformat()
    values = calendar.trade_date if hasattr(calendar, 'trade_date') else calendar
    dates = {str(value)[:10] for value in values}
    for day in dates:
        datetime.strptime(day, '%Y-%m-%d')
    eligible = [day for day in dates if day < today or (day == today and moment.hour >= 16)]
    if not eligible:
        raise ValueError('No closed trading date')
    return max(eligible)


def classify(row):
    row = row or {}
    ratio = row.get('ratio')
    if (row.get('status') == 'ok' and row.get('quality') == 'matched'
            and row.get('dayVolumeDifference') == 0 and isinstance(ratio, (int, float))
            and not isinstance(ratio, bool) and math.isfinite(ratio) and 0 <= ratio <= 100):
        return 'success'
    if row.get('status') == 'unverified' or row.get('quality') == 'source_difference':
        return 'conflict'
    return 'missing'


def counts(rows, codes):
    result = dict(total=len(codes), success=0, conflict=0, missing=0)
    for code in codes:
        result[classify(rows.get(code))] += 1
    return result


def safe_error(stage, exc, code=None):
    # Never serialize exception messages, URLs, credentials, or vendor response bodies.
    result = dict(stage=stage, type=type(exc).__name__)
    if code is not None:
        result['code'] = code
    return result


def read_calendar(http):
    import requests
    original = requests.sessions.Session.request
    try:
        http.install()
        import akshare as ak
        return ak.tool_trade_date_hist_sina()
    finally:
        requests.sessions.Session.request = original


def init_probe_primary(http, fallback):
    # Spawned workers do not inherit a redirect context from the parent.
    sys.stdout = open(os.devnull, 'w')
    sys.stderr = open(os.devnull, 'w')
    daily.init_primary(http, fallback)


def collect_primary(ctx, http, day, deadline):
    fallback = daily.SharedFallbackBudget(ctx.Lock(), ctx.Value('i', 0), ctx.Value('i', 0), maximum=3)
    with ctx.Pool(4, initializer=init_probe_primary, initargs=(http, fallback)) as pool:
        result = pool.map_async(daily.fetch_primary, [(code, name, [day]) for code, name in SAMPLES])
        return {code: rows.get(day) for code, rows in result.get(timeout=max(.1, deadline-time.monotonic()))}


def collect_bao(day, session_factory=BaoStockSession):
    rows, errors = {}, []
    # Independent connection; both SH and SZ must pass even when primary already succeeded.
    with session_factory(timeout=20, interval=.75) as session:
        for code, name in SAMPLES[:2]:
            try:
                minute, daily_frame = session.pair(code, [day])
                rows[code] = collect.evaluate(code, name, day, minute, daily_frame)
            except Exception as exc:
                errors.append(safe_error('baostock', exc, code))
    return rows, errors


def run_probe(moment=None):
    started = time.monotonic()
    ctx = mp.get_context('spawn')
    primary_deadline = started + 600
    http = daily.SharedHTTPBudget(ctx.Lock(), ctx.Value('d', 0), ctx.Value('i', 0),
                                  ctx.Value('q', 0), .75, primary_deadline)
    report = dict(schemaVersion=1, scope='three_stock_sample', fullMarketVerified=False,
                  fullMarketDurationVerified=False,
                  executionEnvironment='github_actions' if os.environ.get('GITHUB_ACTIONS') == 'true' else 'local',
                  selectedDate=None, primaryPath='AKShare Sina with same-source Eastmoney fallback',
                  primaryWorkers=4, httpIntervalSeconds=.75, requestTimeoutSeconds=20, errors=[])
    primary, bao = {}, {}
    try:
        day = select_date(read_calendar(http), moment or datetime.now(ZONE))
        report['selectedDate'] = day
        try:
            primary = collect_primary(ctx, http, day, primary_deadline)
            for code, row in primary.items():
                match = re.fullmatch(r'采集失败：([A-Za-z_][A-Za-z0-9_]*)', (row or {}).get('reason', ''))
                if match:
                    report['errors'].append(dict(stage='primary', code=code, type=match.group(1)))
        except Exception as exc:
            report['errors'].append(safe_error('primary', exc))
        try:
            bao, errors = collect_bao(day)
            report['errors'].extend(errors)
        except Exception as exc:
            report['errors'].append(safe_error('baostock', exc))
    except Exception as exc:
        report['errors'].append(safe_error('calendar', exc))
    codes = [code for code, _ in SAMPLES]
    report['primary'] = counts(primary, codes)
    report['primaryByMarket'] = {collect.market(code): counts(primary, [code]) for code in codes}
    report['baostock'] = dict(counts(bao, codes[:2]), unsupported=1)
    report['backupAvailable'] = report['baostock']['success'] == 2
    report['verifiedShSzCount'] = sum(classify(primary.get(code)) == 'success' or
                                         classify(bao.get(code)) == 'success' for code in codes[:2])
    report['passed'] = bool(report['selectedDate'] and report['backupAvailable'] and report['verifiedShSzCount'] == 2)
    report['httpRequests'] = http.count.value
    report['elapsedSeconds'] = round(time.monotonic()-started, 3)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        report = run_probe()
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
