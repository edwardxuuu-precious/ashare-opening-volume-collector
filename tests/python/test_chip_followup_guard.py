"""Invented operational fixtures only; no AWS, GitHub, prices or market runs."""
import copy,gzip,io,json,unittest
from datetime import datetime,timedelta,timezone
from tooling import chip_followup_guard as g

def at(value):return datetime.fromisoformat(value+'+08:00')
def manifest(day='2026-10-23'):
    return {'schema':'stock.chips.market.manifest.v1','as_of':day,'dataset_id':'a'*16,'active_slot':'a'*16,
        'universe_total':5,'scanned_count':5,'chip_unchanged_rule':g.RULE_ID,
        'chip_stability_thresholds':copy.deepcopy(g.THRESHOLDS),'chip_stability_policy':copy.deepcopy(g.POLICY_METADATA),
        'price_filter_rule':'NO_ABSOLUTE_PRICE_FILTER_V1','price_position_rule':'CLOSE_GT_MEAN_COST_V2',
        'calculation_complete':False,'market_complete':False,'final_screen_counts':{'PASS':0,'FAIL':3,'UNKNOWN':2}}
class Clock:
    def __init__(self,value):self.value=at(value);self.waits=[]
    def __call__(self):return self.value
    def sleep(self,seconds):self.waits.append(seconds);self.value+=timedelta(seconds=seconds)

class ClockRules(unittest.TestCase):
    def test_cutoff_and_bounded_wait(self):
        cases=[('2026-10-26T15:29:59','SKIP',None),('2026-10-26T15:30:00','WAIT',600),
            ('2026-10-26T15:39:59','WAIT',1),('2026-10-26T15:39:59.500000','WAIT',1),
            ('2026-10-26T15:40:00','READY',0)]
        for value,action,wait in cases:
            with self.subTest(value=value):
                result=g.time_gate(at(value));self.assertEqual(result['action'],action)
                if wait is not None:self.assertEqual(result['wait_seconds'],wait)
    def test_utc_is_shanghai_day(self):
        now=datetime(2026,10,26,7,40,tzinfo=timezone.utc)
        self.assertEqual(g.time_gate(now)['as_of'],'2026-10-26')
        self.assertEqual(g.time_gate(now)['action'],'READY')
    def test_weekend_and_exchange_holiday_skip(self):
        for day in ('2026-10-25','2026-10-05'):
            self.assertEqual(g.time_gate(at(day+'T18:00:00'))['reason'],'NON_TRADING_DAY')
    def test_unknown_year_and_naive_clock_fail_closed(self):
        for value in (datetime(2026,10,26,18),at('2027-01-04T18:00:00')):
            with self.assertRaises(ValueError):g.time_gate(value)

class Decisions(unittest.TestCase):
    def setUp(self):self.now=at('2026-10-26T17:00:00')
    def test_missing_and_old_publication_run_today(self):
        for value in (None,manifest()):
            r=g.decide(self.now,value,None);self.assertEqual(r['action'],'RUN');self.assertEqual(r['as_of'],'2026-10-26')
    def test_today_unknowns_do_not_trigger_unlimited_replay(self):
        r=g.decide(self.now,manifest('2026-10-26'),{'expires':0})
        self.assertEqual(r['reason'],'TODAY_ALREADY_PUBLISHED')
    def test_obsolete_today_rule_needs_current_calculation(self):
        m=manifest('2026-10-26');m['chip_unchanged_rule']='INVENTED_OBSOLETE'
        self.assertEqual(g.decide(self.now,m,None)['action'],'RUN')
    def test_active_and_expired_writer(self):
        for delta,action in ((1,'SKIP'),(0,'RUN'),(-1,'RUN')):
            r=g.decide(self.now,manifest(),{'expires':self.now.timestamp()+delta})
            self.assertEqual(r['action'],action)
    def test_malformed_and_future_metadata_fail_closed(self):
        invalid=[{},manifest('2026-10-27')]
        for key,value in [('scanned_count',4),('universe_total',True),('active_slot','b'*16),('as_of','2026-02-30')]:
            m=manifest();m[key]=value;invalid.append(m)
        for m in invalid:
            with self.subTest(manifest=m),self.assertRaises(ValueError):g.decide(self.now,m,None)
        for expiry in (True,float('nan'),float('inf'),-1,'0',None):
            with self.subTest(expiry=expiry),self.assertRaises(ValueError):g.decide(self.now,manifest(),{'expires':expiry})
    def test_completion_requires_published_date_and_adopted_rules(self):
        self.assertTrue(g.verify_publication(manifest('2026-10-26'),'2026-10-26')['publication_verified'])
        for m in (None,manifest(),{}):
            with self.assertRaises(ValueError):g.verify_publication(m,'2026-10-26')

