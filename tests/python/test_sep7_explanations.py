import _bootstrap
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from scripts import refresh
from scripts.no_trade_evidence import evidence, valid_notice, explanation
from scripts.reconciliation import special_status_explained

FIXTURE=json.loads((Path(__file__).parents[1]/'fixtures/sep7_legacy_no_trade.json').read_text())

class September7ExplanationTests(TestCase):
    def test_legacy_proof_identity_and_tamper_rejection_remain_unchanged(self):
        for code,proof in FIXTURE['proofs'].items():
            with self.subTest(code=code):
                self.assertEqual(evidence(code,FIXTURE['date']),proof)
                self.assertTrue(valid_notice(proof,code))
                self.assertFalse(valid_notice(dict(proof,noticeId='forged'),code))
                self.assertFalse(valid_notice(dict(proof,date='2026-09-08'),code))

    def test_all_nine_reviewed_rows_get_explanations_without_market_requests(self):
        for code,proof in FIXTURE['proofs'].items():
            with self.subTest(code=code):
                provider=Mock();provider.stock_zh_a_minute.side_effect=AssertionError('unexpected request')
                with patch.object(refresh,'AK',provider),patch('scripts.sina_daily.daily_unadjusted',side_effect=AssertionError('unexpected request')):
                    result=refresh.fetch((dict(code=code,name='fixture'),FIXTURE['date'],None,{},'catchup'))
                row=result['row']
                self.assertEqual(row['noTradeEvidence'],proof)
                self.assertTrue(refresh.complete(row,FIXTURE['date']))
                self.assertTrue(special_status_explained(row))
                self.assertIsNone(row['first15Volume'])
                self.assertIsNone(row['dailyVolume'])
                self.assertIsNone(row['ratio'])
                self.assertIsNone(result['firstDataRequestAt'])

    def test_bank_freeze_one_day_and_acquisition_classification_are_specific(self):
        for code,start,label_fragment in [('002743','2026-09-07','风险警示'),('002870','2026-09-01','资产收购'),('600825','2026-09-07','重大资产重组')]:
            row=refresh.fetch((dict(code=code,name='fixture'),'2026-09-07',None,{},'catchup'))['row']
            status=row.get('specialStatus',{})
            self.assertEqual(status.get('startedAt'),start)
            self.assertIn(label_fragment,status.get('label',''))
            if code=='002743':self.assertIn('一天',status['description'])
            if code=='002870':self.assertNotIn('重大资产重组',status['label'])

    def test_reviewed_explanations_do_not_leak_to_other_dates_or_mutate_registry(self):
        self.assertIsNone(explanation('002743','2026-09-08'))
        self.assertIsNone(explanation('000001','2026-09-07'))
        first=explanation('600825','2026-09-07')
        first['startedAt']='2099-01-01'
        self.assertEqual(explanation('600825','2026-09-07')['startedAt'],'2026-09-07')
