"""Invented exact-date positive-volume overlays; no external calls."""
import hashlib,json,time,unittest
from backend.chips.current_quote import daily_line,overlay
from backend.chips.runner import frame
from backend.chips.calendar import last_ten
from backend.chips.runner import _calculate,implementation_identity
from backend.chips.eastmoney_model import prepare_eastmoney,estimate_snapshots
from test_chip_daily_v1 import Memory


def quote():
    f=['0']*55
    for i,v in {2:'000573',3:'4.31',4:'4.47',5:'4.70',6:'1000',30:'20261019161457',31:'-0.16',32:'-3.58',33:'4.72',34:'4.20',35:'4.31/1000/445000',36:'1000',37:'44.50',38:'18.07',43:'11.63',47:'4.92'}.items():f[i]=v
    body='~'.join(f)
    return {'date':'2026-10-19','raw_quote_body':body,'raw_quote_sha256':hashlib.sha256(body.encode('gb18030')).hexdigest(),'daily_line':daily_line('000573','2026-10-19',body),'close':'4.31','reference_close':'4.47','upper_limit':'4.92'}


class CurrentQuote(unittest.TestCase):
    def test_star_volume_uses_supplied_shares_not_a_hundredfold_conversion(self):
        f=quote()['raw_quote_body'].split('~');f[2]='688001';f[6]=f[36]='100000';f[35]='4.31/100000/445000'
        self.assertEqual(daily_line('688001','2026-10-19','~'.join(f)).split(',')[5],'1000')
    def test_validated_prior_prefix_plus_final_quote_matches_full_cold_history(self):
        store=Memory();owner='ALL_VALUES_INVENTED';store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+600})
        stock={'code':'000573','name':'ALL_VALUES_INVENTED'}
        lines=[f'{d},4.47,4.47,4.50,4.40,1000,447000,,0,0,3' for d in last_ten('2026-10-16')]
        class Source:
            calls=0
            def history(self,*args,**kwargs):
                self.calls+=1
                if self.calls>1:raise AssertionError('Public history must not repeat for a validated one-session append')
                raw=json.dumps({'rc':0,'data':{'code':'000573','name':stock['name'],'klines':lines}}).encode()
                return json.loads(raw),{'provider':'EASTMONEY','sha256':hashlib.sha256(raw).hexdigest()},raw
        source=Source()
        def ctx(day,batch):return {'slot':batch,'dataset':batch,'days':last_ten(day),'as_of':day,'owner':owner,'pools':{},'actual_limits':{},'execution_identity':implementation_identity()}
        _calculate(store,source,stock,ctx('2026-10-16','aaaaaaaaaaaaaaaa'))
        context=ctx('2026-10-19','bbbbbbbbbbbbbbbb');context['actual_limits']={'2026-10-19':{'000573':quote()}}
        row=_calculate(store,source,stock,context);self.assertEqual(row['chip_status'],'READY');self.assertEqual(source.calls,1)
        published=store.get('data/chips/daily/bbbbbbbbbbbbbbbb/000573.json')[0]
        full=json.dumps({'rc':0,'data':{'code':'000573','name':stock['name'],'klines':lines+[quote()['daily_line']]}}).encode()
        bars,_=prepare_eastmoney(full,expected_code='000573',cutoff='2026-10-19');cold=estimate_snapshots(bars,['2026-10-16','2026-10-19'],security_id='000573')
        for day,s in published['snapshots'].items():
            self.assertEqual({k:v for k,v in s.items() if k not in ('comparison_anchor_shares_percent','comparison_anchor_definition')},cold[day])
        self.assertEqual(published['source']['previous_session_cached_prefix_date'],'2026-10-16')

    def test_real_bar_replaces_only_false_no_trade_claim_and_keeps_both_sources(self):
        value={'rc':0,'data':{'code':'000573','name':'ALL_VALUES_INVENTED','klines':[
            '2026-10-15,4.20,4.20,4.30,4.10,1000,420000,,0,0,2',
            '2026-10-16,4.47,4.47,4.47,4.47,1000,447000,,6.43,0.27,3']},
            'confirmed_no_trade_sessions':['2026-10-19'],'original_source_utf8':'INVENTED_ORIGINAL'}
        raw=json.dumps(value).encode();q=quote();stock={'code':'000573','name':'ALL_VALUES_INVENTED'}
        new,receipt=overlay(raw,{'provider':'THS_UNADJUSTED'},stock,'2026-10-19',q)
        decoded=json.loads(new)
        self.assertEqual(decoded['confirmed_no_trade_sessions'],[])
        self.assertEqual(decoded['original_source_utf8'],'INVENTED_ORIGINAL')
        self.assertTrue(decoded['current_session_quote_overlay']['historical_zero_volume_placeholder_overridden'])
        row,out=frame(stock,new,receipt,last_ten('2026-10-19'),{},'INVENTED',actual_limits={'2026-10-19':{'000573':q}})
        self.assertEqual(row['chip_status'],'READY')
        self.assertEqual(out['snapshots']['2026-10-19']['close_cny'],'4.31')
        self.assertNotIn('market_session_status',out['snapshots']['2026-10-19'])
        self.assertEqual(out['history'][-1]['volume_shares'],'100000')

    def test_zero_or_stale_quote_cannot_make_a_candle(self):
        q=quote()
        with self.assertRaisesRegex(ValueError,'DATE'):
            daily_line('000573','2026-10-20',q['raw_quote_body'])
        f=q['raw_quote_body'].split('~');f[6]=f[36]='0';f[35]='4.31/0/0'
        self.assertIsNone(daily_line('000573','2026-10-19','~'.join(f)))
        f=q['raw_quote_body'].split('~');f[30]='20261019250000'
        with self.assertRaisesRegex(ValueError,'DATE_TIME'):
            daily_line('000573','2026-10-19','~'.join(f))

    def test_hash_or_trade_identity_conflict_is_rejected(self):
        q=quote();q['raw_quote_body']+='MUTATED'
        raw=json.dumps({'data':{'code':'000573','klines':[]}}).encode()
        with self.assertRaisesRegex(ValueError,'HASH'):
            overlay(raw,{}, {'code':'000573'},'2026-10-19',q)
        q=quote();f=q['raw_quote_body'].split('~');f[36]='999'
        with self.assertRaisesRegex(ValueError,'TRIPLE'):
            daily_line('000573','2026-10-19','~'.join(f))

    def test_positive_trade_with_invalid_turnover_cannot_silently_carry_yesterday(self):
        q=quote();f=q['raw_quote_body'].split('~');f[38]=''
        q['raw_quote_body']='~'.join(f)
        q['raw_quote_sha256']=hashlib.sha256(q['raw_quote_body'].encode('gb18030')).hexdigest()
        q.pop('daily_line');q['daily_line_error']='INVALID_TURNOVER'
        raw=json.dumps({'data':{'code':'000573','klines':[]},'confirmed_no_trade_sessions':['2026-10-19']}).encode()
        with self.assertRaises(ArithmeticError):
            overlay(raw,{}, {'code':'000573'},'2026-10-19',q)

    def test_positive_volume_without_amount_is_rejected(self):
        q=quote();f=q['raw_quote_body'].split('~');f[35]='4.31/1000/0'
        with self.assertRaisesRegex(ValueError,'VOLUME_AMOUNT'):
            daily_line('000573','2026-10-19','~'.join(f))

    def test_final_quote_replaces_stale_intraday_row_and_preserves_original(self):
        old='2026-10-19,4.70,4.50,4.72,4.40,900,410000,,0,0,16.20'
        raw=json.dumps({'data':{'code':'000573','klines':[old]}}).encode();q=quote()
        new,_=overlay(raw,{}, {'code':'000573'},'2026-10-19',q);v=json.loads(new)
        self.assertEqual(v['data']['klines'],[q['daily_line']])
        self.assertEqual(v['current_session_quote_overlay']['replaced_current_history_line'],old)
        self.assertEqual(overlay(new,{}, {'code':'000573'},'2026-10-19',q)[0],new)

    def test_missing_or_zero_quote_does_not_certify_ths_placeholder_as_suspension(self):
        raw=json.dumps({'data':{'code':'000573','klines':[]},'confirmed_no_trade_sessions':['2026-10-19']}).encode()
        q=quote();f=q['raw_quote_body'].split('~');f[6]=f[36]='0';f[35]='4.31/0/0';q['raw_quote_body']='~'.join(f);q['daily_line']=None
        q['raw_quote_sha256']=hashlib.sha256(q['raw_quote_body'].encode('gb18030')).hexdigest()
        for value in (None,q):
            new,_=overlay(raw,{'provider':'THS_UNADJUSTED'}, {'code':'000573'},'2026-10-19',value)
            self.assertEqual(json.loads(new)['confirmed_no_trade_sessions'],[])
            self.assertEqual(json.loads(new)['unconfirmed_no_trade_placeholder_dates'],['2026-10-19'])

    def test_current_nonzero_ths_row_without_final_quote_is_not_an_eod_candle(self):
        old='2026-10-19,4.70,4.50,4.72,4.40,900,410000,,0,0,16.20'
        raw=json.dumps({'data':{'code':'000573','klines':[old]},'confirmed_no_trade_sessions':[]}).encode()
        new,_=overlay(raw,{'provider':'THS_UNADJUSTED'}, {'code':'000573'},'2026-10-19',None,require_final_quote=True)
        self.assertEqual(json.loads(new)['data']['klines'],[])
        self.assertEqual(json.loads(new)['unconfirmed_current_session_history_lines'],[old])
        self.assertEqual(overlay(raw,{'provider':'THS_UNADJUSTED'}, {'code':'000573'},'2026-10-19',None)[0],raw)