class Followup(unittest.TestCase):
    def test_before_close_never_reads_metadata(self):
        def forbidden(key):self.fail('Unexpected metadata read')
        self.assertEqual(g.run_guard(forbidden,Clock('2026-10-26T10:00:00'))['should_run'],'false')
    def test_wait_then_read_fresh_publication_and_skip(self):
        clock=Clock('2026-10-26T15:35:00');reads=[]
        def fetch(key):
            reads.append(clock());return manifest('2026-10-26') if key==g.MANIFEST else None
        r=g.run_guard(fetch,clock,clock.sleep)
        self.assertEqual(clock.waits,[300]);self.assertTrue(all(t>=at('2026-10-26T15:40:00') for t in reads))
        self.assertEqual(r['reason'],'TODAY_ALREADY_PUBLISHED')
    def test_wait_then_recheck_new_active_lease(self):
        clock=Clock('2026-10-26T15:30:00');reads=[]
        def fetch(key):
            reads.append((key,clock()))
            return manifest() if key==g.MANIFEST else {'expires':clock().timestamp()+60}
        r=g.run_guard(fetch,clock,clock.sleep);self.assertEqual(r['reason'],'CHIP_WRITER_ACTIVE')
        self.assertEqual([key for key,_ in reads],[g.MANIFEST,g.LEASE])
        self.assertTrue(all(t>=at('2026-10-26T15:40:00') for _,t in reads))
        self.assertEqual(clock.waits,[600])
    def test_wait_is_deducted_from_original_budget(self):
        clock=Clock('2026-10-26T15:30:00')
        r=g.run_guard(lambda key:manifest() if key==g.MANIFEST else None,clock,clock.sleep)
        self.assertEqual(r['should_run'],'true');self.assertEqual(r['max_minutes'],'275')
        self.assertEqual(r['wait_seconds'],600)
    def test_no_wait_retains_maximum_budget(self):
        clock=Clock('2026-10-26T17:00:00')
        r=g.run_guard(lambda key:None,clock,clock.sleep)
        self.assertEqual(r['max_minutes'],'285');self.assertEqual(clock.waits,[])
    def test_stuck_clock_exhausted_budget_and_day_rollover_stop(self):
        clock=Clock('2026-10-26T15:30:00')
        with self.assertRaises(ValueError):g.run_guard(lambda key:None,clock,lambda seconds:None)
        for seconds in (300*60,24*3600):
            clock=Clock('2026-10-26T17:00:00')
            def fetch(key):
                if key==g.MANIFEST:clock.value+=timedelta(seconds=seconds)
                return None
            with self.assertRaises(ValueError):g.run_guard(fetch,clock,clock.sleep)

class TrustAndTransport(unittest.TestCase):
    def test_trusted_completions_and_fork_rejection(self):
        event={'action':'completed','workflow_run':{'status':'completed','name':g.WORKFLOW,
            'head_branch':'main','head_repository':{'full_name':g.REPOSITORY},'conclusion':'failure'}}
        g.trusted_event(event) # A failed close collection can still hand off safely.
        for field,value in [('head_branch','topic'),('name','Other'),('status','in_progress'),
            ('head_repository',{'full_name':'INVENTED_FORK/repo'})]:
            bad=copy.deepcopy(event);bad['workflow_run'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):g.trusted_event(bad)
    def test_bounded_plain_and_compressed_metadata(self):
        class Client:
            def __init__(self,raw):self.raw=raw
            def get_object(self,**kwargs):return {'Body':io.BytesIO(self.raw)}
        value=manifest();raw=json.dumps(value).encode()
        for b in (raw,gzip.compress(raw)):
            self.assertEqual(g.read_metadata(Client(b),'INVENTED',g.MANIFEST),value)
        for b in (b' '*1000001,gzip.compress(b' '*1000001)):
            with self.assertRaises(ValueError):g.read_metadata(Client(b),'INVENTED',g.MANIFEST)
    def test_access_denied_is_not_treated_as_missing(self):
        class Missing(Exception):response={'Error':{'Code':'NoSuchKey'}}
        class Denied(Exception):response={'Error':{'Code':'AccessDenied'}}
        class Client:
            def __init__(self,error):self.error=error
            def get_object(self,**kwargs):raise self.error()
        self.assertIsNone(g.read_metadata(Client(Missing),'INVENTED',g.MANIFEST))
        with self.assertRaises(Denied):g.read_metadata(Client(Denied),'INVENTED',g.MANIFEST)

if __name__=='__main__':unittest.main()
