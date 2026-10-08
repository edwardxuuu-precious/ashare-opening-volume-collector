import _bootstrap
"""Publication boundary tests; no network or AWS credentials required."""
import hashlib, importlib.util, io, json, sys, tempfile, unittest
from unittest.mock import patch
from pathlib import Path
spec=importlib.util.spec_from_file_location('cloud_worker', Path(__file__).resolve().parents[2]/'backend/server/cloud_worker.py')
w=importlib.util.module_from_spec(spec); spec.loader.exec_module(w)

class FakeS3:
    class exceptions:
        class NoSuchKey(Exception): pass
    def __init__(self, manifest=None):
        self.objects={} if manifest is None else {'data/manifest.json': json.dumps(manifest).encode()}
        self.metadata={};self.writes=[];self.write_args=[];self.heads=[]
    def etag(self,key):return '"'+hashlib.md5(self.objects[key]).hexdigest()+'"'
    def get_object(self, Bucket, Key):
        if Key not in self.objects: raise self.exceptions.NoSuchKey()
        return {'Body': io.BytesIO(self.objects[Key])}
    def put_object(self, **args):
        key=args['Key']
        if args.get('IfNoneMatch')=='*' and key in self.objects:raise RuntimeError('precondition')
        if args.get('IfMatch') and (key not in self.objects or args['IfMatch']!=self.etag(key)):raise RuntimeError('precondition')
        self.objects[args['Key']]=args['Body']; self.writes.append(args['Key'])
        self.metadata[key]=args.get('Metadata',{});self.write_args.append(args)
    def head_object(self,Bucket,Key):
        self.heads.append(Key)
        if Key not in self.objects:raise self.exceptions.NoSuchKey()
        return {'ETag':self.etag(Key),'Metadata':self.metadata.get(Key,{})}
    def upload_file(self,path,bucket,key,**kwargs):
        self.objects[key]=Path(path).read_bytes();self.metadata[key]=kwargs.get('ExtraArgs',{}).get('Metadata',{})

