"""New fictional whole-market grid cases. No market I/O."""
from copy import deepcopy
import unittest

from backend.chips.calendar import sessions
from backend.chips.golden import VERSION, document, grid_row


def fixture(low='14'):
    axis = sessions('2026-01-01', '2026-06-01')
    axis = axis[:67]
    rows = [{'date': day, 'open': '15', 'high': '15.4', 'low': '14.8', 'close': '15'} for day in axis]
    rows[10].update(open='19', high='20', low='18', close='19')
    rows[20].update(open=low, high='14.4' if low == '14' else '10.4', low=low, close=low)
    rows[65].update(open='15', high='16.2', low='14.9', close='16')
    return {'security_id': '000001', 'name': 'FICTIONAL', 'as_of': axis[65], 'history': rows}, axis


class GoldenGridCases(unittest.TestCase):
    def test_known_grid_and_same_axis_crossing(self):
        frame, _ = fixture()
        result = grid_row(frame)
        self.assertEqual(result['status'], 'PASS')
        g = result['geometry']
        self.assertEqual([g[k] for k in ('H', 'L', 'midpoint', 'upper', 'main_midpoint', 'quarter', 'eighth')],
                         ['20', '14', '15.5', '17', '17', '15.5', '14.75'])
        self.assertEqual(g['crossing'], 'UP')
        self.assertTrue(g['intraday_touch'])
        self.assertIsNone(result['known_at'])

    def test_equality_touch_and_changed_axis_are_distinct(self):
        frame, _ = fixture()
        frame['history'][65]['close'] = '15.5'
        equal = grid_row(frame)['geometry']
        self.assertEqual((equal['close_side'], equal['crossing']), ('AT', 'NONE'))
        frame['history'][60].update(open='14.2', high='14.4', low='14', close='14.2')
        changed = grid_row(frame)['geometry']
        self.assertEqual((changed['crossing'], changed['comparison']), ('UNKNOWN', 'STRUCTURE_CHANGED'))

    def test_missing_session_and_low_breach_not_passed(self):
        frame, _ = fixture()
        broken = deepcopy(frame)
        broken['history'].pop(30)
        self.assertEqual(grid_row(broken)['status'], 'UNKNOWN')
        frame['history'][65]['low'] = '13.9'
        result = grid_row(frame)
        self.assertEqual((result['status'], result['reason']), ('FAIL', 'LATER_LOW_BREACH'))
        self.assertEqual(result['geometry']['crossing'], 'UNKNOWN')

    def test_future_values_do_not_change_as_of(self):
        frame, _ = fixture()
        before = grid_row(frame)
        frame['history'][66].update(open='200', high='999', low='1', close='200')
        self.assertEqual(before, grid_row(frame))

    def test_market_denominator_and_mixed_batches_are_checked(self):
        frame, _ = fixture()
        row = grid_row(frame)
        manifest = {'dataset_id': 'a'*16, 'as_of': frame['as_of'], 'universe_total': 1,
                    'universe_coverage': 'SITE_CATALOG_UNVERIFIED'}
        result = document(manifest, [row])
        self.assertEqual(result['counts'], {'PASS': 1, 'FAIL': 0, 'UNKNOWN': 0})
        self.assertEqual(result['version'], VERSION)
        with self.assertRaises(ValueError):
            document(manifest, [])
        with self.assertRaises(ValueError):
            document({**manifest, 'universe_total': 2}, [row, row])
        with self.assertRaises(ValueError):
            document(manifest, [{**row, 'as_of': '2026-01-01'}])


if __name__ == '__main__':
    unittest.main()
