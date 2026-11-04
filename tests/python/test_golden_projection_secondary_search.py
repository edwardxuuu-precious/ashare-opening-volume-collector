from copy import deepcopy
from decimal import Decimal
import unittest

from backend.golden_projection import model
from test_golden_projection_model import DATES, candles


def source(knots):
    return candles(knots)


def anchor(rows, index=35):
    return {'direction': 'UP', 'date': rows[index]['date'], 'field': 'low', 'price': rows[index]['low']}


def right_ready():
    return source([(0, 30), (5, 35), (10, 25), (15, 32), (20, 22), (25, 34),
                   (30, 26), (35, 10), (40, 24), (45, 20), (50, 25), (55, 26.3), (59, 26)])


def right_too_early():
    return source([(0, 30), (5, 35), (10, 25), (15, 32), (20, 22), (25, 34),
                   (30, 26), (35, 10), (40, 24), (45, 12), (50, 23), (59, 23)])


def ancestor_pairing():
    rows = source([(0, 40), (5, 45), (10, 25), (15, 32), (20, 27), (25, 34),
                   (30, 31.5), (35, 31), (40, 33), (45, 31.5), (50, 33),
                   (55, 33.5), (59, 32.8)])
    for index in range(47, 56):
        close = Decimal('32.5')+Decimal(index-47)/8
        rows[index].update(open=str(close), close=str(close),
                           high=str(close+Decimal('.1')), low=str(close-Decimal('.1')))
    for index, open_, high, low, close in [
            (56, '33.3', '33.4', '33.2', '33.3'),
            (57, '32.8', '32.9', '32.7', '32.8'),
            (58, '31.8', '32', '30', '31.8'),
            (59, '31.8', '32', '31.7', '31.8')]:
        rows[index].update(open=open_, high=high, low=low, close=close)
    return rows


def next_bar(rows, *, high, low, close):
    from backend.chips.calendar import sessions
    dates = sessions('2026-01-01', '2026-12-31')
    day = dates[dates.index(rows[-1]['date'])+1]
    return {'date': day, 'open': str(close), 'high': str(high), 'low': str(low),
            'close': str(close), 'volume_shares': '100000'}


def detail(rows, origin, previous=None):
    return model.classify_frame(
        {'schema': 'stock.chips.market.stock.v1', 'security_id': '123456',
         'dataset_id': 'a'*16, 'as_of': rows[-1]['date'], 'history': deepcopy(rows)},
        code='123456', name='虚构次低搜索', as_of=rows[-1]['date'], dataset_id='b'*16,
        source_dataset_id='a'*16, source_sha256='c'*64, previous=previous, origin=origin,
        recorded_at=rows[-1]['date']+'T08:01:00+00:00', effective_from='2026-11-02')


