"""Exact private summary path using the invented account and storage fixture."""
import unittest
import test_chips_auth_additions as fixture


class ConcentrationAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ChipAuthNewCases.setUpClass()
        cls.api = fixture.ChipAuthNewCases.module
        cls.s3 = fixture.ChipAuthNewCases.s3

    def setUp(self):
        self.s3.calls.clear(); self.s3.missing = False
        self.api.current_user = lambda _: 'INVENTED'

    def request(self, path, method='GET'):
        return self.api.handler({'rawPath': path, 'requestContext': {'http': {'method': method}},
            'headers': {'origin': 'https://invented.invalid'}}, None)

    def test_summary_read_requires_authentication_before_storage(self):
        self.api.current_user = lambda _: None
        self.assertEqual(self.request('/data/chips/daily/0123456789abcdef/concentration.json')['statusCode'], 401)
        self.assertEqual(self.s3.calls, [])

    def test_exact_versioned_summary_reads_private_data_only(self):
        result = self.request('/data/chips/daily/0123456789abcdef/concentration.json')
        self.assertEqual(result['statusCode'], 200)
        self.assertEqual(self.s3.calls, [{'Bucket': 'INVENTED_BUCKET', 'Key': 'data/chips/daily/0123456789abcdef/concentration.json'}])
        self.assertIn('private', result['headers']['cache-control'])

    def test_addition_has_no_unversioned_proxy_or_write_route(self):
        for path in ['/data/chips/concentration.json', '/data/chips/daily/concentration.json',
                     '/data/chips/daily/0123456789abcdef/../concentration.json',
                     '/data/chips/daily/0123456789abcdef/concentration-more.json',
                     '/collector/chips/concentration/0123456789abcdef.json']:
            self.assertEqual(self.request(path)['statusCode'], 404)
        self.assertEqual(self.request('/data/chips/daily/0123456789abcdef/concentration.json', 'POST')['statusCode'], 404)
        self.assertEqual(self.s3.calls, [])

    def test_missing_summary_is_not_zero_matches(self):
        self.s3.missing = True
        self.assertEqual(self.request('/data/chips/daily/0123456789abcdef/concentration.json')['statusCode'], 404)


if __name__ == '__main__': unittest.main()
