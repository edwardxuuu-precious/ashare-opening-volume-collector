import _bootstrap
from datetime import date, datetime, timedelta, timezone
import json
import math
import unittest

from backend.cycle_bottom.daily import publish, publication_status
from backend.cycle_bottom.model import build_comparison, build_snapshot, entry_events, forward_outcomes
from backend.cycle_bottom.sources import eligible_end, parse_csi


def invented_prices():
    equity, reference = [], []
    day = date(2009, 1, 1)
    for index in range(5000):
        if day.weekday() < 5:
            value = 100 * math.exp(index / 16000)
            reference.append({'date': day.isoformat(), 'close': value})
            equity.append({'date': day.isoformat(), 'close': 5 * value ** 2})
        day += timedelta(days=1)
    return equity, reference


class CycleMath(unittest.TestCase):
    def build(self, equity, reference):
        return build_comparison(equity, reference, identifier='government', name='INVENTED', code='INVENTED')

    def test_known_power_law_is_recovered_without_fitting_to_five_year_labels(self):
        equity, reference = invented_prices()
        result = self.build(equity, reference)
        self.assertAlmostEqual(result['fit']['beta'], 2, places=10)
        self.assertAlmostEqual(result['fit']['alpha'], math.log(5), places=10)
        self.assertAlmostEqual(result['latest']['gapPct'], 0, places=8)
        self.assertEqual(result['fit']['holdoutBelowTrainingDays'], 0)

    def test_future_prices_do_not_change_frozen_training_parameters(self):
        equity, reference = invented_prices()
        original = self.build(equity, reference)
        changed = [{**row, 'close': row['close'] * .5 if row['date'] > '2019-12-31' else row['close']} for row in equity]
        result = self.build(changed, reference)
        self.assertEqual(result['fit']['alpha'], original['fit']['alpha'])
        self.assertEqual(result['fit']['beta'], original['fit']['beta'])
        self.assertAlmostEqual(result['latest']['gapPct'], -50, places=8)
        self.assertGreater(result['fit']['holdoutBelowTrainingDays'], 0)

    def test_index_rebasing_does_not_change_fitted_distance(self):
        equity, reference = invented_prices()
        original = self.build(equity, reference)
        changed = self.build([{**r, 'close': r['close'] * 3} for r in equity], [{**r, 'close': r['close'] * 7} for r in reference])
        self.assertAlmostEqual(changed['latest']['gapPct'], original['latest']['gapPct'], places=8)
        self.assertAlmostEqual(changed['latest']['rawGapPct'], original['latest']['rawGapPct'], places=8)

    def test_incomplete_forward_window_remains_unknown_not_zero(self):
        day = date(2020, 1, 1)
        equity = {(day + timedelta(days=i)).isoformat(): 100 + i for i in range(130)}
        result = forward_outcomes(day.isoformat(), equity)
        self.assertTrue(result[0]['complete'])
        self.assertAlmostEqual(result[0]['returnPct'], 126, places=10)
        self.assertFalse(result[1]['complete'])
        self.assertIsNone(result[1]['returnPct'])
        self.assertIsNone(result[1]['downsidePct'])

    def test_downside_is_relative_to_entry_and_does_not_claim_peak_to_trough_drawdown(self):
        day = date(2020, 1, 1)
        equity = {(day + timedelta(days=i)).isoformat(): 110 if i == 1 else 105 for i in range(253)}
        equity[day.isoformat()] = 100
        result = forward_outcomes(day.isoformat(), equity)
        self.assertEqual(result[0]['downsidePct'], 0)
        self.assertAlmostEqual(result[0]['returnPct'], 5)

    def test_direct_overlap_can_enter_from_below_but_deeply_below_is_not_close(self):
        day = date(2020, 1, 1)
        points = [{'date': (day + timedelta(days=i)).isoformat(), 'rawGapPct': gap, 'gapPct': gap} for i, gap in enumerate([-10, -8, -2, 0, 8])]
        equity = {p['date']: 100 for p in points}
        result = entry_events(points, equity, field='rawGapPct')
        self.assertEqual([r['date'] for r in result], [points[2]['date']])


