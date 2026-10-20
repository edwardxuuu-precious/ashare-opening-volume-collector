"""Coverage repair regressions; all fixture market values are invented."""
import copy
import hashlib
import json
import time
import unittest
import urllib.error
from unittest.mock import patch

from backend.chips import ths_history
from backend.chips.runner import run,session_snapshots,_calculate,implementation_identity,frame
from backend.chips.model import prepare_eastmoney
from backend.chips.source import Source
from backend.chips.screen import closing_limit_state,prefilter
from backend.chips.listing import verified_current
from backend.chips.runner import encoded
from test_chip_daily_v1 import DAYS, FakeSource, Memory, STOCK,HIT


class CoverageRepair(unittest.TestCase):
    def ths(self, turnover='2.50'):
        return {'data': '20261015,5.00,5.10,4.90,5.00,1000,5000.00,' + turnover + ',,,0',
                'num': 1, 'total': '1'}

    def test_declared_share_amount_and_turnover_units_are_exact(self):
        normalized, raw = ths_history.normalize(STOCK, self.ths(), b'ORIGINAL_INVENTED', DAYS[-1])
        fields = normalized['data']['klines'][0].split(',')
        self.assertEqual(fields[5:7], ['10', '5000.00'])
        self.assertEqual(fields[10], '2.50')
        self.assertEqual(json.loads(raw)['original_source_utf8'], 'ORIGINAL_INVENTED')

    def test_missing_turnover_stays_empty_and_capped_history_is_rejected(self):
        value, _ = ths_history.normalize(STOCK, self.ths(''), b'INVENTED', DAYS[-1])
        self.assertEqual(value['data']['klines'][0].split(',')[10], '')
        capped = self.ths(); capped['total'] = '2'
        with self.assertRaisesRegex(ValueError, 'CAPPED'):
            ths_history.normalize(STOCK, capped, b'INVENTED', DAYS[-1])

    def test_adjusted_or_other_stock_callback_is_never_accepted(self):
        class Transport:
            def read_bytes(self, url):
                return b'quotebridge_v6_line_hs_000607_01_last10000({})'
        with self.assertRaisesRegex(ValueError, 'IDENTITY'):
            ths_history.history(Transport(), STOCK, DAYS[-1])

    def test_incomplete_committed_generation_is_repaired_without_overwriting_it(self):
        class Flaky(FakeSource):
            failing = True
            def history(self, *args, **kwargs):
                if self.failing: raise ConnectionError('INVENTED_TRANSPORT_FAILURE')
                return super().history(*args, **kwargs)
        store = Memory()
        with patch('backend.chips.runner.Source', Flaky):
            first = run(store, as_of=DAYS[-1], max_minutes=2, workers=1)
            old = copy.deepcopy(store.get('data/chips/daily/manifest.json')[0])
            old_frame = copy.deepcopy(store.get(f'data/chips/daily/{old["dataset_id"]}/{STOCK["code"]}.json')[0])
            self.assertEqual(first['model_ready'], 0)
            Flaky.failing = False
            second = run(store, as_of=DAYS[-1], max_minutes=2, workers=1)
            current = store.get('data/chips/daily/manifest.json')[0]
            self.assertEqual(second['model_ready'], 1)
            self.assertNotEqual(current['dataset_id'], old['dataset_id'])
            self.assertEqual(store.get(f'data/chips/daily/{old["dataset_id"]}/{STOCK["code"]}.json')[0], old_frame)
            self.assertIsNone(current['unchanged_threshold'])
            self.assertEqual(current['coefficient'], 2)

    def test_fallback_keeps_all_provider_failures(self):
        source = Source(interval=0)
        class Bao:
            def history(self, *args): raise ConnectionError('INVENTED_BAO_FAILURE')
        source.bao = Bao()
        with patch.object(source, 'em_history', side_effect=ConnectionError('INVENTED_EM_FAILURE')):
            with patch('backend.chips.ths_history.history', side_effect=ValueError('INVENTED_THS_CONFLICT')):
                with self.assertRaises(ConnectionError) as caught:
                    source.history(STOCK, DAYS[-1])
        self.assertEqual(len(caught.exception.chip_source_errors), 3)

    def test_cached_provider_failure_falls_back_to_full_other_provider(self):
        source = Source(interval=0)
        cached = {'_source_provider': 'THS_UNADJUSTED', 'last_date': DAYS[-2],
                  '_source_history': [DAYS[0] + ',INVENTED']}
        full = {'data': {'klines': ['INVENTED_NEW_PROVIDER_FULL_HISTORY']}}
        with patch('backend.chips.ths_history.history', side_effect=ConnectionError('INVENTED')):
            with patch.object(source, 'em_history', return_value=(full, {}, b'INVENTED')) as fallback:
                result = source.history(STOCK, DAYS[-1], cached)
        self.assertEqual(fallback.call_args.args, (STOCK, DAYS[-1], None))
        self.assertNotIn('cached_full_prefix', result[0])
        self.assertEqual(result[1]['cached_preferred_source_failure']['provider'], 'THS_UNADJUSTED')

    def test_unverified_ex_dividend_reference_cannot_exclude_real_limit_evidence(self):
        stock = {**STOCK, 'reference_close_verified': False}
        fields = [DAYS[-1], '5', '5', '5', '5', '10', '5000', '', '0', '0', '2.5']
        actual = {'date': DAYS[-1], 'close': '5', 'reference_close': '4.55', 'upper_limit': '5'}
        self.assertEqual(closing_limit_state(stock, fields, None, actual)[0], 'PASS')
        self.assertEqual(closing_limit_state(stock, fields, None)[0], 'UNKNOWN')
        pool = {STOCK['code']: {'p': 5000, 'amount': 5000, 'zdp': 9.89}}
        self.assertEqual(closing_limit_state(stock, fields, pool)[0], 'PASS')

    def test_permanent_security_errors_do_not_trigger_global_transport_stop(self):
        class Missing(FakeSource):
            def universe(self):
                return [{**STOCK, 'code': '000' + str(i).zfill(3)} for i in range(1, 22)], []
            def history(self, *args, **kwargs): raise ValueError('INVENTED_PERMANENT_MISSING_HISTORY')
        with patch('backend.chips.runner.Source', Missing):
            result = run(Memory(), as_of=DAYS[-1], max_minutes=2, workers=1)
        self.assertEqual(result['outcome'], 'COMMITTED')
        self.assertEqual(result['total'], 21)
        self.assertEqual(result['model_ready'], 0)

    def test_confirmed_halt_carries_mass_without_creating_candles(self):
        raw=json.dumps({'rc':0,'data':{'code':STOCK['code'],'klines':[
            '2026-10-14,5,5,5.1,4.9,10,5000,,0,0,2.5']}}).encode()
        bars,_=prepare_eastmoney(raw,expected_code=STOCK['code'],cutoff=DAYS[-1])
        snapshots,_=session_snapshots(bars,DAYS[-2:],security_id=STOCK['code'],no_trade=DAYS[-2:])
        self.assertEqual(len(bars),1)
        self.assertEqual(snapshots[DAYS[-1]]['profile'],snapshots[DAYS[-2]]['profile'])
        self.assertEqual(snapshots[DAYS[-1]]['last_trade_date'],'2026-10-14')
        self.assertEqual(snapshots[DAYS[-1]]['effective_replacement'],'0')
        with self.assertRaisesRegex(ValueError,'MISSING|UNCONFIRMED'):
            session_snapshots(bars,DAYS[-2:],security_id=STOCK['code'],no_trade=[DAYS[-1]])

    def test_halt_cannot_carry_old_close_across_an_unconfirmed_missing_day(self):
        stock={**STOCK,'confirmed_no_trade_sessions':[DAYS[-1]]}
        lines=['2026-10-14,9.5,9.5,9.5,9.5,10,9500,,10,0.86,2.5']
        result=prefilter(stock,lines,DAYS,{})
        self.assertIsNone(result['close_cny'])
        self.assertEqual(result['prefilter_status'],'UNKNOWN')

    def test_transient_http_502_is_retried_but_invalid_source_is_not(self):
        payload=('quotebridge_v6_line_hs_000607_00_last10000('+json.dumps(self.ths())+')').encode()
        class Transport:
            calls=0
            def read_bytes(self,url):
                self.calls+=1
                if self.calls==1:raise urllib.error.HTTPError(url,502,'INVENTED_GATEWAY',{},None)
                return payload
        source=Transport()
        with patch('backend.chips.ths_history.time.sleep'):
            _,receipt,_=ths_history.history(source,STOCK,DAYS[-1])
        self.assertEqual(source.calls,2)
        self.assertEqual(receipt['transport_failures_before_success'][0]['http_status'],502)

    def test_ready_frame_with_old_normalizer_is_recomputed(self):
        store=Memory();owner='INVENTED';identity=implementation_identity()
        store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+1000})
        def context(slot,previous=None):return {'slot':slot,'dataset':slot,'days':DAYS,'as_of':DAYS[-1],
            'owner':owner,'pools':{},'actual_limits':{},'execution_identity':identity,'previous_slot':previous}
        class AcceptCache(FakeSource):
            def history(self,stock,day,cached=None):return super().history(stock,day)
        source=AcceptCache();FakeSource.calls=0
        old='1111111111111111';new='2222222222222222'
        _calculate(store,source,STOCK,context(old))
        key=f'data/chips/daily/{old}/{STOCK["code"]}.json'
        value,etag=store.get(key);value['execution_identity']['history_adapter_sha256']='OLD_INVENTED_NORMALIZER'
        store.put(key,value,match=etag)
        cache_key=f'collector/chips/model/{STOCK["code"]}.json'
        cache,etag=store.get(cache_key);cache['_history_adapter_sha256']='OLD_INVENTED_NORMALIZER'
        store.put(cache_key,cache,match=etag)
        result=_calculate(store,source,STOCK,context(new,old))
        self.assertEqual(result['chip_status'],'READY')
        self.assertEqual(FakeSource.calls,2)

    def test_same_session_cache_can_recover_a_failed_frame_without_network(self):
        store=Memory();owner='INVENTED';identity=implementation_identity()
        store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+1000})
        def context(slot):return {'slot':slot,'dataset':slot,'days':DAYS,'as_of':DAYS[-1],
            'owner':owner,'pools':{},'actual_limits':{},'execution_identity':identity}
        _calculate(store,FakeSource(),STOCK,context('1111111111111111'))
        class NoNetwork:
            def history(self,*args,**kwargs):raise AssertionError('same-session source should be reused')
        result=_calculate(store,NoNetwork(),STOCK,context('2222222222222222'))
        self.assertEqual(result['chip_status'],'READY')
        saved=store.get(f'data/chips/daily/2222222222222222/{STOCK["code"]}.json')[0]
        self.assertEqual(saved['source']['same_session_cached_raw_key'],f'collector/chips/raw/1111111111111111/{STOCK["code"]}.json')

    def test_verified_first_listing_has_current_chips_without_fabricating_previous(self):
        raw=json.dumps({'rc':0,'data':{'code':STOCK['code'],'klines':[
            DAYS[-1]+',5,5,5.1,4.9,10,5000,,0,0,2.5']}}).encode()
        stock={**STOCK,'listed':DAYS[-1]}
        unverified,_=frame(stock,raw,{'provider':'THS_UNADJUSTED'},DAYS,{},'INVENTED')
        self.assertEqual(unverified['chip_status'],'MISSING')
        self.assertEqual(unverified['prefilter_status'],'UNKNOWN')
        stock['listing_evidence']={'security_id':stock['code'],'listed':stock['listed'],
            'as_of':DAYS[-1],'provider':'OFFICIAL_CURRENT_LIST_RECONCILIATION','sha256':'a'*64}
        row,value=frame(stock,raw,{'provider':'THS_UNADJUSTED'},DAYS,{},'INVENTED')
        self.assertEqual(row['chip_status'],'READY')
        self.assertEqual(row['comparison_status'],'NO_PREVIOUS_LISTED_SESSION')
        self.assertEqual(list(value['snapshots']),[DAYS[-1]])
        self.assertEqual(row['previous_peak_prices_cny'],[])
        self.assertEqual(row['original_peak_changes'],[])
        self.assertEqual(row['prefilter_status'],'FAIL')
        self.assertEqual(row['limit_evidence'][0]['reason'],'NOT_YET_LISTED')
        missing,_=frame(STOCK,raw,{'provider':'THS_UNADJUSTED'},DAYS,{},'INVENTED')
        self.assertEqual(missing['chip_status'],'MISSING')

    def test_official_reconciliation_cannot_qualify_another_date_or_security_set(self):
        stocks=[{'code':str(600000+i),'name':'ALL_VALUES_INVENTED','listed':'2020-01-01'} for i in range(1001)]
        bundle={'schema':'stock.chips.official-current-universe.v1','as_of':DAYS[-1],
            'historical_membership':False,'stocks':stocks,'observed_at':'INVENTED',
            'stocks_sha256':hashlib.sha256(encoded(stocks)).hexdigest(),
            'receipts':[{'provider':p} for p in ('SSE_CURRENT_LIST','SZSE_CURRENT_LIST','BSE_CURRENT_LIST_VISIBLE_BROWSER_TABLE')]}
        self.assertEqual(verified_current(bundle,stocks,DAYS[-1])[1]['total'],1001)
        with self.assertRaisesRegex(ValueError,'MISMATCH'):
            verified_current(bundle,stocks,DAYS[-2])
        with self.assertRaisesRegex(ValueError,'MISMATCH'):
            verified_current(bundle,[{**s,'code':'600999'} if i==0 else s for i,s in enumerate(stocks)],DAYS[-1])
        altered=copy.deepcopy(bundle);altered['stocks'][0]['listed']='2026-01-01'
        with self.assertRaisesRegex(ValueError,'MISMATCH'):
            verified_current(altered,stocks,DAYS[-1])

    def test_listing_correction_refreshes_cached_screen_and_rejects_a_false_first_day(self):
        store=Memory();owner='INVENTED';identity=implementation_identity()
        store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+1000})
        def context(slot,previous=None):return {'slot':slot,'dataset':slot,'days':DAYS,'as_of':DAYS[-1],
            'owner':owner,'pools':{d:HIT for d in DAYS},'actual_limits':{},'execution_identity':identity,'previous_slot':previous}
        old='1111111111111111'
        self.assertEqual(_calculate(store,FakeSource(),STOCK,context(old))['prefilter_status'],'PASS')
        listed={**STOCK,'listed':DAYS[-2],'listing_evidence':{'security_id':STOCK['code'],
            'listed':DAYS[-2],'as_of':DAYS[-1],'provider':'OFFICIAL_CURRENT_LIST_RECONCILIATION','sha256':'a'*64}}
        refreshed=_calculate(store,FakeSource(),listed,context('2222222222222222',old))
        self.assertEqual(refreshed['chip_status'],'READY')
        self.assertEqual(refreshed['prefilter_status'],'FAIL')
        self.assertEqual(refreshed['recent_limit_up_dates'],[])
        listed.update(listed=DAYS[-1]);listed['listing_evidence']['listed']=DAYS[-1]
        conflict=_calculate(store,FakeSource(),listed,context('3333333333333333',old))
        self.assertEqual(conflict['chip_status'],'MISSING')

    def test_correcting_an_old_first_day_never_reuses_its_single_snapshot(self):
        store=Memory();owner='INVENTED';identity=implementation_identity()
        store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+1000})
        def context(slot,previous=None):return {'slot':slot,'dataset':slot,'days':DAYS,'as_of':DAYS[-1],
            'owner':owner,'pools':{},'actual_limits':{},'execution_identity':identity,'previous_slot':previous}
        stock={**STOCK,'listed':DAYS[-1],'listing_evidence':{'security_id':STOCK['code'],
            'listed':DAYS[-1],'as_of':DAYS[-1],'provider':'OFFICIAL_CURRENT_LIST_RECONCILIATION','sha256':'a'*64}}
        class FirstDaySource(FakeSource):
            def history(self,stock,day):
                raw=json.dumps({'rc':0,'data':{'code':stock['code'],'klines':[
                    DAYS[-1]+',5,5,5.1,4.9,10,5000,,0,0,2.5']}}).encode()
                return json.loads(raw),{'sha256':hashlib.sha256(raw).hexdigest()},raw
        old='1111111111111111'
        self.assertEqual(_calculate(store,FirstDaySource(),stock,context(old))['chip_status'],'READY')
        stock['listed']=DAYS[-2];stock['listing_evidence']['listed']=DAYS[-2]
        changed=_calculate(store,FirstDaySource(),stock,context('2222222222222222',old))
        self.assertEqual(changed['chip_status'],'MISSING')
        saved=store.get(f'data/chips/daily/2222222222222222/{STOCK["code"]}.json')[0]
        self.assertEqual(saved['snapshots'],{})
        self.assertIsNone(saved.get('comparison_status'))

    def test_final_publication_rechecks_lease_after_screen_read_and_screen_write(self):
        for after_write in (False,True):
            with self.subTest(after_write=after_write):
                store=Memory();read=store.get;write=store.put
                def revoke():write('collector/chips/lease.json',{'owner':'OTHER_INVENTED_WRITER','expires':time.time()+1000})
                def get(key):
                    result=read(key)
                    if not after_write and key.endswith('/screening.json'):revoke()
                    return result
                def put(key,value,**kwargs):
                    result=write(key,value,**kwargs)
                    if after_write and key.endswith('/screening.json'):revoke()
                    return result
                store.get=get;store.put=put
                with patch('backend.chips.runner.Source',FakeSource):
                    with self.assertRaisesRegex(RuntimeError,'LEASE_LOST'):
                        run(store,as_of=DAYS[-1],max_minutes=2,workers=1)
                self.assertIsNone(read('data/chips/daily/manifest.json')[0])


if __name__ == '__main__': unittest.main()
