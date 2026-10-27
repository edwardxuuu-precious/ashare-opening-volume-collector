"""Invented source/date/conflict fixtures; no cloud, market reads or dispatches."""
import base64,copy,hashlib,json,time,unittest
from decimal import Decimal as D
from unittest.mock import patch
from backend.chips.calendar import last_ten
from backend.chips.baostock_history import normalize
from backend.chips.limit_reference import prepared,archived_reference,merge_reference_evidence,matched
from backend.chips import runner
from backend.chips.stability import classify,_reference_no_trade
from test_chip_daily_v1 import Memory

DAY='2026-10-27';DAYS=last_ten(DAY);OLD='2026-10-26';OLD_DAYS=last_ten(OLD)
CODE='000607';STOCK={'code':CODE,'name':'ALL_VALUES_INVENTED','listed':'2000-01-01'}
KEY=f'collector/chips/reference-daily/{OLD}/{CODE}.json'


def record(day,*,close='10',reference='10',volume='1000',amount='10000'):
    return [day,'sz.'+CODE,close,close,close,close,reference,volume,amount,'3','1','1',
        str((D(close)/D(reference)-1)*100),'0']


def bundle(day=OLD,rows=None):
    days=last_ten(day)
    _,raw=normalize(STOCK,rows if rows is not None else [record(d) for d in days],day,days[0])
    return {'schema':'stock.chips.limit.reference.prepared.v1','security_id':CODE,'as_of':day,
        'official_sessions':days,'published':False,'raw_base64':base64.b64encode(raw).decode(),
        'receipt':{'provider':'BAOSTOCK_0.9.3','adjustflag':'3','bytes':len(raw),
            'sha256':hashlib.sha256(raw).hexdigest(),'url':'https://www.baostock.com/helpDocsHome?file=stockKData.md',
            'ingested_at':'2026-10-27T00:00:00Z'}}


def alter_raw(value,change):
    result=copy.deepcopy(value);raw=json.loads(base64.b64decode(result['raw_base64']))
    change(raw);encoded=json.dumps(raw,separators=(',',':')).encode()
    result['raw_base64']=base64.b64encode(encoded).decode()
    result['receipt'].update(bytes=len(encoded),sha256=hashlib.sha256(encoded).hexdigest())
    return result


def archived(value=None,day=OLD):
    return archived_reference(value or bundle(day),CODE,DAYS,expected_as_of=day,
        private_key=f'collector/chips/reference-daily/{day}/{CODE}.json')


class OriginalIdentity(unittest.TestCase):
    def test_original_request_hash_dates_and_only_observed_overlap_are_retained(self):
        value=bundle();before=copy.deepcopy(value);evidence=archived(value)
        self.assertEqual(value,before)
        self.assertEqual(evidence['source_sha256'],value['receipt']['sha256'])
        self.assertEqual(evidence['archived_request_as_of'],OLD)
        self.assertEqual(evidence['archived_official_sessions'],OLD_DAYS)
        self.assertEqual(sorted(evidence['rows']),DAYS[:-1]);self.assertNotIn(DAY,evidence['rows'])
        for day,sources in evidence['row_sources'].items():
            self.assertEqual(sources[0]['source_as_of'],OLD)
            self.assertEqual(sources[0]['official_sessions'],OLD_DAYS)
            self.assertEqual(sources[0]['private_source_key'],KEY)
            self.assertEqual(sources[0]['source_sha256'],value['receipt']['sha256'])
            self.assertEqual(evidence['rows'][day]['date'],day)

    def test_wrong_code_hash_asof_and_archive_key_are_rejected(self):
        wrong=bundle();wrong['receipt']['sha256']='0'*64
        with self.assertRaises(ValueError):archived(wrong)
        wrong=bundle();wrong['security_id']='000608'
        with self.assertRaises(ValueError):archived(wrong)
        wrong=bundle();wrong['as_of']=DAYS[-3]
        with self.assertRaises(ValueError):archived(wrong)
        with self.assertRaises(ValueError):
            archived_reference(bundle(),CODE,DAYS,expected_as_of=OLD,private_key=KEY.replace(CODE,'000608'))

    def test_current_future_or_outside_nine_session_archive_cannot_be_projected(self):
        for day in (DAY,'2026-10-28','2026-10-13'):
            with self.subTest(day=day),self.assertRaises(ValueError):archived(bundle(day),day=day)

    def test_raw_future_row_or_changed_original_request_window_are_rejected(self):
        future=alter_raw(bundle(),lambda raw:raw['original_sdk_response']['rows'][-1].__setitem__(0,DAY))
        start=alter_raw(bundle(),lambda raw:raw['original_sdk_response'].__setitem__('start_date',OLD_DAYS[1]))
        end=alter_raw(bundle(),lambda raw:raw['original_sdk_response'].__setitem__('end_date',DAY))
        for value in (future,start,end):
            with self.assertRaises(ValueError):archived(value)

    def test_sohu_archive_is_outside_this_baostock_only_reuse(self):
        value=bundle();value['schema']='stock.chips.limit.sohu.prepared.v1'
        with self.assertRaises(ValueError):archived(value)


