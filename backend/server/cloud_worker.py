#!/usr/bin/env python3
"""Single cloud writer: bounded AKShare collection, private progress, manifest-last S3 publication."""
from __future__ import annotations
import argparse, fcntl, hashlib, json, math, os, queue, re, signal, subprocess, sys, threading, time, tempfile, uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'server'))
from publisher_state import PublisherState, canonical, digest_bytes, file_digest, semantic_digest, checkpoint_digest
from retention import six_month_start, accumulated_manifest
from download_only import POLICY

DATE_FILE = re.compile(r'^\d{4}-\d{2}-\d{2}\.json$')

def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')

def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False))
    tmp.replace(path)

def validate_day(data, date):
    if data.get('date') != date or not isinstance(data.get('rows'), list):
        raise ValueError('Date or rows mismatch')
    codes = [row.get('code') for row in data['rows']]
    if len(codes) != len(set(codes)):
        raise ValueError('Duplicate stock')
    for row in data['rows']:
        if not isinstance(row.get('code'), str) or not re.fullmatch(r'\d{6}', row['code']):
            raise ValueError('Invalid stock code')
        ratio = row.get('ratio')
        if ratio is not None:
            first, daily = row.get('first15Volume'), row.get('dailyVolume')
            if row.get('status') != 'ok' or (row.get('calculationPolicy') != POLICY and
                    (row.get('quality') != 'matched' or row.get('dayVolumeDifference') != 0)):
                raise ValueError('Unverified ratio cannot be published')
            if not all(type(x) in (int, float) and math.isfinite(x) for x in (ratio, first, daily)):
                raise ValueError('Invalid numeric value')
            if daily <= 0 or not 0 <= first <= daily or not 0 <= ratio <= 100 or abs(ratio - first / daily * 100) > 1e-5:
                raise ValueError('Invalid ratio arithmetic')
    return data

