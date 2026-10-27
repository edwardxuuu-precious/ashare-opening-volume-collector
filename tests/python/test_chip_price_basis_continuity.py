"""Invented chronological price-basis breaks; no network or market fixtures."""
import copy
import unittest

from backend.chips.calendar import last_ten
from backend.chips.stability import classify


DAYS = last_ten('2026-10-26')
FIRST = '2026-10-19'
BREAK = '2026-10-23'


def inputs(*, break_day=BREAK, failure_day=None):
    row = {'prefilter_status': 'PASS', 'price_position_status': 'PASS',
        'chip_status': 'READY', 'recent_limit_up_dates': [FIRST],
        'limit_evidence': [{'date': day, 'status': 'PASS' if day == FIRST else 'FAIL'} for day in DAYS]}
    history = [{'date': day, 'open': '100', 'high': '100', 'low': '100', 'close': '100',
        'volume_shares': '1000', 'amount_cny': '100000', 'turnover_percent': '50'} for day in DAYS]
    if failure_day:
        next(h for h in history if h['date'] == failure_day).update(
            open='110', high='110', low='110', close='110', amount_cny='110000')
    observed = next(h for h in history if h['date'] == break_day)
    reference = {'rows': {break_day: {**{k: observed[k] for k in ('open', 'high', 'low', 'close')},
        'preclose': '99.86'}}}
    return row, history, reference


def run_case(row, history, reference, **kwargs):
    return classify(row, history, DAYS, security_id='INVENTED', reference_evidence=reference, **kwargs)


class PriceBasisContinuity(unittest.TestCase):
    def test_earlier_confirmed_failure_survives_a_later_price_basis_break(self):
        row, history, reference = inputs(failure_day='2026-10-20')
        before = copy.deepcopy((row, history, reference))
        result, proof = run_case(row, history, reference)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['evaluated_sessions'], 4)
        self.assertEqual(result['required_sessions'], 6)
        self.assertIn('PEAK_REGION_MOVED', result['reasons'])
        self.assertIn('PRICE_BASIS_INCOMPARABLE', result['reasons'])
        self.assertNotIn('CHIP_HISTORY_INCOMPLETE', result['reasons'])
        self.assertEqual(proof['first_incomparable_date'], BREAK)
        self.assertEqual(proof['price_basis_incomparable_sessions'], [BREAK, '2026-10-26'])
        self.assertEqual([d['date'] for d in proof['daily']],
            ['2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22'])
        self.assertEqual(proof['price_basis_discontinuities'][BREAK], {
            'previous_observed_date': '2026-10-22', 'previous_observed_close_cny': '100',
            'reported_reference_close_cny': '99.86', 'source': 'HISTORICAL_REFERENCE_PRECLOSE'})
        self.assertEqual(proof['summary'], result)
        self.assertTrue(proof['unadjusted_inputs_preserved'])
        self.assertEqual((row, history, reference), before)

    def test_earlier_pass_cannot_promote_an_incomparable_tail_to_pass(self):
        result, proof = run_case(*inputs())
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['evaluated_sessions'], 4)
        self.assertEqual(result['reasons'], ['PRICE_BASIS_INCOMPARABLE'])
        self.assertTrue(all(d['status'] == 'PASS' for d in proof['daily']))

    def test_break_on_first_limit_day_evaluates_no_incomparable_sessions(self):
        result, proof = run_case(*inputs(break_day=FIRST))
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['evaluated_sessions'], 0)
        self.assertEqual(result['reasons'], ['PRICE_BASIS_INCOMPARABLE'])
        self.assertEqual(proof['price_basis_incomparable_sessions'], DAYS[4:])

    def test_apparent_failure_after_the_break_is_not_used_as_a_witness(self):
        result, proof = run_case(*inputs(break_day='2026-10-21', failure_day=BREAK))
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['evaluated_sessions'], 2)
        self.assertNotIn('PEAK_REGION_MOVED', result['reasons'])
        self.assertNotIn('ANCHOR_BAND_MASS_DECREASED', result['reasons'])
        self.assertTrue(all(d['date'] < '2026-10-21' for d in proof['daily']))

    def test_an_unrelated_prior_history_gap_remains_separately_visible(self):
        row, history, reference = inputs()
        history = [h for h in history if h['date'] != '2026-10-20']
        result, proof = run_case(row, history, reference)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['evaluated_sessions'], 1)
        self.assertEqual(result['reasons'], ['CHIP_HISTORY_INCOMPLETE', 'PRICE_BASIS_INCOMPARABLE'])
        self.assertEqual(proof['summary'], result)

    def test_conflicting_daily_ohlc_is_not_relabelled_as_a_price_basis_event(self):
        row, history, reference = inputs()
        reference['rows'][BREAK]['open'] = '99'
        result, proof = run_case(row, history, reference)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIn('DATA_PRICE_EVIDENCE_CONFLICT', result['reasons'])
        self.assertNotIn('PRICE_BASIS_INCOMPARABLE', result['reasons'])
        self.assertNotIn('price_basis_discontinuities', proof)

    def test_actual_quote_reference_break_has_the_same_dated_boundary(self):
        row, history, _ = inputs(failure_day='2026-10-20')
        quotes = {BREAK: {'INVENTED': {'date': BREAK, 'close': '100', 'reference_close': '99.86'}}}
        result, proof = run_case(row, history, {}, actual_limits=quotes)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(proof['price_basis_discontinuities'][BREAK]['source'],
            'ACTUAL_LIMIT_QUOTE_REFERENCE_CLOSE')
        self.assertIn('PRICE_BASIS_INCOMPARABLE', result['reasons'])

    def test_undated_global_incomparability_stays_unknown(self):
        result, proof = run_case(*inputs(failure_day='2026-10-20'), incomparable=True)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['reasons'], ['PRICE_BASIS_INCOMPARABLE'])
        self.assertIsNone(proof)


if __name__ == '__main__':
    unittest.main()
