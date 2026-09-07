#!/usr/bin/env python3
"""Private ephemeral runner adapter; probe is read-only and source workers remain unchanged.

Run outside the repository with an OIDC-scoped S3 role. Workflow concurrency and
stopping the legacy EC2 writer are migration prerequisites: that older writer does
not participate in this adapter's lease. Never upload the temporary root as a
GitHub artifact. Public stdout contains counts/error types, never collector logs.
"""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'scripts'))
import cloud_worker as worker
from import_checkpoint_batch import strict_json, valid_date, record_disposition
from publisher_state import canonical, file_digest
from universe_cache import validate_universe

ARCHIVE_KEY = 'collector/checkpoint.tar.gz'
STATE_KEY = 'collector/actions-state.json'
LEASE_KEY = 'collector/actions-lease.json'
MAX_COMPRESSED = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_SNAPSHOTS = 4 * 1024 * 1024 * 1024
MAX_DATE_BYTES = 64 * 1024 * 1024
MAX_CHECKPOINT = 8 * 1024 * 1024
STATE_LIMITS = {'universe.json': 4*1024*1024, 'calendar.json': 2*1024*1024,
                'run-status.json': 2*1024*1024, 'daily-state.json': 192*1024*1024,
                'daily-attempts.jsonl': 128*1024*1024}
CP_NAME = re.compile(r'checkpoint/([0-9]{6})\.json')
SHA = re.compile(r'[0-9a-f]{64}')
LEASE_GUARD_SECONDS = 180


class RestoreError(ValueError):
    pass