class Publisher:
    """Validate to private disk, upload one date at a time, and publish its directory last."""
    def __init__(self, client, bucket, out):
        self.client, self.bucket, self.out = client, bucket, Path(out)
        self.state = PublisherState(self.out.parent / 'publisher-state.json', bucket)
        self.verified = set()
        self.latest_published = {}
        self.metrics = dict(uploadedObjects=0, uploadedBytes=0, skippedObjects=0, checkpointUploads=0)
        try:
            self.manifest = json.loads(client.get_object(Bucket=bucket, Key='data/manifest.json')['Body'].read())
        except client.exceptions.NoSuchKey:
            self.manifest = {'dates': []}

    def receipt(self, key):
        receipt = self.state.receipts.get(key)
        if not receipt or key in self.verified:
            return receipt
        try:
            if hasattr(self.client, 'head_object'):
                remote = self.client.head_object(Bucket=self.bucket, Key=key)
                actual = remote.get('Metadata', {}).get('sha256')
            else:
                actual = digest_bytes(self.client.get_object(Bucket=self.bucket, Key=key)['Body'].read())
            if actual != receipt['sha256']:
                self.state.forget(key)
                return None
        except self.client.exceptions.NoSuchKey:
            self.state.forget(key)
            return None
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
                self.state.forget(key)
                return None
            raise
        self.verified.add(key)
        return receipt

    def put_json(self, key, value):
        body = canonical(value)
        digest = digest_bytes(body)
        receipt = self.receipt(key)
        if receipt and receipt['sha256'] == digest:
            self.metrics['skippedObjects'] += 1
            return False
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body,
                               ContentType='application/json; charset=utf-8', CacheControl='no-store',
                               ServerSideEncryption='AES256', Metadata={'sha256': digest})
        self.state.record(key, digest, semantic_digest(value), value.get('generatedAt'))
        self.verified.add(key)
        self.metrics['uploadedObjects'] += 1
        self.metrics['uploadedBytes'] += len(body)
        return True

    def publish_status(self, status):
        visible = dict(status, **self.latest_published, state=status['status'], id=status.get('id', 'full-market'),
                       latestDate=max(status.get('dates') or [status.get('latestDate', '')]))
        if 'pendingStockDates' in status:
            visible['pendingCount'] = status['pendingStockDates']
        self.put_json('data/collection-status.json', visible)

    def _stage(self, folder, key, value):
        body = canonical(value)
        target = Path(folder) / key.removeprefix('data/')
        target.write_bytes(body)
        return {'key': key, 'path': target, 'sha256': digest_bytes(body),
                'semantic': semantic_digest(value), 'generatedAt': value.get('generatedAt')}

    def _upload_staged(self, item):
        if file_digest(item['path']) != item['sha256']:
            raise ValueError('Staged publication hash changed')
        receipt = self.receipt(item['key'])
        if receipt and receipt['sha256'] == item['sha256']:
            self.metrics['skippedObjects'] += 1
            return False
        # Keep only this one date's serialized bytes resident during its upload.
        body = item['path'].read_bytes()
        if digest_bytes(body) != item['sha256']:
            raise ValueError('Staged publication hash changed while reading')
        self.client.put_object(Bucket=self.bucket, Key=item['key'], Body=body,
            ContentType='application/json; charset=utf-8', CacheControl='no-store',
            ServerSideEncryption='AES256', Metadata={'sha256': item['sha256']})
        self.state.record(item['key'], item['sha256'], item['semantic'], item['generatedAt'])
        self.verified.add(item['key'])
        self.metrics['uploadedObjects'] += 1
        self.metrics['uploadedBytes'] += len(body)
        return True

    def publish(self, all_dates=False):
        path = self.out / 'manifest.json'
        if not path.exists():
            return 0
        source_bytes = path.read_bytes()
        source = json.loads(source_bytes)
        start = six_month_start(now()[:10])
        allowed = getattr(self,'allowed_review_dates',None)
        if allowed is not None and any(d['date'] not in allowed for d in source.get('dates',[])):
            raise ValueError('Review attempted publication outside frozen dates')
        entries = sorted([d for d in source.get('dates', []) if allowed is not None or d['date'] >= start], key=lambda d: d['date'], reverse=True)
        if not entries:
            return 0
        if not all_dates:
            entries = entries[:1]
        previous = {d['date']: d for d in self.manifest.get('dates', [])}
        selected_count = 0
        with tempfile.TemporaryDirectory(prefix='.publish-', dir=self.out.parent) as folder:
            staged = []
            newest = None
            # Validate every selected date before the first data/catalog upload; retain only disk copies.
            for entry in entries:
                if not DATE_FILE.fullmatch(entry['file']) or entry['file'] != entry['date'] + '.json':
                    raise ValueError('Unsafe date filename')
                payload = validate_day(json.loads((self.out / entry['file']).read_text()), entry['date'])
                protected=getattr(self,'protected_review_out',None)
                if protected is not None:
                    original=validate_day(json.loads((Path(protected)/entry['file']).read_text()),entry['date'])
                    incoming={row['code']:row for row in payload['rows']}
                    for row in original['rows']:
                        if row.get('status') in ('ok','suspended') and incoming.get(row['code']) != row:
                            raise ValueError('Historical review changed an already verified row')
                if payload.get('generatedAt') != entry.get('generatedAt'):
                    return 0
                key = 'data/' + entry['file']
                semantic = semantic_digest(payload)
                receipt = self.receipt(key)
                published_entry = dict(entry)
                if receipt and receipt.get('semantic') == semantic:
                    # Keep the actually published generation when only the local clock was rewritten.
                    published_entry['generatedAt'] = receipt.get('generatedAt', payload.get('generatedAt'))
                    self.metrics['skippedObjects'] += 1
                else:
                    staged.append(self._stage(folder, key, payload))
                previous[entry['date']] = published_entry
                if newest is None:
                    newest = dict(publishedStocks=payload.get('attemptedCount', 0), latestPublishedDate=entry['date'],
                                  latestPublishedAt=published_entry.get('generatedAt'))
                selected_count += 1
                del payload
            catalog_path = self.out / 'universe.json'
            if catalog_path.exists():
                catalog = json.loads(catalog_path.read_text())
                rows = [{'code': r['code'], 'name': r['name']} for r in catalog['rows']]
                if len(rows) != catalog['total'] or len({r['code'] for r in rows}) != len(rows):
                    raise ValueError('Invalid catalog')
                staged.insert(0, self._stage(folder, 'data/stocks.json', dict(asOf=catalog['asOf'],
                    historicalMembership=False, total=len(rows), rows=rows)))
                del catalog, rows
            manifest = accumulated_manifest(dict(source, dates=sorted(previous.values(), key=lambda d: d['date'], reverse=True),
                            automationStatus='cloud_collector', collectorStatusPath='/data/collection-status.json'))
            if semantic_digest(manifest) == semantic_digest(self.manifest):
                manifest['generatedAt'] = self.manifest.get('generatedAt', manifest.get('generatedAt'))
            manifest_stage = self._stage(folder, 'data/manifest.json', manifest)
            if path.read_bytes() != source_bytes:
                return 0
            # Recheck every immutable stage before allowing any upload.
            if any(file_digest(item['path']) != item['sha256'] for item in staged + [manifest_stage]):
                raise ValueError('Staged publication hash mismatch')
            for item in staged:
                self._upload_staged(item)
            # Never commit an old directory after a concurrent collector has advanced it.
            if path.read_bytes() != source_bytes:
                return 0
            self._upload_staged(manifest_stage)
            self.manifest = manifest
            self.latest_published = newest or {}
        return selected_count

    def expire(self):
        """Published dates accumulate permanently; only local working caches may shrink."""
        return None

    def save_checkpoint(self):
        import gzip, tarfile
        archive = self.out.parent / 'checkpoint.tar.gz'
        digest, members = checkpoint_digest(self.out)
        receipt = self.receipt('collector/checkpoint.tar.gz')
        if receipt and receipt.get('semantic') == digest:
            self.metrics['skippedObjects'] += 1
            return False
        with archive.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode='w') as target:
                for name, expected in members:
                    file = self.out / name
                    if file_digest(file) != expected:
                        raise ValueError('Checkpoint changed before archive')
                    info = target.gettarinfo(str(file), arcname=name)
                    info.mtime = info.uid = info.gid = 0
                    info.uname = info.gname = ''
                    with file.open('rb') as source:
                        target.addfile(info, source)
        if checkpoint_digest(self.out)[0] != digest:
            raise ValueError('Checkpoint changed during archive')
        archive_digest = file_digest(archive)
        self.client.upload_file(str(archive), self.bucket, 'collector/checkpoint.tar.gz',
            ExtraArgs={'ServerSideEncryption':'AES256', 'Metadata':{'sha256': archive_digest, 'content-sha256': digest}})
        self.state.record('collector/checkpoint.tar.gz', archive_digest, digest)
        self.verified.add('collector/checkpoint.tar.gz')
        self.metrics['checkpointUploads'] += 1
        self.metrics['uploadedBytes'] += archive.stat().st_size
        return True