class DateMerging(unittest.TestCase):
    def test_current_values_win_and_conflict_is_exposed_without_mutation(self):
        current=prepared(bundle(DAY),CODE,DAYS,private_key=f'collector/chips/reference-daily/{DAY}/{CODE}.json')
        donor=archived(bundle(rows=[record(DAYS[0],reference='9')]))
        before=copy.deepcopy((current,donor));merged=merge_reference_evidence(current,[donor])
        self.assertEqual(merged['source_sha256'],current['source_sha256'])
        self.assertEqual(merged['rows'][DAYS[0]]['preclose'],'10')
        self.assertEqual(matched(merged['rows'][DAYS[0]],[DAYS[0],'10','10','10','10','10','10000','','','','1']),
            'REFERENCE_ARCHIVE_CONFLICT')
        self.assertIn(DAYS[0],merged['archive_conflicts'])
        self.assertEqual(len(merged['row_sources'][DAYS[0]]),2)
        self.assertEqual((current,donor),before)

    def test_two_old_sources_disagree_and_neither_winner_can_hide_conflict(self):
        one=archived();older='2026-10-23'
        two=archived(bundle(older,rows=[record(DAYS[0],volume='1001')]),day=older)
        for donors in ([one,two],[two,one]):
            merged=merge_reference_evidence(None,donors)
            self.assertTrue(merged['rows'][DAYS[0]]['archive_conflict'])
            self.assertIn(DAYS[0],merged['archive_conflicts'])
            self.assertEqual(len(merged['row_sources'][DAYS[0]]),2)

    def test_equivalent_decimal_spelling_is_not_a_new_source_conflict(self):
        one=archived();older='2026-10-23'
        two=archived(bundle(older,rows=[record(DAYS[0],close='10.0000')]),day=older)
        merged=merge_reference_evidence(None,[one,two])
        self.assertFalse(merged['archive_conflicts'])

    def test_reused_row_must_still_match_current_model_ohlc_and_quantities(self):
        evidence=archived();row=evidence['rows'][DAYS[0]]
        fields=[DAYS[0],'10','10','10','10','10','10000','','','','1']
        self.assertIsNone(matched(row,fields))
        for index,value,reason in ((1,'9.99','REFERENCE_DAILY_OHLC_CONFLICT'),
                (5,'10.01','REFERENCE_DAILY_VOLUME_CONFLICT'),(6,'9998','REFERENCE_DAILY_AMOUNT_CONFLICT')):
            bad=list(fields);bad[index]=value
            self.assertEqual(matched(row,bad),reason)


