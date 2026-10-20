import _bootstrap
import json
import tempfile
import unittest
from pathlib import Path
from scripts.download_only import evaluate_downloaded,downloaded_row,prepare_saved_downloads
from scripts import collect,daily_state
from server.cloud_worker import validate_day
from server.import_checkpoint_batch import record_disposition
from test_reconciliation import DAY,pair


class DownloadOnlyTests(unittest.TestCase):
    def test_only_opening_and_daily_are_needed_no_full_day_or_close_comparison(self):
        minute,daily=pair(daily=1001)
        minute=minute.iloc[:1].copy();minute['close']=999
        row=evaluate_downloaded('000001','test',DAY,minute,daily,collect.market)
        self.assertEqual(row['status'],'ok')
        self.assertAlmostEqual(row['ratio'],200/1001*100,places=5)
        self.assertTrue(daily_state.successful(row))
        validate_day(dict(date=DAY,rows=[row]),DAY)
        record=dict(code='000001',fetchStatus='ok',fetchedOn=DAY,days={DAY:row})
        self.assertEqual(record_disposition(record,'000001',{'000001'},[DAY]),'eligible')

    def test_absent_opening_or_zero_daily_is_missing_never_zero_filled(self):
        minute,daily=pair(daily=0)
        self.assertIsNone(evaluate_downloaded('000001','test',DAY,minute,daily,collect.market)['ratio'])
        minute,daily=pair()
        row=evaluate_downloaded('000001','test',DAY,minute.iloc[1:],daily,collect.market)
        self.assertEqual(row['status'],'missing');self.assertIsNone(row['first15Volume'])
        for invalid in (-1, 0.5, float('nan')):
            minute,daily=pair();minute.loc[0,'volume']=invalid
            row=evaluate_downloaded('000001','test',DAY,minute,daily,collect.market)
            self.assertIsNone(row['first15Volume']);self.assertIsNone(row['ratio'])
            record=dict(code='000001',fetchStatus='ok',fetchedOn=DAY,days={DAY:row})
            self.assertEqual(record_disposition(record,'000001',{'000001'},[DAY]),'missing_only')

    def test_historical_day_absent_from_both_downloads_remains_unexplained_and_retryable(self):
        minute,daily=pair()
        row=evaluate_downloaded('000016','停牌样本',DAY,minute.iloc[0:0],daily.iloc[0:0],collect.market)
        self.assertEqual(row['status'],'missing')
        self.assertIsNone(row['first15Volume']);self.assertIsNone(row['dailyVolume'])
        self.assertIsNone(row['ratio'])
        self.assertEqual(row['priceStatus'],'missing')
        self.assertTrue(all(row[key] is None for key in ('open','high','low','close')))
        self.assertFalse(daily_state.successful(row))

    def test_publish_validation_rejects_partial_and_invalid_ohlc(self):
        minute,daily=pair()
        daily['open']=9.8;daily['high']=10.3;daily['low']=9.7
        row=evaluate_downloaded('000001','test',DAY,minute,daily,collect.market)
        self.assertEqual(row['priceStatus'],'available')
        validate_day(dict(date=DAY,rows=[row]),DAY)
        for changed in (
            dict(close=None),
            dict(open=10.4),
            dict(priceStatus='not_traded'),
        ):
            with self.subTest(changed=changed):
                invalid=dict(row,**changed)
                with self.assertRaises(ValueError):
                    validate_day(dict(date=DAY,rows=[invalid]),DAY)

    def test_old_difference_reclassifies_without_changing_source_volumes(self):
        old=collect.evaluate('000001','test',DAY,*pair(daily=1001))
        new=downloaded_row(old)
        self.assertEqual(new['status'],'ok')
        for key in ('first15Volume','dailyVolume','minuteDayVolume','sourceProvider'):
            self.assertEqual(new[key],old[key])
        self.assertEqual(collect.safe_saved_row(new),new)
        self.assertTrue(daily_state.successful(new))
        validate_day(dict(date=DAY,rows=[new]),DAY)
        self.assertTrue(collect.reusable_checkpoint(dict(fetchedOn=DAY,fetchStatus='ok',days={DAY:new}),[DAY],DAY))

    def test_preparation_retains_dates_and_successes_and_is_idempotent(self):
        good=collect.evaluate('600519','good',DAY,*pair())
        bad=collect.evaluate('000001','bad',DAY,*pair(daily=1001))
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            collect.write_json(out/'manifest.json',dict(dates=[dict(date=DAY,file=DAY+'.json')]))
            collect.write_json(out/(DAY+'.json'),dict(date=DAY,generatedAt='old',rows=[good,bad]))
            collect.write_json(out/'checkpoint/000001.json',dict(code='000001',days={DAY:bad}))
            first=prepare_saved_downloads(out,[DAY])
            self.assertTrue(first['changed']);self.assertEqual(first['promotedRows'],1)
            payload=json.loads((out/(DAY+'.json')).read_text())
            self.assertEqual(payload['rows'][0],good)
            self.assertEqual(payload['rows'][1]['status'],'ok')
            self.assertEqual(json.loads((out/'manifest.json').read_text())['dates'][0]['valid'],2)
            second=prepare_saved_downloads(out,[DAY])
            self.assertEqual(second,dict(changed=False,promotedRows=0))
            self.assertEqual(len(list(out.glob('20*.json'))),1)


if __name__=='__main__':unittest.main()
