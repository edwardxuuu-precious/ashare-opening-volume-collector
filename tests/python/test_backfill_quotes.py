import _bootstrap
import gc
import json
import tempfile
import unittest
import weakref
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import backfill_quotes


def traded(code='000001'):
    return dict(code=code, name='样本', market='SZ', status='ok',
                first15Volume=10, dailyVolume=100, ratio=10,
                pctChange=1.0, amplitude=2.0, verificationVersion=3)


class PriceBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)

    def write_day(self, day, rows):
        (self.out / f'{day}.json').write_text(json.dumps(dict(
            date=day, generatedAt='old', rows=rows, methodology='fixture')))

    def test_scan_selects_only_newest_twenty_incomplete_days_even_when_pct_exists(self):
        start = date(2026, 8, 20)
        days = [(start + timedelta(days=index)).isoformat() for index in range(21)]
        for day in days:
            self.write_day(day, [traded()])

        needs, rows_seen, selected, incomplete = backfill_quotes.scan_needs(
            self.out, {}, False, min(days), max(days), 20)

        self.assertEqual(selected, sorted(days, reverse=True)[:20])
        self.assertEqual(incomplete, sorted(days, reverse=True))
        self.assertEqual(rows_seen, 21)
        self.assertEqual(needs['000001'], sorted(days))

    def test_scan_does_not_retain_every_historical_payload_in_memory(self):
        days=['2026-09-18','2026-09-17','2026-09-16','2026-09-15']
        for day in days:
            self.write_day(day,[traded()])
        live=set()

        class Payload(dict):
            __slots__=('__weakref__',)

        def bounded_load(path, default=None):
            gc.collect()
            self.assertLessEqual(len(live),1)
            day=Path(path).stem
            payload=Payload(date=day,rows=[traded()])
            identity=id(payload);live.add(identity)
            weakref.finalize(payload,live.discard,identity)
            return payload

        with patch.object(backfill_quotes,'load_json',side_effect=bounded_load):
            needs,rows_seen,selected,incomplete=backfill_quotes.scan_needs(
                self.out,{},False,min(days),max(days),1)

        self.assertEqual(selected,['2026-09-18'])
        self.assertEqual(incomplete,days)
        self.assertEqual(rows_seen,4)
        self.assertEqual(needs,{'000001':sorted(days)})

    def test_provider_system_exit_becomes_checkpointed_source_failure(self):
        day='2026-09-18'
        self.write_day(day,[traded()])
        (self.out/'manifest.json').write_text(json.dumps(dict(dates=[])))
        options=SimpleNamespace(
            out=str(self.out),cache=str(self.out/'price-backfill-cache.json'),
            backfill_id='ohlc-v1-20260920',from_date=day,to_date=day,batch_size=20,
            workers=1,interval=.35,cutoff='23:50',lookback=14,retries=0,
            save_every=200,force=False)
        scans=[({'000001':[day]},1,[day],[day]),({},1,[],[day])]
        provider=SimpleNamespace(
            stock_zh_a_hist_tx=Mock(side_effect=SystemExit(1)),
            stock_zh_a_daily=Mock(side_effect=SystemExit(1)))

        with patch.object(backfill_quotes,'scan_needs',side_effect=scans), \
             patch.object(backfill_quotes.SharedHTTPBudget,'install'), \
             patch.dict('sys.modules',{'akshare':provider}), \
             patch.object(backfill_quotes,'patch_files',return_value=dict(
                 filledRows=0,unfilledRows=1,filesChanged=1)), \
             patch.object(backfill_quotes,'touch_manifest'):
            result=backfill_quotes.run(options)

        self.assertEqual(result,1)
        state=json.loads((self.out/'price-backfill-state.json').read_text())
        self.assertEqual(state['exitReason'],'source_incomplete')
        cache=json.loads((self.out/'price-backfill-cache.json').read_text())
        self.assertIn('SystemExit',cache['quotes']['000001']['error'])

    def test_patch_is_atomic_preserves_core_fields_and_marks_confirmed_no_trade(self):
        day = '2026-09-18'
        normal = traded()
        normal['verificationVersion'] = 2
        stopped = dict(traded('600519'), status='suspended', first15Volume=None,
                       dailyVolume=None, ratio=None,
                       noTradeEvidence={'kind':'company_notice','date':day},
                       specialStatus={'type':'suspension','label':'公告停牌',
                           'description':'公告确认目标日停牌','startedAt':day,
                           'source':'交易所公告'})
        stopped['verificationVersion'] = 2
        self.write_day(day, [normal, stopped])
        (self.out/'manifest.json').write_text(json.dumps(dict(
            generatedAt='old', methodology='fixture',
            dates=[dict(date=day,file=day+'.json',generatedAt='old')])) )
        before = {key: normal[key] for key in ('first15Volume','dailyVolume','ratio','status')}
        quote = dict(open=9.8, high=10.3, low=9.7, close=10.0,
                     priceStatus='available', priceSourceProvider='tencent',
                     pctChange=1.1, amplitude=6.0, dailyAdapter='tencent_daily_quote_backfill')

        result = backfill_quotes.patch_files(
            self.out, {'000001':{'days':{day:quote}}}, 'new', [day])
        backfill_quotes.touch_manifest(self.out, 'new', [day])

        payload = json.loads((self.out/(day+'.json')).read_text())
        rows = {row['code']:row for row in payload['rows']}
        self.assertEqual({key:rows['000001'][key] for key in before}, before)
        self.assertEqual(rows['000001']['priceStatus'],'available')
        self.assertEqual(rows['000001']['priceSourceProvider'],'tencent')
        self.assertEqual(rows['000001']['verificationVersion'],3)
        self.assertEqual(rows['600519']['priceStatus'],'not_traded')
        self.assertEqual(rows['600519']['verificationVersion'],3)
        self.assertTrue(all(rows['600519'][key] is None for key in ('open','high','low','close')))
        self.assertEqual(rows['600519']['specialStatus'], stopped['specialStatus'])
        self.assertEqual(rows['600519']['noTradeEvidence'], stopped['noTradeEvidence'])
        self.assertEqual(result['filesChanged'],1)
        self.assertEqual(payload['priceAvailable'],1)
        self.assertEqual(payload['priceNoTrade'],1)
        self.assertEqual(payload['priceMissing'],0)
        self.assertTrue(payload['priceDataComplete'])
        entry=json.loads((self.out/'manifest.json').read_text())['dates'][0]
        self.assertTrue(entry['priceDataComplete'])

    def test_historical_status_evidence_completes_price_without_mutating_core_status(self):
        day='2026-09-15'
        row=dict(traded('301390'),status='missing',ratio=None,reason='尚未采集')
        self.write_day(day,[row])
        evidence={day:{'301390':dict(
            kind='market_suspension_record',provider='eastmoney',code='301390',date=day,
            startDate='2026-09-10',endDate='2026-09-16',reason='刊登重要公告',
            market='深交所创业板',duration='连续停牌',validatedDates=[day],
            sourceUrl='https://data.eastmoney.com/tfpxx/')}}
        before={key:row.get(key) for key in ('first15Volume','dailyVolume','ratio','status','reason','specialStatus')}

        backfill_quotes.patch_files(self.out,{},'new',[day],evidence)

        actual=json.loads((self.out/(day+'.json')).read_text())['rows'][0]
        self.assertEqual({key:actual.get(key) for key in before},before)
        self.assertEqual(actual['priceStatus'],'not_traded')
        self.assertEqual(actual['noTradeEvidence'],evidence[day]['301390'])
        self.assertTrue(all(actual[key] is None for key in ('open','high','low','close')))

    def test_quote_and_no_trade_evidence_conflict_stops_before_file_write(self):
        day='2026-09-15';self.write_day(day,[traded()])
        original=(self.out/(day+'.json')).read_bytes()
        quote=dict(open=9.8,high=10.3,low=9.7,close=10.0,
                   priceStatus='available',priceSourceProvider='tencent')
        evidence={day:{'000001':dict(kind='not_yet_listed',provider='szse',code='000001',
            date=day,listingDate='2026-09-16',validatedDates=[day],
            sourceUrl='https://www.szse.cn/market/product/stock/list/index.html')}}

        with self.assertRaises(ValueError):
            backfill_quotes.patch_files(self.out,{'000001':{'days':{day:quote}}},'new',[day],evidence)

        self.assertEqual((self.out/(day+'.json')).read_bytes(),original)

    def test_v1_pair_cache_never_claims_ohlc_coverage(self):
        day='2026-09-18';self.write_day(day,[traded()])
        old={'000001':{'days':{day:[1.0,2.0]}}}
        needs,_,_,_=backfill_quotes.scan_needs(self.out,old,False,day,day,20)
        self.assertEqual(needs,{'000001':[day]})

    def test_selected_unresolved_quote_writes_explicit_atomic_missing_fields(self):
        day='2026-09-18';row=traded();row['verificationVersion']=2
        self.write_day(day,[row])

        result=backfill_quotes.patch_files(self.out,{},'new',[day])

        payload=json.loads((self.out/(day+'.json')).read_text())
        actual=payload['rows'][0]
        self.assertEqual(result['filesChanged'],1)
        self.assertEqual(actual['verificationVersion'],3)
        self.assertEqual(actual['priceStatus'],'missing')
        self.assertTrue(all(key in actual and actual[key] is None
                            for key in ('open','high','low','close')))
        self.assertEqual(payload['priceMissing'],1)
        self.assertFalse(payload['priceDataComplete'])

    def test_selected_day_still_missing_fails_only_this_batch(self):
        exit_reason, selected_missing, result = backfill_quotes.completion_result(
            ['2026-09-18'], ['2026-09-18', '2026-09-17'], 'completed')
        self.assertEqual(exit_reason, 'source_incomplete')
        self.assertEqual(selected_missing, ['2026-09-18'])
        self.assertEqual(result, 1)

        exit_reason, selected_missing, result = backfill_quotes.completion_result(
            ['2026-09-18'], ['2026-09-17'], 'completed')
        self.assertEqual((exit_reason, selected_missing, result), ('completed', [], 0))

    def test_resume_state_is_bound_to_one_frozen_backfill(self):
        state = dict(version=1, backfillId='ohlc-v1-20260920',
                     fromDate='2026-03-06', toDate='2026-09-18', batchSize=20,
                     selectedDates=['2026-09-18'], remainingDates=['2026-09-17'],
                     completed=False, updatedAt='2026-09-20T12:00:00+08:00',
                     exitReason='completed')
        self.assertIs(backfill_quotes.validate_state(state), state)
        for key, value in (('batchSize', 21), ('completed', True),
                           ('selectedDates', ['2026-03-05'])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                backfill_quotes.validate_state(dict(state, **{key:value}))


if __name__ == '__main__':
    unittest.main()