class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)/'data'; self.root.mkdir()
        self.day=w.now()[:10]; self.before='2026-01-01'
        self.entry={'date': self.day, 'file':self.day+'.json', 'generatedAt':'v1', 'valid':1, 'total':1}
        self.payload={'date':self.day, 'generatedAt':'v1', 'rows':[{'code':'000001','status':'ok','quality':'matched','dayVolumeDifference':0,'first15Volume':15,'dailyVolume':100,'ratio':15}]}
        self.write('manifest.json', {'dates':[self.entry], 'generatedAt':'v1'})
        self.write(self.day+'.json', self.payload)
    def tearDown(self): self.temp.cleanup()
    def write(self, name, value): (self.root/name).write_text(json.dumps(value))
    def test_date_uploaded_before_manifest(self):
        client=FakeS3(); publisher=w.Publisher(client,'bucket',self.root)
        self.assertEqual(publisher.publish(),1)
        self.assertEqual(client.writes, ['data/'+self.day+'.json','data/manifest.json'])
        self.assertEqual(client.write_args[0]['IfNoneMatch'],'*')
        self.assertEqual(client.write_args[1]['IfNoneMatch'],'*')
        self.assertEqual(client.metadata['data/'+self.day+'.json']['sha256'],
                         hashlib.sha256(client.objects['data/'+self.day+'.json']).hexdigest())
    def test_existing_manifest_is_replaced_only_at_its_observed_etag(self):
        client=FakeS3({'dates':[],'generatedAt':'old'})
        w.Publisher(client,'bucket',self.root).publish()
        manifest_write=next(args for args in client.write_args if args['Key']=='data/manifest.json')
        self.assertIn('IfMatch',manifest_write)
    def test_conflicting_ratio_rejected_before_upload(self):
        self.payload['rows'][0]['dayVolumeDifference']=-1; self.write(self.day+'.json',self.payload)
        client=FakeS3(); publisher=w.Publisher(client,'bucket',self.root)
        with self.assertRaises(ValueError): publisher.publish()
        self.assertFalse(client.writes)
    def test_unverified_reference_can_be_preserved(self):
        self.payload['rows'][0].update(status='unverified',quality='source_difference',ratio=None,referenceRatio=15,dayVolumeDifference=-1)
        self.write(self.day+'.json',self.payload)
        client=FakeS3(); self.assertEqual(w.Publisher(client,'bucket',self.root).publish(),1)
    def test_generation_mismatch_defers_entire_batch(self):
        self.payload['generatedAt']='v2'; self.write(self.day+'.json',self.payload)
        client=FakeS3(); self.assertEqual(w.Publisher(client,'bucket',self.root).publish(),0)
        self.assertFalse(client.writes)
    def test_new_unuploaded_history_never_referenced(self):
        entry={'date': self.day[:-2]+'01', 'file':self.day[:-2]+'01.json','generatedAt':'v1'}
        if entry['date']==self.day: self.skipTest('First day of month')
        self.write('manifest.json',{'dates':[self.entry,entry]})
        client=FakeS3(); w.Publisher(client,'bucket',self.root).publish()
        published=json.loads(client.objects['data/manifest.json'])
        self.assertEqual(len(published['dates']),1)
    def test_old_uploaded_history_remains_until_replaced(self):
        old={'date':self.day[:-2]+'01','file':self.day[:-2]+'01.json','generatedAt':'old'}
        if old['date']==self.day:self.skipTest('First day of month')
        client=FakeS3({'dates':[old]}); w.Publisher(client,'bucket',self.root).publish()
        published=json.loads(client.objects['data/manifest.json'])
        self.assertEqual(len(published['dates']),2)
    def test_publishing_new_day_keeps_archived_directory_without_reading_or_uploading_history(self):
        old={'date':'2020-01-02','file':'2020-01-02.json','generatedAt':'archive-v1'}
        client=FakeS3({'dates':[old]})
        archive=b'original archived bytes';client.objects['data/2020-01-02.json']=archive
        publisher=w.Publisher(client,'bucket',self.root)
        publisher.publish(all_dates=True)
        publisher.expire()
        published=json.loads(client.objects['data/manifest.json'])
        self.assertIn(old,published['dates'])
        self.assertIn('2020-01-02',published['tradingDates'])
        self.assertEqual(published['retentionStart'],'2020-01-02')
        self.assertEqual(published['retentionPolicy'],'accumulate')
        self.assertEqual(client.objects['data/2020-01-02.json'],archive)
        self.assertEqual(client.writes,['data/'+self.day+'.json','data/manifest.json'])

    def test_public_manifest_omits_internal_full_calendar(self):
        self.write('manifest.json', {
            'dates':[self.entry],
            'generatedAt':'v1',
            'calendarDates':['2020-01-01']*9000,
            'tradingDates':[self.day],
        })
        client=FakeS3(); w.Publisher(client,'bucket',self.root).publish()
        published=json.loads(client.objects['data/manifest.json'])
        self.assertNotIn('calendarDates',published)
        self.assertEqual(published['tradingDates'],[self.day])
        self.assertLess(len(client.objects['data/manifest.json']),2000)

    def test_bad_path_rejected(self):
        self.entry['file']='../secret.json'; self.write('manifest.json',{'dates':[self.entry]})
        client=FakeS3()
        with self.assertRaises(ValueError): w.Publisher(client,'bucket',self.root).publish()
        self.assertFalse(client.writes)
    def test_status_matches_authenticated_api_contract(self):
        client=FakeS3(); publisher=w.Publisher(client,'bucket',self.root)
        publisher.publish_status({'status':'running','dates':[self.day],'completedStocks':50,'totalStocks':5556})
        actual=json.loads(client.objects['data/collection-status.json'])
        self.assertEqual(actual['state'],'running'); self.assertEqual(actual['latestDate'],self.day)
    def test_public_status_omits_large_internal_calendar(self):
        client=FakeS3(); publisher=w.Publisher(client,'bucket',self.root)
        publisher.publish_status({'status':'running','dates':[self.day],
            'completedStocks':50,'totalStocks':5556,
            'calendarDates':['2020-01-01']*9000,
            'calendarValidThrough':self.day,
            'publicationCommitted':True})
        actual=json.loads(client.objects['data/collection-status.json'])
        self.assertNotIn('calendarDates',actual)
        self.assertNotIn('calendarValidThrough',actual)
        self.assertTrue(actual['publicationCommitted'])
        self.assertLess(len(client.objects['data/collection-status.json']),2000)
    def test_opening_status_separates_target_from_already_published_date(self):
        published = {'date':'2026-09-07','file':'2026-09-07.json',
                     'generatedAt':'2026-09-07T16:00:00+08:00'}
        client = FakeS3({'dates':[published]})
        publisher = w.Publisher(client,'bucket',self.root)
        publisher.publish_status(dict(status='completed',phase='opening',
            targetDate='2026-09-08',dates=['2026-09-08'],
            openingComplete=True,dataComplete=False,publicationCommitted=False,
            lastSuccessfulUpdate='2026-09-07T16:01:00+08:00'))
        actual = json.loads(client.objects['data/collection-status.json'])
        self.assertEqual(actual.get('latestPublishedDate'),'2026-09-07')
        self.assertEqual(actual.get('latestPublishedAt'),published['generatedAt'])
        self.assertEqual(actual['targetDate'],'2026-09-08')
        self.assertFalse(actual['dataComplete'])
        self.assertFalse(actual['publicationCommitted'])
        self.assertEqual(actual['lastSuccessfulUpdate'],'2026-09-07T16:01:00+08:00')
    def test_no_saved_manifest_never_uses_target_as_published_date(self):
        client = FakeS3()
        w.Publisher(client,'bucket',self.root).publish_status(dict(
            status='running',phase='opening',targetDate='2026-09-08',dates=['2026-09-08']))
        actual = json.loads(client.objects['data/collection-status.json'])
        self.assertIn('latestPublishedDate',actual)
        self.assertIsNone(actual['latestPublishedDate'])
        self.assertIsNone(actual['latestPublishedAt'])
    def test_resume_attempted_requires_explicit_flag(self):
        command=w.collector_command(sys.executable,self.root,resume_attempted=True)
        self.assertIn('--resume-attempted',command); self.assertIn('--resume',command)
        self.assertIn('--months',command)
        self.assertIn('--efficient-sina',command)
    def test_daily_command_not_full_history(self):
        command=w.collector_command(sys.executable,self.root,history=False)
        self.assertNotIn('--months',command); self.assertNotIn('--days',command)
        self.assertTrue(command[1].endswith('scripts/daily_collector.py'))
        self.assertEqual(command[2:], ['--out', str(self.root), '--workers', '4', '--interval', '0.75', '--cutoff', '23:55', '--source-policy', 'sina'])
        self.assertNotIn('--reconcile-latest-first',command)
        retry=w.collector_command(sys.executable,self.root,history=False,retry_current_day=True)
        self.assertIn('--retry-current-day',retry)
        history=w.collector_command(sys.executable,self.root,history=True)
        self.assertNotIn('--baostock-fallback',history)
        self.assertEqual(history[history.index('--source-policy')+1],'sina')
    def test_review_refuses_to_mutate_previously_verified_row_before_upload(self):
        originals=self.root.parent/'originals';originals.mkdir()
        (originals/(self.day+'.json')).write_text(json.dumps(self.payload))
        self.payload['rows'][0].update(first15Volume=20,ratio=20)
        self.write(self.day+'.json',self.payload)
        client=FakeS3();publisher=w.Publisher(client,'bucket',self.root)
        publisher.allowed_review_dates={self.day};publisher.protected_review_out=originals
        with self.assertRaisesRegex(ValueError,'already verified'):publisher.publish(all_dates=True)
        self.assertFalse(client.writes)

    def test_review_command_freezes_prior_dates_and_cannot_include_as_of_day(self):
        command=w.collector_command(sys.executable,self.root,history=False,dates=['2026-03-06','2026-09-04'],
            review_id='audit-1',review_as_of='2026-09-07')
        self.assertEqual(command[command.index('--dates')+1],'2026-03-06,2026-09-04')
        self.assertIn('--review-id',command)
        with self.assertRaises(ValueError):w.collector_command(sys.executable,self.root,history=False,dates=['2026-09-07'],
            review_id='audit-1',review_as_of='2026-09-07')

    def test_review_publisher_accepts_old_saved_date_but_rejects_new_date(self):
        old='2020-01-02'
        self.write(old+'.json',dict(self.payload,date=old))
        self.write('manifest.json',{'dates':[dict(self.entry,date=old,file=old+'.json')]})
        client=FakeS3();publisher=w.Publisher(client,'bucket',self.root);publisher.allowed_review_dates={old}
        self.assertEqual(publisher.publish(all_dates=True),1)
        self.write('manifest.json',{'dates':[self.entry]})
        before=list(client.writes)
        with self.assertRaises(ValueError):publisher.publish()
        self.assertEqual(client.writes,before)

    def test_history_command_prioritizes_latest_reconciliation(self):
        self.assertIn('--reconcile-latest-first',w.collector_command(sys.executable,self.root,history=True))
    def test_nan_denied(self):
        self.payload['rows'][0]['ratio']=float('nan')
        with self.assertRaises(ValueError):w.validate_day(self.payload,self.day)
    def test_special_status_coverage_cannot_claim_an_unexplained_row_is_complete(self):
        item=dict(self.payload['rows'][0],status='suspended',ratio=None,
                  noTradeEvidence={'kind':'fixture'},priceStatus='not_traded',
                  open=None,high=None,low=None,close=None)
        payload=dict(self.payload,rows=[item],specialStatusExplained=1,
                     specialStatusUnexplained=0,statusExplanationComplete=True)
        with self.assertRaisesRegex(ValueError,'special-status'):
            w.validate_day(payload,self.day)
    def test_pause_resumes_frozen_history_after_new_trading_day(self):
        previous={'historyTraversalCompleted':False,'status':'paused','dates':['2026-09-03','2026-09-04']}
        dates=w.history_resume_dates(previous,'2026-09-07')
        command=w.collector_command(sys.executable,self.root,history=True,resume_attempted=True,dates=dates)
        self.assertIn('--dates',command); self.assertNotIn('--months',command)
        self.assertEqual(command[command.index('--dates')+1],'2026-09-03,2026-09-04')
    def test_resume_drops_only_expired_dates(self):
        self.assertEqual(w.history_resume_dates({'dates':['2026-03-06','2026-03-09','2026-09-04']},'2026-09-07'),['2026-03-09','2026-09-04'])
    def test_completed_first_pass_moves_to_latest(self):
        self.assertEqual(w.history_resume_dates({'historyTraversalCompleted':True,'dates':[self.day]}),[])
        self.assertTrue(w.needs_latest_followup({'historyTraversalCompleted':True,'status':'completed','dates':['2026-09-04']},{'tradingDates':['2026-09-04','2026-09-07']}))
    def test_paused_pass_does_not_advance_to_daily(self):
        self.assertFalse(w.needs_latest_followup({'historyTraversalCompleted':False,'status':'paused','dates':['2026-09-04']},{'tradingDates':['2026-09-07']}))
    def test_published_stock_count_is_distinct_from_processed_count(self):
        self.payload.update(attemptedCount=50,pendingCount=5506)
        self.write(self.day+'.json',self.payload)
        client=FakeS3();publisher=w.Publisher(client,'bucket',self.root);publisher.publish()
        publisher.publish_status({'status':'running','dates':[self.day],'completedStocks':64,'totalStocks':5556})
        value=json.loads(client.objects['data/collection-status.json'])
        self.assertEqual(value['publishedStocks'],50);self.assertEqual(value['completedStocks'],64)
    def test_startup_seed_gives_visible_scope_without_claiming_sample_processed(self):
        value=w.initial_progress({}, {'universeTotal':5556,'attemptedCount':32,'dates':[{'date':self.day}]})
        self.assertEqual(value,{'completedStocks':0,'totalStocks':5556,'latestDate':self.day})
    def test_startup_status_preserves_seed_date_before_collector_stdout(self):
        client=FakeS3();publisher=w.Publisher(client,'bucket',self.root)
        publisher.publish_status({'status':'paused','dates':[],'latestDate':self.day,'completedStocks':0,'totalStocks':5556})
        value=json.loads(client.objects['data/collection-status.json'])
        self.assertEqual(value['latestDate'],self.day);self.assertEqual(value['totalStocks'],5556)
    def test_checkpoint_archive_includes_universe_for_recovery(self):
        import tarfile
        catalog={'asOf':self.day,'total':1,'rows':[{'code':'000001','name':'test'}]}
        self.write('universe.json',catalog);self.write('calendar.json',{'tradingDates':[self.day]})
        client=FakeS3();w.Publisher(client,'bucket',self.root).save_checkpoint()
        with tarfile.open(fileobj=io.BytesIO(client.objects['collector/checkpoint.tar.gz']),mode='r:gz') as bundle:
            self.assertIn('universe.json',bundle.getnames())
            self.assertEqual(json.load(bundle.extractfile('universe.json')),catalog)

