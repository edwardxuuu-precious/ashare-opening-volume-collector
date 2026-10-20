"""Invented regression cases for historical evidence; no network or trading."""
import base64,copy,hashlib,json,time,unittest
from decimal import Decimal as D
from unittest.mock import patch
from backend.chips.limit_reference import prepared,matched,closing_pool,corroborate_quantity,archived_corroboration
from backend.chips.calendar import last_ten
from backend.chips.screen import prefilter
from backend.chips.baostock_history import normalize
from test_chip_daily_v1 import DAYS,STOCK,FakeSource,Memory
from backend.chips.runner import run,_calculate

def bundle():
    # A verified ex-rights reference of 4.00 must not be replaced by yesterday's 5.00.
    rows=[[DAYS[-1],'sz.'+STOCK['code'],'4.30','4.40','4.20','4.40','4.00','1000','4300','3','1.0','1','10.000000','1']]
    _,raw=normalize(STOCK,rows,DAYS[-1],DAYS[0])
    return {'schema':'stock.chips.limit.reference.prepared.v1','security_id':STOCK['code'],'as_of':DAYS[-1],
        'official_sessions':DAYS,'published':False,'raw_base64':base64.b64encode(raw).decode(),
        'receipt':{'provider':'BAOSTOCK_0.9.3','sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
            'adjustflag':'3','url':'https://www.baostock.com/helpDocsHome?file=stockKData.md','ingested_at':'2026-10-19T01:00:00Z'}}