class RunnerReuse(unittest.TestCase):
    def calculate(self,store,fetch):
        owner='INVENTED_ARCHIVE_TEST';store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+600})
        class THS:
            def history(self,stock,day,cached=None):
                raw=json.dumps({'rc':0,'data':{'code':CODE,'klines':[
                    f'{d},10,10,10,10,10,10000,,0,0,1' for d in DAYS]}}).encode()
                return json.loads(raw),{'provider':'THS_UNADJUSTED','sha256':hashlib.sha256(raw).hexdigest()},raw
        actual={d:{CODE:{'date':d,'close':'10','reference_close':'10','upper_limit':'11'}} for d in DAYS[2:]}
        context={'slot':'a'*16,'dataset':'a'*16,'days':DAYS,'as_of':DAY,'owner':owner,
            'pools':{},'actual_limits':actual,'execution_identity':runner.implementation_identity()}
        reads=[];get=store.get
        def tracked(key):reads.append(key);return get(key)
        with (patch.object(runner,'Storage',Memory),patch.object(runner,'fetch_reference',side_effect=fetch) as fetched,
                patch.object(store,'get',side_effect=tracked)):
            result=runner._calculate(store,THS(),STOCK,context)
        frame=store.get(f'data/chips/daily/{"a"*16}/{CODE}.json')[0]
        return result,frame,fetched,reads

    def test_prior_originals_resolve_needed_dates_without_market_read(self):
        store=Memory();store.put(KEY,bundle())
        row,frame,fetch,reads=self.calculate(store,AssertionError('No market fetch expected'))
        fetch.assert_not_called();self.assertEqual(row['prefilter_status'],'FAIL')
        evidence=frame['historical_limit_reference']
        self.assertEqual(evidence['row_sources'][DAYS[0]][0]['private_source_key'],KEY)
        self.assertNotIn(DAY,evidence['rows'])
        archive_reads=[key for key in reads if key.startswith('collector/chips/reference-daily/') and key.endswith('/'+CODE+'.json')]
        self.assertLessEqual(len(archive_reads),10)
        self.assertEqual([h['close'] for h in frame['history']],['10']*10)

    def test_no_archive_uses_original_fetch_path(self):
        row,frame,fetch,_=self.calculate(Memory(),lambda *_args,**_kwargs:bundle(DAY))
        fetch.assert_called_once();self.assertEqual(row['prefilter_status'],'FAIL')
        self.assertEqual(frame['historical_limit_reference']['row_sources'][DAY][0]['source_as_of'],DAY)

    def test_archive_without_required_dates_still_fetches_current_reference(self):
        store=Memory();store.put(KEY,bundle(rows=[record(DAYS[2])]))
        row,frame,fetch,_=self.calculate(store,lambda *_args,**_kwargs:bundle(DAY))
        fetch.assert_called_once();self.assertEqual(row['prefilter_status'],'FAIL')
        self.assertEqual(frame['historical_limit_reference']['row_sources'][DAY][0]['source_as_of'],DAY)

    def test_independently_complete_current_cache_avoids_unnecessary_old_reads(self):
        store=Memory();current_key=f'collector/chips/reference-daily/{DAY}/{CODE}.json'
        store.put(current_key,bundle(DAY));store.put(KEY,bundle())
        row,_,fetch,reads=self.calculate(store,AssertionError('Complete current original already exists'))
        fetch.assert_not_called();self.assertEqual(row['prefilter_status'],'FAIL')
        self.assertNotIn(KEY,reads)

    def test_current_ohlc_conflict_is_not_washed_by_an_older_matching_source(self):
        store=Memory();rows=[record(d,close='10.01') if d==DAYS[0] else record(d) for d in DAYS]
        store.put(f'collector/chips/reference-daily/{DAY}/{CODE}.json',bundle(DAY,rows=rows))
        store.put(KEY,bundle())
        row,_,fetch,_=self.calculate(store,AssertionError('Cannot select a convenient old value'))
        fetch.assert_not_called();self.assertEqual(row['prefilter_status'],'UNKNOWN')
        self.assertEqual(row['limit_evidence'][0]['reason'],'REFERENCE_DAILY_OHLC_CONFLICT')

    def test_archive_ohlc_conflict_with_current_model_stays_unknown(self):
        store=Memory();store.put(KEY,bundle(rows=[record(d,close='10.01') if d==DAYS[0] else record(d) for d in OLD_DAYS]))
        row,_,fetch,_=self.calculate(store,AssertionError('Model conflict cannot become a pass'))
        fetch.assert_not_called();self.assertEqual(row['prefilter_status'],'UNKNOWN')
        self.assertEqual(row['limit_evidence'][0]['reason'],'REFERENCE_DAILY_OHLC_CONFLICT')

    def test_conflicting_old_originals_stay_unknown_without_trying_a_convenient_fresh_winner(self):
        store=Memory();store.put(KEY,bundle())
        older='2026-10-23';store.put(f'collector/chips/reference-daily/{older}/{CODE}.json',
            bundle(older,rows=[record(DAYS[0],reference='9')]))
        row,frame,fetch,_=self.calculate(store,AssertionError('Conflict must remain visible'))
        fetch.assert_not_called();self.assertEqual(row['prefilter_status'],'UNKNOWN')
        self.assertEqual(row['limit_evidence'][0]['reason'],'REFERENCE_ARCHIVE_CONFLICT')
        self.assertTrue(frame['historical_limit_reference']['archive_conflicts'])


