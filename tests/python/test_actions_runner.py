import _bootstrap
import gzip
import hashlib
import io
import json
import os
import signal
import subprocess
import tarfile
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import actions_runner as runner
from import_checkpoint_batch import REQUIRED_VERSION

DAY = '2026-09-04'
CODE = '600519'
BUCKET = 'private-fixture'


class ConditionalFailure(Exception):
    def __init__(self):
        self.response = {'Error':{'Code':'PreconditionFailed'}}


class FakeS3:
    class exceptions:
        class NoSuchKey(Exception):pass
    def __init__(self):
        self.objects={};self.metadata={};self.writes=[];self.deletes=[]
    def etag(self,key):
        return '"'+hashlib.md5(self.objects[key]).hexdigest()+'"'
    def set(self,key,body,metadata=None):
        self.objects[key]=body if isinstance(body,bytes) else runner.canonical(body)
        self.metadata[key]=metadata or {}
    def get_object(self,Bucket,Key):
        if Key not in self.objects:raise self.exceptions.NoSuchKey()
        return dict(Body=io.BytesIO(self.objects[Key]),ContentLength=len(self.objects[Key]),
                    Metadata=self.metadata.get(Key,{}),ETag=self.etag(Key),VersionId='fixture-v1')
    def head_object(self,Bucket,Key):
        if Key not in self.objects:raise self.exceptions.NoSuchKey()
        return dict(Metadata=self.metadata.get(Key,{}),ETag=self.etag(Key))
    def put_object(self,**kwargs):
        key=kwargs['Key']
        if kwargs.get('IfNoneMatch')=='*' and key in self.objects:raise ConditionalFailure()
        if 'IfMatch' in kwargs and (key not in self.objects or self.etag(key)!=kwargs['IfMatch']):raise ConditionalFailure()
        self.set(key,kwargs['Body'],kwargs.get('Metadata'))
        self.writes.append(kwargs)
        return dict(ETag=self.etag(key))
    def delete_object(self,**kwargs):
        key=kwargs['Key']
        if 'IfMatch' in kwargs and (key not in self.objects or self.etag(key)!=kwargs['IfMatch']):raise ConditionalFailure()
        self.objects.pop(key,None);self.deletes.append(kwargs)
    def upload_file(self,path,bucket,key,**kwargs):
        if kwargs.get('Callback'):kwargs['Callback'](Path(path).stat().st_size)
        extra=kwargs.get('ExtraArgs',{})
        self.put_object(Bucket=bucket,Key=key,Body=Path(path).read_bytes(),**extra)


def valid_row():
    return dict(code=CODE,name='PRIVATE STOCK DATA',market='SH',verificationVersion=REQUIRED_VERSION,status='ok',quality='matched',
                first15Volume=10,dailyVolume=160,minuteDayVolume=160,dayVolumeDifference=0,ratio=6.25)


def base_members():
    return [
        ('universe.json',dict(asOf=DAY,total=1,rows=[dict(code=CODE,name='PRIVATE STOCK DATA')],historicalMembership=False)),
        ('calendar.json',dict(calendarAsOf=DAY,tradingDates=[DAY],retentionMonths=6)),
        ('run-status.json',dict(dates=[DAY],targetCount=1,attemptedCount=1)),
        ('checkpoint/'+CODE+'.json',dict(code=CODE,fetchedOn=DAY,fetchStatus='ok',days={DAY:valid_row()})),
    ]


def archive_bytes(members):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as archive:
        for item in members:
            if isinstance(item,tarfile.TarInfo):
                archive.addfile(item)
                continue
            name,value=item
            raw=value if isinstance(value,bytes) else runner.canonical(value)
            info=tarfile.TarInfo(name);info.size=len(raw);info.mtime=0
            archive.addfile(info,io.BytesIO(raw))
    return output.getvalue()


def fixture(client,members=None,history=False):
    archive=archive_bytes(base_members() if members is None else members)
    sha=hashlib.sha256(archive).hexdigest()
    client.set(runner.ARCHIVE_KEY,archive,{'sha256':sha})
    client.set('data/manifest.json',dict(generatedAt='v1',universeTotal=1,dates=[dict(date=DAY,file=DAY+'.json',generatedAt='v1')]))
    client.set('data/'+DAY+'.json',dict(date=DAY,generatedAt='v1',rows=[valid_row()]))
    client.set('data/collection-status.json',dict(status='paused',dates=[DAY],historyTraversalCompleted=history,completedStocks=1,totalStocks=1))
    return sha


class ActionsRestoreTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)/'runner';self.client=FakeS3();fixture(self.client)
    def restore(self,**kwargs):
        return runner.restore(self.client,BUCKET,self.root,minimum_universe=1,**kwargs)
    def test_valid_private_restore_is_read_only_and_installs_after_validation(self):
        report=self.restore()
        self.assertEqual(report['checkpointCount'],1)
        self.assertEqual(report['restoredDates'],1)
        self.assertEqual(json.loads((self.root/'data/checkpoint'/f'{CODE}.json').read_text())['days'][DAY]['ratio'],6.25)
        self.assertFalse(self.client.writes)
        self.assertEqual((self.root/'data/checkpoint'/f'{CODE}.json').stat().st_mode & 0o777,0o600)
    def test_large_archived_catalog_restores_only_recent_workspace(self):
        from datetime import date,timedelta
        archived=[dict(date=(date(2020,1,1)+timedelta(days=i)).isoformat(),
                       file=(date(2020,1,1)+timedelta(days=i)).isoformat()+'.json',generatedAt='old') for i in range(200)]
        self.client.set('data/manifest.json',dict(dates=archived+[dict(date=DAY,file=DAY+'.json',generatedAt='v1')]))
        # Old files deliberately absent from the fake: the runner must never download them.
        with patch.object(runner.worker,'now',return_value='2026-09-07T09:00:00+08:00'):
            report=self.restore()
        self.assertEqual(report['restoredDates'],1)
        self.assertEqual(report['archivedDates'],200)
        self.assertFalse(self.client.writes)
        self.assertFalse(self.client.deletes)
        self.assertEqual(len(json.loads(self.client.objects['data/manifest.json'])['dates']),201)

    def test_review_restore_includes_frozen_old_saved_date_and_excludes_other_dates(self):
        old='2026-03-06'
        self.client.set('data/'+old+'.json',dict(date=old,generatedAt='v1',rows=[valid_row()]))
        self.client.set('data/manifest.json',dict(dates=[dict(date=old,file=old+'.json',generatedAt='v1'),dict(date=DAY,file=DAY+'.json',generatedAt='v1')]))
        report=self.restore(review_dates=[old])
        self.assertEqual(report['restoredDates'],1)
        self.assertTrue((self.root/'data'/f'{old}.json').exists())
        self.assertFalse((self.root/'data'/f'{DAY}.json').exists())
        self.assertFalse(self.client.writes)
    def test_review_refuses_unpublished_date_before_installing(self):
        with self.assertRaises(runner.RestoreError):self.restore(review_dates=['2026-01-02'])
        self.assertFalse((self.root/'data').exists())

    def test_explicit_hash_supports_legacy_metadata_without_trusting_unhashed_input(self):
        sha=self.client.metadata[runner.ARCHIVE_KEY]['sha256'];self.client.metadata[runner.ARCHIVE_KEY]={}
        with self.assertRaises(runner.RestoreError):self.restore()
        self.restore(expected_sha=sha)
    def test_hash_mismatch_installs_nothing(self):
        with self.assertRaises(runner.RestoreError):self.restore(expected_sha='0'*64)
        self.assertFalse((self.root/'data').exists())
    def test_traversal_absolute_unknown_link_and_duplicate_rejected(self):
        link=tarfile.TarInfo('checkpoint/000001.json');link.type=tarfile.SYMTYPE;link.linkname='/etc/passwd'
        hard=tarfile.TarInfo('checkpoint/000001.json');hard.type=tarfile.LNKTYPE;hard.linkname='universe.json'
        additions=[('../escape',b'x'),('/tmp/escape',b'x'),('unknown.json',b'{}'),link,hard,base_members()[0]]
        for addition in additions:
            with self.subTest(member=str(addition)):
                fixture(self.client,base_members()+[addition])
                with self.assertRaises(runner.RestoreError):self.restore()
                self.assertFalse((self.root/'data').exists())
    def test_old_checkpoint_directory_header_allowed(self):
        directory=tarfile.TarInfo('checkpoint/');directory.type=tarfile.DIRTYPE
        fixture(self.client,[directory]+base_members())
        self.assertEqual(self.restore()['checkpointCount'],1)
    def test_expanded_member_bound_and_pax_size_bomb_rejected(self):
        fixture(self.client,base_members()+[('checkpoint/000001.json',b'x'*10000)])
        with patch.object(runner,'MAX_CHECKPOINT',8192),self.assertRaises(runner.RestoreError):self.restore()
        header=tarfile.TarInfo('pax');header.type=tarfile.XHDTYPE;header.size=runner.MAX_CHECKPOINT+1024
        raw=gzip.compress(header.tobuf()+b'x'*1024)
        self.client.set(runner.ARCHIVE_KEY,raw,{'sha256':hashlib.sha256(raw).hexdigest()})
        with self.assertRaises(runner.RestoreError):self.restore()
    def test_cp_identity_version_bad_ratio_and_json_duplicate_rejected(self):
        for change in ('identity','version','ratio','duplicate'):
            with self.subTest(change=change):
                members=base_members();record=members[-1][1]
                if change=='identity':record['code']='000001'
                if change=='version':record['days'][DAY]['verificationVersion']=1
                if change=='ratio':record['days'][DAY]['ratio']=99
                if change=='duplicate':members[-1]=(members[-1][0],b'{"code":"600519","code":"000001"}')
                fixture(self.client,members)
                with self.assertRaises(ValueError):self.restore()
                self.assertFalse((self.root/'data').exists())
    def test_failed_cp_is_preserved_not_dropped_or_claimed_verified(self):
        members=base_members();record=members[-1][1]
        record.update(fetchStatus='error',days={DAY:dict(code=CODE,status='missing',reason='采集失败：TimeoutError',verificationVersion=REQUIRED_VERSION,ratio=None)})
        fixture(self.client,members);self.restore()
        actual=json.loads((self.root/'data/checkpoint'/f'{CODE}.json').read_text())
        self.assertEqual(actual['fetchStatus'],'error');self.assertIsNone(actual['days'][DAY]['ratio'])
    def test_snapshot_path_generation_and_hash_fail_before_any_install(self):
        for change in ('path','generation','hash'):
            with self.subTest(change=change):
                fixture(self.client)
                if change=='path':self.client.set('data/manifest.json',dict(dates=[dict(date=DAY,file='../bad',generatedAt='v1')]))
                if change=='generation':self.client.set('data/'+DAY+'.json',dict(date=DAY,generatedAt='v2',rows=[valid_row()]))
                if change=='hash':self.client.metadata['data/'+DAY+'.json']={'sha256':'0'*64}
                with self.assertRaises(ValueError):self.restore()
                self.assertFalse((self.root/'data').exists())
    def test_actions_state_only_reused_when_bound_to_acknowledged_archive(self):
        sha=fixture(self.client)
        receipt=dict(version=1,bucket=BUCKET,receipts={runner.ARCHIVE_KEY:dict(sha256=sha)})
        state=dict(version=1,bucket=BUCKET,checkpointSha256=sha,workerState=dict(historyTraversalCompleted=True,dates=[DAY]),publisherState=receipt)
        self.client.set(runner.STATE_KEY,state);report=self.restore()
        self.assertTrue(report['boundRunnerState'])
        self.assertTrue(json.loads((self.root/'worker-state.json').read_text())['historyTraversalCompleted'])
        self.assertTrue((self.root/'publisher-state.json').exists())
    def test_stale_actions_state_falls_back_to_private_current_status(self):
        self.client.set(runner.STATE_KEY,dict(version=1,bucket=BUCKET,checkpointSha256='0'*64,workerState=dict(historyTraversalCompleted=True)))
        report=self.restore()
        self.assertFalse(report['boundRunnerState'])
        self.assertFalse(json.loads((self.root/'worker-state.json').read_text())['historyTraversalCompleted'])
    def test_existing_target_and_symlink_root_are_never_overwritten(self):
        self.root.mkdir();(self.root/'data').mkdir();(self.root/'data/user-file').write_text('keep')
        with self.assertRaises(runner.RestoreError):self.restore()
        self.assertEqual((self.root/'data/user-file').read_text(),'keep')
    def test_probe_does_not_acquire_lease_execute_sources_or_publish(self):
        args=SimpleNamespace(root=str(self.root),bucket=BUCKET,mode='probe',max_minutes=12,region='us-east-1')
        execute=Mock(side_effect=AssertionError('No source execution'))
        report=runner.run(args,self.client,executor=execute,minimum_universe=1)
        execute.assert_not_called();self.assertEqual(report['productionWrites'],0)
        self.assertEqual(report['status'],'validated');self.assertFalse(self.client.writes)


