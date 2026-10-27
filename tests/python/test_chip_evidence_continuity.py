"""Invented evidence-continuity regressions; no network or real-market fixtures."""
import copy
import hashlib
import json
import unittest

from backend.chips.calendar import last_ten
from backend.chips.listing import restore_archived_listing_facts, trusted_listing
from backend.chips.stability import classify


SOURCE_DAY='2026-10-23'
AS_OF='2026-10-26'
SOURCE_KEY=f'collector/chips/universe-verified/{SOURCE_DAY}.json'


def checksum(value, *, sort_keys=True):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=sort_keys,
        separators=(',',':')).encode()).hexdigest()


def donor():
    stocks=[{'code':str(600000+i),'name':f'INVENTED_{i}','listed':'2026-10-15'}
        for i in range(1001)]
    return {'schema':'stock.chips.official-current-universe.v1','as_of':SOURCE_DAY,
        'historical_membership':False,'stocks':stocks,'observed_at':'INVENTED',
        'stocks_sha256':hashlib.sha256(json.dumps(stocks,ensure_ascii=False,
            separators=(',',':')).encode()).hexdigest(),
        'receipts':[{'provider':p} for p in ('SSE_CURRENT_LIST','SZSE_CURRENT_LIST',
            'BSE_CURRENT_LIST_VISIBLE_BROWSER_TABLE')]}


def current():
    return [{'code':'600000','name':'INVENTED_0','listed':None,'secid':'1.600000'},
        {'code':'609999','name':'INVENTED_CURRENT_ONLY','listed':None}]


class ArchivedListingFacts(unittest.TestCase):
    def test_restores_only_matching_current_facts_and_preserves_membership(self):
        stocks=current();bundle=donor();before=copy.deepcopy((stocks,bundle))
        restored,receipt=restore_archived_listing_facts(stocks,bundle,AS_OF,SOURCE_KEY)
        self.assertEqual([s['code'] for s in restored],[s['code'] for s in stocks])
        self.assertEqual(restored[0]['listed'],'2026-10-15')
        self.assertEqual(trusted_listing(restored[0],AS_OF),'2026-10-15')
        self.assertIsNone(restored[1]['listed'])
        self.assertEqual(restored[0]['secid'],'1.600000')
        self.assertEqual(receipt['provider'],'ARCHIVED_OFFICIAL_LISTING_FACTS')
        self.assertEqual(receipt['restored_count'],1)
        self.assertFalse(receipt['current_membership_verified'])
        self.assertEqual(receipt['source_bundle_sha256'],checksum(bundle,sort_keys=False))
        self.assertEqual(receipt['source_stocks_sha256'],bundle['stocks_sha256'])
        self.assertEqual((stocks,bundle),before)

    def test_cannot_use_same_or_future_snapshot_or_source_alias(self):
        for as_of,key in ((SOURCE_DAY,SOURCE_KEY),('2026-10-22',SOURCE_KEY),
            (AS_OF,'collector/chips/universe-verified/latest.json')):
            with self.subTest(as_of=as_of,key=key),self.assertRaisesRegex(ValueError,'SOURCE_DATE_OR_KEY'):
                restore_archived_listing_facts(current(),donor(),as_of,key)

    def test_donor_must_pass_its_own_hash_sources_and_listing_clock(self):
        altered=donor();altered['stocks'][0]['listed']='2026-10-16'
        with self.assertRaisesRegex(ValueError,'MISMATCH'):
            restore_archived_listing_facts(current(),altered,AS_OF,SOURCE_KEY)
        incomplete=donor();incomplete['receipts'].pop()
        with self.assertRaisesRegex(ValueError,'MARKET_MISSING'):
            restore_archived_listing_facts(current(),incomplete,AS_OF,SOURCE_KEY)
        future=donor();future['stocks'][0]['listed']='2026-10-26'
        future['stocks_sha256']=hashlib.sha256(json.dumps(future['stocks'],ensure_ascii=False,
            separators=(',',':')).encode()).hexdigest()
        with self.assertRaisesRegex(ValueError,'LISTING_DATE_INVALID'):
            restore_archived_listing_facts(current(),future,AS_OF,SOURCE_KEY)

    def test_name_and_untrusted_listing_conflicts_are_not_overwritten(self):
        for field,value,reason in (('name','OTHER_INVENTED_NAME','SECURITY_NAME_CONFLICT'),
            ('listed','2026-10-16','DATE_CONFLICT')):
            stocks=current();stocks[0][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,reason):
                restore_archived_listing_facts(stocks,donor(),AS_OF,SOURCE_KEY)

    def test_current_dated_official_fact_is_preserved(self):
        stocks=current();stocks[0]['listed']='2026-10-16'
        stocks[0]['listing_evidence']={'provider':'SSE_CURRENT_LIST','security_id':'600000',
            'listed':'2026-10-16','sha256':'a'*64,'as_of':AS_OF}
        restored,receipt=restore_archived_listing_facts(stocks,donor(),AS_OF,SOURCE_KEY)
        self.assertEqual(restored,stocks)
        self.assertEqual(receipt['restored_count'],0)
        self.assertIsNone(trusted_listing(stocks[0],SOURCE_DAY))

    def test_archived_fact_remains_dated_and_tamper_evident(self):
        stock=restore_archived_listing_facts(current(),donor(),AS_OF,SOURCE_KEY)[0][0]
        self.assertIsNone(trusted_listing(stock,SOURCE_DAY))
        tampered=copy.deepcopy(stock);tampered['listing_evidence']['source_bundle_sha256']='f'*64
        self.assertIsNone(trusted_listing(tampered,AS_OF))
        for changes in ({'source_key':'collector/chips/universe-verified/latest.json'},
            {'current_membership_verified':True},{'source_as_of':AS_OF},
            {'source_stocks_sha256':'invented-non-hash'}):
            invalid=copy.deepcopy(stock);evidence=invalid['listing_evidence'];evidence.update(changes)
            evidence['sha256']=checksum({k:v for k,v in evidence.items() if k!='sha256'})
            with self.subTest(changes=changes):self.assertIsNone(trusted_listing(invalid,AS_OF))


