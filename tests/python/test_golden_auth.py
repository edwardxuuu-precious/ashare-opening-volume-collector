"""New exact grid path authorization checks with invented clients only."""
import unittest
import test_chips_auth_additions as fixture


class GoldenAuthCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ChipAuthNewCases.setUpClass()
        cls.api = fixture.ChipAuthNewCases.module
        cls.s3 = fixture.ChipAuthNewCases.s3

    def setUp(self):
        self.s3.calls.clear()
        self.api.current_user = lambda token: 'FICTIONAL'

    def request(self, path, method='GET'):
        return self.api.handler({'rawPath': path, 'requestContext': {'http': {'method': method}}, 'headers': {'origin':'https://invented.invalid'}}, None)

    def test_grid_requires_authentication_before_private_read(self):
        self.api.current_user = lambda token: None
        self.assertEqual(self.request('/data/chips/daily/0123456789abcdef/golden.json')['statusCode'], 401)
        self.assertEqual(self.s3.calls, [])

    def test_only_exact_generation_grid_get_is_allowed(self):
        self.assertEqual(self.request('/data/chips/daily/0123456789abcdef/golden.json')['statusCode'], 200)
        self.assertEqual(self.s3.calls, [{'Bucket': 'INVENTED_BUCKET', 'Key': 'data/chips/daily/0123456789abcdef/golden.json'}])
        self.s3.calls.clear()
        for path in ('/data/chips/daily/golden.json', '/data/chips/0123456789abcdef/golden.json', '/data/chips/daily/../../collector/golden.json'):
            self.assertEqual(self.request(path)['statusCode'], 404)
        self.assertEqual(self.request('/data/chips/daily/0123456789abcdef/golden.json', 'POST')['statusCode'], 404)
        self.assertEqual(self.s3.calls, [])
