"""SZSE catalog 1798 rows read on 2026-09-27; no live network in regression tests."""
import _bootstrap
import unittest
from unittest.mock import Mock

from scripts import exchange_status
from scripts.reconciliation import special_status_explained


DAY = '2026-09-24'
CURRENT_ROWS = [
    dict(zqdm='000016', zqjc='*ST康佳A', tpkssj='2026-09-04 开市', fpkssj='', tpsj='停牌', tpyy='重大事项'),
    dict(zqdm='002731', zqjc='*ST萃华', tpkssj='2026-09-01 开市', fpkssj='', tpsj='停牌', tpyy='重大事项'),
    dict(zqdm='002860', zqjc='星帅尔', tpkssj='2026-09-22 开市', fpkssj='', tpsj='停牌', tpyy='重大事项'),
    dict(zqdm='300082', zqjc='奥克股份', tpkssj='2026-09-21 开市', fpkssj='', tpsj='停牌', tpyy='重大事项'),
    dict(zqdm='300096', zqjc='ST易联众', tpkssj='2026-09-24 开市', fpkssj='2026-09-28 开市', tpsj='1天', tpyy='撤销其他风险警示'),
    dict(zqdm='301139', zqjc='*ST元道', tpkssj='2026-08-31 开市', fpkssj='', tpsj='停牌', tpyy='重大事项'),
]


def report(rows, page=1, pages=1, total=None):
    return [dict(metadata=dict(catalogid='1798', tabkey='tab1', pageno=page,
                              pagecount=pages, recordcount=len(rows) if total is None else total,
                              pagesize=10), data=rows, error=None)]


