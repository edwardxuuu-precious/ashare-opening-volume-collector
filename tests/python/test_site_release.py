import _bootstrap
import contextlib
import hashlib
import io
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(_bootstrap.ROOT / 'tooling'))
import aws_stock
import release_site


class SiteReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        authority = self.root / 'src/lib/site-routes.ts'
        authority.parent.mkdir(parents=True)
        shutil.copyfile(_bootstrap.ROOT / 'src/lib/site-routes.ts', authority)
        self.routes = release_site.required_html(self.root)
        self.client = self.root / 'dist/client'
        for relative in (*self.routes, 'assets/main.js'):
            path = self.client / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('<html>Protected page</html>', encoding='utf-8')
        self.aws = self.mock('release_site.aws')
        self.account = self.mock('release_site.verify_account')
        self.upload = self.mock('release_site.urllib.request.urlopen')
        self.mock('release_site.time.sleep', side_effect=None)
        self.mock('release_site.ROOT', new=self.root)

    def mock(self, target, **kwargs):
        if 'new' not in kwargs:
            kwargs.setdefault('side_effect', AssertionError('Unexpected external call: ' + target))
        replacement = patch(target, **kwargs)
        self.addCleanup(replacement.stop)
        return replacement.start()

    def run_main(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            release_site.main(list(map(str, args)))
        return json.loads(output.getvalue())

    def artifact(self, *, extra=(), omit=(), name='reviewed.zip'):
        path = self.root / name
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='Duplicate name:')
            with zipfile.ZipFile(path, 'w') as archive:
                for relative in self.routes:
                    if relative not in omit:
                        archive.writestr(relative, '<html>Protected page</html>')
                for relative in extra:
                    archive.writestr(relative, 'extra')
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def assert_no_external_calls(self):
        self.aws.assert_not_called()
        self.account.assert_not_called()
        self.upload.assert_not_called()

    def enable_publish(self, *, status='SUCCEED'):
        self.account.side_effect = None
        self.upload.side_effect = None
        self.upload.return_value.__enter__ = Mock(return_value=Mock(status=200))
        self.upload.return_value.__exit__ = Mock(return_value=False)

        def respond(args, **kwargs):
            operation = args[1]
            if operation == 'list-jobs':
                return {'jobSummaries': [{'jobId': '84', 'status': 'SUCCEED'}]}
            if operation == 'create-deployment':
                return {'jobId': '85', 'zipUploadUrl':
                        'https://uploads.example/site?X-Amz-Signature=private-signature'}
            if operation == 'start-deployment':
                return {}
            if operation == 'get-job':
                return {'job': {'summary': {'status': status}}}
            self.fail('Unexpected AWS operation ' + operation)

        self.aws.side_effect = respond
        return respond

    def test_package_preserves_api_and_contains_every_current_route_and_actual_asset(self):
        self.assertEqual(len(self.routes), 11)
        self.assertIn('golden/video-space.html', self.routes)
        self.assertTrue({'concentration.html', 'triangles.html', 'cycle-bottom.html'} <= set(self.routes))
        output = self.root / 'outputs/releases/site.zip'
        value = release_site.package(self.root, output)
        self.assertEqual(set(value), {'artifact', 'sha256', 'bytes', 'files'})
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {*self.routes, 'assets/main.js'})
        self.assertEqual(value['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(value['bytes'], output.stat().st_size)
        self.assertEqual(value['files'], len(self.routes) + 1)
        self.assertTrue((self.client / 'assets/main.js').is_file())
        self.assert_no_external_calls()

    def test_both_dry_run_modes_report_reviewable_metadata_without_aws(self):
        artifact = self.root / 'portable.zip'
        packaged = self.run_main('--dry-run', '--output', artifact)
        reviewed = self.run_main('--dry-run', '--artifact', artifact,
                                 '--expected-sha256', packaged['sha256'])
        self.assertFalse(packaged['published'])
        self.assertFalse(reviewed['published'])
        self.assertTrue(reviewed['reused_reviewed_artifact'])
        self.assertEqual(packaged['sha256'], reviewed['sha256'])
        self.assertFalse(artifact.with_name('portable-deployment.json').exists())
        self.assert_no_external_calls()

    def test_both_artifact_sources_require_triangles_and_concentration_before_aws(self):
        for missing in ('triangles.html', 'concentration.html'):
            with self.subTest(missing=missing, source='build'):
                source = self.client / missing
                source.unlink()
                with self.assertRaisesRegex(RuntimeError, missing):
                    self.run_main('--dry-run', '--output', self.root / 'missing.zip')
                self.assertFalse((self.root / 'missing.zip').exists())
                source.write_text('<html>Restored route</html>', encoding='utf-8')
            with self.subTest(missing=missing, source='reviewed'):
                artifact, digest = self.artifact(omit={missing})
                with self.assertRaisesRegex(RuntimeError, missing):
                    self.run_main('--artifact', artifact, '--expected-sha256', digest,
                                  '--expected-job', '84')
        self.assert_no_external_calls()

    def test_cli_requires_explicit_reviewed_artifact_hash_and_job(self):
        artifact, digest = self.artifact()
        invalid = [(), ('--dry-run',), ('--output', self.root / 'new.zip'),
                   ('--artifact', artifact, '--expected-job', '84'),
                   ('--dry-run', '--artifact', artifact),
                   ('--artifact', artifact, '--expected-sha256', digest),
                   ('--dry-run', '--artifact', artifact, '--expected-sha256', 'bad'),
                   ('--dry-run', '--output', self.root / 'new.zip', '--expected-sha256', digest)]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(SystemExit) as result:
                self.run_main(*args)
            self.assertEqual(result.exception.code, 2)
        with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
            self.run_main('--artifact', artifact, '--expected-sha256', '0' * 64,
                          '--expected-job', '84')
        self.assert_no_external_calls()

    def test_reviewed_zip_rejects_unsafe_duplicate_public_private_and_symlink_entries(self):
        link = zipfile.ZipInfo('assets/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        entries = ['/absolute.txt', '../escape.txt', 'assets/../../escape.txt',
                   r'assets\escape.txt', 'C:/escape.txt', 'assets/file:stream',
                   'assets//invalid.txt', 'index.html', 'INDEX.HTML', 'data/',
                   'data/market.json', 'assets/data/market.json', 'assets/private/token.json',
                   'assets/.env.production', 'assets/key.PEM', 'assets/key.key',
                   'assets/.aws/credentials', 'assets/secrets.json', link]
        for entry in entries:
            with self.subTest(entry=entry):
                artifact, digest = self.artifact(extra=[entry])
                with self.assertRaises(RuntimeError):
                    self.run_main('--artifact', artifact, '--expected-sha256', digest,
                                  '--expected-job', '84')
        self.assert_no_external_calls()

    def test_new_packages_reject_private_paths_symlinks_and_unsafe_outputs(self):
        for relative in ('data/market.json', 'nested/private/token.json',
                         'nested/.env.production', 'nested/key.pem'):
            with self.subTest(relative=relative):
                path = self.client / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('private', encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'sensitive artifact'):
                    release_site.package(self.root, self.root / 'rejected.zip')
                shutil.rmtree(self.client / relative.split('/')[0])
        link = self.client / 'assets/link'
        link.symlink_to(self.root / 'src/lib/site-routes.ts')
        with self.assertRaisesRegex(RuntimeError, 'Symlinks'):
            release_site.package(self.root, self.root / 'rejected.zip')
        link.unlink()
        with self.assertRaisesRegex(RuntimeError, 'outside dist/client'):
            release_site.package(self.root, self.client / 'recursive.zip')
        existing = self.root / 'existing.zip'
        existing.write_bytes(b'keep existing reviewed bytes')
        with self.assertRaisesRegex(RuntimeError, 'new .zip file'):
            release_site.package(self.root, existing)
        self.assertEqual(existing.read_bytes(), b'keep existing reviewed bytes')
        self.assert_no_external_calls()

    def test_missing_node_bad_bridge_output_and_missing_authority_fail_closed(self):
        for outcome in (FileNotFoundError(), subprocess.CompletedProcess([], 0, 'invalid'),
                        subprocess.CompletedProcess([], 0, '["/", "/"]'),
                        subprocess.CompletedProcess([], 0, '["/", "/index"]'),
                        subprocess.CompletedProcess([], 0, '["/../escape"]')):
            with self.subTest(outcome=outcome):
                with patch('release_site.subprocess.run', side_effect=outcome if isinstance(outcome, Exception)
                           else None, return_value=outcome):
                    with self.assertRaises(RuntimeError):
                        release_site.package(self.root, self.root / 'invalid.zip')
        (self.root / 'src/lib/site-routes.ts').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Cannot read sitePaths'):
            release_site.package(self.root, self.root / 'invalid.zip')
        self.assertFalse((self.root / 'invalid.zip').exists())
        self.assert_no_external_calls()

    def test_publication_forwards_profile_region_and_uploads_verified_snapshot(self):
        artifact, digest = self.artifact()
        reviewed = artifact.read_bytes()
        receipt = artifact.with_name('reviewed-deployment.json')
        respond = self.enable_publish()
        self.account.side_effect = lambda **kwargs: artifact.write_bytes(b'changed after review')

        def aws(args, **kwargs):
            if args[1] in ('create-deployment', 'start-deployment'):
                record = json.loads(receipt.read_text())
                self.assertEqual(record['state'], {'create-deployment': 'CREATE_REQUESTED',
                                                   'start-deployment': 'START_REQUESTED'}[args[1]])
            return respond(args, **kwargs)

        self.aws.side_effect = aws
        result = self.run_main('--artifact', artifact, '--expected-sha256', digest,
                               '--expected-job', '84', '--profile', 'dev', '--region', 'ap-southeast-1')
        self.account.assert_called_once_with(profile='dev', region='ap-southeast-1')
        self.assertTrue(all(call.kwargs == {'profile': 'dev', 'region': 'ap-southeast-1'}
                            for call in self.aws.call_args_list))
        request = self.upload.call_args.args[0]
        self.assertEqual(request.data, reviewed)
        self.assertEqual(request.get_method(), 'PUT')
        self.assertEqual(result['state'], 'SUCCEED')
        self.assertEqual(Path(result['receipt']), receipt)
        record = json.loads(receipt.read_text())
        self.assertEqual(record['sha256'], digest)
        self.assertEqual(record['job_id'], '85')
        self.assertEqual(record['host'], release_site.platform.node())
        self.assertEqual(record['platform'], release_site.platform.system())
        self.assertNotIn('X-Amz-Signature', receipt.read_text() + json.dumps(result))

    def test_account_guard_prevents_amplify_calls(self):
        artifact, digest = self.artifact()
        self.account.side_effect = aws_stock.verify_account
        with patch('aws_stock.aws', return_value={'Account': 'wrong-account'}) as identity:
            with self.assertRaisesRegex(RuntimeError, 'Unexpected AWS account'):
                self.run_main('--artifact', artifact, '--expected-sha256', digest,
                              '--expected-job', '84', '--profile', 'dev', '--region', 'us-west-2')
        identity.assert_called_once_with(['sts', 'get-caller-identity'],
                                         profile='dev', region='us-west-2')
        self.aws.assert_not_called()
        self.upload.assert_not_called()

    def test_latest_job_guard_prevents_writes_for_changed_unhealthy_or_absent_release(self):
        artifact, digest = self.artifact()
        self.account.side_effect = None
        self.aws.side_effect = None
        for jobs in ([{'jobId': 'newer', 'status': 'SUCCEED'}],
                     [{'jobId': '84', 'status': 'FAILED'}], []):
            with self.subTest(jobs=jobs):
                self.aws.reset_mock()
                self.aws.return_value = {'jobSummaries': jobs}
                with self.assertRaisesRegex(RuntimeError, 'Amplify advanced'):
                    self.run_main('--artifact', artifact, '--expected-sha256', digest,
                                  '--expected-job', '84')
                self.assertEqual(self.aws.call_count, 1)
                self.assertEqual(self.aws.call_args.args[0][1], 'list-jobs')
        self.assertFalse(artifact.with_name('reviewed-deployment.json').exists())
        self.upload.assert_not_called()

    def test_ambiguous_writes_preserve_job_receipt_and_block_automatic_retry(self):
        for stage in ('CREATE', 'UPLOAD', 'START'):
            with self.subTest(stage=stage):
                artifact, digest = self.artifact(name=stage + '.zip')
                receipt = artifact.with_name(stage + '-deployment.json')
                respond = self.enable_publish()

                def aws(args, **kwargs):
                    if args[1] == {'CREATE': 'create-deployment', 'START': 'start-deployment'}.get(stage):
                        raise TimeoutError('Do not log a presigned URL')
                    return respond(args, **kwargs)

                self.aws.side_effect = aws
                if stage == 'UPLOAD':
                    self.upload.side_effect = TimeoutError('Do not log a presigned URL')
                with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                    self.run_main('--artifact', artifact, '--expected-sha256', digest,
                                  '--expected-job', '84')
                record = json.loads(receipt.read_text())
                self.assertEqual(record['state'], stage + '_UNKNOWN')
                if stage != 'CREATE':
                    self.assertEqual(record['job_id'], '85')
                self.assertNotIn('X-Amz-Signature', receipt.read_text())
                calls = self.aws.call_count
                with self.assertRaisesRegex(RuntimeError, 'receipt already exists'):
                    self.run_main('--artifact', artifact, '--expected-sha256', digest,
                                  '--expected-job', '84')
                self.assertEqual(self.aws.call_count, calls)

    def test_known_job_polling_is_bounded_and_keeps_last_readback(self):
        artifact, digest = self.artifact()
        self.enable_publish(status='RUNNING')
        with self.assertRaises(SystemExit) as result:
            self.run_main('--artifact', artifact, '--expected-sha256', digest, '--expected-job', '84')
        self.assertEqual(result.exception.code, 1)
        polls = [call for call in self.aws.call_args_list if call.args[0][1] == 'get-job']
        self.assertEqual(len(polls), 60)
        self.assertTrue(all(call.args[0][-2:] == ['--job-id', '85'] for call in polls))
        record = json.loads(artifact.with_name('reviewed-deployment.json').read_text())
        self.assertEqual(record['job_id'], '85')
        self.assertEqual(record['job_status'], 'RUNNING')


class AwsProfileTests(unittest.TestCase):
    def test_cli_wrapper_preserves_defaults_and_forwards_explicit_account_context(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({'Account': aws_stock.EXPECTED_ACCOUNT}), '')
        with patch('aws_stock.shutil.which', return_value='/local/aws'), \
                patch('aws_stock.subprocess.run', return_value=result) as execute:
            for expected in ({'profile': 'stock-dev', 'region': 'us-east-1'},
                             {'profile': 'dev', 'region': 'ap-southeast-1'}):
                with self.subTest(expected=expected):
                    kwargs = {} if expected['profile'] == 'stock-dev' else expected
                    self.assertEqual(aws_stock.verify_account(**kwargs)['Account'], aws_stock.EXPECTED_ACCOUNT)
                    args = execute.call_args.args[0]
                    self.assertEqual(args[args.index('--profile') + 1], expected['profile'])
                    self.assertEqual(args[args.index('--region') + 1], expected['region'])
                    self.assertEqual(execute.call_args.kwargs['env']['AWS_MAX_ATTEMPTS'], '1')
                    self.assertEqual(execute.call_args.kwargs['timeout'], 45)


if __name__ == '__main__':
    unittest.main()
