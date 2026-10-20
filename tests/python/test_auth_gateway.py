import _bootstrap
"""Security boundaries for the cloud gateway, without contacting AWS."""
import base64
import gzip
import importlib.util
import json
import os
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock


class FakeClientError(Exception):
    def __init__(self, code):
        self.response = {'Error': {'Code': code}}


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.update(USER_POOL_ID='us-east-1_test', USER_POOL_CLIENT_ID='client-test',
                          DATA_BUCKET='private-data', ALLOWED_ORIGIN='https://stock.iteready.com',
                          AWS_REGION='us-east-1')
        cls.old = {key: sys.modules.get(key) for key in ('boto3', 'botocore', 'botocore.exceptions')}
        sys.modules['boto3'] = types.SimpleNamespace(client=lambda name: MagicMock())
        sys.modules['botocore'] = types.ModuleType('botocore')
        sys.modules['botocore.exceptions'] = types.SimpleNamespace(ClientError=FakeClientError)
        spec = importlib.util.spec_from_file_location('stock_auth_test', Path(__file__).parents[2] / 'deploy/auth/handler.py')
        cls.auth = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.auth)

    @classmethod
    def tearDownClass(cls):
        for key, value in cls.old.items():
            if value is None: sys.modules.pop(key, None)
            else: sys.modules[key] = value

    def setUp(self):
        self.auth.cognito.reset_mock(return_value=True, side_effect=True)
        self.auth.s3.reset_mock(return_value=True, side_effect=True)

    def token(self, **change):
        claims = {'iss': self.auth.ISSUER, 'client_id': self.auth.CLIENT,
                  'token_use': 'access', 'exp': time.time() + 3600}
        claims.update(change)
        return 'header.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.signature'

    def event(self, path='/data/manifest.json', token=None, method='GET', origin=None, body=None):
        return {'rawPath': path, 'requestContext': {'http': {'method': method}},
                'headers': {'origin': origin} if origin else {},
                'cookies': [self.auth.ACCESS + '=' + token] if token else [],
                'body': json.dumps(body or {})}

    def test_anonymous_data_is_denied_before_s3(self):
        self.assertEqual(self.auth.handler(self.event(), None)['statusCode'], 401)
        self.auth.s3.get_object.assert_not_called()

    def test_other_pool_client_expired_and_id_tokens_are_denied(self):
        for change in ({'iss': 'other-pool'}, {'client_id': 'other-client'},
                       {'token_use': 'id'}, {'exp': 1}):
            self.assertEqual(self.auth.handler(self.event(token=self.token(**change)), None)['statusCode'], 401)
        self.auth.cognito.get_user.assert_not_called()
        self.auth.s3.get_object.assert_not_called()

    def test_revoked_or_disabled_session_is_denied(self):
        self.auth.cognito.get_user.side_effect = FakeClientError('NotAuthorizedException')
        self.assertEqual(self.auth.handler(self.event(token=self.token()), None)['statusCode'], 401)
        self.auth.s3.get_object.assert_not_called()

    def test_non_object_claims_are_denied_without_cognito_or_s3(self):
        for claims in (None, [], 'claims', 42, True):
            with self.subTest(claims=claims):
                payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
                token = 'header.' + payload + '.signature'
                result = self.auth.handler(self.event(token=token), None)
                self.assertEqual(result['statusCode'], 401)
                self.auth.cognito.get_user.assert_not_called()
                self.auth.s3.get_object.assert_not_called()

    def test_non_object_login_json_is_a_format_error_without_authentication(self):
        for payload in (None, [], 'login', 42, True):
            with self.subTest(payload=payload):
                event = self.event('/auth/login', method='POST', origin=self.auth.ORIGIN)
                event['body'] = json.dumps(payload)
                result = self.auth.handler(event, None)
                self.assertEqual(result['statusCode'], 400)
                self.auth.cognito.admin_initiate_auth.assert_not_called()
                self.auth.s3.get_object.assert_not_called()

    def test_authorized_data_and_no_cache(self):
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        self.auth.s3.get_object.return_value = {'Body': types.SimpleNamespace(read=lambda: b'{"rows":[]}')}
        result = self.auth.handler(self.event(token=self.token()), None)
        self.assertEqual(result['statusCode'], 200)
        self.assertEqual(result['headers']['cache-control'], 'no-store, private')
        self.auth.s3.get_object.assert_called_once_with(Bucket='private-data', Key='data/manifest.json')

    def test_raw_files_and_traversal_are_not_exported(self):
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        for path in ('/data/checkpoint/000001.json', '/data/../secret.json', '/data/universe.json'):
            self.assertEqual(self.auth.handler(self.event(path, token=self.token()), None)['statusCode'], 404)
        self.auth.s3.get_object.assert_not_called()

    def test_collection_status_requires_current_session(self):
        path = '/data/collection-status.json'
        for token in (None, 'forged', self.token(exp=1)):
            self.assertEqual(self.auth.handler(self.event(path, token=token), None)['statusCode'], 401)
        self.auth.s3.get_object.assert_not_called()
        self.auth.cognito.get_user.side_effect = FakeClientError('NotAuthorizedException')
        self.assertEqual(self.auth.handler(self.event(path, token=self.token()), None)['statusCode'], 401)
        self.auth.s3.get_object.assert_not_called()

    def test_collection_status_reads_only_named_private_object(self):
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        body = b'{"id":"run1","state":"running","completedStocks":12,"totalStocks":5556}'
        self.auth.s3.get_object.return_value = {'Body': types.SimpleNamespace(read=lambda: body)}
        result = self.auth.handler(self.event('/data/collection-status.json', token=self.token()), None)
        self.assertEqual(result['statusCode'], 200)
        self.assertEqual(json.loads(result['body']), json.loads(body))
        self.assertEqual(result['headers']['cache-control'], 'no-store, private')
        self.auth.s3.get_object.assert_called_once_with(Bucket='private-data', Key='data/collection-status.json')

    def test_collection_status_does_not_expand_to_internal_run_files_or_writes(self):
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        for path in ('/data/run-status.json', '/data/collection-status.json.bak',
                     '/data/checkpoint/collection-status.json', '/data/../collection-status.json'):
            self.assertEqual(self.auth.handler(self.event(path, token=self.token()), None)['statusCode'], 404)
        self.assertEqual(self.auth.handler(self.event('/data/collection-status.json', token=self.token(),
            method='POST', origin=self.auth.ORIGIN), None)['statusCode'], 404)
        self.auth.s3.get_object.assert_not_called()

    def test_missing_collection_status_returns_404(self):
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        self.auth.s3.get_object.side_effect = FakeClientError('NoSuchKey')
        self.assertEqual(self.auth.handler(self.event('/data/collection-status.json', token=self.token()), None)['statusCode'], 404)

    def test_unpublished_date_is_404_without_requesting_hidden_missing_key(self):
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        manifest = {'dates': [{'date': '2026-09-30', 'file': '2026-09-30.json'}]}
        def read(**args):
            if args['Key'] == 'data/manifest.json':
                return {'Body': types.SimpleNamespace(read=lambda: json.dumps(manifest).encode())}
            # The production reader has GetObject without ListBucket. A missing
            # date therefore reports AccessDenied, rather than NoSuchKey.
            raise FakeClientError('AccessDenied')
        self.auth.s3.get_object.side_effect = read
        result = self.auth.handler(self.event('/data/2026-10-08.json', token=self.token()), None)
        self.assertEqual(result['statusCode'], 404)
        self.assertEqual(json.loads(result['body'])['error'], '数据尚未保存')
        self.auth.s3.get_object.assert_called_once_with(Bucket='private-data', Key='data/manifest.json')

    def test_saved_date_requires_committed_manifest_and_preserves_payload(self):
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        payload = {'date': '2026-09-30', 'rows': [{'code': '000001', 'ratio': 12.5}]}
        manifest = {'dates': [{'date': payload['date'], 'file': payload['date']+'.json'}]}
        def read(**args):
            value = manifest if args['Key'] == 'data/manifest.json' else payload
            return {'Body': types.SimpleNamespace(read=lambda: json.dumps(value).encode())}
        self.auth.s3.get_object.side_effect = read
        result = self.auth.handler(self.event('/data/2026-09-30.json', token=self.token()), None)
        self.assertEqual(result['statusCode'], 200)
        self.assertEqual(json.loads(result['body']), payload)
        self.assertEqual(self.auth.s3.get_object.call_count, 2)

    def test_saved_date_access_failure_is_not_misreported_as_unsaved(self):
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        manifest = {'dates': [{'date': '2026-09-30', 'file': '2026-09-30.json'}]}
        def read(**args):
            if args['Key'] == 'data/manifest.json':
                return {'Body': types.SimpleNamespace(read=lambda: json.dumps(manifest).encode())}
            raise FakeClientError('AccessDenied')
        self.auth.s3.get_object.side_effect = read
        self.assertEqual(self.auth.handler(self.event('/data/2026-09-30.json', token=self.token()), None)['statusCode'], 503)

    def test_unavailable_publication_manifest_is_a_service_error(self):
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        for payload in ({}, {'dates': None}, {'dates': 'unknown'}):
            with self.subTest(payload=payload):
                self.auth.s3.get_object.return_value = {
                    'Body': types.SimpleNamespace(read=lambda: json.dumps(payload).encode())}
                self.assertEqual(self.auth.handler(self.event('/data/2026-09-30.json', token=self.token()), None)['statusCode'], 503)

    def test_embedded_template_handler_is_current(self):
        from deploy.auth.chip_bundle import inline_handler
        directory = Path(__file__).parents[2] / 'deploy/auth'
        template = json.loads((directory / 'template.json').read_text(encoding='utf-8'))
        self.assertEqual(template['Resources']['Function']['Properties']['Code']['ZipFile'],
                         inline_handler())

    def test_full_market_gzip_fits_lambda_envelope_and_decodes_exactly(self):
        observation = {'source': 'sina', 'stage': 'refresh', 'status': 'unverified',
            'first15Volume': 775576, 'dailyVolume': 4541564, 'minuteDayVolume': 4496964,
            'dayVolumeDifference': -44600, 'dayVolumeDifferencePct': -0.982041,
            'referenceRatio': 17.077289, 'reason': '成交量未完全核对一致'}
        payload = {'date': '2026-09-04', 'scope': 'full', 'total': 5556,
            'rows': [dict(observation, code=f'{index:06d}', name=f'测试股票{index}', ratio=None,
                open=10.1, high=10.8, low=9.9, close=10.5,
                priceStatus='available', priceSourceProvider='sina',
                reconciliationAttempts=[dict(observation, stage=stage) for stage in
                    ('initial', 'refresh', 'fallback')]) for index in range(5556)]}
        identity = self.auth.response(200, payload)
        self.assertGreater(len(json.dumps(identity).encode()), 6000000)
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        manifest = {'dates': [{'date': payload['date'], 'file': payload['date']+'.json'}]}
        def read(**args):
            value = manifest if args['Key'] == 'data/manifest.json' else payload
            return {'Body': types.SimpleNamespace(read=lambda: json.dumps(value).encode())}
        self.auth.s3.get_object.side_effect = read
        event = self.event('/data/2026-09-04.json', token=self.token())
        event['headers']['accept-encoding'] = 'br, gzip, deflate'
        result = self.auth.handler(event, None)
        self.assertEqual(result['statusCode'], 200)
        self.assertTrue(result['isBase64Encoded'])
        self.assertEqual(result['headers']['content-encoding'], 'gzip')
        self.assertIn('Accept-Encoding', result['headers']['vary'])
        self.assertLess(len(json.dumps(result).encode()), 5300000)
        self.assertEqual(json.loads(gzip.decompress(base64.b64decode(result['body']))), payload)

    def test_gzip_negotiation_and_oversized_identity_fail_safely(self):
        for header in ('gzip;q=0, *;q=1', 'br', '', 'gzip;q=invalid', 'gzip;q=1.1'):
            self.assertFalse(self.auth.accepts_gzip(header))
        for header in ('gzip', 'br, gzip;q=0.8', '*;q=0.2', 'GZIP'):
            self.assertTrue(self.auth.accepts_gzip(header))
        payload = {'large': '测试' * 700000}
        result = self.auth.data_response(payload, 'gzip;q=0')
        self.assertEqual(result['statusCode'], 413)
        self.assertNotIn('isBase64Encoded', result)
        self.assertNotIn('content-encoding', result['headers'])

    def test_small_data_and_auth_errors_remain_plain_json(self):
        result = self.auth.data_response({'state': 'running'}, 'gzip')
        self.assertEqual(json.loads(result['body']), {'state': 'running'})
        self.assertNotIn('isBase64Encoded', result)
        event = self.event('/data/collection-status.json')
        event['headers']['accept-encoding'] = 'gzip'
        error = self.auth.handler(event, None)
        self.assertEqual(error['statusCode'], 401)
        self.assertNotIn('isBase64Encoded', error)
        self.assertEqual(error['headers']['vary'], 'Origin')

    def test_cross_origin_login_is_rejected(self):
        for origin in (None, 'https://evil.example'):
            self.assertEqual(self.auth.handler(self.event('/auth/login', method='POST', origin=origin), None)['statusCode'], 403)
        self.auth.cognito.admin_initiate_auth.assert_not_called()

    def test_login_cookies_are_http_only_and_not_returned_in_body(self):
        self.auth.cognito.admin_initiate_auth.return_value = {'AuthenticationResult': {
            'AccessToken': 'access-test', 'RefreshToken': 'refresh-test', 'ExpiresIn': 3600}}
        result = self.auth.handler(self.event('/auth/login', method='POST', origin=self.auth.ORIGIN,
            body={'username': 'edward', 'password': 'not-a-real-password'}), None)
        self.assertEqual(result['statusCode'], 200)
        self.assertNotIn('access-test', result['body'])
        for cookie in result['cookies']:
            self.assertIn('Secure; HttpOnly; SameSite=Strict', cookie)
            self.assertNotIn('Domain=', cookie)

    def test_logout_revokes_refresh_and_clears_cookies(self):
        event = self.event('/auth/logout', method='POST', origin=self.auth.ORIGIN)
        event['cookies'] = [self.auth.REFRESH + '=refresh-test']
        result = self.auth.handler(event, None)
        self.auth.cognito.revoke_token.assert_called_once_with(Token='refresh-test', ClientId='client-test')
        self.assertTrue(all('Max-Age=0' in c for c in result['cookies']))


if __name__ == '__main__':
    unittest.main()
