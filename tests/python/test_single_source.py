import _bootstrap
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from scripts import daily_collector as daily
from scripts import collect
from test_reconciliation import pair, DAY


class SingleSourceTests(unittest.TestCase):
    def test_conflict_fetches_one_pair_and_never_calls_fallback_or_refresh(self):
        minute, day = pair(daily=1001)
        ak=SimpleNamespace(stock_zh_a_daily=Mock(return_value=day))
        blocked=Mock(side_effect=AssertionError('Another source or refresh must not run'))
        with patch.object(daily,'PRIMARY',(ak,blocked,True)), \
             patch.object(daily.collect,'minute_unadjusted',return_value=minute) as fetch, \
             patch.object(daily,'reconcile_stock',blocked), patch.object(daily,'eastmoney_pair',blocked):
            code, rows=daily.fetch_primary(('000001','test',[DAY]))
        fetch.assert_called_once()
        ak.stock_zh_a_daily.assert_called_once()
        blocked.assert_not_called()
        self.assertEqual(rows[DAY]['sourceProvider'],'sina')
        self.assertEqual(rows[DAY]['status'],'ok')
        self.assertAlmostEqual(rows[DAY]['ratio'],200/1001*100,places=5)
        self.assertEqual(rows[DAY]['calculationPolicy'],'download_only')
        self.assertNotIn('reconciliationAttempts',rows[DAY])

    def test_single_source_publication_preserves_historical_provider(self):
        old=dict(collect.evaluate('600519','old',DAY,*pair()),sourceProvider='baostock')
        new=collect.evaluate('000001','new',DAY,*pair())
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            collect.write_json(out/(DAY+'.json'),dict(date=DAY,generatedAt='old',scope='full',universeTotal=2,rows=[old]))
            collect.publish([dict(code='000001',days={DAY:new})],[DAY],out,'partial',2,
                            single_source=True,apply_retention=False)
            payload=json.loads((out/(DAY+'.json')).read_text())
            preserved=next(r for r in payload['rows'] if r['code']=='600519')
            self.assertEqual({key:preserved[key] for key in old},old)
            self.assertEqual(preserved['priceStatus'],'missing')
            self.assertTrue(all(preserved[key] is None for key in ('open','high','low','close')))
            self.assertEqual(payload['collectionSourcePolicy'],['sina'])
            self.assertEqual(payload['sourcePolicy'],['baostock','sina'])
            self.assertEqual(json.loads((out/'manifest.json').read_text())['collectionSourcePolicy'],['sina'])
            self.assertEqual(json.loads((out/'manifest.json').read_text())['sourcePolicy'],['baostock','sina'])


if __name__=='__main__':unittest.main()
