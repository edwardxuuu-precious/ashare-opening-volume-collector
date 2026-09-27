"""Completion labels must follow row status, not a stale placeholder reason."""
import _bootstrap
import json
import tempfile
import unittest
from pathlib import Path

from scripts import daily_collector as daily
import test_refresh_worker as fixtures


class PendingLabelTests(unittest.TestCase):
    def test_fresh_success_clears_placeholder_reason_without_losing_saved_fields(self):
        fixture = fixtures.RefreshWorkerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        code = fixtures.CODES[0]
        pending = fixtures.row(code, status='missing', first15Volume=None,
                               dailyVolume=None, ratio=None, reason='尚未采集',
                               fixtureMarker='preserved')
        fixture.write(fixtures.DAY + '.json', dict(date=fixtures.DAY,
                      generatedAt='fixture', universeTotal=1, rows=[pending]))

        self.assertEqual(fixture.run_worker(), 0)

        saved = fixture.read(fixtures.DAY + '.json')
        completed = saved['rows'][0]
        self.assertEqual(completed['status'], 'ok')
        self.assertNotIn('reason', completed)
        self.assertEqual(completed['fixtureMarker'], 'preserved')
        self.assertEqual((completed['first15Volume'], completed['dailyVolume'],
                          completed['ratio']), (10, 100, 10))
        self.assertNotIn('reason', fixture.read('checkpoint/' + code + '.json')
                         ['days'][fixtures.DAY])
        self.assertTrue(fixture.visible()['dataComplete'])
        self.assertEqual(saved['scope'], 'full')
        self.assertEqual(fixture.read('manifest.json')['dates'][0]['status'], 'complete')

    def publish(self, rows):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        out = Path(temp.name)
        items = [dict(code=row['code'], name=row['name']) for row in rows]
        daily.publish_changes(out, items,
                              {fixtures.DAY: {row['code']: row for row in rows}},
                              single_source=True, require_no_trade_evidence=True)
        return (json.loads((out / (fixtures.DAY + '.json')).read_text()),
                json.loads((out / 'manifest.json').read_text()))

    def test_usable_rows_with_stale_placeholder_reason_are_attempted(self):
        good = fixtures.row(fixtures.CODES[0], reason='尚未采集')
        stopped = fixtures.row(fixtures.CODES[1], status='suspended',
                               first15Volume=None, dailyVolume=0, ratio=None,
                               reason='尚未采集', noTradeEvidence=dict(
                                   kind='explicit_zero_daily_volume', provider='sina',
                                   date=fixtures.DAY, symbol='sh600519', volume=0))

        saved, manifest = self.publish([good, stopped])

        self.assertEqual(saved['attemptedCount'], 2)
        self.assertEqual(saved['pendingCount'], 0)
        self.assertEqual(saved['scope'], 'full')
        self.assertEqual(manifest['dates'][0]['status'], 'complete')
        self.assertEqual(manifest['dates'][0]['missing'], 0)

    def test_missing_placeholder_still_keeps_snapshot_partial(self):
        pending = fixtures.row(fixtures.CODES[0], status='missing',
                               first15Volume=None, dailyVolume=None, ratio=None,
                               reason='尚未采集')

        saved, manifest = self.publish([pending])

        self.assertEqual(saved['attemptedCount'], 0)
        self.assertEqual(saved['pendingCount'], 1)
        self.assertEqual(saved['scope'], 'partial')
        self.assertEqual(manifest['dates'][0]['status'], 'partial')
        self.assertEqual(manifest['dates'][0]['missing'], 1)


if __name__ == '__main__':
    unittest.main()