DAYS=last_ten(AS_OF)
NO_TRADE=['2026-09-30','2026-10-08','2026-10-09','2026-10-12','2026-10-13']
CODE='002000'


def suspension(day):
    return {'date':day,'code':'sz.'+CODE,'tradestatus':'0','adjustflag':'3',
        'open':'100','high':'100','low':'100','close':'100','preclose':'100',
        'volume':'','amount':'0','turn':''}


def stability_inputs():
    row={'prefilter_status':'PASS','price_position_status':'PASS','chip_status':'READY',
        'recent_limit_up_dates':[DAYS[1]],
        'limit_evidence':[{'date':day,'status':'PASS' if day==DAYS[1] else 'FAIL'} for day in DAYS]}
    history=[{'date':day,'open':'100','high':'100','low':'100','close':'100',
        'volume_shares':'1000','amount_cny':'100000','turnover_percent':'1'}
        for day in ['2026-09-28','2026-09-29']+DAYS[1:]]
    reference={'provider':'BAOSTOCK_0.9.3','source_sha256':'a'*64,
        'rows':{day:suspension(day) for day in NO_TRADE}}
    return row,history,reference


class ReferenceSuspensionContinuity(unittest.TestCase):
    def test_full_checked_suspension_chain_carries_without_inventing_candles(self):
        row,history,reference=stability_inputs();before=copy.deepcopy((row,history,reference))
        result,evidence=classify(row,history,DAYS,security_id=CODE,reference_evidence=reference)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['evaluated_sessions'],9)
        self.assertEqual(evidence['observed_anchor_dates']['2026-10-13'],'2026-09-29')
        self.assertEqual((row,history,reference),before)

    def test_missing_link_does_not_authorize_carry(self):
        row,history,reference=stability_inputs();del reference['rows']['2026-10-08']
        result,evidence=classify(row,history,DAYS,security_id=CODE,reference_evidence=reference)
        self.assertEqual(result['reasons'],['BASELINE_NOT_AVAILABLE'])
        self.assertIsNone(evidence)

    def test_wrong_provenance_or_row_identity_cannot_fill_the_baseline(self):
        for changes in ({'provider':'INVENTED_PROVIDER'},{'source_sha256':'bad-hash'}):
            row,history,reference=stability_inputs();reference.update(changes)
            result,_=classify(row,history,DAYS,security_id=CODE,reference_evidence=reference)
            self.assertEqual(result['reasons'],['BASELINE_NOT_AVAILABLE'])
        for changes in ({'code':'sz.002001'},{'date':'2026-10-14'},
            {'volume':'100'},{'amount':'NaN'}):
            row,history,reference=stability_inputs();reference['rows'][NO_TRADE[-1]].update(changes)
            result,_=classify(row,history,DAYS,security_id=CODE,reference_evidence=reference)
            with self.subTest(changes=changes):self.assertEqual(result['reasons'],['BASELINE_NOT_AVAILABLE'])

    def test_observed_trade_conflicts_at_baseline_or_carried_anchor_stay_unknown(self):
        for day in ('2026-10-13','2026-09-29'):
            row,history,reference=stability_inputs()
            if day=='2026-10-13':
                history.append({**history[0],'date':day});history.sort(key=lambda r:r['date'])
            else:reference['rows'][day]=suspension(day)
            result,evidence=classify(row,history,DAYS,security_id=CODE,reference_evidence=reference)
            with self.subTest(day=day):
                self.assertEqual(result['status'],'UNKNOWN')
                self.assertEqual(result['reasons'],['DATA_TRADE_STATUS_CONFLICT'])
                self.assertIsNone(evidence)

    def test_existing_no_trade_and_reference_trade_conflict_remains_unknown(self):
        row,history,reference=stability_inputs();reference['rows'][NO_TRADE[-1]]['tradestatus']='1'
        result,_=classify(row,history,DAYS,security_id=CODE,no_trade=[NO_TRADE[-1]],
            reference_evidence=reference)
        self.assertEqual(result['reasons'],['DATA_TRADE_STATUS_CONFLICT'])

    def test_recovered_baseline_can_reveal_an_actual_calculated_failure(self):
        row,history,reference=stability_inputs()
        history[-1].update(open='110',high='110',low='110',close='110',amount_cny='110000',turnover_percent='50')
        result,_=classify(row,history,DAYS,security_id=CODE,reference_evidence=reference)
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['evaluated_sessions'],9)
        self.assertIn('PEAK_REGION_MOVED',result['reasons'])


if __name__=='__main__':unittest.main()