class ObservationEnvironmentTests(unittest.TestCase):
    def observe(self):
        status = dict(id='environment-check', phase='daily', startedAt='2026-09-07T17:10:00+08:00')
        with tempfile.TemporaryDirectory() as root, patch.object(w.time, 'monotonic', return_value=3700):
            result = w.write_observation(root, status, {}, 100, None)
            saved = json.loads((Path(root) / 'observations/2026-09-07/environment-check.json').read_bytes())
            self.assertEqual(saved, result)
            return result

    def test_github_does_not_read_host_uptime_or_claim_free_compute(self):
        with patch.dict(w.os.environ, {'GITHUB_ACTIONS': 'true'}), patch.object(Path, 'read_text') as read:
            actual = self.observe()
            read.assert_not_called()
        self.assertEqual(actual['executionEnvironment'], 'github_actions')
        self.assertEqual(actual['elapsedSeconds'], 3600)
        self.assertIsNone(actual['observedBootSeconds'])
        self.assertIsNone(actual['estimatedComputeAndIpv4Usd'])
        self.assertIsNone(actual['computeAndIpv4HourlyRateUsd'])
        self.assertEqual(actual['estimateScope'], 'not_estimated')

    def test_ec2_keeps_boot_based_estimate(self):
        with patch.dict(w.os.environ, {}, clear=True), patch.object(Path, 'read_text', return_value='7200.0 0.0') as read:
            actual = self.observe()
            read.assert_called_once_with()
        self.assertEqual(actual['executionEnvironment'], 'ec2')
        self.assertEqual(actual['observedBootSeconds'], 7200)
        self.assertEqual(actual['estimatedComputeAndIpv4Usd'], 0.0932)
        self.assertEqual(actual['computeAndIpv4HourlyRateUsd'], 0.0466)
        self.assertEqual(actual['estimateScope'], 'boot_to_observation')

    def test_ec2_without_uptime_keeps_runtime_fallback(self):
        with patch.dict(w.os.environ, {'GITHUB_ACTIONS': 'false'}), patch.object(Path, 'read_text', side_effect=OSError):
            actual = self.observe()
        self.assertEqual(actual['executionEnvironment'], 'ec2')
        self.assertIsNone(actual['observedBootSeconds'])
        self.assertEqual(actual['estimatedComputeAndIpv4Usd'], 0.0466)
        self.assertEqual(actual['estimateScope'], 'worker_runtime_only')

if __name__=='__main__': unittest.main()
