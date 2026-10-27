"""Invented evidence-flow regressions; no vendor or cloud requests."""
import base64,copy,hashlib,json,time,unittest
from decimal import Decimal
from unittest.mock import patch
from backend.chips.runner import needs_historical_reference,restore_listing_context,_calculate
from backend.chips.screen import closing_limit_state
from test_chip_daily_v1 import DAYS,STOCK,FakeSource,Memory

MISSING='HISTORICAL_REFERENCE_CLOSE_NOT_VERIFIED'
VOLUME='REFERENCE_DAILY_VOLUME_CONFLICT'
def candidate(first=None,missing_day=None,reason=MISSING):
    return {'prefilter_status':'PASS','close_cny':'4.95','recent_limit_up_dates':[first or DAYS[-1]],
        'limit_evidence':[{'date':missing_day or DAYS[0],'status':'UNKNOWN','reason':reason}]}
SNAP={'close_cny':'4.95','model_mean_cost_cny':'4.60','peak_prices_cny':['4.50']}
def lines():
    return [f'{d},{p},{p},{p},{p},100,{amount},0,0,{delta},1.00' for d,p,amount,delta in
        [(d,'4.95' if d==DAYS[-1] else '4.50','49500' if d==DAYS[-1] else '45000','.45' if d==DAYS[-1] else '0') for d in DAYS]]
def primary(volume_conflict=False):
    return {'provider':'BAOSTOCK_0.9.3','rows':{d:{'date':d,'tradestatus':'1','isST':'0',
        'open':p,'high':p,'low':p,'close':p,'preclose':'4.50','pctChg':'10' if d==DAYS[-1] else '0',
        'volume':'10005' if volume_conflict and d==DAYS[0] else '10000',
        'amount':'49500' if d==DAYS[-1] else '45000'} for d,p in [(d,'4.95' if d==DAYS[-1] else '4.50') for d in DAYS]}}
def context(store,slot,reference=None):
    owner='INVENTED_ANCHOR_REPAIR'
    store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+600})
    return {'slot':slot,'dataset':slot,'days':DAYS,'as_of':DAYS[-1],'owner':owner,'pools':{},
        'actual_limits':{DAYS[-1]:{STOCK['code']:{'date':DAYS[-1],'close':'4.95','upper_limit':'4.95','reference_close':'4.50'}}},
        'historical_references':{STOCK['code']:reference} if reference else {}}

