from copy import deepcopy
from decimal import Decimal
import unittest

from backend.golden_projection import model
from test_golden_projection_model import candles


def conversion_rows():
    rows = candles([(0, 30), (5, 35), (10, 25), (15, 32), (20, 14), (25, 34),
                    (30, 26), (35, 10), (40, 24), (45, 12), (50, 28), (59, 29)])
    for index, open_, high, low, close in [
            (30, '27.2', '27.3', '26', '27'),
            (31, '27.1', '27.5', '26.1', '27.3'),
            (32, '25', '25.1', '24.9', '25'),
            (35, '10.2', '10.4', '10', '10.2'),
            (36, '12.8', '19', '12.7', '12.8'),
            (40, '20', '27.2', '19.9', '20'),
            (49, '26.5', '26.7', '26.4', '26.5')]:
        rows[index].update(open=open_, high=high, low=low, close=close)
    return rows


def origin(rows, index=35, direction='UP'):
    field = 'low' if direction == 'UP' else 'high'
    return {'direction': direction, 'date': rows[index]['date'], 'field': field,
            'price': rows[index][field]}


def reflect(rows):
    return [{**row, 'open': str(200-Decimal(row['open'])),
             'close': str(200-Decimal(row['close'])),
             'high': str(200-Decimal(row['low'])), 'low': str(200-Decimal(row['high']))}
            for row in rows]


def source_attempts(value, rows, index=31):
    return [candidate for step in value['search_trace'] if step['side'] == 'LEFT'
            for candidate in step['candidates']
            if candidate['basis'] == 'ROLE' and candidate['point']['date'] == rows[index]['date']]


