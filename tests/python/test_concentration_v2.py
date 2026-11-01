"""V2 endpoint semantics using invented inputs only."""
import unittest
from copy import deepcopy
from unittest.mock import patch
from test_concentration import fixture, manifest
from backend.chips import concentration_v2 as v2
from backend.chips.concentration import concentration_row, trailing_sessions


class Rules(unittest.TestCase):
    def test_equality_and_precision_and_known_failure_priority(self):
        self.assertEqual(set(v2.classify([0] * 22, {}).values()), {'PASS'})
        values = [1 - i * .000000001 for i in range(22)]
        self.assertEqual(set(v2.classify(values, {}).values()), {'PASS'})
        values[-1] = values[-2] + .0000000001
        values[0] = None
        self.assertEqual(set(v2.classify(values, {}).values()), {'FAIL'})
        for bad in [float('inf'), float('nan'), -1, 101]:
            with self.assertRaises(ValueError): v2.classify([bad] * 22, {})

    def test_no_trade_direction_and_long_threshold(self):
        values = [None] + [20] * 21
        result = v2.classify(values, {'0': 'NO_TRADE'})
        self.assertEqual(result['20'], 'PASS'); self.assertEqual(result['LONG'], 'UNKNOWN')
        self.assertEqual(v2.classify([20, None] + [20] * 20, {'1': 'NO_TRADE'})['LONG'], 'FAIL')
        self.assertEqual(v2.classify([20] * 21, {})['LONG'], 'UNKNOWN')

    def test_closed_and_calendar_lower_bound_curves(self):
        days = v2.supported_sessions('2026-10-30')
        row = {'security_id': '999999', 'name': 'INVENTED', 'close_cny': '10', 'values': [20] * 21,
               'point_reasons': {}, 'short_history_days': 0}
        values = [19] + [20] * 67
        result = v2.finish_row(row, days[-68:], values, {})
        self.assertEqual(result['streak_days'], 66)
        self.assertEqual(len(result['streak_values']), 67)
        self.assertEqual(result['streak_boundary']['kind'], 'INCREASE')
        v2.validate_row(result, days[-21:], days)
        result = v2.finish_row(row, days, [20] * len(days), {})
        self.assertEqual(result['streak_days'], 197)
        self.assertEqual(result['streak_boundary']['kind'], 'CALENDAR_LIMIT')
        missing = deepcopy(row); missing['values'][-1] = None; missing['point_reasons']['20'] = 'NO_TRADE'
        result = v2.recent_row(missing, days[-21:])
        self.assertIsNone(result['streak_start_session'])

    def test_independent_endpoints_short_history_and_archive_hash(self):
        frame, raw = fixture(); days = trailing_sessions(frame['as_of'])
        source = v2.EndpointSource(frame)
        row = concentration_row(frame, sessions=days)
        for i in (0, 5, 20): self.assertEqual(source.point(days[i])[0], row['values'][i])
        frame['history'] = frame['history'][-10:]
        source = v2.EndpointSource(frame)
        self.assertTrue(source.requires_archive(days[0]))
        self.assertEqual(source.point(days[-1])[1], 'ARCHIVED_WARMUP_UNAVAILABLE')
        with self.assertRaisesRegex(ValueError, 'ARCHIVE_HASH'): v2.EndpointSource(frame, raw + b' ')
        self.assertEqual(v2.EndpointSource(frame, raw).point(days[0])[0], row['values'][0])

    def test_segment_bound_and_resume(self):
        frame, _ = fixture(); axis = v2.supported_sessions(frame['as_of'])
        row = {'security_id': frame['security_id'], 'name': 'INVENTED', 'close_cny': '10', 'values': [20] * 21,
               'point_reasons': {}, 'short_history_days': 0}
        with patch.object(v2.EndpointSource, 'point', return_value=(20, None)):
            first = v2.trace_segment(frame, row, axis, None)
            second = v2.trace_segment(frame, row, axis, first)
        self.assertEqual(len(first['values']), 53); self.assertFalse(first['complete'])
        self.assertEqual(len(second['values']), 85)
        self.assertEqual(second['values'][-53:], first['values'])


if __name__ == '__main__': unittest.main()
