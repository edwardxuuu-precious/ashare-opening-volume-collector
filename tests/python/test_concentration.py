"""Invented prices and identities; no saved market inputs or network."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import unittest

from backend.chips.concentration import (GROUPS, concentration_document, concentration_row,
    concentration_value, decisions, needs_archive, trailing_sessions)
from backend.chips.eastmoney_model import MODEL, calculate
from backend.chips.history import is_session
from backend.chips.model import Bar

DATASET = '0123456789abcdef'


def fixture(code='999999'):
    bars = []
    day = date(2025, 4, 1)
    while day <= date(2026, 10, 30):
        if is_session(day.isoformat()):
            p = Decimal(10) + Decimal(len(bars) % 30) / 100
            bars.append(Bar(day.isoformat(), p, p + Decimal('.2'), p - Decimal('.2'),
                p + Decimal('.05'), Decimal(10000), p * 10000, Decimal('2.5')))
        day += timedelta(days=1)
    window = bars[-210:]
    snapshot = calculate(window, security_id=code, window_start=window[0].source_date, window_rows=len(window))
    fields = ('date', 'open', 'high', 'low', 'close', 'volume_shares', 'amount_cny', 'turnover_percent')
    history = [dict(zip(fields, map(str, (b.source_date, b.open, b.high, b.low, b.close,
        b.volume_shares, b.amount_cny, b.turnover_pct)))) for b in bars]
    lines = [','.join(map(str, (b.source_date, b.open, b.close, b.high, b.low, b.volume_shares / 100,
        b.amount_cny, '0', '0', '0', b.turnover_pct))) for b in bars]
    raw = json.dumps({'rc': 0, 'data': {'code': code, 'name': 'INVENTED', 'klines': lines}}, separators=(',', ':')).encode()
    frame = {'schema': 'stock.chips.market.stock.v1', 'dataset_id': DATASET, 'as_of': '2026-10-30',
        'security_id': code, 'name': 'INVENTED', 'chip_status': 'READY', 'model': MODEL,
        'source': {'sha256': hashlib.sha256(raw).hexdigest()}, 'history': history,
        'source_audit': {'used_rows': len(bars), 'used_first_source_date': bars[0].source_date,
                         'used_last_source_date': bars[-1].source_date},
        'snapshots': {'2026-10-30': snapshot}}
    return frame, raw


def manifest(total=1):
    return {'schema': 'stock.chips.market.manifest.v1', 'dataset_id': DATASET, 'active_slot': DATASET,
        'as_of': '2026-10-30', 'coefficient': 2, 'model_version': MODEL['version'],
        'universe_total': total, 'scanned_count': total, 'model_ready_count': total,
        'universe_coverage': 'SITE_CATALOG_UNVERIFIED'}


class ConcentrationRules(unittest.TestCase):
    def test_quantile_definition_uses_5_and_95_not_10_and_90(self):
        profile = [{'price_cny': str(p), 'share_percent': str(s)} for p, s in [(8, 5), (9, 10), (10, 70), (11, 10), (12, 5)]]
        self.assertAlmostEqual(concentration_value(profile), 3 / 19 * 100, places=12)
        self.assertEqual(concentration_value([{'price_cny': '10', 'share_percent': '100'}]), 0)
        for invalid in [[{'price_cny': '0', 'share_percent': '100'}], [{'price_cny': '10', 'share_percent': '99'}]]:
            with self.assertRaises(ValueError): concentration_value(invalid)

    def test_five_exact_transition_boundaries_and_overlapping_groups(self):
        for n in GROUPS:
            values = [None] * (20 - n) + list(range(n + 1, 0, -1))
            row = decisions(values, {})
            self.assertEqual(row['streak_days'], n)
            for group in GROUPS:
                self.assertEqual(row['groups'][str(group)], 'PASS' if group <= n else 'UNKNOWN')
        self.assertEqual(decisions(list(range(21, 0, -1)), {})['streak_boundary'], 'WINDOW_LIMIT')

    def test_equal_breaks_but_equal_display_rounding_does_not(self):
        values = [30 - i for i in range(21)]
        values[-1] = values[-2]
        self.assertEqual(set(decisions(values, {})['groups'].values()), {'FAIL'})
        values = [1 - i * .000001 for i in range(21)]
        self.assertEqual(len({f'{v:.2f}' for v in values}), 1)
        self.assertEqual(set(decisions(values, {})['groups'].values()), {'PASS'})

    def test_known_failure_dominates_missing_and_recent_suffix_remains_usable(self):
        values = [None] * 15 + [6, 5, 4, 3, 2, 1]
        self.assertEqual(decisions(values, {})['groups'], {'3': 'PASS', '5': 'PASS', '10': 'UNKNOWN', '15': 'UNKNOWN', '20': 'UNKNOWN'})
        values[-1] = 2
        self.assertEqual(set(decisions(values, {})['groups'].values()), {'FAIL'})
        values[-1] = None
        self.assertEqual(set(decisions(values, {'20': 'NO_TRADE'})['groups'].values()), {'FAIL'})
        self.assertEqual(set(decisions(values, {})['groups'].values()), {'UNKNOWN'})

    def test_market_axis_skips_exchange_holidays_and_requires_21_values(self):
        days = trailing_sessions('2026-10-30')
        self.assertEqual((len(days), days[0], days[-1]), (21, '2026-09-24', '2026-10-30'))
        self.assertNotIn('2026-10-01', days)
        for day in ['2026-10-31', '2027-10-29']:
            with self.assertRaises(ValueError): trailing_sessions(day)

    def test_each_date_has_independent_window_and_future_bars_do_not_leak(self):
        frame, _ = fixture(); days = trailing_sessions(frame['as_of'])
        row = concentration_row(frame, sessions=days)
        self.assertIsNotNone(row['values'][-1])
        selected = days[5]
        changed = deepcopy(frame)
        for bar in changed['history']:
            if bar['date'] > selected: bar['high'] = '900.00'
        altered = concentration_row(changed, sessions=days)
        self.assertEqual(row['values'][:6], altered['values'][:6])
        expected_bars = [b for b in frame['history'] if b['date'] <= selected][-210:]
        bars = [Bar(b['date'], *(Decimal(b[k]) for k in ('open', 'high', 'low', 'close', 'volume_shares', 'amount_cny', 'turnover_percent'))) for b in expected_bars]
        expected = calculate(bars, security_id=frame['security_id'], window_start=bars[0].source_date, window_rows=210)
        self.assertEqual(row['values'][5], concentration_value(expected['profile']))

    def test_sealed_latest_mismatch_cannot_pass(self):
        frame, _ = fixture(); frame['snapshots'][frame['as_of']]['raw_node_masses'][0] = '999'
        row = concentration_row(frame, sessions=trailing_sessions(frame['as_of']))
        self.assertIsNone(row['values'][-1])
        self.assertEqual(row['point_reasons']['20'], 'SEALED_SNAPSHOT_MISMATCH')
        self.assertNotIn('PASS', row['groups'].values())

    def test_short_observed_source_does_not_invent_a_listing_date(self):
        frame, _ = fixture(); frame['history'] = frame['history'][-6:]
        frame['source_audit'].update(used_rows=6, used_first_source_date=frame['history'][0]['date'])
        bars = [Bar(r['date'], *(Decimal(r[k]) for k in ('open', 'high', 'low', 'close', 'volume_shares', 'amount_cny', 'turnover_percent'))) for r in frame['history']]
        frame['snapshots'][frame['as_of']] = calculate(bars, security_id=frame['security_id'], window_start=bars[0].source_date, window_rows=6)
        self.assertFalse(needs_archive(frame, trailing_sessions(frame['as_of'])))
        row = concentration_row(frame, sessions=trailing_sessions(frame['as_of']))
        self.assertEqual(row['short_history_days'], 6)
        self.assertNotIn('PRE_LISTING', row['point_reasons'].values())
        self.assertEqual(len([v for v in row['values'] if v is not None]), 6)

    def test_archive_hash_and_model_identity_are_not_repaired(self):
        frame, raw = fixture(); frame['history'] = frame['history'][-10:]
        self.assertTrue(needs_archive(frame, trailing_sessions(frame['as_of'])))
        with self.assertRaisesRegex(ValueError, 'SOURCE_HASH'): concentration_row(frame, sessions=trailing_sessions(frame['as_of']), archived_raw=raw + b' ')
        row = concentration_row(frame, sessions=trailing_sessions(frame['as_of']), archived_raw=raw)
        self.assertTrue(all(v is not None for v in row['values']))
        frame['model'] = {**frame['model'], 'version': 'INVALID'}
        with self.assertRaisesRegex(ValueError, 'IDENTITY'): concentration_row(frame, sessions=trailing_sessions(frame['as_of']))

    def test_document_requires_complete_denominator_and_retains_coverage_limit(self):
        frame, _ = fixture(); row = concentration_row(frame, sessions=trailing_sessions(frame['as_of']))
        document = concentration_document(manifest(), [row], input_fingerprint='a' * 64)
        self.assertEqual(document['source_universe_coverage'], 'SITE_CATALOG_UNVERIFIED')
        for counts in document['groups'].values(): self.assertEqual(sum(counts.values()), 1)
        with self.assertRaisesRegex(ValueError, 'UNIVERSE'): concentration_document(manifest(2), [row], input_fingerprint='a' * 64)


if __name__ == '__main__': unittest.main()