class ActionsLeaseTests(unittest.TestCase):
    def setUp(self):self.client=FakeS3()
    def test_second_owner_blocked_and_matching_owner_releases_conditionally(self):
        lease=runner.Lease(self.client,BUCKET).acquire(600)
        with self.assertRaises(runner.LeaseBusy):runner.Lease(self.client,BUCKET).acquire(600)
        self.assertTrue(lease.release())
        self.assertIn('IfMatch',self.client.deletes[-1])
    def test_expired_lease_replaced_conditionally_not_unconditionally_deleted(self):
        self.client.set(runner.LEASE_KEY,dict(owner='expired',expiresAt=time.time()-1))
        old=self.client.etag(runner.LEASE_KEY)
        lease=runner.Lease(self.client,BUCKET).acquire(600)
        self.assertEqual(self.client.writes[-1]['IfMatch'],old)
        self.assertNotEqual(lease.owner,'expired')
    def test_lost_owner_cannot_publish_or_unlock_successor(self):
        lease=runner.Lease(self.client,BUCKET).acquire(600)
        self.client.set(runner.LEASE_KEY,dict(owner='successor',expiresAt=time.time()+600))
        with self.assertRaises(runner.LeaseLost):runner.LeasedClient(self.client,lease).put_object(Bucket=BUCKET,Key='data/x',Body=b'x')
        self.assertFalse(lease.release());self.assertFalse(self.client.deletes)
    def test_near_expiry_stops_writes_before_lease_can_be_stolen(self):
        lease=runner.Lease(self.client,BUCKET).acquire(600)
        self.client.set(runner.LEASE_KEY,dict(owner=lease.owner,expiresAt=time.time()+100))
        with self.assertRaises(runner.LeaseLost):lease.check()


class ActionsExecutionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)/'runner';self.client=FakeS3();fixture(self.client)
        self.args=SimpleNamespace(root=str(self.root),bucket=BUCKET,mode='auto',max_minutes=12,region='us-east-1')
    def test_timeout_sends_term_then_kill_and_waits_before_returning(self):
        process=Mock(pid=12345)
        process.wait.side_effect=[subprocess.TimeoutExpired('worker',1),subprocess.TimeoutExpired('worker',1),-9]
        process.poll.return_value=-9
        popen=Mock(return_value=process)
        options=SimpleNamespace(**dict(vars(self.args),lease_owner='owner'))
        with patch.object(runner.os,'killpg') as kill:
            code,timed=runner.spawn_worker(options,io.StringIO(),popen=popen)
        self.assertEqual((code,timed),(-9,True))
        self.assertEqual([call.args[1] for call in kill.call_args_list],[signal.SIGTERM,signal.SIGKILL])
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertNotIn('systemctl',popen.call_args.args[0])
    def test_price_backfill_hard_failure_preserves_daily_status_and_reports_safe_stage(self):
        self.args.mode='price_backfill';self.args.max_minutes=20
        self.args.backfill_id='ohlc-v1-20260920';self.args.backfill_from='2026-03-06'
        self.args.backfill_to='2026-09-18';self.args.backfill_batch_size=20
        def execute(options,log):
            (Path(options.root)/'runner-private-error.log').write_text(
                'Traceback (most recent call last):\n'
                '  File "/home/runner/work/repo/backend/scripts/backfill_quotes.py", line 333, in run\n'
                '    import akshare as ak\n'
                "FileNotFoundError: private source detail\n")
            return 1,False

        with self.assertRaises(runner.WorkerFailure) as caught:
            runner.run(self.args,self.client,executor=execute,minimum_universe=1)

        self.assertEqual(caught.exception.public_type,'FileNotFoundError')
        self.assertEqual(caught.exception.public_stage,'backfill_quotes.py:333:run')
        self.assertEqual(caught.exception.exit_code,1)
        written_keys=[item['Key'] for item in self.client.writes]
        self.assertNotIn('data/collection-status.json',written_keys)
        self.assertNotIn(runner.STATE_KEY,written_keys)
        self.assertFalse((self.root/'data/price-backfill-state.json').exists())
    def test_worker_reuses_history_resume_and_follows_latest_with_remaining_budget(self):
        self.root.mkdir();(self.root/'data').mkdir()
        runner.private_json(self.root/'worker-state.json',dict(historyTraversalCompleted=False,dates=[DAY]))
        runner.private_json(self.root/'data/calendar.json',dict(tradingDates=[DAY,'2026-09-07']))
        lease=runner.Lease(self.client,BUCKET).acquire(1000)
        self.args.lease_owner=lease.owner;self.args.max_minutes=20
        calls=[]
        def execute(options,publisher):
            calls.append(options.max_hours)
            runner.private_json(self.root/'worker-state.json',dict(status='completed',historyTraversalCompleted=True,dates=[DAY]))
            return 0
        with patch.object(runner.worker,'run',side_effect=execute):
            self.assertEqual(runner.execute_worker(self.args,self.client),0)
        self.assertEqual(len(calls),2);self.assertLessEqual(calls[1],calls[0])
    def test_forced_budget_pause_saves_private_checkpoint_and_never_claims_complete(self):
        self.args.max_minutes=20
        def execute(options,log):
            log.write('PRIVATE STOCK DATA and raw source diagnostics\n')
            return -9,True
        transfer=SimpleNamespace(TransferConfig=lambda **kwargs:kwargs)
        with patch.dict('sys.modules',{'boto3.s3.transfer':transfer}):
            result=runner.run(self.args,self.client,executor=execute,minimum_universe=1)
        self.assertTrue(result['budgetExhausted']);self.assertEqual(result['status'],'paused')
        self.assertTrue(result['checkpointSaved']);self.assertFalse(result['historyTraversalCompleted'])
        self.assertIn(runner.ARCHIVE_KEY,self.client.objects);self.assertIn(runner.STATE_KEY,self.client.objects)
        self.assertNotIn('PRIVATE STOCK DATA',json.dumps(result))
        self.assertFalse(any('log' in item['Key'] for item in self.client.writes))
        self.assertEqual((self.root/'worker-private.log').stat().st_mode&0o777,0o600)
        self.assertEqual(result['outcome'], 'incomplete_checkpointed')
        self.assertFalse(runner.successful_exit(result))
    def test_daily_cannot_falsely_mark_incomplete_history_complete(self):
        self.args.mode='daily'
        execute=Mock()
        with self.assertRaises(RuntimeError):runner.run(self.args,self.client,executor=execute,minimum_universe=1)
        execute.assert_not_called()
        self.assertNotIn(runner.LEASE_KEY,self.client.objects)

    def test_normal_soft_budget_pause_preserves_backup_but_fails_the_run(self):
        def execute(options,log):
            state=json.loads((Path(options.root)/'worker-state.json').read_text())
            state.update(status='paused',exitReason='interrupted')
            runner.private_json(Path(options.root)/'worker-state.json',state)
            self.assertGreater(options.max_minutes,10.1)
            self.assertLess(options.max_minutes,12)
            return 1,False
        transfer=SimpleNamespace(TransferConfig=lambda **kwargs:kwargs)
        with patch.dict('sys.modules',{'boto3.s3.transfer':transfer}):
            result=runner.run(self.args,self.client,executor=execute,minimum_universe=1)
        self.assertTrue(result['expectedPause']);self.assertTrue(result['checkpointSaved'])
        self.assertEqual(result['status'],'paused')
        self.assertEqual(result['outcome'],'incomplete_checkpointed')
        self.assertFalse(runner.successful_exit(result))

    def test_source_or_publication_failure_is_not_an_expected_budget_success(self):
        self.assertFalse(runner.successful_exit(dict(status='paused',checkpointSaved=True,expectedPause=False)))
        self.assertFalse(runner.successful_exit(dict(status='paused',checkpointSaved=True,expectedPause=False,workerExitCode=1)))
        self.assertFalse(runner.successful_exit(dict(status='paused',checkpointSaved=False,expectedPause=True)))

    def test_normal_retry_pause_is_never_reported_as_success(self):
        safe = dict(status='paused', checkpointSaved=True, expectedPause=False, workerExitCode=0,
                    pendingStockDates=49)
        self.assertFalse(runner.successful_exit(safe))
        self.assertFalse(runner.successful_exit(dict(safe, checkpointSaved=False)))
        self.assertFalse(runner.successful_exit(dict(safe, workerExitCode=1)))

    def test_actions_observation_never_uses_ec2_host_uptime_or_rate(self):
        self.root.mkdir();(self.root/'data').mkdir()
        runner.private_json(self.root/'worker-state.json',dict(historyTraversalCompleted=False,dates=[DAY]))
        lease=runner.Lease(self.client,BUCKET).acquire(1000)
        self.args.lease_owner=lease.owner
        def execute(options,publisher):
            state=dict(id='actions-test',phase='history',startedAt=runner.worker.now(),status='paused')
            runner.worker.write_observation(self.root,state,{},time.monotonic(),publisher)
            return 1
        with patch.object(runner.worker,'run',side_effect=execute):
            runner.execute_worker(self.args,self.client)
        observation=next(json.loads(body) for key,body in self.client.objects.items() if key.startswith('collector/observations/'))
        self.assertEqual(observation['runnerEnvironment'],'github_actions')
        self.assertIsNone(observation['estimatedComputeAndIpv4Usd'])
        self.assertIsNone(observation['observedBootSeconds'])


if __name__=='__main__':unittest.main()