class SecondarySearchTests(unittest.TestCase):
    def test_qualified_right_low_wins_before_left(self):
        rows = right_ready()
        value = model.select_structure(rows, origin=anchor(rows))
        self.assertEqual(value['state'], 'DRAW')
        self.assertEqual(value['structure']['axis']['field'], 'low')
        self.assertEqual(value['structure']['axis']['date'], rows[45]['date'])
        self.assertEqual([step['side'] for step in value['search_trace']], ['RIGHT'])

    def test_early_target_rejection_allows_left_intrinsic_main_low(self):
        rows = right_too_early()
        value = model.select_structure(rows, origin=anchor(rows))
        self.assertEqual([step['side'] for step in value['search_trace']], ['RIGHT', 'LEFT'])
        self.assertTrue(any(attempt['result']=='TARGET_BEFORE_EVIDENCE'
                            for attempt in value['search_trace'][0]['candidates']))
        self.assertEqual(value['search_trace'][1]['trigger'], 'RIGHT_NO_USABLE_COMBINATION')
        self.assertEqual(value['state'], 'DRAW')
        self.assertEqual(value['structure']['axis']['field'], 'low')
        self.assertEqual(value['structure']['axis']['date'], rows[20]['date'])
        self.assertEqual(Decimal(value['structure']['levels']['H']), Decimal('33.9'))

    def test_later_reach_keeps_right_geometry_and_does_not_search_left(self):
        rows = right_ready()
        origin = anchor(rows)
        before = model.select_structure(rows, origin=origin)['structure']
        rows.append(next_bar(rows, high='30.5', low='29.5', close='30'))
        value = model.select_structure(rows, origin=origin)
        self.assertEqual(value['structure']['id'], before['id'])
        self.assertEqual(value['structure']['life_state'], 'REACHED')
        self.assertEqual([step['side'] for step in value['search_trace']], ['RIGHT'])

    def test_later_invalid_right_does_not_seek_a_left_rescue(self):
        rows = right_ready()
        origin = anchor(rows)
        before = model.select_structure(rows, origin=origin)['structure']
        rows.append(next_bar(rows, high='22', low='18.5', close='19'))
        value = model.select_structure(rows, origin=origin)
        self.assertEqual(value['structure']['id'], before['id'])
        self.assertEqual(value['structure']['life_state'], 'INVALID')
        self.assertEqual([step['side'] for step in value['search_trace']], ['RIGHT'])

    def test_competing_formed_right_lows_do_not_fall_back_left(self):
        rows = source([(0, 30), (5, 35), (10, 25), (15, 32), (20, 22), (25, 34),
                       (30, 26), (35, 10), (40, 24), (45, 20), (50, 26), (55, 24), (59, 25)])
        value = model.select_structure(rows, origin=anchor(rows))
        self.assertEqual(value['state'], 'AMBIGUOUS')
        self.assertIsNone(value['structure'])
        self.assertEqual([step['side'] for step in value['search_trace']], ['RIGHT'])

    def test_platform_is_reported_pending_not_promoted_to_confirmed_low(self):
        rows = source([(0, 40), (35, 10), (40, 12), (45, 10.1), (50, 12), (59, 12)])
        rows[25].update(open='19', high='19.3', low='17', close='19.2')
        rows[26].update(open='18.5', high='18.8', low='16.8', close='18.7')
        value = model.select_structure(rows, origin=anchor(rows))
        attempts = [a for step in value['search_trace'] if step['side']=='LEFT' for a in step['candidates']]
        self.assertTrue(any(a['basis']=='PLATFORM' and a['qualification']=='PENDING' for a in attempts))
        self.assertFalse(any(a['point']['date']==rows[26]['date'] and a['qualification']=='CONFIRMED' for a in attempts))

    def test_explicit_origin_checks_the_actual_source_field_and_price(self):
        rows = right_ready()
        invalid = anchor(rows)
        invalid['field'] = 'high'
        with self.assertRaises(ValueError):
            model.select_structure(rows, origin=invalid)
        invalid = {**anchor(rows), 'price': '9.8'}
        with self.assertRaises(ValueError):
            model.select_structure(rows, origin=invalid)

    def test_audit_points_are_actual_and_evidence_not_future(self):
        rows = right_too_early()
        value = model.select_structure(rows, origin=anchor(rows))
        by_date = {row['date']: row for row in rows}
        for step in value['search_trace']:
            for attempt in step['candidates']:
                point = attempt['point']
                self.assertEqual(Decimal(point['price']), Decimal(by_date[point['date']][point['field']]))
                self.assertEqual(point['field'], 'low')
                if attempt['evidence_through']:
                    self.assertLessEqual(attempt['evidence_through'], rows[-1]['date'])

    def test_frame_passes_search_trace_and_does_not_inherit_other_origin_context(self):
        rows = right_ready()
        origin = anchor(rows)
        before = detail(rows, origin)
        later = rows+[next_bar(rows, high='27', low='25', close='26')]
        prior = deepcopy(before)
        prior['origin_context'] = None
        value = detail(later, origin, prior)
        self.assertEqual(value['origin_context'], origin)
        self.assertTrue(value['search_trace'])
        self.assertEqual(value['row']['basis'], 'CURRENT_REFERENCE')
        self.assertEqual(value['row']['new_crossing'], 'UNKNOWN')
        self.assertIsNone(value['historical_known_at'])

    def test_left_intrinsic_point_without_current_pair_evidence_is_not_open(self):
        rows = right_too_early()
        rows[40].update(open='20', high='20.1', low='19.9', close='20')
        for row in rows[46:]:
            row.update(open='20', high='20.1', low='19.9', close='20')
        value = model.select_structure(rows, origin=anchor(rows))
        left = next(step for step in value['search_trace'] if step['side']=='LEFT')
        attempts = [a for a in left['candidates'] if a['point']['date']==rows[20]['date']]
        self.assertTrue(attempts)
        self.assertEqual(attempts[0]['qualification'], 'CONFIRMED')
        self.assertEqual(attempts[0]['basis'], 'TURN')
        self.assertEqual(attempts[0]['result'], 'PAIR_EVIDENCE_PENDING')
        self.assertIn('尚无收盘', attempts[0]['reason'])
        self.assertIsNotNone(attempts[0]['evidence_through'])
        if value['structure'] is not None:
            self.assertNotEqual(value['structure']['axis']['date'], rows[20]['date'])

    def test_left_pairing_waits_for_current_parent_context_before_checking_target(self):
        rows = ancestor_pairing()
        value = model.select_structure(rows)
        left = next(step for step in value['search_trace']
                    if step['side']=='LEFT' and step['origin']['date']==rows[25]['date'])
        attempt = next(attempt for attempt in left['candidates']
                       if attempt['point']['date']==rows[15]['date'])
        self.assertEqual(attempt['result'], 'TARGET_BEFORE_EVIDENCE')
        self.assertEqual(attempt['evidence_through'], rows[58]['date'])
        if value['structure'] is not None:
            self.assertNotEqual((value['structure']['origin']['date'], value['structure']['axis']['date']),
                                (rows[25]['date'], rows[15]['date']))

    def test_left_pairing_remains_pending_without_a_close_after_parent_context_is_known(self):
        rows = ancestor_pairing()
        for row in rows[58:]:
            row.update(open='32.8', high='32.9', close='32.8')
        value = model.select_structure(rows)
        left = next(step for step in value['search_trace']
                    if step['side']=='LEFT' and step['origin']['date']==rows[25]['date'])
        attempt = next(attempt for attempt in left['candidates']
                       if attempt['point']['date']==rows[15]['date'])
        self.assertEqual(attempt['qualification'], 'CONFIRMED')
        self.assertEqual(attempt['result'], 'PAIR_EVIDENCE_PENDING')
        if value['structure'] is not None:
            self.assertNotEqual((value['structure']['origin']['date'], value['structure']['axis']['date']),
                                (rows[25]['date'], rows[15]['date']))

    def test_exterior_intervening_low_prevents_rescuing_an_older_qualified_axis(self):
        rows = right_too_early()
        rows[40]['high'] = '35'
        for row in rows[46:]:
            row.update(open='27', high='27.1', low='26.9', close='27')
        value = model.select_structure(rows, origin=anchor(rows))
        self.assertIsNone(value['structure'])
        left = next(step for step in value['search_trace'] if step['side']=='LEFT')
        old = next(a for a in left['candidates'] if a['point']['date']==rows[10]['date'])
        self.assertEqual(old['qualification'], 'CONFIRMED')
        self.assertEqual(old['result'], 'PAIR_EVIDENCE_PENDING')
        self.assertIn(rows[20]['date'], old['reason'])
        self.assertTrue(any(a['point']['date']==rows[20]['date'] and a['result']=='TARGET_BEFORE_EVIDENCE'
                            for a in left['candidates']))

    def test_internal_higher_low_keeps_the_preconstructed_enclosing_left_family(self):
        rows = source([(0, 30), (5, 35), (10, 22), (15, 32), (20, 25), (25, 34),
                       (30, 26), (35, 10), (40, 24), (45, 12), (50, 23), (59, 23)])
        value = model.select_structure(rows, origin=anchor(rows))
        left = next(step for step in value['search_trace'] if step['side']=='LEFT')
        enclosing = next(a for a in left['candidates'] if a['point']['date']==rows[10]['date'])
        self.assertEqual(enclosing['qualification'], 'CONFIRMED')
        self.assertNotEqual(enclosing['result'], 'PAIR_EVIDENCE_PENDING')
        self.assertEqual(value['structure']['axis']['date'], rows[10]['date'])

    def test_down_search_uses_actual_secondary_high_with_the_same_left_ownership(self):
        rows = [{**row, 'open': str(200-Decimal(row['open'])),
                 'close': str(200-Decimal(row['close'])),
                 'high': str(200-Decimal(row['low'])), 'low': str(200-Decimal(row['high']))}
                for row in right_too_early()]
        origin = {'direction': 'DOWN', 'date': rows[35]['date'], 'field': 'high', 'price': rows[35]['high']}
        value = model.select_structure(rows, origin=origin)
        self.assertEqual([step['side'] for step in value['search_trace']], ['RIGHT', 'LEFT'])
        self.assertEqual(value['structure']['direction'], 'DOWN')
        self.assertEqual(value['structure']['axis']['field'], 'high')
        self.assertEqual(value['structure']['axis']['date'], rows[20]['date'])
        self.assertEqual(Decimal(value['structure']['levels']['L']), Decimal('166.1'))

    def test_up_role_selects_current_retest_low_and_retains_old_high_only_as_proof(self):
        original = source([(0, 100), (20, 85), (24, 80.1), (28, 120), (34, 90),
                           (39, 110), (44, 85), (48, 79), (51, 79.9), (55, 78), (59, 79)])
        rows = [{**row, 'open': str(200-Decimal(row['open'])),
                 'close': str(200-Decimal(row['close'])),
                 'high': str(200-Decimal(row['low'])), 'low': str(200-Decimal(row['high']))}
                for row in original]
        value = model.select_structure(rows)
        structure = value['structure']
        self.assertEqual(structure['direction'], 'UP')
        self.assertEqual(structure['route'], 'ROLE')
        self.assertEqual(structure['axis']['field'], 'low')
        self.assertGreater(structure['axis']['date'], structure['origin']['date'])
        self.assertTrue(any(proof['role']=='已有结构支撑／阻力' and proof['field']=='high'
                            and proof['date']<structure['origin']['date'] for proof in structure['evidence']))
        self.assertTrue(any(proof['role']=='本波段收盘失守／收复'
                            and proof['date']>structure['origin']['date'] for proof in structure['evidence']))

    def test_missing_right_confirmation_is_pending_turn_not_platform(self):
        rows = right_too_early()
        rows[-2].update(open='22', high='23', low='14', close='22')
        rows[-1].update(open='22', high='23', low='16', close='23')
        value = model.select_structure(rows, origin=anchor(rows))
        right = value['search_trace'][0]
        pending = next(a for a in right['candidates'] if a['point']['date']==rows[-2]['date'])
        self.assertEqual(pending['basis'], 'TURN')
        self.assertEqual(pending['qualification'], 'PENDING')
        self.assertEqual(pending['result'], 'UNASSESSED')
        self.assertIsNone(pending['evidence_through'])

    def test_breakout_resistance_high_is_not_a_substitute_for_secondary_low(self):
        rows = source([(0, 30), (35, 10), (40, 11), (45, 12), (50, 13), (59, 14)])
        rows[36].update(open='11.2', high='12', low='10.8', close='11.3')
        rows[37].update(open='11.3', high='12.5', low='11', close='11.4')
        value = model.select_structure(rows, origin=anchor(rows))
        self.assertIsNone(value['structure'])
        self.assertTrue(all(a['point']['field']=='low' for step in value['search_trace'] for a in step['candidates']))

    def test_changed_rule_and_hash_cannot_inherit_a_matching_manual_origin_lock(self):
        rows = right_ready()
        origin = anchor(rows)
        before = detail(rows, origin)
        later = rows+[next_bar(rows, high='27', low='25', close='26')]
        for key, value in [('rule_id', 'GOLDEN_V21_HYBRID_DAILY_V2'), ('policy_sha256', 'd'*64)]:
            with self.subTest(key=key):
                old = deepcopy(before)
                old[key] = value
                result = detail(later, origin, old)
                self.assertEqual(result['row']['basis'], 'CURRENT_REFERENCE')
                self.assertEqual(result['row']['new_crossing'], 'UNKNOWN')

    def test_price_alias_does_not_create_a_different_origin_context(self):
        rows = right_ready()
        origin = anchor(rows)
        alias = {**origin, 'price': origin['price']+'00'}
        one = model.select_structure(rows, origin=origin)
        two = model.select_structure(rows, origin=alias)
        self.assertEqual(one['origin_context'], two['origin_context'])
        self.assertEqual(one['search_trace'], two['search_trace'])


if __name__ == '__main__':
    unittest.main()
