import _bootstrap
"""Real serialized snapshots, receipts and archives; no network or AWS credentials."""
import gc
import io
import json
import sys
import tarfile
import tempfile
import types
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cloud_worker import w, FakeS3


class RecordingS3(FakeS3):
    def __init__(self):
        super().__init__()
        self.metadata = {}
        self.fail = None

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey()
        return {'Metadata': self.metadata.get(Key, {}), 'ETag': self.etag(Key)}

    def put_object(self, **args):
        if args['Key'] == self.fail:
            raise OSError('injected failed upload')
        super().put_object(**args)
        self.metadata[args['Key']] = args.get('Metadata', {})

    def upload_file(self, path, bucket, key, **kwargs):
        self.put_object(Bucket=bucket, Key=key, Body=Path(path).read_bytes(),
                        Metadata=kwargs.get('ExtraArgs', {}).get('Metadata', {}))


class PublisherReceiptsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / 'data'
        self.out.mkdir()
        self.day = w.now()[:10]
        self.client = RecordingS3()
        self.payload = dict(date=self.day, generatedAt='v1', attemptedCount=1, rows=[dict(
            code='000001', status='ok', quality='matched', dayVolumeDifference=0,
            first15Volume=15, dailyVolume=100, ratio=15)])
        self.entry = dict(date=self.day, file=self.day + '.json', generatedAt='v1', total=1, valid=1)
        self.snapshot()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.out / name).write_text(json.dumps(value))

    def snapshot(self, version='v1'):
        self.payload['generatedAt'] = self.entry['generatedAt'] = version
        self.write(self.day + '.json', self.payload)
        self.write('manifest.json', dict(generatedAt=version, dates=[self.entry]))

    def publisher(self):
        return w.Publisher(self.client, 'test-bucket', self.out)

    def test_restart_and_envelope_timestamp_change_skip_all_unchanged_objects(self):
        self.write('universe.json', dict(asOf=self.day, total=1, rows=[dict(code='000001', name='平安银行')]))
        self.publisher().publish(True)
        original = dict(self.client.objects)
        self.client.writes.clear()
        self.snapshot('v2')
        self.publisher().publish(True)
        self.assertEqual(self.client.writes, [])
        self.assertEqual(self.client.objects, original)
        self.assertEqual(json.loads(original['data/manifest.json'])['dates'][0]['generatedAt'], 'v1')

    def test_real_row_change_uploads_date_then_manifest(self):
        self.publisher().publish(True)
        self.client.writes.clear()
        self.payload['rows'][0].update(first15Volume=20, ratio=20)
        self.snapshot('v2')
        self.publisher().publish(True)
        self.assertEqual(self.client.writes, ['data/' + self.day + '.json', 'data/manifest.json'])
        actual = json.loads(self.client.objects[self.client.writes[0]])
        self.assertEqual(actual['rows'][0]['ratio'], 20)

    def test_failed_manifest_is_not_acknowledged_and_restart_finishes_directory(self):
        self.client.fail = 'data/manifest.json'
        with self.assertRaises(OSError):
            self.publisher().publish(True)
        receipts = json.loads((self.root / 'publisher-state.json').read_text())['receipts']
        self.assertNotIn('data/manifest.json', receipts)
        self.client.fail = None
        self.client.writes.clear()
        self.publisher().publish(True)
        self.assertEqual(self.client.writes, ['data/manifest.json'])

    def test_remote_receipt_hash_mismatch_forces_upload_after_restart(self):
        self.publisher().publish(True)
        key = 'data/' + self.day + '.json'
        self.client.objects[key] = b'corrupted'
        self.client.metadata[key] = {'sha256': 'wrong'}
        self.client.writes.clear()
        self.publisher().publish(True)
        self.assertEqual(self.client.writes, [key])
        self.assertEqual(json.loads(self.client.objects[key]), self.payload)

    def test_staged_hash_tamper_rejects_entire_batch_before_upload(self):
        publisher = self.publisher()
        stage = publisher._stage
        def tamper(*args):
            item = stage(*args)
            if item['key'].endswith(self.day + '.json'):
                item['path'].write_bytes(b'changed')
            return item
        with patch.object(publisher, '_stage', side_effect=tamper):
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                publisher.publish(True)
        self.assertFalse(self.client.writes)

    def test_collector_advances_during_upload_never_commits_old_manifest(self):
        publisher = self.publisher()
        upload = publisher._upload_staged
        def advance(item):
            result = upload(item)
            self.snapshot('v2')
            return result
        with patch.object(publisher, '_upload_staged', side_effect=advance):
            self.assertEqual(publisher.publish(True), 0)
        self.assertNotIn('data/manifest.json', self.client.writes)

    def test_invalid_second_day_prevents_first_day_upload(self):
        from datetime import date, timedelta
        older = (date.fromisoformat(self.day) - timedelta(days=1)).isoformat()
        self.write(older + '.json', dict(date=older, generatedAt='v1', rows=[dict(code='bad')]))
        self.write('manifest.json', dict(dates=[self.entry, dict(date=older, file=older + '.json', generatedAt='v1')]))
        with self.assertRaises(ValueError):
            self.publisher().publish(True)
        self.assertFalse(self.client.writes)

    def test_day_payloads_are_released_before_network_upload(self):
        class TrackedDay(dict):
            pass
        references = []
        validate = w.validate_day
        put = self.client.put_object
        def track(value, day):
            result = TrackedDay(validate(value, day))
            references.append(weakref.ref(result))
            return result
        def check(**args):
            gc.collect()
            self.assertTrue(references)
            self.assertTrue(all(ref() is None for ref in references))
            return put(**args)
        with patch.object(w, 'validate_day', side_effect=track), patch.object(self.client, 'put_object', side_effect=check):
            self.publisher().publish(True)

    def test_checkpoint_content_skip_restart_and_restore_all_pending_files(self):
        (self.out / 'checkpoint').mkdir()
        self.write('checkpoint/000001.json', {'observations': ['preserved']})
        self.write('universe.json', {'total': 1})
        self.write('calendar.json', {'tradingDates': [self.day]})
        self.write('run-status.json', {'pendingStockDates': 1})
        self.write('daily-state.json', {'pending': ['000001:' + self.day]})
        (self.out / 'daily-attempts.jsonl').write_text('{"code":"000001"}\n')
        expected = {str(p.relative_to(self.out)): p.read_bytes() for p in self.out.rglob('*') if p.is_file() and p.name not in ('manifest.json', self.day + '.json')}
        self.assertTrue(self.publisher().save_checkpoint())
        self.client.writes.clear()
        (self.out / 'daily-state.json').touch()
        self.assertFalse(self.publisher().save_checkpoint())
        self.assertFalse(self.client.writes)
        restored = self.root / 'restored'
        restored.mkdir()
        with tarfile.open(fileobj=io.BytesIO(self.client.objects['collector/checkpoint.tar.gz']), mode='r:gz') as archive:
            for member in archive.getmembers():
                self.assertTrue(member.isfile())
                self.assertFalse(Path(member.name).is_absolute())
                self.assertNotIn('..', Path(member.name).parts)
                target = restored / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
        self.assertEqual(expected, {str(p.relative_to(restored)): p.read_bytes() for p in restored.rglob('*') if p.is_file()})
        self.write('daily-state.json', {'pending': []})
        self.assertTrue(self.publisher().save_checkpoint())
        self.assertEqual(self.client.writes, ['collector/checkpoint.tar.gz'])

    def test_failed_archive_upload_has_no_receipt(self):
        self.write('daily-state.json', {'pending': []})
        self.client.fail = 'collector/checkpoint.tar.gz'
        publisher = self.publisher()
        with self.assertRaises(OSError):
            publisher.save_checkpoint()
        self.assertNotIn('collector/checkpoint.tar.gz', publisher.state.receipts)

    def test_corrupt_receipt_file_forces_safe_reupload(self):
        self.publisher().publish(True)
        (self.root / 'publisher-state.json').write_text('{"version":1,"bucket":"test-bucket","receipts":[]}')
        self.client.writes.clear()
        self.publisher().publish(True)
        self.assertEqual(self.client.writes, ['data/' + self.day + '.json', 'data/manifest.json'])

    def test_pending_records_override_stock_pending_for_public_status(self):
        publisher = self.publisher()
        publisher.publish_status(dict(status='running', dates=[self.day], pendingCount=5,
                                      pendingStockDates=126, completedStocks=1, totalStocks=2))
        self.assertEqual(json.loads(self.client.objects['data/collection-status.json'])['pendingCount'], 126)


class DailyWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'data').mkdir()
        w.atomic_json(self.root / 'worker-state.json', {'historyTraversalCompleted': True})

    def tearDown(self):
        self.temp.cleanup()

    def test_no_op_does_not_publish_snapshots_expire_or_backup(self):
        self.check_no_op(True)

    def test_no_op_with_dispatch_only_is_paused_and_does_not_publish(self):
        self.check_no_op(False)

    def check_no_op(self, terminal):
        publisher = types.SimpleNamespace(manifest={}, metrics={}, publish_status=lambda _: None,
                                         put_json=lambda *_: None)
        calls = []
        for name in ('publish', 'expire', 'save_checkpoint'):
            setattr(publisher, name, lambda *a, method=name, **k: calls.append(method))
        def popen(*args, **kwargs):
            started = json.loads((self.root / 'worker-state.json').read_text())['startedAt']
            w.atomic_json(self.root / 'data/run-status.json', dict(startedAt=started, noOp=True,
                exitReason='no_work' if terminal else 'retry_next_trading_day', targetCount=5556,
                attemptedCount=5556 if terminal else 5552, latestTraversalCompleted=terminal, traversalCompleted=terminal))
            return types.SimpleNamespace(stdout=[], poll=lambda: 0, wait=lambda: 0)
        modules = {'boto3': types.SimpleNamespace(client=lambda *a, **kw: None),
                   'botocore.config': types.SimpleNamespace(Config=lambda **kw: None)}
        args = types.SimpleNamespace(root=str(self.root), region='test', bucket='test', resume_attempted=False,
                                     max_hours=1, publish_seconds=30)
        with patch.dict(sys.modules, modules), patch.object(w.subprocess, 'Popen', side_effect=popen), patch.object(w.signal, 'signal'):
            self.assertEqual(w.run(args, publisher), 0 if terminal else 1)
        self.assertFalse(calls)
        records = list((self.root / 'observations').rglob('*.json'))
        self.assertEqual(len(records), 1)
        result = json.loads(records[0].read_text())
        self.assertTrue(result['noOp'])
        self.assertEqual(result['status'], 'completed' if terminal else 'paused')
        self.assertFalse(result['publishedBefore22'])
        self.assertFalse(result['benchmarkPassed'])

    def test_old_pending_cutoff_does_not_erase_successful_latest_publication(self):
        publisher = types.SimpleNamespace(manifest={}, metrics={}, publish_status=lambda _: None,
            put_json=lambda *_: None, publish=lambda **_: 1, expire=lambda: None, save_checkpoint=lambda: None)
        def popen(*args, **kwargs):
            started = json.loads((self.root / 'worker-state.json').read_text())['startedAt']
            w.atomic_json(self.root / 'data/run-status.json', dict(startedAt=started, noOp=False,
                exitReason='cutoff', targetCount=5556, attemptedCount=5556, latestTraversalCompleted=True,
                traversalCompleted=True, pendingStockDates=100, totalStockDates=700056,
                completedStockDates=699956, dates=['2026-09-04', '2026-09-07']))
            return types.SimpleNamespace(stdout=[], poll=lambda: 0, wait=lambda: 0)
        modules = {'boto3': types.SimpleNamespace(client=lambda *a, **kw: None),
                   'botocore.config': types.SimpleNamespace(Config=lambda **kw: None)}
        args = types.SimpleNamespace(root=str(self.root), region='test', bucket='test', resume_attempted=False,
                                     max_hours=1, publish_seconds=30)
        with patch.dict(sys.modules, modules), patch.object(w.subprocess, 'Popen', side_effect=popen), patch.object(w.signal, 'signal'), patch.object(w, 'now', return_value='2026-09-07T21:56:00+08:00'):
            self.assertEqual(w.run(args, publisher), 1)
        state = json.loads((self.root / 'worker-state.json').read_text())
        self.assertEqual(state['status'], 'paused')
        self.assertTrue(state['shutdownReady'])
        self.assertEqual(state['lastSuccessfulUpdate'], '2026-09-07T21:56:00+08:00')
        record = json.loads(next((self.root / 'observations').rglob('*.json')).read_text())
        self.assertTrue(record['benchmarkPassed'])
        self.assertEqual(record['pendingCount'], 100)

    def test_full_day_benchmark_requires_real_current_day_traversal_and_early_publication(self):
        publisher = types.SimpleNamespace(metrics={}, put_json=lambda *_: None)
        status = dict(id='run-1', phase='daily', startedAt='2026-09-07T19:00:00+08:00',
                      status='completed', exitCode=0, publicationCommitted=True,
                      lastDataPublicationAt='2026-09-07T21:50:00+08:00', completedStocks=5556, totalStocks=5556)
        report = dict(dates=['2026-09-07'], targetCount=5556, attemptedCount=5556,
                      latestTraversalCompleted=True, pendingStockDates=17, exitReason='retry_next_trading_day')
        with patch.object(w, 'now', return_value='2026-09-07T21:50:00+08:00'), patch.object(w.time, 'monotonic', return_value=100):
            record = w.write_observation(self.root, status, report, 10, publisher)
            self.assertTrue(record['baselineEligible'])
            self.assertTrue(record['benchmarkPassed'])
            self.assertEqual(record['pendingCount'], 17)
            self.assertEqual(record['elapsedSeconds'], 90)
            partial = w.write_observation(self.root, status, dict(report, attemptedCount=5500, latestTraversalCompleted=False), 10, publisher)
            self.assertFalse(partial['benchmarkPassed'])
            old = w.write_observation(self.root, status, dict(report, dates=['2026-09-04']), 10, publisher)
            self.assertFalse(old['baselineEligible'])
            backlog = w.write_observation(self.root, dict(status, status='paused'), dict(report, exitReason='cutoff'), 10, publisher)
            self.assertTrue(backlog['benchmarkPassed'])
            failed = w.write_observation(self.root, dict(status, exitCode=1), dict(report, exitReason='failed'), 10, publisher)
            self.assertFalse(failed['benchmarkPassed'])
        with patch.object(w, 'now', return_value='2026-09-07T22:01:00+08:00'):
            late = w.write_observation(self.root, dict(status, lastDataPublicationAt='2026-09-07T22:01:00+08:00'), report, 10, publisher)
            self.assertFalse(late['publishedBefore22'])
            self.assertFalse(late['benchmarkPassed'])


if __name__ == '__main__':
    unittest.main()
