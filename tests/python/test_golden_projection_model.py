from copy import deepcopy
from decimal import Decimal
import unittest

from backend.chips.calendar import sessions
from backend.golden_projection import model
from backend.golden_projection.model import (POLICY_SHA256, RULE_ID, classify_frame,
                                              classify_position, evaluate_structure, select_structure)


SOURCE = 'a' * 16
DATASET = 'b' * 16
CODE = '123456'
DATES = sessions('2026-01-01', '2026-10-30')[-60:]


def candles(knots=None):
    knots = knots or [(0, 100), (20, 90), (25, 80), (30, 130), (35, 80),
                      (40, 110), (45, 70), (50, 95), (55, 75), (59, 78)]
    result = []
    for i, day in enumerate(DATES):
        for (left, a), (right, b) in zip(knots, knots[1:]):
            if left <= i <= right:
                value = Decimal(str(a)) + (Decimal(str(b))-Decimal(str(a))) * (i-left) / (right-left)
                break
        result.append({'date': day, 'open': str(value), 'close': str(value),
                       'high': str(value + Decimal('.1')), 'low': str(value - Decimal('.1')),
                       'volume_shares': '100000'})
    return result


def frame(rows):
    return {'schema': 'stock.chips.market.stock.v1', 'security_id': CODE,
            'dataset_id': SOURCE, 'as_of': rows[-1]['date'], 'history': deepcopy(rows)}


def detail(rows, previous=None, **overrides):
    kwargs = {'code': CODE, 'name': '虚构案例', 'as_of': rows[-1]['date'], 'dataset_id': DATASET,
              'source_dataset_id': SOURCE, 'source_sha256': 'c' * 64, 'previous': previous,
              'recorded_at': rows[-1]['date'] + 'T08:01:00+00:00',
              'effective_from': '2026-11-02' if rows[-1]['date'] == '2026-10-30' else '2026-11-03'}
    kwargs.update(overrides)
    return classify_frame(frame(rows), **kwargs)


def simple_structure(h='100', m='90', low='80'):
    return {'direction': 'DOWN', 'levels': {'H': h, 'M': m, 'L': low},
            'evidence_through': '2026-10-29', 'life_state': 'OPEN', 'guides_end_on': None}


def bar(close, high=None, low=None):
    return {'date': '2026-10-30', 'close': str(close), 'high': str(high or close), 'low': str(low or close)}


class PositionTests(unittest.TestCase):
    def test_two_percent_includes_boundary_but_not_next_cent(self):
        self.assertEqual(classify_position(bar('81.60'), simple_structure())['category'], 'NEAR_L')
        self.assertEqual(classify_position(bar('81.61'), simple_structure())['category'], 'FAR')
        self.assertEqual(classify_position(bar('98.00'), simple_structure())['category'], 'NEAR_H')
        self.assertEqual(classify_position(bar('97.99'), simple_structure())['category'], 'FAR')

    def test_close_outside_takes_priority_and_equal_is_not_breakout(self):
        self.assertEqual(classify_position(bar('100.01'), simple_structure())['category'], 'ABOVE')
        self.assertEqual(classify_position(bar('79.99'), simple_structure())['category'], 'BELOW')
        self.assertEqual(classify_position(bar('100'), simple_structure())['category'], 'NEAR_H')
        self.assertEqual(classify_position(bar('80'), simple_structure())['category'], 'NEAR_L')

    def test_wicks_are_events_not_close_breakouts(self):
        value = classify_position(bar('81', '101', '79'), simple_structure())
        self.assertEqual(value['category'], 'NEAR_L')
        self.assertIn('UPPER_REJECTED', value['intraday_tags'])
        self.assertIn('LOWER_RECOVERED', value['intraday_tags'])
        self.assertEqual(set(value['intraday_tags']) & {'TOUCH_H', 'TOUCH_M', 'TOUCH_L'},
                         {'TOUCH_H', 'TOUCH_M', 'TOUCH_L'})

    def test_exact_proportional_tie_is_not_arbitrarily_ranked(self):
        value = classify_position(bar('99.99'), simple_structure(h='101', m='99', low='97'))
        self.assertEqual(value['category'], 'AMBIGUOUS')
        self.assertIn('NEAR_H', value['near_tags'])
        self.assertIn('NEAR_M', value['near_tags'])

    def test_new_crossing_requires_known_same_structure(self):
        self.assertEqual(classify_position(bar('101'), simple_structure(), bar('99'))['new_crossing'], 'UNKNOWN')
        self.assertEqual(classify_position(bar('101'), simple_structure(), bar('99'), same_structure=True)['new_crossing'], 'UP')
        self.assertEqual(classify_position(bar('102'), simple_structure(), bar('101'), same_structure=True)['new_crossing'], 'NONE')