class TemporalConflicts(unittest.TestCase):
    def inputs(self,*,early_failure=False):
        first=DAYS[-3]
        row={'prefilter_status':'PASS','price_position_status':'PASS','chip_status':'READY',
            'recent_limit_up_dates':[first],
            'limit_evidence':[{'date':d,'status':'PASS' if d==first else 'FAIL'} for d in DAYS]}
        history=[{'date':d,'open':'100','high':'100','low':'100','close':'100',
            'volume_shares':'1000','amount_cny':'100000','turnover_percent':'.01'} for d in DAYS]
        if early_failure:
            history[-3].update(open='110',high='110',low='110',close='110',amount_cny='110000',turnover_percent='50')
        reference={'provider':'BAOSTOCK_0.9.3','source_sha256':'a'*64,'rows':{
            DAYS[-2]:{'date':DAYS[-2],'code':'sz.'+CODE,'adjustflag':'3','tradestatus':'0',
                'volume':'0','amount':'0','turn':'0','archive_conflict':True}}}
        return row,history,reference

    def test_conflict_after_first_hit_preserves_earlier_supported_failure(self):
        row,history,reference=self.inputs(early_failure=True)
        result,evidence=classify(row,history,DAYS,security_id=CODE,reference_evidence=reference)
        self.assertEqual(result['status'],'FAIL');self.assertEqual(result['evaluated_sessions'],1)
        self.assertIn('ANCHOR_BAND_MASS_DECREASED',result['reasons'])
        self.assertEqual(evidence['data_conflicts'][DAYS[-2]],'DATA_REFERENCE_ARCHIVE_CONFLICT')

    def test_earlier_pass_then_conflict_cannot_carry_zero_trade_or_pass(self):
        row,history,reference=self.inputs();history=[h for h in history if h['date']!=DAYS[-2]]
        self.assertNotIn(DAYS[-2],_reference_no_trade(reference,CODE,DAY))
        result,evidence=classify(row,history,DAYS,security_id=CODE,reference_evidence=reference)
        self.assertEqual(result['status'],'UNKNOWN');self.assertEqual(result['evaluated_sessions'],1)
        self.assertIn('DATA_REFERENCE_ARCHIVE_CONFLICT',result['reasons'])
        self.assertNotIn(DAYS[-2],evidence['observed_anchor_dates'])

    def test_conflicting_baseline_or_pre_first_unknown_never_passes(self):
        row,history,reference=self.inputs();baseline=DAYS[-4]
        reference['rows']={baseline:{'archive_conflict':True}}
        result,_=classify(row,history,DAYS,security_id=CODE,reference_evidence=reference)
        self.assertEqual(result['reasons'],['DATA_REFERENCE_ARCHIVE_CONFLICT'])
        row['limit_evidence'][0]['status']='UNKNOWN'
        result,_=classify(row,history,DAYS,security_id=CODE,reference_evidence=reference)
        self.assertEqual(result['reasons'],['EARLIER_LIMIT_STATUS_UNKNOWN'])


if __name__=='__main__':unittest.main()
