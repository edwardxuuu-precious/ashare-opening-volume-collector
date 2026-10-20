import _bootstrap
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
from scripts import actions_source_probe as probe

DAY = '2026-09-07'
GOOD = dict(status='ok', quality='matched', ratio=6.25, dayVolumeDifference=0,
            first15Volume=10, dailyVolume=160)
DOWNLOADED = dict(status='ok', quality='downloaded', calculationPolicy='download_only',
                  ratio=6.25, first15Volume=10, dailyVolume=160)


def moment(value):
    return datetime.fromisoformat(value).replace(tzinfo=probe.ZONE)


class ProbeTests(unittest.TestCase):
    def test_cli_writes_only_summary_and_returns_failure_for_unusable_backup(self):
        for passed in (True, False):
            with self.subTest(passed=passed), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'report.json'
                with patch.object(probe.sys, 'argv', ['probe', '--report', str(path)]), \
                     patch.object(probe, 'run_probe', return_value=dict(passed=passed, scope='three_stock_sample')):
                    self.assertEqual(probe.main(), 0 if passed else 1)
                self.assertEqual(json.loads(path.read_text()), dict(passed=passed, scope='three_stock_sample'))

    def test_closed_date_cutoff_and_weekend(self):
        calendar = ['2026-09-04', DAY, '2026-09-08']
        self.assertEqual(probe.select_date(calendar, moment(DAY+'T14:59:59')), '2026-09-04')
        self.assertEqual(probe.select_date(calendar, moment(DAY+'T15:00:00')), DAY)
        self.assertEqual(probe.select_date(calendar, moment('2026-09-06T20:00:00')), '2026-09-04')
        with self.assertRaises(ValueError):
            probe.select_date([DAY], moment(DAY+'T14:59:59'))

    def test_download_only_primary_result_is_ready(self):
        self.assertEqual(probe.classify(DOWNLOADED), 'success')

    def test_conflict_reference_is_not_success(self):
        self.assertEqual(probe.classify(dict(GOOD, status='unverified', referenceRatio=6.25)), 'conflict')
        self.assertEqual(probe.classify(dict(GOOD, ratio=float('nan'))), 'missing')
        self.assertEqual(probe.classify(dict(GOOD, ratio=True)), 'missing')
        self.assertEqual(probe.classify(None), 'missing')

    def run_fixture(self, primary, bao):
        with patch.object(probe, 'read_calendar', return_value=[DAY]), \
             patch.object(probe, 'collect_primary', return_value=primary), \
             patch.object(probe, 'collect_bao', return_value=(bao, [])):
            return probe.run_probe(moment(DAY+'T17:00:00'))

    def test_backup_success_with_primary_missing_does_not_fake_bj_success(self):
        report = self.run_fixture({}, {code:GOOD for code, _ in probe.SAMPLES[:2]})
        self.assertFalse(report['passed'])
        self.assertFalse(report['primaryReady'])
        self.assertEqual(report['verifiedShSzCount'], 2)
        self.assertEqual(report['primary'], dict(total=3, success=0, conflict=0, missing=3))
        self.assertEqual(report['primaryByMarket']['BJ']['missing'], 1)
        self.assertEqual(report['baostock']['unsupported'], 1)
        self.assertFalse(report['fullMarketVerified'])
        self.assertFalse(report['fullMarketDurationVerified'])

    def test_one_valid_bao_stock_is_not_backup_available(self):
        report = self.run_fixture({code:GOOD for code, _ in probe.SAMPLES}, {'000001':GOOD})
        self.assertTrue(report['passed'])
        self.assertTrue(report['primaryReady'])
        self.assertFalse(report['backupAvailable'])
        self.assertEqual(report['baostock']['missing'], 1)

    def test_conflict_counts_and_safe_error_summary_no_row_export(self):
        rows = {'000001':dict(GOOD, status='unverified', reason='private payload'),
                '600519':dict(status='missing', reason='采集失败：ReadTimeout'),
                '920002':dict(status='missing', reason='url=https://secret.invalid?token=SECRET')}
        report = self.run_fixture(rows, {})
        self.assertEqual(report['primary'], dict(total=3, success=0, conflict=1, missing=2))
        self.assertEqual(report['errors'], [dict(stage='primary', code='600519', type='ReadTimeout')])
        serialized = json.dumps(report)
        for forbidden in ('private payload', 'secret.invalid', 'SECRET', 'referenceRatio', 'first15Volume'):
            self.assertNotIn(forbidden, serialized)

    def test_calendar_failure_never_calls_sources_or_exports_exception_message(self):
        with patch.object(probe, 'read_calendar', side_effect=RuntimeError('secret body')), \
             patch.object(probe, 'collect_primary') as primary, patch.object(probe, 'collect_bao') as bao:
            report = probe.run_probe()
        primary.assert_not_called()
        bao.assert_not_called()
        self.assertFalse(report['passed'])
        self.assertIsNone(report['selectedDate'])
        self.assertEqual(report['errors'], [dict(stage='calendar', type='RuntimeError')])

    def test_primary_timeout_still_tests_independent_bao(self):
        with patch.object(probe, 'read_calendar', return_value=[DAY]), \
             patch.object(probe, 'collect_primary', side_effect=TimeoutError('raw timeout body')), \
             patch.object(probe, 'collect_bao', return_value=({code:GOOD for code, _ in probe.SAMPLES[:2]}, [])):
            report = probe.run_probe(moment(DAY+'T17:00:00'))
        self.assertFalse(report['passed'])
        self.assertFalse(report['primaryReady'])
        self.assertTrue(report['backupAvailable'])
        self.assertEqual(report['errors'], [dict(stage='primary', type='TimeoutError')])

    def test_four_worker_spawn_pool_uses_daily_primary_path(self):
        ctx, http = Mock(), Mock()
        pool = ctx.Pool.return_value.__enter__ = Mock(return_value=Mock())
        ctx.Pool.return_value.__exit__ = Mock(return_value=False)
        pool.return_value.map_async.return_value.get.return_value = [('000001', {DAY:GOOD})]
        with patch.object(probe.daily, 'SharedFallbackBudget', return_value='fallback'):
            rows = probe.collect_primary(ctx, http, DAY, probe.time.monotonic()+10)
        ctx.Pool.assert_called_once_with(4, initializer=probe.init_probe_primary, initargs=(http, 'fallback'))
        self.assertEqual(pool.return_value.map_async.call_args.args[0], probe.daily.fetch_primary)
        self.assertEqual(len(pool.return_value.map_async.call_args.args[1]), 3)
        self.assertEqual(rows, {'000001':GOOD})

    def test_bao_serial_same_pair_exact_evaluation_and_no_bj_request(self):
        minute = pd.DataFrame([dict(day=DAY+' '+clock, volume=10, close=10) for clock in probe.collect.TIMES])
        daily = pd.DataFrame([dict(date=DAY, volume=160, close=10)])
        calls = []
        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def pair(self, code, dates):
                calls.append((code, dates))
                return minute, daily if code == '000001' else daily.assign(volume=161)
        factory = Mock(return_value=Session())
        rows, errors = probe.collect_bao(DAY, factory)
        factory.assert_called_once_with(timeout=20, interval=.75)
        self.assertEqual(calls, [('000001', [DAY]), ('600519', [DAY])])
        self.assertEqual(probe.classify(rows['000001']), 'success')
        self.assertEqual(probe.classify(rows['600519']), 'conflict')
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
