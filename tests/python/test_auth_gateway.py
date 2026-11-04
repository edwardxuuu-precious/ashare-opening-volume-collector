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
from unittest.mock import MagicMock, patch


class FakeClientError(Exception):
    def __init__(self, code):
        self.response = {'Error': {'Code': code}}


class FakeBotoCoreError(Exception):
    pass


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.update(USER_POOL_ID='us-east-1_test', USER_POOL_CLIENT_ID='client-test',
                          DATA_BUCKET='private-data', ALLOWED_ORIGIN='https://stock.iteready.com',
                          AWS_REGION='us-east-1')
        cls.old = {key: sys.modules.get(key) for key in ('boto3', 'botocore', 'botocore.exceptions')}
        sys.modules['boto3'] = types.SimpleNamespace(client=lambda name: MagicMock())
        sys.modules['botocore'] = types.ModuleType('botocore')
        sys.modules['botocore.exceptions'] = types.SimpleNamespace(ClientError=FakeClientError,
            BotoCoreError=FakeBotoCoreError)
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
                  'token_use': 'access', 'auth_time': time.time(), 'exp': time.time() + 3600}
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
        import base64
        import hashlib
        from io import BytesIO
        import re
        import zipfile
        from deploy.auth.chip_bundle import inline_handler
        directory = Path(__file__).parents[2] / 'deploy/auth'
        template = json.loads((directory / 'template.json').read_text(encoding='utf-8'))

        def contents(code):
            match = re.search(r"_bundle64\.b64decode\('([A-Za-z0-9+/=]+)'\)", code)
            self.assertIsNotNone(match)
            raw = base64.b64decode(match.group(1), validate=True)
            digest = hashlib.sha256(raw).hexdigest()
            library = f"_chip_library='/tmp/stock-chip-library-{digest}.zip'"
            self.assertIn(library, code)
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                modules = []
                for item in archive.infolist():
                    self.assertIn(item.compress_type, (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))
                    modules.append((item.filename, item.compress_type, archive.read(item)))
            normalized = code.replace(match.group(0), "_bundle64.b64decode('<shared-module-archive>')", 1)
            normalized = normalized.replace(library, "_chip_library='<shared-module-path>'", 1)
            return modules, normalized

        self.assertEqual(contents(template['Resources']['Function']['Properties']['Code']['ZipFile']),
                         contents(inline_handler()))

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

    def refresh_event(self, path='/auth/me', token=None, method='GET', origin=None, body=None):
        event = self.event(path, token=token, method=method, origin=origin, body=body)
        event['cookies'].append(self.auth.REFRESH + '=refresh-test')
        return event

    def renewed_token(self, **claims):
        token = self.token(**claims)
        self.auth.cognito.admin_initiate_auth.return_value = {'AuthenticationResult': {
            'AccessToken': token, 'ExpiresIn': 3600}}
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        return token

    def assert_cleared_session(self, result):
        self.assertEqual(result['statusCode'], 401)
        self.assertEqual(len(result['cookies']), 2)
        self.assertEqual({value.split('=', 1)[0] for value in result['cookies']},
                         {self.auth.ACCESS, self.auth.REFRESH})
        self.assertTrue(all('Max-Age=0' in value for value in result['cookies']))

    def test_login_issues_fifteen_day_persistent_refresh_cookie(self):
        self.auth.cognito.admin_initiate_auth.return_value = {'AuthenticationResult': {
            'AccessToken': 'access-test', 'RefreshToken': 'refresh-test', 'ExpiresIn': 3600}}
        result = self.auth.handler(self.event('/auth/login', method='POST', origin=self.auth.ORIGIN,
            body={'username': 'edward', 'password': 'not-a-real-password'}), None)
        cookies = {value.split('=', 1)[0]: value for value in result['cookies']}
        self.assertIn('Max-Age=1296000', cookies[self.auth.REFRESH])
        self.assertIn('Max-Age=3600', cookies[self.auth.ACCESS])
        for value in cookies.values():
            self.assertIn('Path=/; Secure; HttpOnly; SameSite=Strict', value)
            self.assertNotIn('Domain=', value)

    def test_valid_access_is_checked_online_without_refresh(self):
        token = self.token()
        self.auth.cognito.get_user.return_value = {'Username': 'edward'}
        result = self.auth.handler(self.refresh_event(token=token), None)
        self.assertEqual(result['statusCode'], 200)
        self.auth.cognito.get_user.assert_called_once_with(AccessToken=token)
        self.auth.cognito.admin_initiate_auth.assert_not_called()
        self.assertNotIn('cookies', result)

    def test_missing_and_naturally_expired_access_refresh_the_original_request(self):
        for token in (None, self.token(auth_time=time.time() - 7200, exp=time.time() - 1)):
            with self.subTest(access='missing' if token is None else 'expired'):
                self.auth.cognito.reset_mock(return_value=True, side_effect=True)
                renewed = self.renewed_token()
                result = self.auth.handler(self.refresh_event(token=token), None)
                self.assertEqual(result['statusCode'], 200)
                self.assertEqual(json.loads(result['body']), {'username': 'edward'})
                self.auth.cognito.admin_initiate_auth.assert_called_once_with(
                    UserPoolId=self.auth.POOL, ClientId=self.auth.CLIENT,
                    AuthFlow='REFRESH_TOKEN_AUTH', AuthParameters={'REFRESH_TOKEN': 'refresh-test'})
                self.auth.cognito.get_user.assert_called_once_with(AccessToken=renewed)
                self.assertEqual(len(result['cookies']), 1)
                self.assertTrue(result['cookies'][0].startswith(self.auth.ACCESS + '='))
                self.assertNotIn(self.auth.REFRESH, result['cookies'][0])
                self.auth.s3.get_object.assert_not_called()

    def test_bad_access_cannot_turn_into_a_refresh_attempt(self):
        missing_auth_time = {'iss': self.auth.ISSUER, 'client_id': self.auth.CLIENT,
                             'token_use': 'access', 'exp': time.time() + 3600}
        payload = base64.urlsafe_b64encode(json.dumps(missing_auth_time).encode()).decode().rstrip('=')
        tokens = ['forged', self.token(iss='other-pool'), self.token(client_id='other-client'),
                  self.token(token_use='id'), self.token(auth_time=None), self.token(exp=float('nan')),
                  self.token(exp=10**400), self.token(auth_time=10**400),
                  'header.' + payload + '.signature']
        for token in tokens:
            with self.subTest(token=tokens.index(token)):
                result = self.auth.handler(self.refresh_event(token=token), None)
                self.assert_cleared_session(result)
        self.auth.cognito.get_user.assert_not_called()
        self.auth.cognito.admin_initiate_auth.assert_not_called()
        self.auth.s3.get_object.assert_not_called()

    def test_revoked_valid_access_does_not_fall_back_to_refresh(self):
        self.auth.cognito.get_user.side_effect = FakeClientError('NotAuthorizedException')
        result = self.auth.handler(self.refresh_event(token=self.token()), None)
        self.assert_cleared_session(result)
        self.auth.cognito.admin_initiate_auth.assert_not_called()
        self.auth.s3.get_object.assert_not_called()

    def test_expired_revoked_and_disabled_refresh_clear_both_cookies(self):
        for code in ('NotAuthorizedException', 'UserNotFoundException', 'InvalidParameterException',
                     'PasswordResetRequiredException', 'UserNotConfirmedException'):
            with self.subTest(code=code):
                self.auth.cognito.admin_initiate_auth.side_effect = FakeClientError(code)
                self.assert_cleared_session(self.auth.handler(self.refresh_event(), None))
        self.auth.s3.get_object.assert_not_called()

    def test_refresh_service_failures_preserve_credentials(self):
        for error in (FakeClientError('TooManyRequestsException'),
                      FakeClientError('InternalErrorException'), FakeBotoCoreError()):
            with self.subTest(error=type(error).__name__):
                self.auth.cognito.admin_initiate_auth.side_effect = error
                result = self.auth.handler(self.refresh_event(), None)
                self.assertEqual(result['statusCode'], 503)
                self.assertNotIn('cookies', result)
        self.auth.s3.get_object.assert_not_called()

    def test_online_validation_service_failure_does_not_clear_session(self):
        self.auth.cognito.get_user.side_effect = FakeBotoCoreError()
        result = self.auth.handler(self.refresh_event(token=self.token()), None)
        self.assertEqual(result['statusCode'], 503)
        self.assertNotIn('cookies', result)
        self.auth.cognito.admin_initiate_auth.assert_not_called()

    def test_session_window_caps_existing_and_refreshed_access(self):
        now = 2000000000
        with patch.object(self.auth.time, 'time', return_value=now):
            for authenticated in (None, True, '2000000000', float('nan'), now + 31,
                                  now - self.auth.SESSION_SECONDS):
                with self.subTest(authenticated=str(authenticated)):
                    token = self.token(auth_time=authenticated)
                    self.assert_cleared_session(self.auth.handler(self.refresh_event(token=token), None))
            self.auth.cognito.admin_initiate_auth.assert_not_called()
            self.renewed_token(auth_time=now - self.auth.SESSION_SECONDS)
            self.assert_cleared_session(self.auth.handler(self.refresh_event(), None))
            self.renewed_token(auth_time=now - self.auth.SESSION_SECONDS + 120)
            result = self.auth.handler(self.refresh_event(), None)
            self.assertEqual(result['statusCode'], 200)
            self.assertEqual(len(result['cookies']), 1)
            self.assertIn('Max-Age=120', result['cookies'][0])
            self.assertNotIn(self.auth.REFRESH, result['cookies'][0])
        self.auth.s3.get_object.assert_not_called()

    def test_refreshed_access_is_checked_again_before_private_reads(self):
        for claims in ({'iss': 'other-pool'}, {'client_id': 'other-client'}, {'token_use': 'id'}):
            self.renewed_token(**claims)
            result = self.auth.handler(self.refresh_event('/data/stocks.json'), None)
            self.assert_cleared_session(result)
        self.auth.s3.get_object.assert_not_called()

    def test_renewal_cookies_survive_gzip_and_business_errors_without_leaking(self):
        renewed = self.renewed_token()
        payload = {'rows': ['fictional' * 10000]}
        self.auth.s3.get_object.return_value = {'Body': types.SimpleNamespace(
            read=lambda: json.dumps(payload).encode())}
        event = self.refresh_event('/data/stocks.json')
        event['headers']['accept-encoding'] = 'gzip'
        result = self.auth.handler(event, None)
        self.assertEqual(json.loads(gzip.decompress(base64.b64decode(result['body']))), payload)
        self.assertEqual(len(result['cookies']), 1)
        self.assertIn(renewed, result['cookies'][0])
        self.auth.s3.get_object.side_effect = FakeClientError('AccessDenied')
        result = self.auth.handler(event, None)
        self.assertEqual(result['statusCode'], 503)
        self.assertEqual(len(result['cookies']), 1)
        self.assertIn(renewed, result['cookies'][0])
        result = self.auth.handler(self.refresh_event('/data/not-allowed.json'), None)
        self.assertEqual(result['statusCode'], 404)
        self.assertEqual(len(result['cookies']), 1)
        self.assertIn(renewed, result['cookies'][0])
        anonymous = self.auth.handler(self.event('/auth/me'), None)
        self.assert_cleared_session(anonymous)
        self.assertTrue(all(renewed not in value for value in anonymous['cookies']))

    def test_authorized_annotation_post_renews_then_saves_once(self):
        from backend.golden.annotations import empty
        renewed = self.renewed_token()
        def get(Bucket, Key):
            import io
            value = empty('600519') if '/annotations/' in Key else dict(
                schema='stock.golden.example.v1', symbol='600519', dataset_id='a' * 16, as_of='2020-01-02')
            return {'Body': io.BytesIO(json.dumps(value).encode()), 'ETag': '"etag"'}
        self.auth.s3.get_object.side_effect = get
        body = dict(revision=0, kind='review', as_of='2020-01-02', dataset_id='a' * 16,
                    status='UNCERTAIN', note='', axis_id='', auto_mode='HL')
        event = self.refresh_event('/data/golden-workbench/annotations/600519.json', method='POST',
                                   origin=self.auth.ORIGIN, body=body)
        result = self.auth.handler(event, None)
        self.assertEqual(result['statusCode'], 200)
        self.auth.cognito.admin_initiate_auth.assert_called_once()
        self.auth.s3.put_object.assert_called_once()
        saved = json.loads(self.auth.s3.put_object.call_args.kwargs['Body'])
        self.assertEqual(saved['reviews'][0]['owner'], 'edward')
        self.assertIn(renewed, result['cookies'][0])

    def test_invalid_origin_and_preflight_never_refresh(self):
        for method, origin, expected in (('POST', 'https://other.example', 403),
                                          ('POST', None, 403), ('OPTIONS', self.auth.ORIGIN, 204)):
            result = self.auth.handler(self.refresh_event(method=method, origin=origin), None)
            self.assertEqual(result['statusCode'], expected)
            self.assertNotIn('cookies', result)
        self.auth.cognito.admin_initiate_auth.assert_not_called()
        self.auth.cognito.get_user.assert_not_called()
        self.auth.s3.put_object.assert_not_called()

    def test_logout_with_expired_access_revokes_without_renewal(self):
        event = self.refresh_event('/auth/logout', method='POST', origin=self.auth.ORIGIN,
            token=self.token(auth_time=time.time() - 7200, exp=time.time() - 1))
        result = self.auth.handler(event, None)
        self.assertEqual(result['statusCode'], 200)
        self.auth.cognito.revoke_token.assert_called_once_with(Token='refresh-test', ClientId=self.auth.CLIENT)
        self.auth.cognito.admin_initiate_auth.assert_not_called()
        self.auth.cognito.get_user.assert_not_called()
        self.assertTrue(all('Max-Age=0' in value for value in result['cookies']))

    def test_template_enables_fixed_fifteen_day_refresh_without_widening_iam(self):
        template = json.loads((Path(__file__).parents[2] / 'deploy/auth/template.json').read_text())
        properties = template['Resources']['UserClient']['Properties']
        self.assertEqual(properties['RefreshTokenValidity'], 15)
        self.assertEqual(properties['TokenValidityUnits']['RefreshToken'], 'days')
        self.assertEqual(properties['AccessTokenValidity'], 1)
        self.assertTrue(properties['EnableTokenRevocation'])
        self.assertEqual(set(properties['ExplicitAuthFlows']),
                         {'ALLOW_ADMIN_USER_PASSWORD_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH'})
        permissions = template['Resources']['Role']['Properties']['Policies'][0]['PolicyDocument']['Statement']
        self.assertEqual([value['Action'] for value in permissions if
                          any(action.startswith('cognito-idp:') for action in value['Action'])],
                         [['cognito-idp:AdminInitiateAuth']])
        self.assertEqual([value['Resource'] for value in permissions if value['Action'] == ['s3:PutObject']],
                         [{'Fn::Sub': '${DataBucket.Arn}/data/golden-workbench/annotations/*.json'}])


if __name__ == '__main__':
    unittest.main()
