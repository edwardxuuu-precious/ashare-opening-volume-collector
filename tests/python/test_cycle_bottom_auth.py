import _bootstrap
import gzip
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class InventedClientError(Exception):
    def __init__(self, code):
        self.response = {'Error': {'Code': code}}


class Storage:
    def __init__(self):
        self.calls, self.raw, self.missing = [], b'{"invented":true}', False

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.missing:
            raise InventedClientError('NoSuchKey')
        return {'Body': BytesIO(self.raw)}


class CycleAuthentication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = Storage()
        boto = types.ModuleType('boto3')
        boto.client = lambda service: cls.storage if service == 's3' else object()
        exceptions = types.ModuleType('botocore.exceptions')
        exceptions.ClientError = InventedClientError
        exceptions.BotoCoreError = type('InventedBotoError', (Exception,), {})
        spec = importlib.util.spec_from_file_location('invented_cycle_auth', Path(__file__).resolve().parents[2] / 'deploy/auth/handler.py')
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'boto3': boto, 'botocore': types.ModuleType('botocore'), 'botocore.exceptions': exceptions}), patch.dict(os.environ, {
            'USER_POOL_ID': 'INVENTED_POOL', 'USER_POOL_CLIENT_ID': 'INVENTED_CLIENT',
            'DATA_BUCKET': 'INVENTED_BUCKET', 'ALLOWED_ORIGIN': 'https://invented.invalid', 'AWS_REGION': 'us-east-1'}):
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.storage.calls.clear()
        self.storage.raw, self.storage.missing = b'{"invented":true}', False
        self.module.current_user = lambda token: 'INVENTED_USER'

    def request(self, path, method='GET'):
        return self.module.handler({'rawPath': path, 'requestContext': {'http': {'method': method}},
                                    'headers': {'origin': 'https://invented.invalid'}}, None)

    def test_all_new_reads_require_the_existing_login(self):
        self.module.current_user = lambda token: None
        for suffix in ['manifest.json', 'status.json', 'a' * 16 + '/analysis.json']:
            self.assertEqual(self.request('/data/cycle-bottom/' + suffix)['statusCode'], 401)
        self.assertEqual(self.storage.calls, [])

    def test_authenticated_analysis_decompresses_and_reads_only_the_selected_private_key(self):
        self.storage.raw = gzip.compress(self.storage.raw)
        path = '/data/cycle-bottom/' + 'a' * 16 + '/analysis.json'
        result = self.request(path)
        self.assertEqual(result['statusCode'], 200)
        self.assertEqual(json.loads(result['body']), {'invented': True})
        self.assertEqual(self.storage.calls, [{'Bucket': 'INVENTED_BUCKET', 'Key': path[1:]}])

    def test_raw_inputs_and_arbitrary_paths_are_not_exposed(self):
        for suffix in ['raw/secret.raw', '../manifest.json', 'a' * 16 + '/raw.json', 'g' * 16 + '/analysis.json', 'a' * 16 + '/analysis.json/extra']:
            self.assertEqual(self.request('/data/cycle-bottom/' + suffix)['statusCode'], 404)
        self.assertEqual(self.storage.calls, [])

    def test_new_data_does_not_create_a_write_endpoint(self):
        self.assertEqual(self.request('/data/cycle-bottom/manifest.json', method='POST')['statusCode'], 404)
        self.assertEqual(self.storage.calls, [])

    def test_missing_object_is_not_an_empty_successful_analysis(self):
        self.storage.missing = True
        self.assertEqual(self.request('/data/cycle-bottom/manifest.json')['statusCode'], 404)

    def test_truncated_and_excessively_inflated_data_fail_closed(self):
        for raw in [b'\x1f\x8bbroken', gzip.compress(b'x' * (8 * 1024 * 1024 + 1))]:
            self.storage.raw = raw
            self.assertEqual(self.request('/data/cycle-bottom/' + 'a' * 16 + '/analysis.json')['statusCode'], 503)

    def test_existing_private_manifest_route_is_preserved(self):
        self.assertEqual(self.request('/data/manifest.json')['statusCode'], 200)
        self.assertEqual(self.storage.calls[-1]['Key'], 'data/manifest.json')