class AnchorAcquisition(unittest.TestCase):
    def test_later_hit_does_not_suppress_earlier_reference(self):
        self.assertTrue(needs_historical_reference(candidate(),(MISSING,),SNAP))
        self.assertTrue(needs_historical_reference(candidate(reason=VOLUME),(VOLUME,),SNAP))
    def test_irrelevant_later_unknown_and_failed_mean_do_not_fetch(self):
        self.assertFalse(needs_historical_reference(candidate(first=DAYS[0],missing_day=DAYS[-1]),(MISSING,),SNAP))
        self.assertFalse(needs_historical_reference(candidate(),(MISSING,),{**SNAP,'model_mean_cost_cny':'4.95'}))
        self.assertFalse(needs_historical_reference(candidate(),(MISSING,),None))
        self.assertFalse(needs_historical_reference({**candidate(),'prefilter_status':'FAIL'},(MISSING,),SNAP))
    def test_original_unknown_prefilter_repair_remains_available(self):
        self.assertTrue(needs_historical_reference({**candidate(),'prefilter_status':'UNKNOWN','recent_limit_up_dates':[]},(MISSING,)))
    def test_reason_and_dated_unknown_required(self):
        row=candidate();row['limit_evidence'][0]['status']='FAIL'
        self.assertFalse(needs_historical_reference(row,(MISSING,),SNAP))
        self.assertFalse(needs_historical_reference(candidate(reason=VOLUME),(MISSING,),SNAP))
    def test_full_runner_fetches_reference_after_prefilter_already_passed(self):
        class THS(FakeSource):
            def history(self,*args):
                value,receipt,raw=super().history(*args)
                return value,{**receipt,'provider':'THS_UNADJUSTED'},raw
        store=Memory();ctx=context(store,'invented-passed-later-hit')
        with patch('test_chip_daily_v1.LINES',lines()),patch('backend.chips.runner.Storage',Memory),\
            patch('backend.chips.runner.fetch_reference',return_value={'fixture':'INVENTED'}) as fetch,\
            patch('backend.chips.runner.reference_prepared',return_value=primary()):
            row=_calculate(store,THS(),STOCK,ctx)
        fetch.assert_called_once()
        self.assertEqual(row['prefilter_status'],'PASS');self.assertEqual(row['price_position_status'],'PASS')
        self.assertEqual(row['recent_limit_up_dates'],[DAYS[-1]])
        self.assertNotIn('EARLIER_LIMIT_STATUS_UNKNOWN',row['chip_stability']['reasons'])
        self.assertEqual(row['chip_stability']['evaluated_sessions'],1)
    def test_full_runner_fetches_quantity_witness_after_later_hit(self):
        p=primary(True);before=copy.deepcopy(p);code=STOCK['code'];day=DAYS[0]
        raw=json.dumps([{'status':0,'code':'cn_'+code,'hq':[[day,'4.50','4.50','0','0%','4.50','4.50','100','4.500','-']]}]).encode()
        b={'schema':'stock.chips.limit.sohu.prepared.v1','security_id':code,'as_of':DAYS[-1],
            'official_sessions':DAYS,'published':False,'raw_base64':base64.b64encode(raw).decode(),
            'receipt':{'provider':'SOHU_REPORTED_DAILY_CHANGE','url':f'https://q.stock.sohu.com/hisHq?code=cn_{code}&end={DAYS[-1].replace("-","")}',
                'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'ingested_at':'2026-10-19T01:00:00Z'}}
        store=Memory();ctx=context(store,'invented-volume-after-pass',p)
        with patch('test_chip_daily_v1.LINES',lines()),patch('backend.chips.runner.Storage',Memory),\
            patch('backend.chips.runner.fetch_reference',return_value=b) as fetch:
            row=_calculate(store,FakeSource(),STOCK,ctx)
        fetch.assert_called_once();self.assertEqual(fetch.call_args.kwargs['provider'],'SOHU_REPORTED_DAILY_CHANGE')
        self.assertEqual(row['prefilter_status'],'PASS');self.assertNotIn('EARLIER_LIMIT_STATUS_UNKNOWN',row['chip_stability']['reasons'])
        self.assertEqual(p,before)
        frame=store.data[f'data/chips/daily/{ctx["slot"]}/{code}.json']
        self.assertEqual(frame['historical_limit_reference']['primary_rows'][day]['volume'],'10005')
        self.assertEqual(frame['history'][0]['volume_shares'],'10000')
    def test_unverified_reference_is_not_a_pool_field_conflict(self):
        fields=lines()[-1].split(',');fields[8]='10'
        closing={'date':DAYS[-1],'close':'4.95','pct':'10','turnover_percent':'1.0'}
        state,reason=closing_limit_state({**STOCK,'reference_close_verified':False},fields,None,closing=closing)
        self.assertEqual((state,reason),('UNKNOWN','INCLUSIVE_LIMIT_POOL_REFERENCE_CLOSE_NOT_VERIFIED'))
        self.assertEqual(closing_limit_state(STOCK,fields,None,closing=closing)[0],'PASS')
        self.assertEqual(closing_limit_state(STOCK,fields,None,closing={**closing,'close':'4.94'})[1],
            'INCLUSIVE_LIMIT_POOL_DAILY_FIELDS_CONFLICT')

class ListingContextRecovery(unittest.TestCase):
    def test_one_name_conflict_does_not_drop_other_valid_listing_facts(self):
        from test_chip_evidence_continuity import donor,current,SOURCE_KEY,AS_OF
        from backend.chips.calendar import last_ten
        from backend.chips.listing import trusted_listing
        stocks=current()+[{'code':'600001','name':'INVENTED_CHANGED_NAME','listed':None}]
        before=copy.deepcopy(stocks);store=Memory();store.put(SOURCE_KEY,donor())
        restored,notes=restore_listing_context(store,stocks,last_ten(AS_OF),AS_OF)
        self.assertEqual(stocks,before)
        self.assertEqual([s['code'] for s in restored],[s['code'] for s in stocks])
        self.assertEqual(trusted_listing(restored[0],AS_OF),'2026-10-15')
        self.assertIsNone(trusted_listing(restored[-1],AS_OF))
        self.assertEqual(notes[-1]['conflicting_stock_count'],1)
        self.assertFalse(notes[-1]['current_membership_verified'])
    def test_corrupt_donor_cannot_restore_dates(self):
        from test_chip_evidence_continuity import donor,current,SOURCE_KEY,AS_OF
        from backend.chips.calendar import last_ten
        bundle=donor();bundle['stocks_sha256']='0'*64
        store=Memory();store.put(SOURCE_KEY,bundle)
        stocks=current();restored,notes=restore_listing_context(store,stocks,last_ten(AS_OF),AS_OF)
        self.assertEqual(restored,stocks)
        self.assertEqual(notes[0]['provider'],'ARCHIVED_LISTING_FACT_REJECTED')

if __name__=='__main__':unittest.main()