class RoleConversionTests(unittest.TestCase):
    def test_shared_support_boundary_low_is_the_axis_not_retest_high(self):
        rows = conversion_rows()
        value = model.select_structure(rows, origin=origin(rows))
        self.assertEqual(value['state'], 'DRAW')
        structure = value['structure']
        self.assertEqual(structure['axis'], {'date': rows[31]['date'], 'field': 'low', 'price': '26.1'})
        self.assertEqual(Decimal(structure['levels']['H']), Decimal('42.2'))
        self.assertEqual(structure['evidence_through'], rows[50]['date'])
        self.assertEqual(structure['role_zone'], {'lower': '26.1', 'upper': '27',
                                                'start_date': rows[30]['date'], 'end_date': rows[40]['date']})
        self.assertEqual([step['side'] for step in value['search_trace']], ['RIGHT', 'LEFT'])
        self.assertTrue(any(item['date'] == rows[30]['date'] and item['field'] == 'low'
                            and Decimal(item['value']) == Decimal('26') for item in structure['evidence']))
        self.assertTrue(any(item['date'] == rows[40]['date'] and item['field'] == 'high'
                            and Decimal(item['value']) == Decimal('27.2') for item in structure['evidence']))
        self.assertFalse(any(candidate['point']['date'] == rows[30]['date'] and candidate['result'] == 'UNASSESSED'
                             for step in value['search_trace'] for candidate in step['candidates']))

    def test_public_automatic_path_consumes_the_same_conversion(self):
        rows = conversion_rows()
        automatic = model.select_structure(rows)
        explicit = model.select_structure(rows, origin=origin(rows))
        self.assertEqual(automatic['state'], 'DRAW')
        self.assertEqual(automatic['structure']['id'], explicit['structure']['id'])
        self.assertEqual(automatic['structure']['axis']['field'], 'low')

    def test_down_mirror_uses_actual_high_at_the_shared_upper_boundary(self):
        rows = reflect(conversion_rows())
        value = model.select_structure(rows, origin=origin(rows, direction='DOWN'))
        self.assertEqual(value['state'], 'DRAW')
        self.assertEqual(value['structure']['axis']['date'], rows[31]['date'])
        self.assertEqual(value['structure']['axis']['field'], 'high')
        self.assertEqual(Decimal(value['structure']['levels']['M']), Decimal('173.9'))
        self.assertEqual(Decimal(value['structure']['levels']['L']), Decimal('157.8'))
        self.assertEqual(value['structure']['evidence_through'], rows[50]['date'])

    def test_missing_source_break_or_valid_failed_retest_cannot_supply_axis(self):
        base = conversion_rows()
        variants = {}
        rows = deepcopy(base)
        rows[31].update(open='27.1', high='27.5', low='27.1', close='27.3')
        variants['one_source_wick'] = rows
        rows = deepcopy(base)
        for index in (32, 33, 34):
            rows[index].update(open='28', high='28.1', low='27.9', close='28')
        variants['break_only_on_origin_day'] = rows
        rows = deepcopy(base)
        rows[40]['high'] = '26.0'
        variants['missed_zone'] = rows
        rows = deepcopy(base)
        rows[40]['open'] = '26.5'
        variants['body_crosses_zone'] = rows
        rows = deepcopy(base)
        rows[38].update(open='28', high='28.1', low='27.9', close='28')
        variants['already_reclaimed_before_retest'] = rows
        rows = deepcopy(base)
        rows[35]['high'] = '27.2'
        rows[40]['high'] = '26.0'
        variants['retest_only_on_origin_day'] = rows
        for label, rows in variants.items():
            with self.subTest(label=label):
                value = model.select_structure(rows, origin=origin(rows))
                self.assertFalse(source_attempts(value, rows))

    def test_origin_after_the_failed_retest_cannot_reuse_the_earlier_event(self):
        rows = conversion_rows()
        value = model.select_structure(rows, origin=origin(rows, index=41))
        self.assertFalse(source_attempts(value, rows))

    def test_close_inside_core_does_not_complete_pairing(self):
        rows = conversion_rows()
        for row in rows[50:]:
            row.update(open='26.5', high='26.7', low='26.4', close='26.5')
        value = model.select_structure(rows, origin=origin(rows))
        attempts = source_attempts(value, rows)
        self.assertTrue(attempts)
        self.assertTrue(all(item['qualification'] == 'CONFIRMED' and item['result'] == 'PAIR_EVIDENCE_PENDING'
                            for item in attempts))
        self.assertTrue(all(item['evidence_through'] == rows[40]['date'] for item in attempts))

    def test_target_before_full_core_pairing_is_rejected(self):
        rows = conversion_rows()
        rows[49]['high'] = '42.3'
        value = model.select_structure(rows, origin=origin(rows))
        attempts = source_attempts(value, rows)
        self.assertTrue(attempts)
        self.assertTrue(all(item['result'] == 'TARGET_BEFORE_EVIDENCE' for item in attempts))
        self.assertTrue(all(item['evidence_through'] == rows[50]['date'] for item in attempts))

    def test_later_target_reach_keeps_the_source_and_real_terminal_state(self):
        from backend.chips.calendar import sessions
        rows = conversion_rows()
        before = model.select_structure(rows, origin=origin(rows))
        self.assertEqual(before['state'], 'DRAW')
        dates = sessions('2026-01-01', '2026-12-31')
        rows.append({'date': dates[dates.index(rows[-1]['date'])+1], 'open': '42.5', 'high': '43',
                     'low': '42.3', 'close': '42.6', 'volume_shares': '100000'})
        after = model.select_structure(rows, origin=origin(rows, index=35))
        self.assertEqual(after['structure']['id'], before['structure']['id'])
        self.assertEqual(after['structure']['life_state'], 'REACHED')
        self.assertEqual(after['structure']['guides_end_on'], rows[-1]['date'])

    def test_all_source_contacts_must_belong_to_the_preconstructed_left_scope(self):
        rows = conversion_rows()
        for index, open_, high, low, close in [
                (18, '27', '28', '26', '27.2'),
                (19, '27.1', '28.1', '26.1', '27.3'),
                (20, '27.2', '27.5', '13.9', '27.3'),
                (21, '27', '27.4', '26.2', '27.1')]:
            rows[index].update(open=open_, high=high, low=low, close=close)
        for index in range(22, 35):
            close = Decimal('26')-Decimal(index-22)/10
            rows[index].update(open=str(close), high=str(close+Decimal('.02')),
                               low=str(close-Decimal('.02')), close=str(close))
        rows[25]['high'] = '34.1'
        value = model.select_structure(rows, origin=origin(rows))
        attempts = source_attempts(value, rows, index=21)
        self.assertTrue(attempts)
        self.assertTrue(all(item['result'] == 'PAIR_EVIDENCE_PENDING' for item in attempts))
        self.assertTrue(all(rows[20]['date'] in item['reason'] for item in attempts))
        self.assertIsNone(value['structure'])

    def test_independent_valid_left_regions_remain_competing(self):
        rows = conversion_rows()
        for index, open_, high, low, close in [
                (27, '30.2', '30.5', '29', '30'),
                (28, '30.1', '30.5', '29.1', '30.3'),
                (40, '20', '30.2', '19.9', '20')]:
            rows[index].update(open=open_, high=high, low=low, close=close)
        for index in range(50, 60):
            close = Decimal('31')+Decimal(index-50)/10
            rows[index].update(open=str(close), high=str(close+Decimal('.1')),
                               low=str(close-Decimal('.1')), close=str(close))
        value = model.select_structure(rows, origin=origin(rows))
        self.assertEqual(value['state'], 'AMBIGUOUS')
        self.assertIsNone(value['structure'])
        self.assertEqual(value['search_trace'][-1]['side'], 'LEFT')
        self.assertEqual(value['search_trace'][-1]['outcome'], 'AMBIGUOUS')
        self.assertEqual({item['point']['date'] for index in (28, 31)
                          for item in source_attempts(value, rows, index=index) if item['result'] == 'OPEN'},
                         {rows[28]['date'], rows[31]['date']})


if __name__ == '__main__':
    unittest.main()
