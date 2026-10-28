"""Invented native-cell counterexamples. No network or market inputs."""
import copy
from decimal import Decimal, localcontext
from fractions import Fraction as F
from itertools import product
import unittest

from test_chip_stability import snapshot, BASE, FIRST
from backend.chips.stability import judge_snapshots, relative_distance


class NativeCellBounds(unittest.TestCase):
    def compare(self, base, current):
        return judge_snapshots({BASE: base, FIRST: current}, BASE, [FIRST], first_limit_up_date=FIRST)

    def test_coarse_new_distant_top_is_conclusive_even_with_mass_retained(self):
        base = snapshot(step='2')
        current = snapshot({10: '20', 30: '19', **{i: '6.1' for i in range(40, 50)}}, step='2')
        summary, proof = self.compare(base, current)
        self.assertEqual(summary['status'], 'FAIL')
        self.assertIn('PEAK_REGION_MOVED', summary['reasons'])
        self.assertEqual(summary['min_mass_ratio'], '1')
        self.assertIsNone(summary['max_shift_fraction'])
        day = proof['daily'][0]
        self.assertEqual(day['shift_status'], 'FAIL')
        self.assertFalse(day['native_resolution_sufficient'])
        bound = day['displacement_bounds']
        self.assertGreater(F(bound['lower_fraction']), F('.01'))
        self.assertEqual(bound['method'], 'SAME_NATIVE_GRID_CELL_INTERVALS_V1')
        self.assertEqual(bound['witness']['direction'], 'CURRENT_TO_BASELINE')

    def test_coarse_identical_top_uses_exact_modeled_identity(self):
        base = snapshot(step='2')
        summary, proof = self.compare(base, copy.deepcopy(base))
        self.assertEqual(summary['status'], 'PASS')
        self.assertIsNone(proof['daily'][0]['displacement_bounds'])
        self.assertEqual(summary['max_shift_fraction'], '0')
        self.assertEqual(proof['daily'][0]['shift_status'], 'PASS')
        self.assertTrue(proof['daily'][0]['exact_model_top_region_identity'])

    def test_exact_one_percent_lower_bound_does_not_fail(self):
        base = snapshot(low='179', step='2')
        current = snapshot({10: '20', 12: '19', **{i: '6.1' for i in range(40, 50)}}, low='179', step='2')
        summary, proof = self.compare(base, current)
        self.assertEqual(summary['status'], 'UNKNOWN')
        self.assertEqual(F(proof['daily'][0]['displacement_bounds']['lower_fraction']), F('.01'))

    def test_touching_native_cells_cannot_prove_movement(self):
        base = snapshot(step='2')
        current = snapshot({10: '20', 11: '19', **{i: '6.1' for i in range(40, 50)}}, step='2')
        summary, proof = self.compare(base, current)
        self.assertEqual(summary['status'], 'UNKNOWN')
        self.assertEqual(F(proof['daily'][0]['displacement_bounds']['lower_fraction']), 0)

    def test_tiny_strict_violation_is_decided_before_display_rounding(self):
        with localcontext() as context:
            context.prec = 100
            low = Decimal(179)-Decimal('1e-60')
            base = snapshot(low='179', step='2')
            current = snapshot({10: '20', 12: '19', **{i: '6.1' for i in range(40, 50)}}, low='179', step='2')
            for value in (base, current):
                for i, point in enumerate(value['profile']):
                    point['price_cny'] = str(low+Decimal(2)*i)
            summary, proof = self.compare(base, current)
        self.assertEqual(summary['status'], 'FAIL')
        self.assertEqual(proof['daily'][0]['shift_status'], 'FAIL')
        self.assertGreater(F(proof['daily'][0]['displacement_bounds']['lower_exact_fraction']), F('.01'))

    def test_zero_clipped_top_cannot_gain_a_bound(self):
        base = snapshot({0: '20', **{i: '8' for i in range(20, 30)}}, low='.5', step='2')
        summary, proof = self.compare(base, copy.deepcopy(base))
        self.assertEqual(summary['status'], 'UNKNOWN')
        self.assertIsNone(proof['daily'][0]['displacement_bounds'])

    def test_changed_native_grid_does_not_gain_a_false_precision_proof(self):
        base = snapshot(step='2')
        current = snapshot({10: '20', 30: '19', **{i: '6.1' for i in range(40, 50)}}, low='90.1', step='2')
        summary, proof = self.compare(base, current)
        self.assertEqual(summary['status'], 'UNKNOWN')
        self.assertIsNone(proof['daily'][0]['displacement_bounds'])

    def test_missing_later_day_preserves_proven_movement(self):
        base = snapshot(step='2')
        current = snapshot({10: '20', 30: '19', **{i: '6.1' for i in range(40, 50)}}, step='2')
        summary, _ = judge_snapshots({BASE: base, FIRST: current}, BASE, [FIRST, '2026-10-23'], first_limit_up_date=FIRST)
        self.assertEqual(summary['status'], 'FAIL')
        self.assertIn('CHIP_HISTORY_INCOMPLETE', summary['reasons'])

    def test_interval_bounds_enclose_disconnected_point_metrics(self):
        from backend.chips.stability import relative_distance_bounds
        cases = [([F(100)], [F(100), F(120)]),
                 ([F(100), F(140)], [F(100)]),
                 ([F(2), F(8)], [F(4), F(8)]),
                 ([F(100), F(106)], [F(102), F(110)])]
        for baseline, current in cases:
            proof = relative_distance_bounds([(q-1, q+1) for q in baseline], [(p-1, p+1) for p in current])
            lower, upper = F(proof['lower_fraction']), F(proof['upper_fraction'])
            for offsets in product((F(-1), F(0), F(1)), repeat=len(baseline)+len(current)):
                left = sorted(q+offset for q, offset in zip(baseline, offsets))
                right = sorted(p+offset for p, offset in zip(current, offsets[len(baseline):]))
                actual = relative_distance(left, right)
                self.assertLessEqual(lower, actual)
                self.assertGreaterEqual(upper, actual)

    def test_pair_bounds_always_use_baseline_price_denominators(self):
        from backend.chips.stability import relative_distance_bounds
        right = relative_distance_bounds([(F(99), F(101))], [(F(119), F(121))])
        left = relative_distance_bounds([(F(99), F(101))], [(F(79), F(81))])
        self.assertEqual(right['lower_fraction'], F(18, 101))
        self.assertEqual(right['upper_fraction'], F(22, 99))
        self.assertEqual(left['lower_fraction'], F(18, 99))
        self.assertEqual(left['upper_fraction'], F(22, 101))


if __name__ == '__main__':
    unittest.main()