class CycleSourceContract(unittest.TestCase):
    def test_missing_stock_sessions_cannot_silently_shorten_forward_windows(self):
        with self.assertRaisesRegex(ValueError, 'EQUITY_HISTORY_GAP'):
            build_snapshot({}, [{'id': 'equity', 'missingSessions': 1}], expected_as_of='2026-11-06')

    def test_unverified_calendar_year_is_visible_even_when_sources_agree(self):
        snapshot = {'generatedAt': '2027-01-03T00:00:00Z', 'expectedAsOf': '2026-12-31',
                    'sources': [{'name': 'INVENTED', 'lastDate': '2026-12-31', 'calendarVerified': False}]}
        result = publication_status(snapshot, checked_at='2027-01-03T01:00:00Z')
        self.assertEqual(result['state'], 'partial')
        self.assertIn('日历待核对', result['message'])

    def test_source_identity_and_calendar_are_checked_before_modeling(self):
        start = date(2010, 1, 1)
        days = [(start + timedelta(days=i)).isoformat() for i in range(502)]
        rows = [{'indexCode': 'H00300', 'tradeDate': day.replace('-', ''), 'close': 1000 + i} for i, day in enumerate(days)]
        calendar = set(days[1:])
        actual, excluded = parse_csi(json.dumps({'success': True, 'data': rows}), 'H00300', minimum=days[0], end=days[-1], calendar=calendar)
        self.assertEqual(len(actual), 501)
        self.assertEqual(excluded, 1)
        rows[1]['indexCode'] = '000300'
        with self.assertRaisesRegex(ValueError, 'IDENTITY'):
            parse_csi(json.dumps({'success': True, 'data': rows}), 'H00300', minimum=days[0], end=days[-1], calendar=calendar)

    def test_intraday_record_is_not_treated_as_completed_daily_observation(self):
        self.assertEqual(eligible_end(datetime(2026, 11, 9, 8, 29, tzinfo=timezone.utc)), '2026-11-08')
        self.assertEqual(eligible_end(datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc)), '2026-11-09')


class MemoryStorage:
    def __init__(self):
        self.objects, self.writes, self.fail_analysis = {}, [], False

    def read(self, path):
        return self.objects.get(path), 'INVENTED_ETAG' if path in self.objects else None

    def write(self, path, raw, **kwargs):
        if self.fail_analysis and path.endswith('/analysis.json'):
            raise RuntimeError('INVENTED_STORAGE_FAILURE')
        if kwargs.get('immutable') and path in self.objects:
            if self.objects[path] != raw:
                raise RuntimeError('IMMUTABLE_MISMATCH')
            return
        self.objects[path] = raw
        self.writes.append(path)


class CyclePublication(unittest.TestCase):
    def snapshot(self):
        return {'version': 'a' * 16, 'generatedAt': '2026-11-08T00:00:00Z', 'expectedAsOf': '2026-11-06', 'sources': [{'name': 'INVENTED', 'lastDate': '2026-11-06'}]}

    def test_analysis_failure_never_moves_publication_manifest(self):
        store = MemoryStorage()
        store.objects['manifest.json'] = b'{"version":"bbbbbbbbbbbbbbbb"}'
        store.fail_analysis = True
        with self.assertRaisesRegex(RuntimeError, 'STORAGE_FAILURE'):
            publish(store, self.snapshot(), {'source': b'INVENTED'})
        self.assertEqual(store.objects['manifest.json'], b'{"version":"bbbbbbbbbbbbbbbb"}')
        self.assertNotIn('manifest.json', store.writes)

    def test_manifest_is_last_and_repeated_identical_data_preserves_original_snapshot_time(self):
        store = MemoryStorage()
        snapshot = self.snapshot()
        publish(store, snapshot, {'source': b'INVENTED'})
        published_at = json.loads(store.objects['manifest.json'])['publishedAt']
        self.assertLess(store.writes.index('a' * 16 + '/analysis.json'), store.writes.index('manifest.json'))
        store.writes.clear()
        snapshot['generatedAt'] = '2026-11-09T00:00:00Z'
        self.assertEqual(publish(store, snapshot, {'source': b'INVENTED'})['state'], 'unchanged')
        self.assertEqual(store.writes, ['status.json'])
        self.assertEqual(json.loads(store.objects['status.json'])['lastSuccessAt'], published_at)
