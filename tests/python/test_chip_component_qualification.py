"""Independent-condition counterexamples; all inputs are invented."""
import copy
import unittest

from test_chip_stability import snapshot, BASE, FIRST, END
from backend.chips.stability import judge_snapshots


class ComponentQualification(unittest.TestCase):
    def judge(self, peak, other, *, missing=False):
        base = snapshot(step='2')
        current = snapshot({10: peak, **{i: other for i in range(20, 30)}}, step='2', day=FIRST)
        values = {BASE: base, FIRST: current, END: copy.deepcopy(current)}
        if missing:
            del values[END]
        return judge_snapshots(values, BASE, [FIRST, END], first_limit_up_date=FIRST)

    def test_coarse_displacement_cannot_hide_exact_mass_failure(self):
        summary, proof = self.judge('17.99', '8.201')
        self.assertEqual(summary['status'], 'FAIL')
        self.assertEqual(summary['max_shift_fraction'], '0')
        self.assertEqual(summary['shift_evaluated_sessions'], 2)
        self.assertEqual(summary['mass_evaluated_sessions'], 2)
        self.assertNotIn('NATIVE_RESOLUTION_INSUFFICIENT', summary['reasons'])
        self.assertIn('ANCHOR_BAND_MASS_DECREASED', summary['reasons'])
        for day in proof['daily']:
            self.assertEqual(day['shift_status'], 'PASS')
            self.assertEqual(day['mass_status'], 'FAIL')
            self.assertEqual(day['top_centers'], ['110'])
            self.assertFalse(day['native_resolution_sufficient'])

    def test_exact_ninety_percent_mass_with_identical_region_passes(self):
        summary, proof = self.judge('18', '8.2')
        self.assertEqual(summary['status'], 'PASS')
        self.assertEqual(summary['min_mass_ratio'], '0.9')
        self.assertNotIn('ANCHOR_BAND_MASS_DECREASED', summary['reasons'])
        self.assertEqual(proof['daily'][0]['status'], 'PASS')

    def test_unchanged_coarse_model_region_generates_exact_pass(self):
        summary, _ = self.judge('20', '8')
        self.assertEqual(summary['status'], 'PASS')
        self.assertEqual(summary['min_mass_ratio'], '1')

    def test_missing_later_day_does_not_erase_earlier_mass_failure(self):
        summary, _ = self.judge('17.99', '8.201', missing=True)
        self.assertEqual(summary['status'], 'FAIL')
        self.assertEqual(summary['evaluated_sessions'], 1)
        self.assertIn('CHIP_HISTORY_INCOMPLETE', summary['reasons'])

    def test_fine_grid_still_requires_both_conditions(self):
        base = snapshot()
        current = snapshot({12: '20', **{i: '8' for i in range(20, 30)}}, day=FIRST)
        summary, proof = judge_snapshots({BASE: base, FIRST: current}, BASE, [FIRST], first_limit_up_date=FIRST)
        self.assertEqual(summary['status'], 'FAIL')
        self.assertEqual(summary['shift_evaluated_sessions'], 1)
        self.assertEqual(proof['daily'][0]['shift_status'], 'FAIL')
        self.assertTrue(proof['daily'][0]['native_resolution_sufficient'])


if __name__ == '__main__':
    unittest.main()