def history_resume_dates(previous, today=None):
    """Finish the frozen first traversal before adding a newly closed trading day."""
    if previous.get('historyTraversalCompleted'):
        return []
    today = today or now()[:10]
    start = six_month_start(today)
    return sorted({d for d in previous.get('dates', [])
                   if isinstance(d, str) and DATE_FILE.fullmatch(d + '.json') and start <= d <= today})


def collector_command(python, out, history=True, resume_attempted=False, dates=None, review_id=None,
                      review_as_of=None, retry_current_day=False):
    if not history:
        command = [python, str(ROOT / 'scripts/daily_collector.py'), '--out', str(out),
                '--workers', '4', '--interval', '0.75', '--cutoff', '23:55', '--source-policy', 'sina']
        if review_id:
            if not dates or not review_as_of or any(day >= review_as_of for day in dates):
                raise ValueError('Review requires frozen prior dates')
            command += ['--review-id',review_id,'--review-as-of',review_as_of,'--dates',','.join(dates)]
        elif retry_current_day:
            command += ['--retry-current-day']
        return command
    cmd = [python, str(ROOT / 'scripts/collect.py'), '--full', '--out', str(out), '--resume', '--publish-every', '50', '--interval', '0.75', '--efficient-sina', '--source-policy', 'sina']
    cmd += ['--dates', ','.join(dates)] if history and dates else (['--months', '6'] if history else ['--days', '1'])
    if history:
        cmd += ['--reconcile-latest-first']
    if resume_attempted:
        cmd += ['--resume-attempted']
    return cmd


