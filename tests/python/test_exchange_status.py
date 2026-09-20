import _bootstrap
import unittest
from unittest.mock import Mock

from scripts.exchange_status import (listing_evidence, parse_listing_records,
                                     parse_market_suspensions,
                                     load_sse_suspensions, parse_sse_suspensions)


class ExchangeStatusTests(unittest.TestCase):
    def test_exchange_listing_catalog_proves_exact_prelisting_days(self):
        catalog = parse_listing_records(
            [{'A股代码':'301688','A股上市日期':'2026-09-02'}],
            code_key='A股代码', date_key='A股上市日期', provider='szse',
            source_url='https://www.szse.cn/market/product/stock/list/index.html')

        evidence = listing_evidence(catalog, '301688', '2026-09-01')

        self.assertEqual(evidence['kind'], 'not_yet_listed')
        self.assertEqual(evidence['listingDate'], '2026-09-02')
        self.assertEqual(evidence['date'], '2026-09-01')
        self.assertIsNone(listing_evidence(catalog, '301688', '2026-09-02'))

    def test_market_suspension_catalog_requires_a_full_day_exact_interval(self):
        rows = [
            {'代码':'002731','停牌时间':'2026-09-01','停牌截止时间':None,
             '停牌期限':'连续停牌','停牌原因':'未如期刊登定期报告','所属市场':'深交所风险警示板'},
            {'代码':'920288','停牌时间':'2026-08-28','停牌截止时间':'2026-08-28',
             '停牌期限':'盘中停牌','停牌原因':'交易异常波动','所属市场':'北交所'},
            {'代码':'301390','停牌时间':'2026-09-10','停牌截止时间':'2026-09-16',
             '停牌期限':'连续停牌','停牌原因':'刊登重要公告','所属市场':'深交所创业板'},
        ]

        result = parse_market_suspensions(rows, '2026-09-15')

        self.assertEqual(set(result), {'002731','301390'})
        self.assertEqual(result['002731']['kind'], 'market_suspension_record')
        self.assertEqual(result['301390']['endDate'], '2026-09-16')
        self.assertNotIn('920288', result)

    def test_parser_keeps_only_equities_suspended_on_exact_target_day(self):
        rows = [
            dict(productCode='601238', productName='广汽集团', controlType='TR',
                 startStopDate='20260915', endStopDate='', type='LXTP',
                 stopReason='拟筹划重大资产重组'),
            dict(productCode='600301', productName='华锡有色', controlType='TR',
                 startStopDate='20260914', endStopDate='20260918', type='LXTP',
                 stopReason='重要公告'),
            dict(productCode='603159', productName='已复牌', controlType='TR',
                 startStopDate='20260910', endStopDate='20260916', type='LXTP',
                 stopReason='重要公告'),
            dict(productCode='118060', productName='可转债', controlType='GB',
                 startStopDate='20260918', endStopDate='20260924', type='LXTP',
                 stopReason='重要公告'),
        ]

        result = parse_sse_suspensions(rows, '2026-09-18')

        self.assertEqual(set(result), {'601238', '600301'})
        evidence = result['601238']
        self.assertEqual(evidence['date'], '2026-09-18')
        self.assertEqual(evidence['kind'], 'exchange_suspension_record')
        self.assertEqual(evidence['specialStatus']['type'], 'major_restructuring')
        self.assertEqual(evidence['specialStatus']['label'], '重大资产重组停牌')
        self.assertIn('上交所记录', evidence['specialStatus']['description'])

    def test_loader_uses_one_bounded_official_exchange_request(self):
        response = Mock()
        response.json.return_value = {'result': [
            dict(productCode='601238', productName='广汽集团', controlType='TR',
                 startStopDate='20260915', endStopDate='', type='LXTP',
                 stopReason='拟筹划重大资产重组'),
        ]}
        get = Mock(return_value=response)

        result = load_sse_suspensions('2026-09-18', get=get)

        self.assertIn('601238', result)
        get.assert_called_once()
        _, kwargs = get.call_args
        self.assertEqual(kwargs['timeout'], 15)
        self.assertEqual(kwargs['headers']['Referer'], 'https://www.sse.com.cn/')
        self.assertEqual(kwargs['params']['sqlId'], 'GW_PL_JYTS_TFPXX')
        self.assertEqual(kwargs['params']['endStopDate'], '20260918')
        response.raise_for_status.assert_called_once()

    def test_invalid_payload_fails_closed(self):
        response = Mock()
        response.json.return_value = {'result': 'not-a-list'}
        get = Mock(return_value=response)
        with self.assertRaises(ValueError):
            load_sse_suspensions('2026-09-18', get=get)


if __name__ == '__main__':
    unittest.main()

