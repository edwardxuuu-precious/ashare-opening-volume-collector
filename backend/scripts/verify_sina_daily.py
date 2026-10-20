#!/usr/bin/env python3
"""Read-only differential probe: compare both paths on identical live Sina bytes.

Example: python backend/scripts/verify_sina_daily.py --date 2026-09-07 \
    --universe var/data/full-market-live/universe.json

This makes the official two HTTP requests per stock, replays the captured daily
response through the optimized adapter, and checks date and volume equality.
It never uploads, changes collector state, or marks production rows verified.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sys
import time
from unittest.mock import patch

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.sina_daily import (AKSHARE_VERSION, SOURCE_SHA256, DECODER_SHA256,
                                adapter_ready, daily_unadjusted)


def market(code):
    if code.startswith(('60', '68')):
        return 'sh'
    if code.startswith(('00', '30')):
        return 'sz'
    return 'bj'


def select_symbols(universe, per_market):
    result = {}
    for exchange in ('sh', 'sz', 'bj'):
        codes = sorted({row['code'] for row in universe['rows'] if market(row['code']) == exchange})
        if len(codes) < per_market:
            raise ValueError('Universe does not cover requested market sample')
        indices = [round(i * (len(codes) - 1) / max(1, per_market - 1)) for i in range(per_market)]
        result[exchange] = [exchange + codes[index] for index in indices]
    return result


def run(args):
    import akshare as ak
    import akshare.stock.stock_zh_a_sina as provider
    import pandas as pd
    import requests

    target = date.fromisoformat(args.date)
    if args.interval < .75 or args.per_market < 10:
        raise ValueError('Probe requires at least 0.75 seconds and 10 stocks per market')
    if not adapter_ready(ak):
        raise RuntimeError('Pinned adapter is not active; a fallback is not an acceptance pass')
    universe = json.loads(Path(args.universe).read_text())
    samples = select_symbols(universe, args.per_market)
    for symbol in args.extra_symbol:
        exchange = symbol[:2]
        if exchange not in samples or len(symbol) != 8 or not symbol[2:].isdigit() or market(symbol[2:]) != exchange:
            raise ValueError('Extra sample must be an explicit A-share symbol')
        if symbol not in samples[exchange]:
            samples[exchange].append(symbol)
    request_times, results, observed_raw = [], [], {}
    original_request = requests.sessions.Session.request

    def bounded(session, method, url, **kwargs):
        if request_times:
            time.sleep(max(0, args.interval - (time.monotonic() - request_times[-1])))
        request_times.append(time.monotonic())
        kwargs['timeout'] = 20
        response = original_request(session, method, url, **kwargs)
        response.raise_for_status()
        if '/hisdata_klc2/klc_kl.js' in url:
            observed_raw[url] = response
        return response

    started = datetime.now(timezone.utc).isoformat()
    with patch.object(requests.sessions.Session, 'request', bounded):
        for exchange, symbols in samples.items():
            for symbol in symbols:
                error = None
                for attempt in range(1, 4):
                    before = len(request_times)
                    stage = 'official_baseline'
                    try:
                        old = ak.stock_zh_a_daily(symbol=symbol, start_date=target.strftime('%Y%m%d'),
                                                  end_date=target.strftime('%Y%m%d'), adjust='')
                        response = observed_raw[provider.zh_sina_a_stock_hist_url.format(symbol)]
                        stage = 'optimized_adapter'
                        with patch.object(provider.requests, 'get', return_value=response) as replay:
                            new = daily_unadjusted(ak, symbol, target, target)
                        old_pairs = list(zip(old['date'].astype(str), old['volume'].astype(float)))
                        new_pairs = list(zip(new['date'].astype(str), new['volume'].astype(float)))
                        equal = (old_pairs == new_pairs and len(new_pairs) == 1 and
                                 new_pairs[0][0] == args.date and math.isfinite(new_pairs[0][1]))
                        if equal:
                            stage = 'common_columns_comparison'
                            pd.testing.assert_frame_equal(old[['date', 'open', 'high', 'low', 'close', 'volume', 'amount']], new)
                        results.append(dict(symbol=symbol, market=exchange, equal=equal,
                                            targetRows=len(new), sourceRequests=len(request_times)-before,
                                            optimizedRequests=replay.call_count, attempts=attempt,
                                            speedDegraded=bool(new.attrs.get('speedDegraded'))))
                        error = None
                        break
                    except Exception as exc:
                        error = type(exc).__name__
                if error:
                    results.append(dict(symbol=symbol, market=exchange, equal=False, error=error,
                                        errorStage=stage, attempts=3))
    by_market = {exchange: {'tested': sum(r['market'] == exchange for r in results),
                            'matched': sum(r['market'] == exchange and r['equal'] for r in results)}
                 for exchange in samples}
    intervals = [b - a for a, b in zip(request_times, request_times[1:])]
    report = dict(startedAt=started, finishedAt=datetime.now(timezone.utc).isoformat(),
                  targetDate=args.date, akshareVersion=AKSHARE_VERSION, sourceSha256=SOURCE_SHA256,
                  decoderSha256=DECODER_SHA256, sameRawResponse=True, marketCounts=by_market,
                  passed=all(r['equal'] and not r.get('speedDegraded') for r in results),
                  comparisonSampleRequirementMet=all(counts['matched'] >= args.per_market for counts in by_market.values()),
                  networkRequests=len(request_times), minimumRequestInterval=min(intervals) if intervals else None,
                  samples=results)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    parser.add_argument('--universe', required=True)
    parser.add_argument('--per-market', type=int, default=10)
    parser.add_argument('--interval', type=float, default=.75)
    parser.add_argument('--extra-symbol', action='append', default=[],
                        help='Additional explicit sample; prior source failures remain visible')
    raise SystemExit(run(parser.parse_args()))
