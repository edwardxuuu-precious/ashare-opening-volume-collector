"""Modeled-region identity is exact; it does not locate hidden holders."""
import copy
import unittest

from test_chip_stability import snapshot, BASE, FIRST, END
from backend.chips.stability import judge_snapshots


class ModelRegionIdentity(unittest.TestCase):
    def judge(self, base, current, required=None):
        return judge_snapshots({BASE: base, FIRST: current}, BASE, required or [FIRST],
            first_limit_up_date=FIRST)

    def test_identical_coarse_regions_pass_when_the_rest_of_profile_changes(self):
        base = snapshot(step='2')
        current = snapshot({10: '19', **{i: '8.1' for i in range(20, 30)}}, step='2')
        summary, proof = self.judge(base, current)
        self.assertEqual(summary['status'], 'PASS')
        self.assertEqual(summary['max_shift_fraction'], '0')
        self.assertEqual(summary['min_mass_ratio'], '0.95')
        self.assertEqual(summary['shift_evaluated_sessions'], 1)
        self.assertEqual(summary['reasons'], [])
        day = proof['daily'][0]
        self.assertFalse(day['native_resolution_sufficient'])
        self.assertTrue(day['exact_model_top_region_identity'])
        self.assertEqual(day['shift_basis'], 'EXACT_MODEL_TOP_REGION_IDENTITY')
        self.assertEqual(day['model_top_region_intervals_cny'], [['109', '111']])
        self.assertNotEqual(base['profile'], current['profile'])

    def test_exact_identity_cannot_override_anchor_mass_failure(self):
        base = snapshot(step='2')
        current = snapshot({10: '17.99', **{i: '8.201' for i in range(20, 30)}}, step='2')
        summary, proof = self.judge(base, current)
        self.assertEqual(summary['status'], 'FAIL')
        self.assertEqual(summary['max_shift_fraction'], '0')
        self.assertEqual(proof['daily'][0]['shift_status'], 'PASS')
        self.assertEqual(proof['daily'][0]['mass_status'], 'FAIL')
        self.assertEqual(summary['reasons'], ['ANCHOR_BAND_MASS_DECREASED'])

    def test_complete_disconnected_top_sets_are_compared(self):
        base = snapshot({10: '20', 50: '20', **{i: '6' for i in range(20, 30)}}, step='2')
        passed, proof = self.judge(base, copy.deepcopy(base))
        self.assertEqual(passed['status'], 'PASS')
        self.assertEqual(len(proof['daily'][0]['model_top_region_intervals_cny']), 2)
        changed = snapshot({10: '20', 50: '20', 75: '19', **{i: '4.1' for i in range(20, 30)}}, step='2')
        failed, proof = self.judge(base, changed)
        self.assertEqual(failed['status'], 'FAIL')
        self.assertFalse(proof['daily'][0]['exact_model_top_region_identity'])
        self.assertIn('PEAK_REGION_MOVED', failed['reasons'])

    def test_same_top_center_with_different_cell_width_is_not_identity(self):
        base = snapshot(step='2')
        current = snapshot(low='80', step='3')
        summary, proof = self.judge(base, current)
        self.assertEqual(summary['status'], 'UNKNOWN')
        self.assertFalse(proof['daily'][0]['exact_model_top_region_identity'])
        self.assertIsNone(summary['max_shift_fraction'])

    def test_identity_on_one_day_does_not_fill_a_missing_later_day(self):
        base = snapshot(step='2')
        summary, _ = self.judge(base, copy.deepcopy(base), [FIRST, END])
        self.assertEqual(summary['status'], 'UNKNOWN')
        self.assertEqual(summary['max_shift_fraction'], '0')
        self.assertIn('CHIP_HISTORY_INCOMPLETE', summary['reasons'])


if __name__ == '__main__':
    unittest.main()
