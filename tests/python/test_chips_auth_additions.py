"""New invented route cases. No AWS account, credentials, or network is used."""
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class FakeClientError(Exception):
    def __init__(self, code):
        self.response = {'Error': {'Code': code}}


class FakeS3:
    def __init__(self):
        self.calls = []
        self.missing = False

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.missing:
            raise FakeClientError('NoSuchKey')
        return {'Body': BytesIO(b'{"all_values_invented":true}')}


class ChipAuthNewCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s3 = FakeS3()
        boto = types.ModuleType('boto3')
        boto.client = lambda service: cls.s3 if service == 's3' else object()
        exceptions = types.ModuleType('botocore.exceptions')
        exceptions.ClientError = FakeClientError
        path = Path(__file__).resolve().parents[2] / 'deploy/auth/handler.py'
        spec = importlib.util.spec_from_file_location('chip_auth_new_case_candidate', path)
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'boto3': boto, 'botocore': types.ModuleType('botocore'), 'botocore.exceptions': exceptions}), patch.dict(os.environ, {
            'USER_POOL_ID':'INVENTED_POOL','USER_POOL_CLIENT_ID':'INVENTED_CLIENT','DATA_BUCKET':'INVENTED_BUCKET',
            'ALLOWED_ORIGIN':'https://invented.invalid','AWS_REGION':'us-east-1'}):
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.s3.calls.clear()
        self.s3.missing = False
        self.module.current_user = lambda token: 'ALL_VALUES_INVENTED'

    def request(self, path, method='GET'):
        return self.module.handler({'rawPath':path,'requestContext':{'http':{'method':method}},'headers':{'origin':'https://invented.invalid'}},None)

    def test_new_anonymous_chip_route_still_requires_login(self):
        self.module.current_user = lambda token: None
        self.assertEqual(self.request('/data/chips/manifest.json')['statusCode'],401)
        self.assertEqual(self.s3.calls,[])

    def test_new_authenticated_manifest_reads_only_private_key(self):
        r=self.request('/data/chips/manifest.json')
        self.assertEqual(r['statusCode'],200)
        self.assertEqual(self.s3.calls,[{'Bucket':'INVENTED_BUCKET','Key':'data/chips/manifest.json'}])
        self.assertIn('private',r['headers']['cache-control'])

    def test_new_immutable_sample_and_screen_are_readable(self):
        for leaf in ['999999','screening']:
            self.assertEqual(self.request('/data/chips/0123456789abcdef/'+leaf+'.json')['statusCode'],200)
        self.assertEqual(len(self.s3.calls),2)

    def test_new_route_rejects_traversal_and_unknown_names(self):
        for path in ['/data/chips/../../collector/checkpoint.json','/data/chips/0123456789abcdef/secrets.json','/data/chips/wrong/999999.json']:
            self.assertEqual(self.request(path)['statusCode'],404)
        self.assertEqual(self.s3.calls,[])

    def test_new_route_does_not_add_a_write_endpoint(self):
        self.assertEqual(self.request('/data/chips/manifest.json','POST')['statusCode'],404)
        self.assertEqual(self.s3.calls,[])

    def test_new_missing_object_is_not_an_empty_screen_result(self):
        self.s3.missing=True
        self.assertEqual(self.request('/data/chips/manifest.json')['statusCode'],404)

    def test_new_addition_preserves_existing_catalog_route(self):
        self.assertEqual(self.request('/data/manifest.json')['statusCode'],200)
        self.assertEqual(self.s3.calls[0]['Key'],'data/manifest.json')


if __name__ == '__main__':
    unittest.main()
