import _bootstrap
import unittest
from unittest.mock import Mock

from scripts.exchange_status import (listing_evidence, parse_listing_records,
                                     parse_market_suspensions,
                                     load_listing_catalog, load_sse_suspensions,
                                     official_disclosure_suspensions,
                                     parse_sse_suspensions,
                                     valid_historical_no_trade)


class ExchangeStatusTests(unittest.TestCase):
    def test_official_szse_disclosures_prove_exact_suspension_interval(self):
        during = official_disclosure_suspensions('2026-08-07')

        evidence = during['300862']
        self.assertEqual(evidence['kind'], 'official_disclosure_suspension')
        self.assertEqual(evidence['provider'], 'szse')
        self.assertEqual(evidence['startDate'], '2026-07-27')
        self.assertEqual(evidence['endDate'], '2026-08-07')
        self.assertEqual(evidence['specialStatus']['type'], 'major_restructuring')
        self.assertTrue(valid_historical_no_trade(evidence, '300862', '2026-08-07'))
        self.assertNotIn('300862', official_disclosure_suspensions('2026-07-24'))
        self.assertNotIn('300862', official_disclosure_suspensions('2026-08-10'))

        report_delay = official_disclosure_suspensions('2026-06-29')['002731']
        self.assertEqual(report_delay['startDate'], '2026-05-06')
        self.assertEqual(report_delay['endDate'], '2026-07-06')
        self.assertEqual(report_delay['resumeDate'], '2026-07-07')
        self.assertEqual(report_delay['specialStatus']['label'], '定期报告未披露停牌')
        self.assertTrue(valid_historical_no_trade(report_delay, '002731', '2026-06-29'))
        self.assertNotIn('002731', official_disclosure_suspensions('2026-05-05'))
        self.assertNotIn('002731', official_disclosure_suspensions('2026-07-07'))

        capital_increase = official_disclosure_suspensions('2026-06-18')['000793']
        self.assertEqual(capital_increase['startDate'], '2026-06-18')
        self.assertEqual(capital_increase['endDate'], '2026-06-18')
        self.assertEqual(capital_increase['resumeDate'], '2026-06-22')
        self.assertEqual(capital_increase['specialStatus']['label'], '重整转增股本停牌')
        self.assertTrue(valid_historical_no_trade(capital_increase, '000793', '2026-06-18'))
        self.assertNotIn('000793', official_disclosure_suspensions('2026-06-17'))
        self.assertNotIn('000793', official_disclosure_suspensions('2026-06-22'))

        warning_days = {
            ('301139','2026-05-11'):('2026-05-12','重大违法退市风险警示停牌'),
            ('000016','2026-04-29'):('2026-04-30','退市及其他风险警示停牌'),
            ('002175','2026-04-29'):('2026-04-30','退市风险警示停牌'),
            ('000838','2026-04-24'):('2026-04-27','退市及其他风险警示停牌'),
        }
        for (code, day), (resume, label) in warning_days.items():
            with self.subTest(code=code, day=day):
                evidence = official_disclosure_suspensions(day)[code]
                self.assertEqual(evidence['startDate'], day)
                self.assertEqual(evidence['endDate'], day)
                self.assertEqual(evidence['resumeDate'], resume)
                self.assertEqual(evidence['specialStatus']['label'], label)
                self.assertTrue(valid_historical_no_trade(evidence, code, day))

        risk_warning = official_disclosure_suspensions('2026-03-25')['000908']
        self.assertEqual(risk_warning['startDate'], '2026-03-25')
        self.assertEqual(risk_warning['endDate'], '2026-03-25')
        self.assertEqual(risk_warning['resumeDate'], '2026-03-26')
        self.assertEqual(risk_warning['specialStatus']['label'], '风险警示变更停牌')
        self.assertTrue(valid_historical_no_trade(risk_warning, '000908', '2026-03-25'))
        self.assertNotIn('000908', official_disclosure_suspensions('2026-03-24'))
        self.assertNotIn('000908', official_disclosure_suspensions('2026-03-26'))

        pre_restructuring = official_disclosure_suspensions('2026-03-20')['300385']
        self.assertEqual(pre_restructuring['startDate'], '2026-03-17')
        self.assertEqual(pre_restructuring['endDate'], '2026-03-20')
        self.assertEqual(pre_restructuring['resumeDate'], '2026-03-23')
        self.assertEqual(pre_restructuring['specialStatus']['label'], '预重整投资人遴选停牌')
        self.assertTrue(valid_historical_no_trade(pre_restructuring, '300385', '2026-03-20'))
        self.assertNotIn('300385', official_disclosure_suspensions('2026-03-16'))
        self.assertNotIn('300385', official_disclosure_suspensions('2026-03-23'))

        volatility_review = official_disclosure_suspensions('2026-03-19')['000711']
        self.assertEqual(volatility_review['startDate'], '2026-03-13')
        self.assertEqual(volatility_review['endDate'], '2026-03-19')
        self.assertEqual(volatility_review['resumeDate'], '2026-03-20')
        self.assertEqual(volatility_review['specialStatus']['label'], '交易异常波动核查停牌')
        self.assertTrue(valid_historical_no_trade(volatility_review, '000711', '2026-03-19'))
        self.assertNotIn('000711', official_disclosure_suspensions('2026-03-12'))
        self.assertNotIn('000711', official_disclosure_suspensions('2026-03-20'))

    def test_listing_catalog_retries_each_official_source_before_accepting_it(self):
        class Frame:
            def __init__(self, rows):
                self.rows = rows

            def to_dict(self, orient):
                self.assert_orient = orient
                return self.rows

        sz_rows = [
            {'A股代码':str(300000 + index), 'A股上市日期':'2020-01-01'}
            for index in range(1000)
        ] + [{'A股代码':'301688', 'A股上市日期':'2026-09-02'}]
        sh_frames = {
            '主板A股':Frame([{'证券代码':'601123', '上市日期':'2026-09-01'}]),
            '科创板':Frame([{'证券代码':'688835', '上市日期':'2026-08-25'}]),
        }
        provider = Mock()
        provider.stock_info_sz_name_code.side_effect = [OSError('temporary TLS failure'), Frame(sz_rows)]
        provider.stock_info_sh_name_code.side_effect = lambda board: sh_frames[board]
        provider.stock_info_bj_name_code.return_value = Frame([
            {'证券代码':'920289', '上市日期':'2026-09-04'},
        ])
        sleep = Mock()

        catalog = load_listing_catalog(provider, retries=1, sleep=sleep)

        self.assertEqual(catalog['301688']['listingDate'], '2026-09-02')
        self.assertEqual(catalog['601123']['provider'], 'sse')
        self.assertEqual(catalog['920289']['provider'], 'bse')
        self.assertEqual(provider.stock_info_sz_name_code.call_count, 2)
        sleep.assert_called_once_with(1)

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