def needs_latest_followup(state, calendar):
    return bool(state.get('historyTraversalCompleted') and state.get('status') == 'completed'
                and max(calendar.get('tradingDates') or ['']) > max(state.get('dates') or ['']))


def initial_progress(previous, manifest):
    """Use known scope/date for visible startup status without counting sample rows as processed."""
    total = manifest.get('universeTotal', previous.get('totalStocks', 0))
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        total = 0
    dates = [entry.get('date', '') for entry in manifest.get('dates', []) if isinstance(entry, dict)]
    dates += previous.get('dates', [])
    dates.append(previous.get('latestDate', ''))
    dates = [d for d in dates if isinstance(d, str) and DATE_FILE.fullmatch(d + '.json')]
    return dict(completedStocks=0, totalStocks=total, latestDate=max(dates or ['']))


def write_observation(root, status, report, started, publisher):
    ended = now()
    elapsed = round(time.monotonic() - started, 3)
    run_date = status['startedAt'][:10]
    today_target = report.get('todayTargetCount', report.get('targetCount', 0))
    today_attempted = report.get('todayAttemptedCount', report.get('attemptedCount', 0))
    latest_complete = bool(report.get('latestTraversalCompleted', False))
    latest_date = report.get('latestDate') or max(report.get('dates', status.get('dates', [])) or [''])
    baseline_eligible = bool(status['phase'] == 'daily' and not report.get('noOp') and latest_date == run_date and today_target > 0)
    published_at = status.get('lastDataPublicationAt')
    before_22 = bool(status.get('publicationCommitted') and not report.get('noOp') and published_at
                     and published_at[:10] == run_date and published_at[11:16] < '22:00')
    normal_exit = status.get('exitCode') == 0 and report.get('exitReason') in ('completed', 'retry_next_trading_day', 'cutoff')
    benchmark = bool(baseline_eligible and normal_exit and latest_complete and
                     today_attempted == today_target and before_22)
    execution_environment = 'github_actions' if os.environ.get('GITHUB_ACTIONS') == 'true' else 'ec2'
    boot_seconds = None
    if execution_environment == 'ec2':
        try:
            boot_seconds = round(float(Path('/proc/uptime').read_text().split()[0]), 3)
        except (OSError, ValueError, IndexError):
            pass
    observation = dict(id=status['id'], phase=status['phase'], runDate=run_date,
        runDayKey=status['phase'] + ':' + run_date, startedAt=status['startedAt'], endedAt=ended,
        elapsedSeconds=elapsed, status=status.get('status'), exitReason=report.get('exitReason', status.get('exitReason')),
        exitCode=status.get('exitCode'), noOp=bool(report.get('noOp')), dates=report.get('dates', status.get('dates', [])),
        todayTargetCount=today_target, todayAttemptedCount=today_attempted,
        totalStocks=status.get('totalStocks', 0), completedStocks=status.get('completedStocks', 0),
        totalStockDates=report.get('totalStockDates'), completedStockDates=report.get('completedStockDates'),
        pendingCount=report.get('pendingStockDates', report.get('pendingCount')),
        httpRequests=report.get('httpRequests', status.get('httpRequests', 0)), cacheHits=report.get('cacheHits', 0),
        sourceFailureCounts=report.get('sourceFailureCounts', {}),
        latestDate=latest_date, latestTraversalCompleted=latest_complete,
        publicationCommitted=bool(status.get('publicationCommitted')), publishedBefore22=before_22,
        todayTraversalCompletedAt=report.get('todayTraversalCompletedAt'), lastDataPublicationAt=published_at,
        baselineEligible=baseline_eligible, benchmarkPassed=benchmark, lastSuccessfulUpdate=status.get('lastSuccessfulUpdate'),
        publication=dict(publisher.metrics) if publisher is not None else {}, observedBootSeconds=boot_seconds,
        executionEnvironment=execution_environment,
        estimatedComputeAndIpv4Usd=(round((boot_seconds if boot_seconds is not None else elapsed) / 3600 * 0.0466, 6)
                                   if execution_environment == 'ec2' else None),
        computeAndIpv4HourlyRateUsd=0.0466 if execution_environment == 'ec2' else None,
        estimateScope=('boot_to_observation' if boot_seconds is not None else 'worker_runtime_only')
                      if execution_environment == 'ec2' else 'not_estimated',
        estimateExcludes='storage, requests, startup before measurement and shutdown delay'
                         if execution_environment == 'ec2' else 'GitHub Actions billing, AWS storage and requests')
    safe_id = re.sub(r'[^a-zA-Z0-9._-]', '-', status['id'])
    atomic_json(Path(root) / 'observations' / run_date / (safe_id + '.json'), observation)
    if publisher is not None:
        publisher.put_json('collector/observations/' + run_date + '/' + safe_id + '.json', observation)
    return observation