class SelectionTests(unittest.TestCase):
    def test_near_origin_is_reopened_to_preconstructed_parent(self):
        selected = select_structure(candles())
        self.assertEqual(selected['state'], 'DRAW')
        structure = selected['structure']
        self.assertEqual(structure['direction'], 'DOWN')
        self.assertEqual(Decimal(structure['origin']['price']), Decimal('130.1'))
        self.assertEqual(Decimal(structure['levels']['L']), Decimal('60.1'))
        self.assertEqual(structure['life_state'], 'OPEN')
        self.assertTrue(any(item['reason_code'] == 'TARGET_BEFORE_EVIDENCE'
                            and Decimal(item['origin_price']) == Decimal('110.1')
                            for item in selected['rejected_candidates']))

    def test_up_projection_is_symmetric_without_owner_reference_prices(self):
        reflected = []
        for row in candles():
            reflected.append({**row, 'open': str(200-Decimal(row['open'])),
                              'close': str(200-Decimal(row['close'])),
                              'high': str(200-Decimal(row['low'])), 'low': str(200-Decimal(row['high']))})
        selected = select_structure(reflected)
        self.assertEqual(selected['state'], 'DRAW')
        self.assertEqual(selected['structure']['direction'], 'UP')
        self.assertEqual(Decimal(selected['structure']['levels']['H']), Decimal('139.9'))

    def test_exact_role_can_use_left_source_with_current_break_and_retest(self):
        rows = candles([(0, 100), (20, 85), (24, 80.1), (28, 120), (34, 90),
                        (39, 110), (44, 85), (48, 79), (51, 79.9), (55, 78), (59, 79)])
        selected = select_structure(rows)
        self.assertEqual(selected['state'], 'DRAW')
        structure = selected['structure']
        self.assertEqual(structure['route'], 'ROLE')
        self.assertLess(structure['axis']['date'], structure['origin']['date'])
        self.assertEqual(Decimal(structure['levels']['M']), Decimal('80'))
        self.assertTrue(any(proof['role'] == '本波段收盘失守／收复' for proof in structure['evidence']))

    def test_constant_prices_and_short_background_do_not_force_a_turn(self):
        flat = [{**row, 'open': '90', 'high': '91', 'low': '89', 'close': '90'} for row in candles()]
        self.assertEqual(select_structure(flat)['state'], 'NO_DRAW')
        self.assertEqual(select_structure(candles()[-59:])['state'], 'NO_DRAW')

    def test_right_reversal_does_not_wait_for_another_confirmed_bottom(self):
        rows = candles([(0, 100), (20, 90), (25, 80), (30, 130), (35, 80),
                        (40, 110), (50, 70), (55, 95), (57, 80), (59, 78)])
        selected = select_structure(rows)
        self.assertEqual(selected['state'], 'DRAW')
        self.assertEqual(selected['structure']['axis']['date'], DATES[55])
        self.assertEqual(selected['structure']['life_state'], 'OPEN')
        self.assertLess(selected['structure']['evidence_through'], DATES[-1])

    def test_internal_higher_low_does_not_erase_enclosing_down_wave(self):
        rows = candles([(0, 100), (20, 70), (25, 170), (30, 90),
                        (40, 120), (45, 95), (50, 125), (55, 112), (59, 105)])
        selected = select_structure(rows)
        self.assertEqual(selected['state'], 'DRAW')
        self.assertEqual(selected['structure']['direction'], 'DOWN')
        self.assertEqual(Decimal(selected['structure']['origin']['price']), Decimal('170.1'))
        self.assertEqual(Decimal(selected['structure']['axis']['price']), Decimal('125.1'))

    def test_repeated_pressure_is_a_role_without_a_prior_support_conversion(self):
        rows = candles()
        for index, op, high, close in ((51, '89.7', '90.0', '89.8'), (52, '89.8', '90.1', '89.8'),
                                       (53, '89.8', '90.3', '89.9')):
            rows[index].update(open=op, high=high, low='89.5', close=close)
        for index in range(54, 60):
            rows[index].update(open='86.8', high='87', low='86.5', close='86.8')
        selected = select_structure(rows)
        self.assertEqual(selected['state'], 'DRAW')
        self.assertIn(selected['structure']['route'], ('ROLE', 'BOTH'))
        self.assertTrue(any(proof['role'] == '同段反复上探／下探' for proof in selected['structure']['evidence']))
        self.assertEqual(Decimal(selected['structure']['levels']['M']), Decimal('90.3'))

    def test_evidence_uses_exact_saved_ohlc_and_is_not_future_dated(self):
        rows = candles()
        structure = select_structure(rows)['structure']
        by_date = {row['date']: row for row in rows}
        for proof in structure['evidence']:
            self.assertLessEqual(proof['date'], structure['evidence_through'])
            self.assertEqual(Decimal(proof['value']), Decimal(by_date[proof['date']][proof['field']]))

    def test_later_target_reach_does_not_enlarge_parent_to_avoid_completion(self):
        rows = candles()
        original = select_structure(rows)['structure']
        touched = {**rows[-1], 'date': '2026-11-02', 'open': '78', 'high': '79', 'low': '59', 'close': '60'}
        result = evaluate_structure(rows + [touched], original)
        self.assertEqual(result['id'], original['id'])
        self.assertEqual(result['life_state'], 'REACHED')
        self.assertEqual(result['guides_end_on'], '2026-11-02')


