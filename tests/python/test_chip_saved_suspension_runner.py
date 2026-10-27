"""Invented end-to-end source carry and archive resumption; no market or cloud IO."""
import copy
import hashlib
import json
import time
import unittest

from backend.chips import runner
from backend.chips.calendar import last_ten
from test_chip_daily_v1 import Memory
from test_chip_suspension_evidence import CODE,DAY,publication,sse
from backend.chips.suspension_evidence import extract,overlay


class SavedSuspensionRunner(unittest.TestCase):
    def setup_case(self,*,two_single_days=False,positive_conflict=False):
        days=last_ten(DAY)
        lines=[f'{d},100,100,100,100,100,1000000,0,0,0,1' for d in days if d<'2026-10-26']
        value={'rc':0,'data':{'code':CODE,'name':'INVENTED_NAME','klines':lines}}
        if positive_conflict:
            value['unconfirmed_current_session_history_lines']=[f'{DAY},100,100,100,100,100,1000000,0,0,0,1']
        raw=json.dumps(value,separators=(',',':')).encode()
        receipt={'provider':'THS_UNADJUSTED','sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        class Source:
            history_calls=0
            fallback_calls=0
            def history(self,stock,asof):
                self.history_calls+=1
                return copy.deepcopy(value),copy.deepcopy(receipt),raw
            def confirmed_history(self,*args):
                self.fallback_calls+=1
                raise ConnectionError('INVENTED_TRANSPORT_FAILURE')
        store=Memory()
        store.put('collector/chips/lease.json',{'owner':'INVENTED_OWNER','expires':time.time()+300})
        today=publication(record=sse(DAY,DAY)) if two_single_days else publication()
        store.put(f'data/{DAY}.json',today)
        if two_single_days:
            store.put('data/2026-10-26.json',publication('2026-10-26',record=sse('2026-10-26','2026-10-26')))
        ctx={'slot':'0123456789abcdef','dataset':'0123456789abcdef','days':days,'as_of':DAY,
            'owner':'INVENTED_OWNER','pools':{},'actual_limits':{},'execution_identity':runner.implementation_identity()}
        stock={'code':CODE,'name':'INVENTED_NAME','secid':'1.'+CODE,'listed':'2000-01-01'}
        return store,Source(),stock,ctx,raw

    def test_official_saved_interval_recovers_ready_without_history_or_price_fabrication(self):
        store,source,stock,ctx,raw=self.setup_case()
        row=runner._calculate(store,source,stock,ctx)
        self.assertEqual(row['chip_status'],'READY')
        self.assertEqual(row['close_cny'],'100')
        self.assertEqual(row['last_trade_close_date'],'2026-10-23')
        self.assertEqual(source.fallback_calls,0)
        frame=store.get(f'data/chips/daily/{ctx["dataset"]}/{CODE}.json')[0]
        self.assertEqual([r['date'] for r in frame['history']],[s.split(',')[0] for s in json.loads(raw)['data']['klines']])
        for target in ('2026-10-26',DAY):
            self.assertEqual(frame['snapshots'][target]['last_trade_date'],'2026-10-23')
            self.assertEqual(frame['snapshots'][target]['market_session_status'],'SOURCE_CONFIRMED_NO_TRADE')
        key=f'collector/chips/suspension-inputs/{ctx["dataset"]}/{CODE}/{DAY}.json'
        archived=store.get(key)[0]
        self.assertEqual(archived['private_archive_key'],key)
        self.assertNotIn('private_archive_key',archived['bundle'])
        self.assertNotIn('official_no_trade_input_error',frame)
        self.assertEqual(len(frame['source']['official_no_trade_private_input_keys']),1)

    def test_two_dated_single_session_records_are_both_frozen_and_used(self):
        store,source,stock,ctx,raw=self.setup_case(two_single_days=True)
        row=runner._calculate(store,source,stock,ctx)
        self.assertEqual(row['chip_status'],'READY')
        frame=store.get(f'data/chips/daily/{ctx["dataset"]}/{CODE}.json')[0]
        keys=frame['source']['official_no_trade_private_input_keys']
        self.assertEqual(len(keys),2)
        self.assertTrue(any(k.endswith('/2026-10-26.json') for k in keys))
        self.assertTrue(any(k.endswith('/2026-10-27.json') for k in keys))

    def test_unproven_previous_gap_remains_missing_and_keeps_existing_fallback(self):
        store,source,stock,ctx,raw=self.setup_case(two_single_days=True)
        del store.data['data/2026-10-26.json']
        row=runner._calculate(store,source,stock,ctx)
        self.assertEqual(row['chip_status'],'MISSING')
        self.assertNotEqual(row['screen_status'],'PASS')
        self.assertEqual(source.fallback_calls,1)

    def test_positive_history_conflict_does_not_become_ready(self):
        store,source,stock,ctx,raw=self.setup_case(positive_conflict=True)
        row=runner._calculate(store,source,stock,ctx)
        self.assertEqual(row['chip_status'],'MISSING')
        frame=store.get(f'data/chips/daily/{ctx["dataset"]}/{CODE}.json')[0]
        self.assertIn('POSITIVE_TRADE_CONFLICT',frame['official_no_trade_input_error']['detail'])
        self.assertNotEqual(row['screen_status'],'PASS')

    def test_retry_uses_frozen_original_publication_instead_of_replacing_its_proof(self):
        store,source,stock,ctx,raw=self.setup_case()
        runner._calculate(store,source,stock,ctx)
        key=f'data/chips/daily/{ctx["dataset"]}/{CODE}.json'
        prior=store.get(key)[0]
        del store.data[key]
        store.data[f'data/{DAY}.json']['rows'][0]['verificationVersion']=2
        row=runner._calculate(store,source,stock,ctx)
        current=store.get(key)[0]
        self.assertEqual(row['chip_status'],'READY')
        self.assertEqual(current['source']['sha256'],prior['source']['sha256'])
        self.assertEqual(current['snapshots'],prior['snapshots'])

    def test_generic_quote_placeholder_removal_preserves_revalidated_official_status(self):
        store,source,stock,ctx,raw=self.setup_case()
        _,receipt,raw=source.history(stock,DAY)
        proof=extract(store.data[f'data/{DAY}.json'],CODE,f'data/{DAY}.json')
        raw,receipt=overlay(raw,receipt,stock,DAY,[proof])
        self.assertEqual(runner.overlay_quote_with_saved_status(raw,receipt,stock,DAY,None,
            require_final_quote=True),(raw,receipt))
        value=json.loads(raw)
        value['official_no_trade_evidence'][0]['bundle']['row']['verificationVersion']=2
        invalid=json.dumps(value,separators=(',',':')).encode()
        bad_receipt={**receipt,'sha256':hashlib.sha256(invalid).hexdigest()}
        with self.assertRaises(ValueError):
            runner.overlay_quote_with_saved_status(invalid,bad_receipt,stock,DAY,None,
                require_final_quote=True)
        with self.assertRaises(ValueError):
            runner.frame(stock,invalid,bad_receipt,ctx['days'],{},ctx['dataset'])

    def test_ready_official_evidence_requires_current_validator_identity(self):
        identity=runner.implementation_identity()
        previous={'source_audit':{'official_no_trade_evidence':[{'INVENTED':True}]},
            'execution_identity':copy.deepcopy(identity)}
        self.assertTrue(runner.compatible_suspension_evidence(previous,identity))
        for name in ('suspension_evidence.py','exchange_status.py'):
            changed=copy.deepcopy(identity);changed['files'][name]='f'*64
            with self.subTest(name=name):
                self.assertFalse(runner.compatible_suspension_evidence(previous,changed))
        self.assertTrue(runner.compatible_suspension_evidence({'source_audit':{}},identity))


if __name__=='__main__':unittest.main()
