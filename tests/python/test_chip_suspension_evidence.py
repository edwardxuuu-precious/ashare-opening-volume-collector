"""Invented saved exchange rows and model bars; no network, AWS or market run."""
import copy
import hashlib
import json
import unittest

from backend.chips.suspension_evidence import extract,overlay
from backend.scripts.exchange_status import parse_sse_suspensions,parse_szse_suspensions

CODE='600001'
DAY='2026-10-27'


def sse(start='2026-10-26',end='',code=CODE):
    return {'productCode':code,'controlType':'TR','startStopDate':start.replace('-',''),
        'endStopDate':end.replace('-',''),'stopReason':'INVENTED_REASON','type':'LSTP' if end else 'LXTP'}


def szse(start,*,resume='',code='002001',duration='连续停牌'):
    return {'zqdm':code,'zqjc':'INVENTED_NAME','tpkssj':start+' 开市','fpkssj':resume+' 开市' if resume else '',
        'tpsj':duration,'tpyy':'INVENTED_REASON'}


def publication(source_day=DAY,*,record=None,code=CODE,records=None):
    if records is None:proof=parse_sse_suspensions([record or sse(code=code)],source_day)[code]
    else:proof=parse_szse_suspensions(records,source_day,code=code)[code]
    return {'date':source_day,'generatedAt':source_day+'T16:00:00+08:00','rows':[
        {'code':code,'name':'INVENTED_NAME','status':'suspended','priceStatus':'not_traded','verificationVersion':3,
         'open':None,'high':None,'low':None,'close':None,'first15Volume':None,'dailyVolume':None,'ratio':None,
         'noTradeEvidence':proof,'retained_original_field':'INVENTED_UNPROJECTED_SOURCE_VALUE'}]}


def evidence(source_day=DAY,**kwargs):
    return extract(publication(source_day,**kwargs),kwargs.get('code',CODE),f'data/{source_day}.json','"INVENTED_ETAG"')


def model(days=('2026-10-23',),code=CODE,**extra):
    value={'rc':0,'data':{'code':code,'name':'INVENTED_NAME','klines':[
        f'{d},100,100,100,100,100,1000000,0,0,0,1' for d in days]},**extra}
    raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
    receipt={'provider':'THS_UNADJUSTED','sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
        'original_history_bundle_sha256':'f'*64,'retained_receipt_field':'INVENTED'}
    return raw,receipt