class SzseStatusTests(unittest.TestCase):
    def test_six_zero_volume_rows_get_exact_day_official_explanations(self):
        def get(url, **kwargs):
            code = kwargs['params']['txtDmorjc']
            return Mock(json=Mock(return_value=report([r for r in CURRENT_ROWS if r['zqdm'] == code])))
        evidence = exchange_status.load_szse_suspensions(DAY, [r['zqdm'] for r in CURRENT_ROWS], get=get)
        self.assertEqual(set(evidence), {r['zqdm'] for r in CURRENT_ROWS})
        for code, value in evidence.items():
            self.assertEqual(value['date'], DAY)
            self.assertEqual(value['provider'], 'szse')
            self.assertTrue(exchange_status.valid_exchange_evidence(value, code))
            self.assertTrue(special_status_explained(dict(status='suspended', **value)))
        self.assertEqual(evidence['300096']['resumeDate'], '2026-09-28')
        self.assertEqual(evidence['300096']['reason'], '撤销其他风险警示')
        # The official feed says only this; do not infer delisting or restructuring.
        self.assertEqual(evidence['000016']['reason'], '重大事项')
        self.assertEqual(evidence['000016']['specialStatus']['type'], 'suspension')

    def test_resume_and_later_suspension_bound_old_open_interval(self):
        rows = [dict(CURRENT_ROWS[0]),
                dict(zqdm='000016', zqjc='*ST康佳A', tpkssj='', fpkssj='2026-08-28 开市', tpsj='取消停牌', tpyy='重大事项'),
                dict(CURRENT_ROWS[0], tpkssj='2026-08-24 开市')]
        self.assertEqual(exchange_status.parse_szse_suspensions(rows, DAY, code='000016')['000016']['startDate'], '2026-09-04')
        self.assertEqual(exchange_status.parse_szse_suspensions(rows[1:], '2026-08-28', code='000016'), {})

    def test_intraday_suspension_and_resume_date_are_not_full_day_no_trade(self):
        row = CURRENT_ROWS[4]
        self.assertEqual(exchange_status.parse_szse_suspensions([row], '2026-09-28', code='300096'), {})
        intraday = dict(row, fpkssj='2026-09-24 10:30:00', tpsj='1小时')
        self.assertEqual(exchange_status.parse_szse_suspensions([intraday], DAY, code='300096'), {})
        late = dict(row, tpkssj='2026-09-24 10:00:00')
        self.assertEqual(exchange_status.parse_szse_suspensions([late], DAY, code='300096'), {})

    def test_missing_or_foreign_identity_and_conflicting_records_fail_closed(self):
        row = CURRENT_ROWS[4]
        for rows in ([dict(row, zqdm='300097')], [dict(row, zqdm='')],
                     [row, row], [row, dict(row, tpyy='另一个原因')], [dict(row, tpyy='')]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                exchange_status.parse_szse_suspensions(rows, DAY, code='300096')

    def test_evidence_is_bound_to_code_date_provider_and_query_records(self):
        value = exchange_status.parse_szse_suspensions([CURRENT_ROWS[4]], DAY, code='300096')['300096']
        for key, changed in (('code', '300097'), ('date', '2026-09-28'),
                             ('provider', 'sse'), ('sourceUrl', 'https://example.com'),
                             ('reason', '伪造原因')):
            tampered = dict(value, **{key: changed})
            self.assertFalse(exchange_status.valid_exchange_evidence(tampered, '300096'))

    def test_loader_verifies_complete_pages_before_using_open_intervals(self):
        response = Mock(json=Mock(return_value=report([CURRENT_ROWS[0]])))
        get = Mock(return_value=response)
        exchange_status.load_szse_suspensions(DAY, ['000016'], get=get)
        url, = get.call_args.args
        self.assertEqual(url, 'https://www.szse.cn/api/report/ShowReport/data')
        self.assertEqual(get.call_args.kwargs['params']['txtZzrq'], DAY)
        self.assertEqual(get.call_args.kwargs['params']['txtKsrq'], '2025-09-23')
        self.assertEqual(get.call_args.kwargs['timeout'], 15)
        self.assertEqual(get.call_args.kwargs['headers']['Referer'], 'https://www.szse.cn/disclosure/memo/index.html')
        response.raise_for_status.assert_called_once()
        for bad in (report([CURRENT_ROWS[0]], pages=4, total=31),
                    report([CURRENT_ROWS[0]], total=2),
                    [dict(metadata={}, data=[], error=None)], {'data': []}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                exchange_status.load_szse_suspensions(DAY, ['000016'], get=Mock(return_value=Mock(json=Mock(return_value=bad))))

    def test_page_drift_and_duplicate_rows_fail_closed(self):
        rows = [dict(CURRENT_ROWS[0], tpkssj=f'2026-08-{i:02} 开市',
                     fpkssj=f'2026-08-{i+1:02} 开市', tpsj='1天') for i in range(1, 11)]
        first = report(rows, pages=2, total=11)
        for second in (report([rows[0]], page=2, pages=2, total=11),
                       report([CURRENT_ROWS[0]], page=2, pages=3, total=21)):
            get = Mock(side_effect=[Mock(json=Mock(return_value=first)), Mock(json=Mock(return_value=second))])
            with self.assertRaises(ValueError):
                exchange_status.load_szse_suspensions(DAY, ['000016'], get=get)
            self.assertEqual(get.call_count, 2)

    def test_loader_collects_all_pages_and_binds_them_to_evidence(self):
        rows = [dict(CURRENT_ROWS[0], tpkssj=f'2026-08-{i:02} 开市',
                     fpkssj=f'2026-08-{i+1:02} 开市', tpsj='1天') for i in range(1, 11)]
        get = Mock(side_effect=[Mock(json=Mock(return_value=report(rows, pages=2, total=11))),
                               Mock(json=Mock(return_value=report([CURRENT_ROWS[0]], page=2, pages=2, total=11)))])
        value = exchange_status.load_szse_suspensions(DAY, ['000016'], get=get)['000016']
        self.assertEqual(value['startDate'], '2026-09-04')
        self.assertEqual(len(value['queryRecords']), 11)
        self.assertTrue(exchange_status.valid_exchange_evidence(value, '000016'))
        self.assertEqual(get.call_args_list[1].kwargs['params']['PAGENO'], '2')

    def test_invalid_or_unbounded_targets_never_request_the_provider(self):
        get = Mock()
        for day, codes in ((DAY, ['600001']), (DAY, ['000016'] * 21), ('2026-02-30', ['000016']), (None, ['000016'])):
            with self.subTest(day=day, codes=codes), self.assertRaises(ValueError):
                exchange_status.load_szse_suspensions(day, codes, get=get)
        get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
