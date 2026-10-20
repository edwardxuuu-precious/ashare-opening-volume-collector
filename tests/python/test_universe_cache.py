import _bootstrap
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from scripts.universe_cache import load_universe, UniverseUnavailable, validate_universe


class UniverseCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'universe.json'
        self.rows = [{'code': '000001', 'name': '平安银行'},
                     {'code': '600519', 'name': '贵州茅台'},
                     {'code': '920001', 'name': '测试北交'}]

    def seed(self, day='2026-09-06', **changes):
        payload = dict(asOf=day, historicalMembership=False, total=3, rows=self.rows)
        payload.update(changes)
        self.path.write_text(json.dumps(payload), encoding='utf-8')
        return payload

    def load(self, fetcher, day='2026-09-06', **kwargs):
        return load_universe(self.path, day, fetcher, minimum_count=3, **kwargs)

    def test_same_day_reuses_validated_seed_without_network(self):
        original = self.seed()
        fetcher = Mock(side_effect=AssertionError('network should not run'))
        result = self.load(fetcher)
        fetcher.assert_not_called()
        self.assertEqual(result['rows'], original['rows'])
        self.assertEqual(result['source'], 'cache_same_day')

    def test_successful_refresh_replaces_old_snapshot(self):
        self.seed('2026-09-05')
        result = self.load(Mock(return_value=self.rows))
        self.assertEqual(result['asOf'], '2026-09-06')
        self.assertEqual(result['source'], 'akshare_live')
        self.assertEqual(result, json.loads(self.path.read_text()))
        self.assertNotIn('refreshError', result)

    def test_day_seven_failure_preserves_original_age_and_error(self):
        self.seed('2026-08-30')
        result = self.load(Mock(side_effect=ConnectionResetError('source reset')))
        self.assertEqual(result['asOf'], '2026-08-30')
        self.assertEqual(result['cacheAgeDays'], 7)
        self.assertEqual(result['source'], 'cache_fallback')
        self.assertIn('ConnectionResetError', result['refreshError'])
        self.assertEqual(result, json.loads(self.path.read_text()))
        with self.assertRaises(UniverseUnavailable):
            self.load(Mock(side_effect=ConnectionResetError()), day='2026-09-07')

    def test_expired_cache_fails_without_renewing_snapshot(self):
        self.seed('2026-08-29')
        before = self.path.read_bytes()
        with self.assertRaises(UniverseUnavailable):
            self.load(Mock(side_effect=RuntimeError('offline')))
        self.assertEqual(before, self.path.read_bytes())

    def test_bad_or_future_cache_never_used_on_refresh_failure(self):
        invalid = [dict(total=4), dict(rows=self.rows[:2], total=2),
                   dict(rows=[self.rows[0]] * 3), dict(asOf='2026-09-07'),
                   dict(historicalMembership=True), dict(total=True),
                   dict(rows=[dict(self.rows[0], market='SH')] + self.rows[1:]),
                   dict(rows=[dict(self.rows[0], code='123456')] + self.rows[1:]),
                   dict(rows=[dict(self.rows[0], name=' ')] + self.rows[1:])]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.seed(**changes)
                with self.assertRaises(UniverseUnavailable):
                    self.load(Mock(side_effect=RuntimeError('offline')))

    def test_corrupt_cache_can_be_repaired_by_valid_refresh(self):
        self.path.write_text('{broken', encoding='utf-8')
        result = self.load(Mock(return_value=self.rows))
        self.assertEqual(result['source'], 'akshare_live')

    def test_invalid_fresh_list_uses_eligible_cache(self):
        self.seed('2026-09-05')
        result = self.load(Mock(return_value=self.rows[:1]))
        self.assertEqual(result['source'], 'cache_fallback')
        self.assertEqual(result['asOf'], '2026-09-05')
        self.assertIn('ValueError', result['refreshError'])

    def test_no_cache_and_invalid_or_failed_fetch_fails(self):
        for fetcher in [Mock(return_value=[]), Mock(side_effect=RuntimeError('offline'))]:
            with self.assertRaises(UniverseUnavailable):
                self.load(fetcher)
        self.assertFalse(self.path.exists())

    def test_names_need_not_be_unique_but_codes_must_be(self):
        payload = self.seed(rows=[dict(row, name='同名股票') for row in self.rows])
        validate_universe(payload, minimum_count=3)

    def test_retention_cannot_be_extended_past_seven_days(self):
        with self.assertRaises(ValueError):
            self.load(Mock(), max_age_days=8)

    def test_dataframe_records_and_numeric_codes_are_supported(self):
        frame = Mock()
        frame.to_dict.return_value = [dict(self.rows[0], code=1)] + self.rows[1:]
        result = self.load(Mock(return_value=frame))
        frame.to_dict.assert_called_once_with('records')
        self.assertEqual(result['rows'][0]['code'], '000001')

    def test_unicode_digits_are_not_valid_identifiers(self):
        self.seed(rows=[dict(self.rows[0], code='00000١')] + self.rows[1:])
        with self.assertRaises(UniverseUnavailable):
            self.load(Mock(side_effect=RuntimeError('offline')))


if __name__ == '__main__':
    unittest.main()