class LeaseBusy(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


def missing(exc):
    return (type(exc).__name__ == 'NoSuchKey' or
            getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('NoSuchKey', '404', 'NotFound'))


def precondition(exc):
    return getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('PreconditionFailed', '412', 'ConditionalRequestConflict', '409')


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('wb') as output:
        os.chmod(temp, 0o600)
        output.write(canonical(value)); output.flush(); os.fsync(output.fileno())
    os.replace(temp, path)


def get_bytes(client, bucket, key, limit, optional=False):
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if optional and missing(exc):
            return None, None
        raise
    body = response['Body']
    try:
        if response.get('ContentLength', 0) > limit:
            raise RestoreError('Remote object exceeds size bound')
        raw = body.read(limit+1)
        if len(raw) > limit:
            raise RestoreError('Remote object exceeds size bound')
        return raw, response
    finally:
        body.close()


def download_archive(client, bucket, destination, expected_sha=None):
    response = client.get_object(Bucket=bucket, Key=ARCHIVE_KEY)
    body = response['Body']
    claimed = expected_sha or response.get('Metadata', {}).get('sha256')
    try:
        if not isinstance(claimed, str) or not SHA.fullmatch(claimed.lower()):
            raise RestoreError('Checkpoint SHA256 metadata or explicit expected SHA is required')
        if response.get('ContentLength', 0) > MAX_COMPRESSED:
            raise RestoreError('Compressed checkpoint exceeds bound')
        digest = hashlib.sha256(); size = 0
        with Path(destination).open('wb') as output:
            os.chmod(destination, 0o600)
            while True:
                chunk = body.read(1024*1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_COMPRESSED:
                    raise RestoreError('Compressed checkpoint exceeds bound')
                digest.update(chunk); output.write(chunk)
        actual = digest.hexdigest()
        if actual != claimed.lower():
            raise RestoreError('Checkpoint SHA256 mismatch')
        return {'sha256':actual, 'bytes':size, 'versionId':response.get('VersionId')}
    finally:
        body.close()


class BoundedGzip:
    """Bound extended-header reads too; tarfile must not allocate a malicious PAX size."""
    def __init__(self, path):
        self.stream = gzip.open(path, 'rb')
    def read(self, size):
        if size < 0 or size > MAX_CHECKPOINT or self.tell()+size > MAX_EXPANDED:
            raise RestoreError('Unbounded decompressed read')
        return self.stream.read(size)
    def tell(self):
        return self.stream.tell()
    def seek(self, offset, whence=0):
        if whence != 0 or not 0 <= offset <= MAX_EXPANDED:
            raise RestoreError('Unbounded archive seek')
        return self.stream.seek(offset)
    def close(self):
        self.stream.close()


def unpack_archive(archive, destination):
    destination = Path(destination)
    seen = set(); expanded = 0; checkpoint_count = 0
    source = BoundedGzip(archive)
    try:
        with tarfile.open(fileobj=source, mode='r:') as bundle:
            for member in bundle:
                name = member.name
                if member.isdir() and name in ('checkpoint', 'checkpoint/'):
                    name = 'checkpoint'
                    limit = 0
                else:
                    match = CP_NAME.fullmatch(name)
                    limit = MAX_CHECKPOINT if match else STATE_LIMITS.get(name)
                    if limit is None or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.issparse():
                        raise RestoreError('Unexpected archive member or member type')
                    if match:
                        checkpoint_count += 1
                # Harmless timestamps from older tarfile.add archives are ignored.
                if set(member.pax_headers)-{'mtime','atime','ctime'}:
                    raise RestoreError('Unsupported archive extended header')
                if name in seen:
                    raise RestoreError('Duplicate archive member')
                seen.add(name); expanded += member.size
                if len(seen) > 10010 or member.size < 0 or member.size > limit or expanded > MAX_EXPANDED:
                    raise RestoreError('Expanded archive exceeds bounds')
                if member.isdir():
                    (destination/'checkpoint').mkdir(mode=0o700, exist_ok=True)
                    continue
                target = destination/name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise RestoreError('Archive member body missing')
                remaining = member.size
                with target.open('xb') as output:
                    os.chmod(target, 0o600)
                    while remaining:
                        chunk = stream.read(min(1024*1024, remaining))
                        if not chunk:
                            raise RestoreError('Truncated archive member')
                        remaining -= len(chunk); output.write(chunk)
                stream.close()
    finally:
        source.close()
    if not {'universe.json','calendar.json'} <= seen:
        raise RestoreError('Checkpoint requires its universe and calendar')
    return dict(checkpointCount=checkpoint_count, expandedBytes=expanded, memberCount=len(seen))


def validate_dates(values, *, allow_empty=True):
    if not isinstance(values, list) or len(values) > 186 or len(values) != len(set(values)) or (not values and not allow_empty):
        raise RestoreError('Invalid frozen date list')
    return [valid_date(day) for day in values]


def validate_tree(out, minimum_universe=1000):
    catalog = validate_universe(strict_json((out/'universe.json').read_bytes()), minimum_count=minimum_universe)
    universe = {row['code'] for row in catalog['rows']}
    calendar = strict_json((out/'calendar.json').read_bytes())
    valid_date(calendar.get('calendarAsOf'))
    dates = validate_dates(calendar.get('tradingDates', []))
    for path in sorted((out/'checkpoint').glob('*.json')):
        record = strict_json(path.read_bytes())
        code = path.stem
        if not isinstance(record, dict) or record.get('code') != code or code not in universe:
            raise RestoreError('Checkpoint identity differs from universe')
        if record.get('fetchStatus') not in ('ok','error','pending') or not isinstance(record.get('days'), dict) or not record['days']:
            raise RestoreError('Checkpoint status/days are invalid')
        # Unlike a supplementary import, restoring a backup preserves real failed attempts.
        candidate = dict(record, fetchStatus='ok', fetchedOn=record.get('fetchedOn', calendar['calendarAsOf']))
        record_disposition(candidate, code, universe, [])
    for name in ('run-status.json', 'daily-state.json'):
        path = out/name
        if not path.exists():
            continue
        value = strict_json(path.read_bytes())
        if not isinstance(value, dict):
            raise RestoreError('Invalid checkpoint state object')
        if 'dates' in value:
            validate_dates(value['dates'])
        if name == 'daily-state.json':
            if value.get('version') != 1 or not isinstance(value.get('pending'), dict) or not isinstance(value.get('attempts'), dict):
                raise RestoreError('Unsupported daily state')
            allowed = set(value['dates'])
            for code, pending in value['pending'].items():
                if code not in universe or not set(validate_dates(pending)) <= allowed:
                    raise RestoreError('Unknown pending stock/date')
    journal = out/'daily-attempts.jsonl'
    if journal.exists():
        with journal.open('rb') as stream:
            for line in stream:
                if len(line) > 128*1024:
                    raise RestoreError('Journal line exceeds bound')
                try:
                    event = strict_json(line)
                except ValueError:
                    if not line.endswith(b'\n'):
                        continue  # The existing replay code ignores an interrupted final append.
                    raise
                if event.get('code') not in universe or event.get('provider') not in ('primary','bao'):
                    raise RestoreError('Invalid attempt journal')
                validate_dates(event.get('dates'))
                cycle = event.get('cycle')
                if isinstance(cycle,str) and re.fullmatch(r'review:\d{4}-\d{2}-\d{2}:[A-Za-z0-9_-]{1,64}',cycle):
                    valid_date(cycle.split(':')[1])
                else: valid_date(cycle)
    return catalog, calendar


def validate_worker_state(value):
    if not isinstance(value, dict):
        raise RestoreError('Invalid worker state')
    validate_dates(value.get('dates', []))
    if 'historyTraversalCompleted' in value and type(value['historyTraversalCompleted']) is not bool:
        raise RestoreError('Invalid history completion marker')
    for key in ('completedStocks','totalStocks'):
        if key in value and (type(value[key]) is not int or not 0 <= value[key] <= 10000):
            raise RestoreError('Invalid progress count')
    return value


def restore(client, bucket, root, expected_sha=None, *, minimum_universe=1000, review_dates=None):
    """Validate the entire private staging tree before installing into an empty temporary root."""
    root = Path(root)
    if root.is_symlink() or any((root/name).exists() or (root/name).is_symlink() for name in ('data','worker-state.json','publisher-state.json')):
        raise RestoreError('Restore requires an empty, non-symlink runner root')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.actions-restore-', dir=root.parent) as temporary:
        stage = Path(temporary); data = stage/'data'; data.mkdir(mode=0o700)
        archive = stage/'checkpoint.tar.gz'
        source = download_archive(client, bucket, archive, expected_sha)
        report = unpack_archive(archive, data)
        catalog, calendar = validate_tree(data, minimum_universe)
        raw, _ = get_bytes(client, bucket, 'data/manifest.json', 4*1024*1024)
        manifest = strict_json(raw)
        if not isinstance(manifest, dict) or not isinstance(manifest.get('dates'), list) or len(manifest['dates']) > 10000:
            raise RestoreError('Invalid private manifest')
        seen = set(); entries = []; snapshot_bytes = 0
        review_set = set(validate_dates(review_dates,allow_empty=False)) if review_dates is not None else None
        start = worker.six_month_start(worker.now()[:10])
        for entry in manifest['dates']:
            day = valid_date(entry.get('date'))
            if day in seen or entry.get('file') != day+'.json':
                raise RestoreError('Duplicate or unsafe manifest date')
            seen.add(day)
            if (day not in review_set) if review_set is not None else (day < start):
                continue
            payload_raw, response = get_bytes(client, bucket, 'data/'+entry['file'], MAX_DATE_BYTES)
            snapshot_bytes += len(payload_raw)
            if snapshot_bytes > MAX_SNAPSHOTS:
                raise RestoreError('Snapshots exceed restore bound')
            claimed = response.get('Metadata', {}).get('sha256')
            if claimed and hashlib.sha256(payload_raw).hexdigest() != claimed:
                raise RestoreError('Private date hash mismatch')
            payload = worker.validate_day(strict_json(payload_raw), day)
            if payload.get('generatedAt') != entry.get('generatedAt'):
                raise RestoreError('Private date generation differs from manifest')
            (data/entry['file']).write_bytes(payload_raw); os.chmod(data/entry['file'], 0o600)
            entries.append(entry)
        if review_set is not None and review_set != {entry['date'] for entry in entries}:
            raise RestoreError('Review date missing from private saved catalog')
        private_json(data/'manifest.json', dict(manifest, dates=entries))
        state_raw, _ = get_bytes(client, bucket, STATE_KEY, 8*1024*1024, optional=True)
        state = strict_json(state_raw) if state_raw is not None else {}
        bound = state.get('version') == 1 and state.get('bucket') == bucket and state.get('checkpointSha256') == source['sha256']
        if bound:
            current = validate_worker_state(state.get('workerState'))
            publisher = state.get('publisherState')
            if not isinstance(publisher, dict) or publisher.get('version') != 1 or publisher.get('bucket') != bucket or not isinstance(publisher.get('receipts'), dict):
                raise RestoreError('Invalid publisher receipts')
            for key, receipt in publisher['receipts'].items():
                if not key.startswith(('data/', 'collector/')) or not isinstance(receipt, dict) or not SHA.fullmatch(receipt.get('sha256', '')):
                    raise RestoreError('Invalid publisher receipt')
            private_json(stage/'publisher-state.json', publisher)
        else:
            status_raw, _ = get_bytes(client, bucket, 'data/collection-status.json', 2*1024*1024, optional=True)
            current = validate_worker_state(strict_json(status_raw)) if status_raw else dict(historyTraversalCompleted=False, dates=dates_from_report(data, calendar))
        private_json(stage/'worker-state.json', current)
        report.update(archiveSha256=source['sha256'], archiveVersionId=source['versionId'], compressedBytes=source['bytes'],
                      restoredDates=len(entries), archivedDates=len(seen)-len(entries), snapshotBytes=snapshot_bytes, universeTotal=catalog['total'], boundRunnerState=bound)
        private_json(stage/'restore-report.json', report)
        installed = []
        try:
            for name in ('data','worker-state.json','publisher-state.json','restore-report.json'):
                if (stage/name).exists():
                    if (root/name).exists() or (root/name).is_symlink():
                        raise RestoreError('Restore destination appeared during staging')
                    os.replace(stage/name, root/name); installed.append(root/name)
        except Exception:
            for path in reversed(installed):
                shutil.rmtree(path) if path.is_dir() else path.unlink()
            raise
        return report


def dates_from_report(data, calendar):
    path = data/'run-status.json'
    report = strict_json(path.read_bytes()) if path.exists() else {}
    return validate_dates(report.get('dates', calendar['tradingDates']))


class Lease:
    def __init__(self, client, bucket, owner=None, expires=None):
        self.client, self.bucket = client, bucket
        self.owner = owner or uuid.uuid4().hex
        self.expires = expires
        self.etag = None

    def read(self):
        raw, response = get_bytes(self.client, self.bucket, LEASE_KEY, 8192, optional=True)
        if raw is None:
            return None, None
        value = strict_json(raw)
        if not isinstance(value, dict) or not isinstance(value.get('owner'), str) or type(value.get('expiresAt')) not in (int,float) or not math.isfinite(value['expiresAt']):
            raise LeaseLost('Invalid existing lease')
        if not response.get('ETag'):
            raise LeaseLost('Lease ETag is required')
        return value, response['ETag']

    def acquire(self, ttl_seconds):
        self.expires = time.time()+ttl_seconds
        value, etag = self.read()
        if value is not None and value['expiresAt'] > time.time():
            raise LeaseBusy('Another runner owns the private lease')
        condition = {'IfMatch':etag} if value is not None else {'IfNoneMatch':'*'}
        try:
            response = self.client.put_object(Bucket=self.bucket, Key=LEASE_KEY,
                Body=canonical(dict(version=1,owner=self.owner,expiresAt=self.expires)),
                ContentType='application/json', ServerSideEncryption='AES256', **condition)
        except Exception as exc:
            if precondition(exc):raise LeaseBusy('Concurrent runner acquired lease') from exc
            raise
        self.etag = response.get('ETag')
        self.check()
        return self

    def check(self):
        value, etag = self.read()
        if value is None or value['owner'] != self.owner or value['expiresAt']-time.time() <= LEASE_GUARD_SECONDS:
            raise LeaseLost('Runner no longer owns a safe publication window')
        self.etag, self.expires = etag, value['expiresAt']

    def release(self):
        value, etag = self.read()
        if value is None or value['owner'] != self.owner:
            return False
        try:
            self.client.delete_object(Bucket=self.bucket, Key=LEASE_KEY, IfMatch=etag)
            return True
        except Exception as exc:
            if precondition(exc):return False
            raise


class LeasedClient:
    def __init__(self, client, lease):
        self.client, self.lease = client, lease
    def __getattr__(self, name):
        return getattr(self.client, name)
    def put_object(self, **kwargs):
        self.lease.check(); return self.client.put_object(**kwargs)
    def delete_object(self, **kwargs):
        self.lease.check(); return self.client.delete_object(**kwargs)
    def upload_file(self, path, bucket, key, **kwargs):
        self.lease.check()
        from boto3.s3.transfer import TransferConfig
        last = [0.0]
        def progress(_):
            if self.lease.expires-time.time() <= LEASE_GUARD_SECONDS:
                raise LeaseLost('Upload exceeded the lease safety window')
            if time.monotonic()-last[0] > 30:
                self.lease.check(); last[0] = time.monotonic()
        return self.client.upload_file(path, bucket, key, Callback=progress,
            Config=TransferConfig(use_threads=False), **kwargs)


def create_client(region):
    import boto3
    from botocore.config import Config
    return boto3.client('s3', region_name=region,
        config=Config(connect_timeout=10,read_timeout=30,retries={'total_max_attempts':2}))


def save_actions_state(client, bucket, root):
    root = Path(root)
    archive = root/'checkpoint.tar.gz'
    # Read the successful receipt rather than trusting an archive left by an interrupted upload.
    publisher = strict_json((root/'publisher-state.json').read_bytes())
    receipt = publisher.get('receipts', {}).get(ARCHIVE_KEY)
    if not receipt or not SHA.fullmatch(receipt.get('sha256', '')):
        raise RestoreError('No acknowledged private checkpoint receipt')
    if archive.exists() and file_digest(archive) != receipt['sha256']:
        raise RestoreError('Checkpoint archive differs from acknowledged receipt')
    state = validate_worker_state(strict_json((root/'worker-state.json').read_bytes()))
    client.put_object(Bucket=bucket,Key=STATE_KEY,Body=canonical(dict(version=1,bucket=bucket,
        checkpointSha256=receipt['sha256'],workerState=state,publisherState=publisher)),
        ContentType='application/json',ServerSideEncryption='AES256')


def execute_worker(args, client):
    lease = Lease(client, args.bucket, args.lease_owner)
    lease.check()
    publisher = worker.Publisher(LeasedClient(client, lease), args.bucket, Path(args.root)/'data')
    previous = validate_worker_state(strict_json((Path(args.root)/'worker-state.json').read_bytes()))
    if args.mode == 'daily' and not previous.get('historyTraversalCompleted'):
        raise RuntimeError('Historical traversal must finish before daily mode')
    if args.mode == 'history' and previous.get('historyTraversalCompleted'):
        return 0
    # cloud_worker.run has no EC2/systemd dependency; no shutdown commands are invoked.
    started = time.monotonic()
    options = SimpleNamespace(root=args.root,bucket=args.bucket,region=args.region,
        max_hours=max(.01,(args.max_minutes-10)/60),publish_seconds=30,resume_attempted=True,
        retry_current_day=True,
        review_id=getattr(args,'review_id',None),review_as_of=getattr(args,'review_as_of',None),
        review_dates=getattr(args,'review_dates',None))
    if args.mode == 'review':
        publisher.allowed_review_dates=set(options.review_dates)
        publisher.protected_review_out=Path(args.root)/'review-originals'
    original_observation = worker.write_observation
    def actions_observation(root, status, report, began, active_publisher):
        # The existing EC2 observation uses host uptime and an EC2 hourly rate.
        # Neither is a valid Actions charge; keep usage measurements without inventing a bill.
        observation = original_observation(root,status,report,began,None)
        observation.update(runnerEnvironment='github_actions',observedBootSeconds=None,
            estimatedComputeAndIpv4Usd=None,computeAndIpv4HourlyRateUsd=None,
            estimateScope='runner_runtime_only',estimateExcludes='S3 and possible Actions quota charges',
            publication=dict(active_publisher.metrics) if active_publisher else {})
        safe_id = re.sub(r'[^a-zA-Z0-9._-]','-',status['id'])
        relative = observation['runDate']+'/'+safe_id+'.json'
        private_json(Path(root)/'observations'/relative,observation)
        if active_publisher:
            active_publisher.put_json('collector/observations/'+relative,observation)
        return observation
    worker.write_observation = actions_observation
    try:
        result = worker.run(options, publisher=publisher)
        state_path = Path(args.root)/'worker-state.json'
        calendar_path = Path(args.root)/'data/calendar.json'
        if args.mode != 'review' and result == 0 and state_path.exists() and calendar_path.exists():
            current = strict_json(state_path.read_bytes())
            calendar = strict_json(calendar_path.read_bytes())
            remaining = options.max_hours-(time.monotonic()-started)/3600
            if worker.needs_latest_followup(current,calendar) and remaining > 0:
                options.max_hours = remaining
                result = worker.run(options,publisher=publisher)
        return result
    finally:
        worker.write_observation = original_observation


def spawn_worker(args, log, *, popen=subprocess.Popen):
    command = [sys.executable,str(Path(__file__).resolve()),'--internal-worker','--mode',args.mode,
        '--bucket',args.bucket,'--root',str(args.root),'--region',args.region,
        '--max-minutes',str(args.max_minutes),'--lease-owner',args.lease_owner]
    if args.mode == 'review':
        command += ['--review-id',args.review_id,'--review-as-of',args.review_as_of,
                    '--review-dates',','.join(args.review_dates)]
    process = popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=HERE.parent)
    timed_out = False
    try:
        result = process.wait(timeout=args.max_minutes*60)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:result=process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); result=process.wait(timeout=30)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=30)
    return result, timed_out


