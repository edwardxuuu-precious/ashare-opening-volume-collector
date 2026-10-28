import base64
import gzip
import json
import types

from test_auth_gateway import AuthTests, FakeClientError


class TrendVolumeGateway(AuthTests):
    paths = (
        '/data/trend-volume/manifest.json',
        '/data/trend-volume/abcdef0123456789/summary.json',
        '/data/trend-volume/abcdef0123456789/601086.json',
        '/data/trend-volume/daily/manifest.json',
        '/data/trend-volume/daily/status.json',
        '/data/trend-volume/daily/abcdef0123456789/screening.json',
        '/data/trend-volume/daily/abcdef0123456789/601086.json',
    )

    def test_trend_snapshot_requires_current_cognito_session_before_s3(self):
        for path in self.paths:
            for token in (None, 'forged', self.token(exp=1)):
                with self.subTest(path=path, token_kind='absent_or_invalid'):
                    self.assertEqual(self.auth.handler(self.event(path, token=token), None)['statusCode'], 401)
        self.auth.s3.get_object.assert_not_called()
        self.auth.cognito.get_user.side_effect = FakeClientError('NotAuthorizedException')
        self.assertEqual(self.auth.handler(self.event(self.paths[0], token=self.token()), None)['statusCode'], 401)
        self.auth.s3.get_object.assert_not_called()

    def test_authenticated_exact_paths_read_same_private_s3_key_without_cache(self):
        self.auth.cognito.get_user.return_value = {'Username': 'research-viewer'}
        payload = {'schema': 'fictional.study', 'rows': [{'code': '601086'}]}
        raw = json.dumps(payload).encode()
        for path in self.paths:
            with self.subTest(path=path):
                self.auth.s3.reset_mock()
                self.auth.s3.get_object.return_value = {'Body': types.SimpleNamespace(read=lambda: raw)}
                result = self.auth.handler(self.event(path, token=self.token()), None)
                self.assertEqual(result['statusCode'], 200)
                self.assertEqual(json.loads(result['body']), payload)
                self.assertEqual(result['headers']['cache-control'], 'no-store, private')
                self.assertEqual(result['headers']['access-control-allow-origin'], self.auth.ORIGIN)
                self.auth.s3.get_object.assert_called_once_with(Bucket='private-data', Key=path.lstrip('/'))
        self.auth.s3.put_object.assert_not_called()

    def test_stored_and_transport_gzip_preserve_snapshot_exactly(self):
        self.auth.cognito.get_user.return_value = {'Username': 'research-viewer'}
        payload = {'schema': 'fictional.study', 'rows': [{'note': '量价研究', 'value': i} for i in range(3000)]}
        raw = gzip.compress(json.dumps(payload, ensure_ascii=False).encode(), mtime=0)
        self.auth.s3.get_object.return_value = {'Body': types.SimpleNamespace(read=lambda: raw)}
        event = self.event(self.paths[1], token=self.token())
        event['headers']['accept-encoding'] = 'gzip'
        result = self.auth.handler(event, None)
        self.assertEqual(result['statusCode'], 200)
        self.assertEqual(result['headers']['content-encoding'], 'gzip')
        self.assertTrue(result['isBase64Encoded'])
        self.assertEqual(json.loads(gzip.decompress(base64.b64decode(result['body']))), payload)

    def test_paths_cannot_escape_namespace_or_expand_resource_names(self):
        self.auth.cognito.get_user.return_value = {'Username': 'research-viewer'}
        invalid = (
            '/data/trend-volume/daily/lease.json',
            '/data/trend-volume/daily/abcdef0123456789/summary.json',
            '/data/trend-volume/daily/ABCDEF0123456789/screening.json',
            '/data/trend-volume/daily/abcdef0123456789/601086.json/extra',
            '/data/trend-volume/daily/abcdef0123456789/../601086.json',
            '/data/trend-volume/../manifest.json',
            '/data/trend-volume/%2e%2e/manifest.json',
            '/data/trend-volume/manifest.json.bak',
            '/data/trend-volume/summary.json',
            '/data/trend-volume/ABCDEF0123456789/summary.json',
            '/data/trend-volume/abcdef012345678/summary.json',
            '/data/trend-volume/abcdef01234567890/summary.json',
            '/data/trend-volume/abcdef0123456789/../601086.json',
            '/data/trend-volume/abcdef0123456789/raw.json',
            '/data/trend-volume/abcdef0123456789/601086.json/extra',
            '/data/trend-volume/abcdef0123456789/601086.csv',
            '/data/trend-volume/abcdef0123456789/60108.json',
            '/data/trend-volume/abcdef0123456789/6010860.json',
            '/data/trend-volume/abcdef0123456789/manifest.json',
        )
        for path in invalid:
            with self.subTest(path=path):
                self.assertEqual(self.auth.handler(self.event(path, token=self.token()), None)['statusCode'], 404)
        self.auth.s3.get_object.assert_not_called()

    def test_snapshot_namespace_has_no_writes(self):
        self.auth.cognito.get_user.return_value = {'Username': 'research-viewer'}
        for path in self.paths:
            for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
                with self.subTest(path=path, method=method):
                    event = self.event(path, token=self.token(), method=method, origin=self.auth.ORIGIN)
                    self.assertEqual(self.auth.handler(event, None)['statusCode'], 404)
        self.auth.s3.get_object.assert_not_called()
        self.auth.s3.put_object.assert_not_called()

    def test_missing_snapshot_is_404_and_storage_failures_are_generic_503(self):
        self.auth.cognito.get_user.return_value = {'Username': 'research-viewer'}
        for code, expected in (('NoSuchKey', 404), ('AccessDenied', 503), ('SlowDown', 503)):
            with self.subTest(code=code):
                self.auth.s3.get_object.side_effect = FakeClientError(code)
                result = self.auth.handler(self.event(self.paths[1], token=self.token()), None)
                self.assertEqual(result['statusCode'], expected)
                self.assertNotIn(code, result['body'])

    def test_corrupt_stored_json_or_gzip_is_a_service_error(self):
        self.auth.cognito.get_user.return_value = {'Username': 'research-viewer'}
        for raw in (b'{incomplete', b'\x1f\x8bnot-a-gzip-stream', b'\xff\xff'):
            with self.subTest(raw=raw):
                self.auth.s3.get_object.return_value = {'Body': types.SimpleNamespace(read=lambda: raw)}
                result = self.auth.handler(self.event(self.paths[1], token=self.token()), None)
                self.assertEqual(result['statusCode'], 503)
                self.assertEqual(json.loads(result['body']), {'error': '服务暂时不可用，请稍后重试'})