def run(args, publisher=None):
    import boto3
    from botocore.config import Config
    started = time.monotonic()
    root = Path(args.root); out = root / 'data'; out.mkdir(parents=True, exist_ok=True)
    state_path = root / 'worker-state.json'
    previous = json.loads(state_path.read_text()) if state_path.exists() else {}
    seed_path = out / 'manifest.json'
    seed = json.loads(seed_path.read_text()) if seed_path.exists() else {}
    review_id=getattr(args,'review_id',None)
    history = not previous.get('historyTraversalCompleted', False) and not review_id
    resumed_dates = getattr(args,'review_dates',None) if review_id else history_resume_dates(previous)
    status = dict(id='full-market-' + now().replace(':', '').replace('+', '-') + '-' + uuid.uuid4().hex[:8], status='running', phase='review' if review_id else ('history' if history else 'daily'), startedAt=now(), updatedAt=now(),
                  completedStocks=0, totalStocks=0, dates=resumed_dates, historyTraversalCompleted=not history,
                  serviceInvocationId=os.environ.get('INVOCATION_ID', ''), shutdownReady=False, collectionSourcePolicy=['sina'],
                  calculationPolicy=None,message='正在准备下载股票数据')
    if review_id:status.update(reviewId=review_id,reviewAsOf=args.review_as_of,reviewCompleted=False,
        message='正在整理已有数据并补齐历史下载，暂停新增当日数据')
    status.update(initial_progress(previous, seed))
    status['lastSuccessfulUpdate'] = previous.get('lastSuccessfulUpdate')
    atomic_json(state_path, status)
    client = None
    try:
        client = boto3.client('s3', region_name=args.region, config=Config(connect_timeout=10, read_timeout=30, retries={'max_attempts':3}))
        publisher = publisher or Publisher(client, args.bucket, out)
        status.update(initial_progress(previous, seed or publisher.manifest))
    except Exception as exc:
        status.update(status='paused', state='paused', error=type(exc).__name__, updatedAt=now(),
                      message='云端发布连接初始化失败，采集尚未启动；已有数据仍可查询')
        atomic_json(state_path, status)
        if client is not None:
            try:
                client.put_object(Bucket=args.bucket, Key='data/collection-status.json',
                                  Body=json.dumps(status, ensure_ascii=False).encode(),
                                  ContentType='application/json; charset=utf-8', CacheControl='no-store',
                                  ServerSideEncryption='AES256')
            except Exception:
                pass
        status.update(exitCode=1, exitReason='publisher_initialization_failed')
        write_observation(root, status, {}, started, None)
        return 1
    stop_requested = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop_requested.set())
    try:
        publisher.publish_status(status); atomic_json(state_path, status)
        command = collector_command(sys.executable, out, history=history,
            resume_attempted=args.resume_attempted, dates=resumed_dates,
            review_id=review_id, review_as_of=getattr(args,'review_as_of',None),
            retry_current_day=getattr(args,'retry_current_day',False))
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except Exception as exc:
        status.update(status='paused', error=type(exc).__name__, updatedAt=now(), message='云端采集启动失败，已有数据仍可查询')
        atomic_json(state_path, status)
        try: publisher.publish_status(status)
        except Exception: pass
        status.update(exitCode=1, exitReason='collector_start_failed')
        try: write_observation(root, status, {}, started, publisher)
        except Exception: pass
        return 1
    messages = queue.Queue()
    def consume():
        for line in process.stdout:
            # The protected journal contains endpoint error types only, no credentials.
            print(line.rstrip(), flush=True)
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    messages.put(value)
            except ValueError:
                pass
    threading.Thread(target=consume, daemon=True).start()
    last_sync = 0.; publish_error = None
    has_data_activity = history
    report = {}
    try:
        while process.poll() is None:
            if stop_requested.is_set() or time.monotonic() - started > args.max_hours * 3600:
                status.update(status='paused', message='采集已暂停，断点已保留')
                process.terminate()
                try:process.wait(timeout=90)
                except subprocess.TimeoutExpired:process.kill(); process.wait()
                break
            try:
                msg = messages.get(timeout=1)
                for key in ('completedStocks', 'totalStocks', 'dates', 'code', 'httpRequests', 'cacheHits', 'elapsedSeconds',
                            'completedStockDates', 'totalStockDates', 'pendingStockDates', 'pendingCount', 'reviewCompleted', 'attemptedThisRun', 'collectionSourcePolicy', 'calculationPolicy'):
                    if key in msg: status[key] = msg[key]
                if 'pendingStockDates' in msg: status['pendingCount'] = msg['pendingStockDates']
                if msg.get('stage') == 'collecting' or msg.get('publishCount', 0) > 0:
                    has_data_activity = True
            except queue.Empty:
                pass
            if time.monotonic() - last_sync >= args.publish_seconds:
                status['updatedAt'] = now()
                if has_data_activity: publisher.publish(all_dates=False)
                publisher.publish_status(status); atomic_json(state_path, status)
                last_sync = time.monotonic()
        exitcode = process.wait()
        report_path = out / 'run-status.json'
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        if report.get('startedAt', '') >= status['startedAt']:
            status.update(completedStocks=report.get('attemptedCount', status['completedStocks']),
                          totalStocks=report.get('targetCount', status['totalStocks']),
                          dates=report.get('dates', status['dates']), exitReason=report.get('exitReason'))
            for key in ('completedStockDates', 'totalStockDates', 'pendingStockDates'):
                if key in report: status[key] = report[key]
            if 'pendingStockDates' in report: status['pendingCount'] = report['pendingStockDates']
        else:
            report = {}
        if review_id:
            status['reviewCompleted']=bool(exitcode == 0 and report.get('reviewCompleted'))
        no_op = bool(not history and report.get('noOp') and report.get('exitReason') in ('no_work', 'retry_next_trading_day'))
        complete = (exitcode == 0 and status['totalStocks'] > 0 and status['completedStocks'] == status['totalStocks']) if history else (
            exitcode == 0 and bool(report.get('traversalCompleted', report.get('latestTraversalCompleted', False))))
        if review_id:complete = status['reviewCompleted']
        # A cutoff can kill dispatched requests. A complete current day is recorded separately
        # from unfinished history, and must not turn the entire paused run into "completed".
        if not history and report.get('exitReason') in ('cutoff', 'interrupted', 'failed'):
            complete = False
        if status['status'] != 'paused':
            status.update(status='completed' if complete else 'paused', message=('当前没有新交易日或到期补采任务，已有结果保持不变' if no_op else '本轮下载完成') if complete else '本轮下载已暂停，缺失数据留待补采')
        if review_id:
            status['message']='历史问题记录已复核一遍，仍有差异或缺失的记录保持标记' if complete else '历史复核已暂停，未完成记录保留断点；未录入当日数据'
        status['historyTraversalCompleted'] = bool(previous.get('historyTraversalCompleted') or (complete and history))
        status.update(exitCode=exitcode, updatedAt=now())
        if not no_op:
            published = publisher.publish(all_dates=True)
            if published == 0 and (out / 'manifest.json').exists():
                raise RuntimeError('Final publication deferred by concurrent snapshot change')
            status['publicationCommitted'] = published > 0
            if published > 0: status['lastDataPublicationAt'] = now()
            publisher.expire()
            publisher.save_checkpoint()
            normal_exit = exitcode == 0 and report.get('exitReason') in ('completed', 'retry_next_trading_day', 'cutoff')
            latest_complete = bool(report.get('latestTraversalCompleted') and status['totalStocks'] > 0
                                   and status['completedStocks'] == status['totalStocks'])
            if status['publicationCommitted'] and ((history and complete) or (not history and normal_exit and latest_complete)):
                status['lastSuccessfulUpdate'] = status['lastDataPublicationAt']
        status['shutdownReady'] = True
        publisher.publish_status(status); atomic_json(state_path, status)
        write_observation(root, status, report, started, publisher)
        return 0 if complete else 1
    except Exception as exc:
        publish_error = type(exc).__name__
        raise
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=90)
            except subprocess.TimeoutExpired:process.kill(); process.wait()
        if publish_error:
            status['publicationCommitted'] = False
            status.update(status='paused', shutdownReady=False, message='云端发布失败，采集已停止；本地断点保留', error=publish_error, updatedAt=now())
            atomic_json(state_path, status)
            try: publisher.publish_status(status)
            except Exception: pass
            try: write_observation(root, status, report, started, publisher)
            except Exception: pass


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bucket', required=True); p.add_argument('--root', default='/var/lib/stock-collector')
    p.add_argument('--region', default='us-east-1'); p.add_argument('--max-hours', type=float, default=10)
    p.add_argument('--publish-seconds', type=float, default=30); p.add_argument('--resume-attempted', action='store_true')
    a = p.parse_args()
    if not 0 < a.max_hours <= 24 or a.publish_seconds < 10: p.error('Invalid runtime or publication bounds')
    Path(a.root).mkdir(parents=True, exist_ok=True)
    with (Path(a.root) / '.worker.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return 0
        started = time.monotonic()
        result = run(a)
        state_path = Path(a.root) / 'worker-state.json'
        calendar_path = Path(a.root) / 'data/calendar.json'
        if result == 0 and state_path.exists() and calendar_path.exists():
            state = json.loads(state_path.read_text())
            calendar = json.loads(calendar_path.read_text())
            remaining = a.max_hours - (time.monotonic() - started) / 3600
            if needs_latest_followup(state, calendar) and remaining > 0:
                # A resumed historical pass can finish after another trading day closed.
                # Catch up in the same boot without resetting the total request-time budget.
                a.max_hours = remaining
                result = run(a)
        return result

if __name__ == '__main__':
    raise SystemExit(main())
