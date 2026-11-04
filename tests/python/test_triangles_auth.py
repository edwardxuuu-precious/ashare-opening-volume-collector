import base64
import gzip
import json
import types

from test_auth_gateway import AuthTests, FakeClientError


class TriangleGatewayTests(AuthTests):
    paths = ('/data/triangles/daily/manifest.json', '/data/triangles/daily/status.json',
             '/data/triangles/daily/abcdef0123456789/screening.json',
             '/data/triangles/daily/abcdef0123456789/bundle-001.json')

    def test_triangle_paths_require_authentication_before_storage(self):
        for path in self.paths:
            self.assertEqual(self.auth.handler(self.event(path), None)['statusCode'], 401)
        self.auth.s3.get_object.assert_not_called()

    def test_exact_private_paths_preserve_json_bytes_for_published_hashes(self):
        raw = b'{"float":1.0,"unicode":"\xe9\x87\x8f\xe4\xbb\xb7","z":null}'
        self.auth.cognito.get_user.return_value = {'Username': 'viewer'}
        for path in self.paths:
            self.auth.s3.get_object.return_value = {'Body': types.SimpleNamespace(read=lambda *_: raw)}
            result = self.auth.handler(self.event(path, token=self.token()), None)
            self.assertEqual(result['statusCode'], 200)
            self.assertEqual(result['body'].encode(), raw)
            self.assertEqual(result['headers']['cache-control'], 'no-store, private')

    def test_stored_and_transport_gzip_keep_original_bytes(self):
        raw = json.dumps({'note': '量价' * 40000}, ensure_ascii=False, separators=(',', ':')).encode()
        self.auth.cognito.get_user.return_value = {'Username': 'viewer'}
        self.auth.s3.get_object.return_value = {'Body': types.SimpleNamespace(read=lambda *_: gzip.compress(raw))}
        event = self.event(self.paths[-1], token=self.token())
        event['headers']['accept-encoding'] = 'gzip'
        result = self.auth.handler(event, None)
        self.assertEqual(gzip.decompress(base64.b64decode(result['body'])), raw)

    def test_namespace_is_read_only_and_rejects_lease_or_arbitrary_paths(self):
        self.auth.cognito.get_user.return_value = {'Username': 'viewer'}
        invalid = ('/data/triangles/daily/lease.json', '/data/triangles/daily/abcdef0123456789/manifest.json',
                   '/data/triangles/daily/abcdef0123456789/bundle-1.json',
                   '/data/triangles/daily/ABCDEF0123456789/screening.json',
                   '/data/triangles/daily/abcdef0123456789/../screening.json')
        for path in invalid:
            self.assertEqual(self.auth.handler(self.event(path, token=self.token()), None)['statusCode'], 404)
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            event = self.event(self.paths[0], token=self.token(), method=method, origin=self.auth.ORIGIN)
            self.assertEqual(self.auth.handler(event, None)['statusCode'], 404)
        self.auth.s3.get_object.assert_not_called()
        self.auth.s3.put_object.assert_not_called()

    def test_missing_and_corrupt_data_are_not_empty_success(self):
        self.auth.cognito.get_user.return_value = {'Username': 'viewer'}
        self.auth.s3.get_object.side_effect = FakeClientError('NoSuchKey')
        self.assertEqual(self.auth.handler(self.event(self.paths[0], token=self.token()), None)['statusCode'], 404)
        self.auth.s3.get_object.side_effect = None
        self.auth.s3.get_object.return_value = {'Body': types.SimpleNamespace(read=lambda *_: b'{broken')}
        self.assertEqual(self.auth.handler(self.event(self.paths[0], token=self.token()), None)['statusCode'], 503)