class FrameTests(unittest.TestCase):
    def test_newly_effective_different_structure_does_not_claim_a_new_crossing(self):
        rows = candles([(0, 100), (20, 95), (25, 90), (30, 130), (35, 100),
                        (40, 110), (45, 100), (50, 105), (55, 101), (59, 100.5)])
        structures = {}
        for episode in model._episodes(rows, model._pivots(rows)):
            value, _ = model._assess_episode(rows, episode)
            if value is not None and value['life_state'] == 'OPEN':
                structures[Decimal(value['levels']['M'])] = value
        old, new = structures[Decimal('110.1')], structures[Decimal('105.1')]
        old.update(recorded_at='2026-10-29T08:01:00+00:00', effective_from='2026-10-30')
        new.update(recorded_at='2026-10-30T08:01:00+00:00', effective_from='2026-11-02')
        previous = detail(rows)
        previous.update(structure=old, candidate_next_session=new)
        previous['row'].update(model.classify_position(rows[-1], old), structure_id=old['id'],
                               drawing_state='CURRENT', basis='PREVIOUS_LOCK',
                               H=old['levels']['H'], M=old['levels']['M'], L=old['levels']['L'], direction=old['direction'])
        added = {'date': '2026-11-02', 'open': '81', 'high': '81', 'low': '78', 'close': '79', 'volume_shares': '100000'}
        value = detail(rows + [added], previous)
        self.assertNotEqual(previous['row']['structure_id'], value['row']['structure_id'])
        self.assertEqual(value['row']['structure_id'], new['id'])
        self.assertEqual(value['row']['category'], 'BELOW')
        self.assertEqual(value['row']['drawing_state'], 'CURRENT')
        self.assertEqual(value['row']['new_crossing'], 'UNKNOWN')

    def test_first_publication_is_reference_not_backdated_crossing(self):
        value = detail(candles())
        self.assertEqual(value['rule_id'], RULE_ID)
        self.assertEqual(value['policy_sha256'], POLICY_SHA256)
        self.assertEqual(value['row']['drawing_state'], 'NEW_REFERENCE')
        self.assertEqual(value['row']['new_crossing'], 'UNKNOWN')
        self.assertEqual(value['structure']['recorded_at'], '2026-10-30T08:01:00+00:00')
        self.assertIsNone(value['historical_known_at'])

    def test_next_day_uses_old_lock_and_preserves_today_terminal_event(self):
        rows = candles()
        previous = detail(rows)
        added = {'date': '2026-11-02', 'open': '78', 'high': '79', 'low': '59', 'close': '59.5', 'volume_shares': '100000'}
        value = detail(rows + [added], previous)
        self.assertEqual(value['row']['basis'], 'PREVIOUS_LOCK')
        self.assertEqual(value['row']['drawing_state'], 'CURRENT')
        self.assertEqual(value['row']['category'], 'BELOW')
        self.assertEqual(value['row']['new_crossing'], 'DOWN')
        self.assertEqual(value['structure']['id'], previous['structure']['id'])
        self.assertEqual(value['structure']['life_state'], 'REACHED')
        self.assertEqual(value['structure']['guides_end_on'], '2026-11-02')
        self.assertIsNone(value['candidate_next_session'])
        added2 = {**added, 'date': '2026-11-03', 'open': '59.5', 'high': '60', 'low': '58', 'close': '59'}
        later = detail(rows + [added, added2], value)
        self.assertEqual(later['row']['drawing_state'], 'HISTORY')
        self.assertEqual(later['row']['category'], 'HISTORY')

    def test_future_rows_do_not_change_current_result(self):
        rows = candles()
        original = detail(rows)
        source = frame(rows)
        source['history'].append({'date': '2026-11-02', 'open': '999', 'high': '1000', 'low': '1', 'close': '900', 'volume_shares': '1'})
        changed = classify_frame(source, code=CODE, name='虚构案例', as_of=rows[-1]['date'], dataset_id=DATASET,
                                 source_dataset_id=SOURCE, source_sha256='c'*64,
                                 recorded_at='2026-10-30T08:01:00+00:00', effective_from='2026-11-02')
        self.assertEqual(changed, original)

    def test_missing_or_bad_current_input_remains_unknown(self):
        rows = candles()
        source = frame(rows)
        source['history'].pop()
        for invalid in (source, {**frame(rows), 'security_id': '654321'}):
            value = classify_frame(invalid, code=CODE, name='虚构案例', as_of=rows[-1]['date'],
                                   dataset_id=DATASET, source_dataset_id=SOURCE, source_sha256='c'*64)
            self.assertEqual(value['row']['source_status'], 'UNKNOWN')
            self.assertEqual(value['row']['category'], 'UNKNOWN')
            self.assertIsNone(value['structure'])
        bad = deepcopy(rows)
        bad[-1]['low'] = '200'
        self.assertEqual(detail(bad)['row']['category'], 'UNKNOWN')

    def test_rule_mismatch_and_publication_after_open_cannot_supply_old_lock(self):
        rows = candles()
        previous = detail(rows)
        added = {'date': '2026-11-02', 'open': '78', 'high': '79', 'low': '76', 'close': '77', 'volume_shares': '100000'}
        bad_rule = deepcopy(previous)
        bad_rule['policy_sha256'] = '0'*64
        self.assertEqual(detail(rows + [added], bad_rule)['row']['new_crossing'], 'UNKNOWN')
        late = deepcopy(previous)
        late['candidate_next_session']['recorded_at'] = '2026-11-02T02:00:00+00:00'
        late['structure']['recorded_at'] = '2026-11-02T02:00:00+00:00'
        value = detail(rows + [added], late)
        self.assertEqual(value['row']['drawing_state'], 'NEW_REFERENCE')
        self.assertEqual(value['row']['new_crossing'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