class LimitReference(unittest.TestCase):
    def archived_sohu(self):
        day=DAYS[-2];code=STOCK['code']
        raw=json.dumps([{'status':0,'code':'cn_'+code,'hq':[[day,'4.00','4.00','0.00','0.00%','4.00','4.00','10','0.400','-']]}]).encode()
        return {'schema':'stock.chips.limit.sohu.prepared.v1','security_id':code,'as_of':day,'official_sessions':last_ten(day),'published':False,
            'raw_base64':base64.b64encode(raw).decode(),'receipt':{'provider':'SOHU_REPORTED_DAILY_CHANGE',
            'url':f'https://q.stock.sohu.com/hisHq?code=cn_{code}&end={day.replace("-","")}',
            'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'ingested_at':'2026-10-16T01:00:00Z'}}

    def test_archived_corroboration_keeps_original_identity_and_only_overlap(self):
        b=self.archived_sohu();before=copy.deepcopy(b)
        s=archived_corroboration(b,STOCK['code'],DAYS,expected_as_of=DAYS[-2])
        self.assertEqual(b,before);self.assertEqual(s['archived_request_as_of'],DAYS[-2])
        self.assertEqual(s['source_sha256'],b['receipt']['sha256']);self.assertEqual(s['source_url'],b['receipt']['url'])
        self.assertEqual(s['current_overlap_sessions'],[DAYS[-2]]);self.assertNotIn(DAYS[-1],s['rows'])
        lines=[f'{DAYS[-2]},4.00,4.00,4.00,4.00,10,4000,,,,1',f'{DAYS[-1]},4.00,4.40,4.40,4.00,10,4300,,,,1']
        row=prefilter({**STOCK,'reference_close_verified':False},lines,DAYS,{},reference_evidence=s)
        self.assertEqual(row['prefilter_status'],'UNKNOWN');self.assertEqual(row['limit_evidence'][-1]['status'],'UNKNOWN')

    def test_archived_corroboration_rejects_wrong_key_future_and_raw_identity(self):
        b=self.archived_sohu()
        with self.assertRaises(ValueError):archived_corroboration(b,STOCK['code'],DAYS,expected_as_of=DAYS[-3])
        with self.assertRaises(ValueError):archived_corroboration(b,STOCK['code'],DAYS,expected_as_of=DAYS[-1])
        with self.assertRaises(ValueError):archived_corroboration(b,'000002',DAYS,expected_as_of=DAYS[-2])
        wrong=copy.deepcopy(b);wrong['receipt']['sha256']='0'*64
        with self.assertRaises(ValueError):archived_corroboration(wrong,STOCK['code'],DAYS,expected_as_of=DAYS[-2])
        wrong=copy.deepcopy(b);wrong['official_sessions']=DAYS
        with self.assertRaises(ValueError):archived_corroboration(wrong,STOCK['code'],DAYS,expected_as_of=DAYS[-2])
        with self.assertRaises(ValueError):archived_corroboration(b,STOCK['code'],[],expected_as_of=DAYS[-2])

    def test_runner_uses_old_raw_overlap_without_new_network_or_relabelling(self):
        b=self.archived_sohu();raw=json.dumps([{'status':0,'code':'cn_'+STOCK['code'],
            'hq':[[DAYS[-2],'4.50','4.50','0.00','0.00%','4.50','4.50','100','4.500','-']]}]).encode()
        b.update(raw_base64=base64.b64encode(raw).decode());b['receipt'].update(sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
        primary={'provider':'BAOSTOCK_0.9.3','rows':{d:{'date':d,'tradestatus':'1','isST':'0','open':'4.50','high':'4.50',
            'low':'4.50','close':'4.50','preclose':'4.50','pctChg':'0','volume':'10001' if d==DAYS[-2] else '10000','amount':'45000'} for d in DAYS}}
        store=Memory();owner='INVENTED_ARCHIVE_TEST';slot='invented-archive-replay'
        store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+600})
        old_key=f'collector/chips/reference-corroboration/{DAYS[-2]}/{STOCK["code"]}.json';store.put(old_key,b)
        from backend.chips.runner import implementation_identity
        ctx={'slot':slot,'dataset':slot,'days':DAYS,'as_of':DAYS[-1],'owner':owner,'pools':{},'actual_limits':{},
            'historical_references':{STOCK['code']:primary},'execution_identity':implementation_identity()}
        with patch('backend.chips.runner.Storage',Memory),patch('backend.chips.runner.fetch_reference') as fresh:
            row=_calculate(store,FakeSource(),STOCK,ctx)
        self.assertEqual(row['prefilter_status'],'FAIL');fresh.assert_not_called()
        frame=store.get(f'data/chips/daily/{slot}/{STOCK["code"]}.json')[0]
        source=frame['historical_limit_reference']['corroborating_source']
        self.assertEqual(source['archived_request_as_of'],DAYS[-2]);self.assertEqual(source['private_archive_key'],old_key)
        self.assertEqual(frame['historical_limit_reference']['primary_rows'][DAYS[-2]]['volume'],'10001')
        self.assertIsNone(frame['unchanged_threshold'])
        self.assertNotIn(f'collector/chips/reference-corroboration/{DAYS[-1]}/{STOCK["code"]}.json',store.data)

    def test_ex_rights_reference_and_inclusive_st_pool_restore_positive(self):
        ref=prepared(bundle(),STOCK['code'],DAYS)
        line=f'{DAYS[-1]},4.30,4.40,4.40,4.20,10,4300,,0,-.60,1.0'
        closing={DAYS[-1]:{STOCK['code']:{'date':DAYS[-1],'close':'4.40','pct':'10','turnover_percent':'1'}}}
        stock={**STOCK,'reference_close_verified':False}
        self.assertEqual(prefilter(stock,[line],DAYS,{})['prefilter_status'],'UNKNOWN')
        result=prefilter(stock,[line],DAYS,{},reference_evidence=ref,closing_pools=closing)
        self.assertEqual(result['prefilter_status'],'PASS');self.assertEqual(result['recent_limit_up_dates'],[DAYS[-1]])
        self.assertEqual(prefilter(stock,[line],DAYS,{},reference_evidence=ref)['prefilter_status'],'UNKNOWN')

    def test_corrupt_evidence_and_source_price_conflict_cannot_exclude(self):
        b=bundle();b['receipt']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'HASH'):prepared(b,STOCK['code'],DAYS)
        ref=prepared(bundle(),STOCK['code'],DAYS)
        line=f'{DAYS[-1]},4.30,4.39,4.40,4.20,10,4300,,0,-.61,1.0'
        r=prefilter({**STOCK,'reference_close_verified':False},[line],DAYS,{},reference_evidence=ref)
        self.assertEqual(r['prefilter_status'],'UNKNOWN');self.assertIn('REFERENCE_DAILY_OHLC_CONFLICT',[e['reason'] for e in r['limit_evidence']])

    def test_explicit_no_trade_resolves_gap_but_never_erases_traded_candle(self):
        ref={'rows':{d:{'tradestatus':'0'} for d in DAYS[1:]}}
        line=f'{DAYS[0]},4.30,4.30,4.30,4.30,10,4300,,0,0,1.0'
        r=prefilter({**STOCK,'confirmed_no_trade_sessions':DAYS[1:]},[line],DAYS,{},reference_evidence=ref)
        self.assertEqual(r['close_cny'],'4.30');self.assertEqual(r['prefilter_status'],'FAIL')
        self.assertEqual(matched({'tradestatus':'0'},line.split(',')),'REFERENCE_TRADE_STATUS_CONFLICT')

    def test_sohu_reported_change_is_used_instead_of_previous_close(self):
        code='920010';raw=json.dumps([{'status':0,'code':'cn_'+code,'hq':[[DAYS[-1],'4.30','4.40','0.40','10.00%','4.20','4.40','10','0.430','-']]}]).encode()
        b={'schema':'stock.chips.limit.sohu.prepared.v1','security_id':code,'as_of':DAYS[-1],'official_sessions':DAYS,'published':False,
            'raw_base64':base64.b64encode(raw).decode(),'receipt':{'provider':'SOHU_REPORTED_DAILY_CHANGE','url':'https://q.stock.sohu.com/hisHq?code=cn_920010&end=20261016',
            'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'ingested_at':'2026-10-19T01:00:00Z'}}
        r=prepared(b,code,DAYS)['rows'][DAYS[-1]]
        self.assertEqual(r['preclose'],'4.00')
        self.assertIsNone(matched(r,[DAYS[-1],'4.30','4.40','4.40','4.20','10.47','4308','','','','']))
        wrong=copy.deepcopy(b);wrong['security_id']='920011'
        with self.assertRaisesRegex(ValueError,'REQUEST_IDENTITY'):prepared(wrong,'920011',DAYS)

    def test_repair_does_not_copy_stale_row_when_full_source_cache_is_missing(self):
        class NoPool(FakeSource):
            def pools(self,days):return {d:None for d in days},[]
        store=Memory();FakeSource.calls=0
        with patch('backend.chips.runner.Source',NoPool):
            self.assertEqual(run(store,as_of=DAYS[-1],max_minutes=2,workers=1)['screen_counts']['UNKNOWN'],1)
        key='collector/chips/model/'+STOCK['code']+'.json'
        del store.data[key];del store.version[key]
        with patch('backend.chips.runner.Source',FakeSource):
            self.assertEqual(run(store,as_of=DAYS[-1],max_minutes=2,workers=1)['screen_counts']['PASS'],1)
        self.assertEqual(FakeSource.calls,2)

    def test_quantity_corroboration_requires_exact_independent_reference_agreement(self):
        p=prepared(bundle(),STOCK['code'],DAYS);day=DAYS[-1]
        line=','.join([day,'4.30','4.40','4.40','4.20','10.05','4300','','','','1.0'])
        primary_before=copy.deepcopy(p)
        row={**p['rows'][day],'volume':'1000','volume_tolerance_shares':'100','amount_tolerance_cny':'10'}
        s={'provider':'SOHU_REPORTED_DAILY_CHANGE','rows':{day:row},'source_sha256':'2'*64}
        self.assertEqual(matched(p['rows'][day],line.split(',')),'REFERENCE_DAILY_VOLUME_CONFLICT')
        merged=corroborate_quantity(p,s,[line])
        self.assertIsNone(matched(merged['rows'][day],line.split(',')))
        self.assertEqual(p,primary_before);self.assertEqual(merged['primary_rows'],p['rows'])
        wrong=copy.deepcopy(s);wrong['rows'][day]['preclose']='4.01'
        self.assertEqual(corroborate_quantity(p,wrong,[line]),p)
        wrong=copy.deepcopy(s);wrong['rows'][day]['volume']='1200'
        self.assertEqual(corroborate_quantity(p,wrong,[line]),p)

    def test_dated_actual_price_witness_survives_unrelated_quantity_difference(self):
        ref=prepared(bundle(),STOCK['code'],DAYS);day=DAYS[-1]
        line=','.join([day,'4.30','4.40','4.40','4.20','10.05','4300','','','','1.0'])
        actual={'date':day,'close':'4.40','reference_close':'4.00','upper_limit':'4.40'}
        def check(r=ref,a=actual,l=line,stock=STOCK):
            return prefilter(stock,[l],DAYS,{},actual_limits={day:{STOCK['code']:a}},reference_evidence=r)['limit_evidence'][-1]
        self.assertEqual(check()['status'],'PASS')
        self.assertIn('ACTUAL_PRICE_WITNESS_WITH_SEPARATE_VOLUME_DIFFERENCE',check()['reason'])
        self.assertEqual(check(a={**actual,'upper_limit':'4.41'})['status'],'FAIL')
        for field,value in (('date',DAYS[-2]),('reference_close','4.01'),('close','4.39')):
            self.assertEqual(check(a={**actual,field:value})['status'],'UNKNOWN')
        self.assertEqual(check(l=line.replace('4.30','4.31',1))['status'],'UNKNOWN')
        paused=copy.deepcopy(ref);paused['rows'][day]['tradestatus']='0'
        self.assertEqual(check(r=paused)['status'],'UNKNOWN')
        self.assertEqual(check(stock={**STOCK,'confirmed_no_trade_sessions':[day]})['status'],'UNKNOWN')
        non_limit=copy.deepcopy(ref);non_limit['rows'][day].update(close='4.30',high='4.40')
        self.assertEqual(check(r=non_limit,a={},l=line.replace(',4.40,4.40,',',4.30,4.40,'))['status'],'UNKNOWN')

    def test_same_session_high_price_exclusion_requires_matching_dated_quote(self):
        import test_chip_daily_v1 as fixtures
        class NoPool(FakeSource):
            def pools(self,days):return {d:None for d in days},[]
        cases=(('13.50',DAYS[-1],'13.50',None,True),('13.50',DAYS[-2],'13.50',None,False),
            ('13.50',DAYS[-1],'13.51',None,False),('10.00',DAYS[-1],'10.00',None,True),
            ('9.99',DAYS[-1],'9.99',None,False),('13.50',DAYS[-1],'13.50','13.49',False))
        for price,quote_date,quote_close,snapshot_close,fast in cases:
            lines=[l.replace('4.50',price).replace('45000',str(D(price)*10000)) for l in fixtures.LINES]
            with self.subTest(price=price,quote_date=quote_date,quote_close=quote_close,snapshot_close=snapshot_close):
                store=Memory();FakeSource.calls=0
                with patch('test_chip_daily_v1.LINES',lines),patch('backend.chips.runner.Source',NoPool):
                    run(store,as_of=DAYS[-1],max_minutes=2,workers=1)
                    state=store.data['collector/chips/state.json'];prior=state['dataset_id']
                    if snapshot_close:
                        store.data[f'data/chips/daily/{prior}/'+STOCK['code']+'.json']['snapshots'][DAYS[-1]]['close_cny']=snapshot_close
                    model_key='collector/chips/model/'+STOCK['code']+'.json'
                    del store.data[model_key];del store.version[model_key]
                    store.data['collector/chips/lease.json']={'owner':'INVENTED_TEST','expires':time.time()+600}
                    reads=[];original_get=store.get
                    def get(key):reads.append(key);return original_get(key)
                    store.get=get
                    ctx={**state,'slot':'invented-next','dataset':'invented-next','previous_slot':prior,
                        'owner':'INVENTED_TEST','actual_limits':{DAYS[-1]:{STOCK['code']:{'date':quote_date,'close':quote_close,
                            'reference_close':str(D(price)-D('.41')),'upper_limit':'999.99'}}}}
                    row=_calculate(store,FakeSource(),STOCK,ctx)
                self.assertEqual(row['prefilter_status'],'FAIL')
                self.assertEqual(model_key not in reads,fast)
                self.assertEqual(FakeSource.calls,1 if fast else 2)
                value=store.data['data/chips/daily/invented-next/'+STOCK['code']+'.json']
                self.assertEqual(value.get('price_exclusion_reuse') is not None,fast)
                self.assertEqual(value['unchanged_threshold'],None)