class SavedSuspensionEvidence(unittest.TestCase):
    def test_extract_retains_exact_full_row_and_explicit_canonical_hash_semantics(self):
        value=publication();before=copy.deepcopy(value);bundle=extract(value,CODE,f'data/{DAY}.json')
        self.assertEqual(bundle['row'],value['rows'][0]);self.assertEqual(value,before)
        self.assertEqual(bundle['publication_date'],DAY);self.assertEqual(bundle['source_key'],f'data/{DAY}.json')
        self.assertEqual(bundle['publication_hash_basis'],'CANONICAL_SORTED_SAVED_PUBLICATION_JSON')
        self.assertFalse(bundle['historical_PIT_authenticated']);self.assertTrue(bundle['source_observed_at'])

    def test_no_official_evidence_or_inferred_zero_is_never_accepted(self):
        for proof in (None,{'kind':'explicit_zero_daily_volume','volume':0},
            {'kind':'market_suspension_record','provider':'eastmoney'}):
            value=publication();value['rows'][0]['noTradeEvidence']=proof
            self.assertIsNone(extract(value,CODE,f'data/{DAY}.json'))
        self.assertIsNone(extract(publication(),'600002',f'data/{DAY}.json'))

    def test_wrong_code_date_status_version_and_positive_saved_row_fail_closed(self):
        for field,value in (('date','2026-10-26'),('verificationVersion',2),('status','ok'),
            ('priceStatus','available'),('close',100),('dailyVolume',100),('first15Volume',1)):
            source=publication();source['rows'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):extract(source,CODE,f'data/{DAY}.json')
        with self.assertRaises(ValueError):extract(publication(),CODE,'data/latest.json')
        source=publication();source['rows'].append(copy.deepcopy(source['rows'][0]))
        with self.assertRaises(ValueError):extract(source,CODE,f'data/{DAY}.json')

    def test_complete_current_interval_adds_only_missing_sessions_without_modifying_bars(self):
        raw,receipt=model();before=copy.deepcopy(receipt)
        merged,result=overlay(raw,receipt,{'code':CODE},DAY,[evidence()]);value=json.loads(merged)
        self.assertEqual(value['data'],json.loads(raw)['data'])
        self.assertEqual(value['confirmed_no_trade_sessions'],['2026-10-26',DAY])
        self.assertEqual(result['provider'],'THS_UNADJUSTED');self.assertEqual(receipt,before)
        self.assertEqual(result['original_history_bundle_sha256'],'f'*64)
        self.assertEqual(result['official_no_trade_original_bundle_sha256'],receipt['sha256'])
        self.assertEqual(result['sha256'],hashlib.sha256(merged).hexdigest())
        self.assertEqual(overlay(merged,result,{'code':CODE},DAY,[evidence()])[0],merged)

    def test_single_day_stop_never_fills_a_previous_gap_but_two_exact_sources_do(self):
        raw,receipt=model();today=evidence(record=sse(DAY,DAY))
        partial,meta=overlay(raw,receipt,{'code':CODE},DAY,[today])
        self.assertEqual(json.loads(partial)['confirmed_no_trade_sessions'],[DAY])
        yesterday=evidence('2026-10-26',record=sse('2026-10-26','2026-10-26'))
        merged,result=overlay(partial,meta,{'code':CODE},DAY,[yesterday])
        self.assertEqual(set(json.loads(merged)['confirmed_no_trade_sessions']),{'2026-10-26',DAY})
        self.assertEqual(json.loads(merged)['data']['klines'],json.loads(raw)['data']['klines'])
        self.assertEqual(overlay(merged,result,{'code':CODE},DAY,[today,yesterday]),(merged,result))

    def test_saved_older_open_interval_does_not_prove_a_later_session(self):
        raw,receipt=model();old=evidence('2026-10-26')
        merged,_=overlay(raw,receipt,{'code':CODE},DAY,[old])
        self.assertEqual(json.loads(merged)['confirmed_no_trade_sessions'],['2026-10-26'])

    def test_temporary_sse_stop_requires_an_explicit_single_day_not_an_open_interval(self):
        for end in ('',DAY):
            record=sse('2026-10-26',end);record['type']='LSTP'
            with self.subTest(end=end),self.assertRaisesRegex(ValueError,'TEMPORARY_SUSPENSION_EXACT_DAY'):
                evidence(record=record)

    def test_szse_reparse_retains_original_query_identity_and_excludes_holidays(self):
        code='002001';raw,receipt=model(('2026-09-24',),code)
        bundle=evidence('2026-10-08',code=code,records=[szse('2026-09-25',code=code)])
        merged,_=overlay(raw,receipt,{'code':code},'2026-10-08',[bundle]);value=json.loads(merged)
        self.assertEqual(value['confirmed_no_trade_sessions'],['2026-09-28','2026-09-29','2026-09-30','2026-10-08'])
        saved=value['official_no_trade_evidence'][0]['bundle']['row']['noTradeEvidence']
        self.assertEqual(saved,bundle['row']['noTradeEvidence'])
        self.assertEqual(saved['date'],'2026-10-08');self.assertEqual(saved['queryEnd'],'2026-10-08')
        self.assertEqual(saved['validatedDates'],['2026-10-08'])

    def test_existing_dates_remain_and_same_day_trade_and_moved_trade_never_disappear(self):
        for kwargs in ({'days':('2026-10-23',DAY)},
            {'unconfirmed_current_session_history_lines':[f'{DAY},100,100,100,100,1,10000,0,0,0,1']},
            {'current_session_quote_overlay':{'date':DAY,'observed_positive_volume_trade':True}}):
            raw,receipt=model(**kwargs)
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):overlay(raw,receipt,{'code':CODE},DAY,[evidence()])
        raw,receipt=model(confirmed_no_trade_sessions=['2026-10-22'])
        merged,_=overlay(raw,receipt,{'code':CODE},DAY,[evidence()])
        self.assertEqual(json.loads(merged)['confirmed_no_trade_sessions'][0],'2026-10-22')

    def test_existing_future_or_positive_trade_no_trade_claim_is_not_preserved_as_valid(self):
        for dates in (['2026-10-28'],['2026-10-23'],[{}]):
            raw,receipt=model(confirmed_no_trade_sessions=dates)
            with self.subTest(dates=dates),self.assertRaises(ValueError):overlay(raw,receipt,{'code':CODE},DAY,[evidence()])

    def test_archive_row_proof_or_bundle_mutations_are_revalidated(self):
        for field in ('row','row_sha256','proof_sha256','source_key','bundle_sha256'):
            bundle=evidence()
            if field=='row':bundle[field]['noTradeEvidence']['date']='2026-10-26'
            else:bundle[field]='changed'
            raw,receipt=model()
            with self.subTest(field=field),self.assertRaises(ValueError):overlay(raw,receipt,{'code':CODE},DAY,[bundle])

    def test_same_dated_source_cannot_select_between_conflicting_official_rows(self):
        one=evidence();source=publication();record=sse();record['stopReason']='OTHER_INVENTED_REASON'
        source['rows'][0]['noTradeEvidence']=parse_sse_suspensions([record],DAY)[CODE]
        two=extract(source,CODE,f'data/{DAY}.json');raw,receipt=model()
        with self.assertRaisesRegex(ValueError,'DATED_SOURCE_CONFLICT'):overlay(raw,receipt,{'code':CODE},DAY,[one,two])

    def test_later_explicit_resumption_refutes_an_older_open_stop_claim(self):
        code='002001';old=evidence('2026-10-23',code=code,records=[szse('2026-10-20',code=code)])
        new=evidence(code=code,records=[szse('2026-10-20',resume='2026-10-23',code=code,duration='3天'),szse('2026-10-26',code=code)])
        raw,receipt=model(('2026-10-19',),code)
        with self.assertRaisesRegex(ValueError,'RESUMPTION_CONFLICT'):overlay(raw,receipt,{'code':code},DAY,[old,new])

    def test_future_or_over_366_day_source_and_bad_original_hash_fail_closed(self):
        raw,receipt=model()
        for source_day in ('2026-10-28','2025-10-25'):
            bundle=evidence(source_day,record=sse(source_day,''))
            with self.subTest(source_day=source_day),self.assertRaises(ValueError):overlay(raw,receipt,{'code':CODE},DAY,[bundle])
        with self.assertRaisesRegex(ValueError,'INPUT_HASH'):overlay(raw,{**receipt,'sha256':'0'*64},{'code':CODE},DAY,[evidence()])


if __name__=='__main__':unittest.main()
