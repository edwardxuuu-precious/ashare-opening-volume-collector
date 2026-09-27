import _bootstrap
from unittest import TestCase
from unittest.mock import Mock, patch
import pandas as pd
from scripts import refresh
from scripts.exchange_status import listing_evidence, parse_listing_records, parse_market_suspensions, official_disclosure_suspensions


def proofs():
    day = '2026-09-01'
    catalog = parse_listing_records([{'code':'301688','listed':'2026-09-02'}],
        code_key='code', date_key='listed', provider='szse',
        source_url='https://www.szse.cn/market/product/stock/list/index.html')
    listing = listing_evidence(catalog, '301688', day)
    market = parse_market_suspensions([{'代码':'002731','停牌时间':day,
        '停牌截止时间':None,'停牌期限':'连续停牌','停牌原因':'刊登重要公告',
        '所属市场':'深交所'}],day)['002731']
    disclosure = official_disclosure_suspensions('2026-08-07')['300862']
    return [listing, market, disclosure]


class HistoricalRefreshEvidenceTests(TestCase):
    def test_refresh_accepts_same_validated_evidence_as_historical_backfill(self):
        for proof in proofs():
            with self.subTest(kind=proof['kind']):
                row = dict(code=proof['code'],status='suspended',ratio=None,noTradeEvidence=proof)
                self.assertTrue(refresh.complete(row, proof['date']))
                self.assertFalse(refresh.complete(row, '2026-09-24'))
                forged = dict(proof,validatedDates=[])
                self.assertFalse(refresh.complete(dict(row,noTradeEvidence=forged),proof['date']))

    def test_retry_restores_validated_saved_no_trade_without_market_requests(self):
        for proof in proofs():
            with self.subTest(kind=proof['kind']):
                previous = dict(code=proof['code'],name='fixture',status='missing',ratio=None,
                    first15Volume=None,dailyVolume=None,noTradeEvidence=proof,
                    open=None,high=None,low=None,close=None,priceStatus='not_traded',
                    pctChange=None,amplitude=None)
                provider=Mock();provider.stock_zh_a_minute.side_effect=AssertionError('unexpected minute request')
                with patch.object(refresh,'AK',provider), patch('scripts.sina_daily.daily_unadjusted',side_effect=AssertionError('unexpected daily request')):
                    result=refresh.fetch((dict(code=proof['code'],name='fixture'),proof['date'],None,previous,'catchup'))
                self.assertTrue(refresh.complete(result['row'],proof['date']))
                self.assertEqual(result['row']['noTradeEvidence'],proof)
                self.assertIsNone(result['row']['first15Volume'])
                self.assertIsNone(result['row']['dailyVolume'])
                self.assertIsNone(result['firstDataRequestAt'])

    def test_positive_core_observation_does_not_become_no_trade_from_old_proof(self):
        proof=proofs()[0]
        previous=dict(code=proof['code'],status='ok',first15Volume=10,dailyVolume=100,
                      ratio=10,noTradeEvidence=proof,pctChange=1,amplitude=2)
        result=refresh.fetch((dict(code=proof['code'],name='fixture'),proof['date'],10,previous,'catchup'))
        self.assertEqual(result['row']['status'],'ok')
        self.assertEqual(result['row']['ratio'],10)