def run(args, client, *, executor=spawn_worker, minimum_universe=1000):
    started = time.monotonic()
    root = Path(args.root)
    if root.is_symlink() or root.resolve().is_relative_to(HERE.parents[1]):
        raise RestoreError('Runner root must be outside the checked-out repository')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock_path = root/'.worker.lock'
    fd = os.open(lock_path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise LeaseBusy('Local runner is already active')
        lease = None
        try:
            if args.mode != 'probe':
                lease = Lease(client,args.bucket).acquire(args.max_minutes*60+15*60)
            report = restore(client,args.bucket,root,getattr(args,'expected_checkpoint_sha256',None),minimum_universe=minimum_universe,
                review_dates=getattr(args,'review_dates',None) if args.mode == 'review' else None)
            result = dict(mode=args.mode,restoredCheckpoints=report['checkpointCount'],restoredDates=report['restoredDates'])
            if args.mode == 'probe':
                return dict(result,status='validated',upstreamRequests=0,productionWrites=0)
            if args.mode == 'review':
                originals=root/'review-originals'
                originals.mkdir(mode=0o700)
                for day in args.review_dates:
                    os.link(root/'data'/(day+'.json'),originals/(day+'.json'))
            state = strict_json((root/'worker-state.json').read_bytes())
            if args.mode == 'daily' and not state.get('historyTraversalCompleted'):
                raise RuntimeError('History is unfinished; run history mode first')
            if args.mode == 'history' and state.get('historyTraversalCompleted'):
                return dict(result,status='no_work',historyTraversalCompleted=True)
            remaining = args.max_minutes-(time.monotonic()-started)/60
            if remaining <= 10.1:
                return dict(result,status='paused',budgetExhausted=True,checkpointSaved=True,
                            expectedPause=True,workStarted=False)
            options = SimpleNamespace(**dict(vars(args),lease_owner=lease.owner,max_minutes=remaining))
            log_path = root/'worker-private.log'
            with log_path.open('w') as log:
                os.chmod(log_path,0o600)
                exit_code,timed_out = executor(options,log)
            guarded = LeasedClient(client,lease)
            publisher = worker.Publisher(guarded,args.bucket,root/'data')
            current = validate_worker_state(strict_json((root/'worker-state.json').read_bytes()))
            expected_pause = bool(timed_out or (exit_code != 0 and not current.get('error')
                and current.get('exitReason') in ('interrupted','cutoff','runner_budget_exhausted')))
            if timed_out or exit_code != 0:
                # Children are gone before the coordinator makes its emergency private backup.
                for path in (root/'data/checkpoint').glob('*.json.tmp'):
                    if re.fullmatch(r'[0-9]{6}\.json\.tmp',path.name) and path.is_file() and not path.is_symlink():
                        path.unlink()
                current.update(status='paused',exitReason='runner_budget_exhausted' if timed_out else current.get('exitReason','worker_failed'),
                               runnerBudgetExhausted=timed_out,shutdownReady=False)
                private_json(root/'worker-state.json',current)
                publisher.save_checkpoint()
                publisher.publish_status(current)
            elif not (root/'publisher-state.json').exists():
                publisher.save_checkpoint()
            # A no-op worker may not upload a checkpoint. Capture newly restored receipts if needed.
            receipts = strict_json((root/'publisher-state.json').read_bytes()).get('receipts', {}) if (root/'publisher-state.json').exists() else {}
            if ARCHIVE_KEY not in receipts:
                publisher.save_checkpoint()
            save_actions_state(guarded,args.bucket,root)
            return dict(result,status=current.get('status','paused'),workerExitCode=exit_code,
                budgetExhausted=timed_out,expectedPause=expected_pause,checkpointSaved=True,historyTraversalCompleted=bool(current.get('historyTraversalCompleted')),
                completedStocks=current.get('completedStocks',0),totalStocks=current.get('totalStocks',0),
                reviewId=current.get('reviewId'),reviewCompleted=current.get('reviewCompleted'),
                pendingStockDates=current.get('pendingStockDates'))
        finally:
            if lease is not None:
                lease.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',required=True,choices=('probe','daily','history','auto','review'))
    parser.add_argument('--bucket',required=True);parser.add_argument('--root',required=True)
    parser.add_argument('--region',default='us-east-1');parser.add_argument('--max-minutes',type=float,default=250)
    parser.add_argument('--max-hours',type=float,help='Alternative total budget in hours; includes restore and finalization')
    parser.add_argument('--expected-checkpoint-sha256')
    parser.add_argument('--review-id')
    parser.add_argument('--review-as-of')
    parser.add_argument('--review-dates')
    parser.add_argument('--internal-worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--lease-owner',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.mode == 'review':
        if not args.review_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',args.review_id):
            parser.error('A bounded review ID is required')
        valid_date(args.review_as_of)
        args.review_dates=validate_dates((args.review_dates or '').split(','),allow_empty=False)
        if args.review_as_of > worker.now()[:10] or any(day >= args.review_as_of for day in args.review_dates):
            parser.error('Review only accepts saved dates before its as-of date')
    elif any((args.review_id,args.review_as_of,args.review_dates)):
        parser.error('Review arguments require review mode')
    if args.max_hours is not None:
        if not math.isfinite(args.max_hours):parser.error('Invalid max-hours')
        args.max_minutes=args.max_hours*60
    minimum = 10.1 if args.internal_worker else 12
    if not math.isfinite(args.max_minutes) or not minimum <= args.max_minutes <= 300:
        parser.error('max-minutes must be 12..300, including ten minutes reserved for finalization')
    os.umask(0o077)
    if args.internal_worker:
        if args.mode == 'probe' or not args.lease_owner:
            parser.error('Internal worker requires a collection mode and lease owner')
        return execute_worker(args,create_client(args.region))
    started=time.monotonic()
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    try:
        result=run(args,create_client(args.region))
        print(json.dumps(dict(result,elapsedSeconds=round(time.monotonic()-started,2)),separators=(',',':')))
        return 0 if successful_exit(result) else 1
    except Exception as exc:
        root=Path(args.root)
        if root.is_dir() and not root.is_symlink() and not root.resolve().is_relative_to(HERE.parents[1]):
            with (root/'runner-private-error.log').open('a') as log:
                os.chmod(log.name,0o600);traceback.print_exc(file=log)
        print(json.dumps(dict(status='failed',errorType=type(exc).__name__),separators=(',',':')))
        return 1


def successful_exit(result):
    return (result.get('status') in ('validated','completed','no_work') or
            bool(result.get('expectedPause') and result.get('checkpointSaved')))


if __name__=='__main__':
    raise SystemExit(main())
