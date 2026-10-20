"""Read-only historical route security using the existing invented auth fixture."""
import json
from io import BytesIO
from test_chips_auth_additions import ChipAuthNewCases
from test_chip_history_v1 import fixture,DATASET,CODE
import unittest

class HistoryAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ChipAuthNewCases.setUpClass();cls.api=ChipAuthNewCases.module;cls.s3=ChipAuthNewCases.s3
    def setUp(self):
        self.s3.calls.clear();self.api.current_user=lambda t:'INVENTED';self.api._history_store=None
    def request(self,path,method='GET'):
        return self.api.handler({'rawPath':path,'requestContext':{'http':{'method':method}},'headers':{'origin':'https://invented.invalid'}},None)
    def test_history_requires_authentication_before_any_input_read(self):
        self.api.current_user=lambda t:None
        self.assertEqual(self.request(f'/data/chips/daily/{DATASET}/{CODE}/history/2025-11-20.json')['statusCode'],401)
        self.assertEqual(self.s3.calls,[])
    def test_history_route_is_read_only_and_has_no_raw_path_proxy(self):
        for path in [f'/data/chips/daily/{DATASET}/{CODE}/history/../../collector.json',f'/data/chips/daily/{DATASET}/secret/history/2025-11-20.json',f'/data/chips/daily/{DATASET}/{CODE}/history/2025-11-20.json/extra']:
            self.assertEqual(self.request(path)['statusCode'],404)
        self.assertEqual(self.request(f'/data/chips/daily/{DATASET}/{CODE}/history/2025-11-20.json','POST')['statusCode'],404)
        self.assertEqual(self.s3.calls,[])
    def test_authenticated_history_uses_same_private_frame_and_bounded_model(self):
        _,frame,_=fixture();old=self.s3.get_object
        def read(**kw):
            self.s3.calls.append(kw);return {'Body':BytesIO(json.dumps(frame).encode())}
        self.s3.get_object=read
        try:
            r=self.request(f'/data/chips/daily/{DATASET}/{CODE}/history/2025-11-20.json')
            self.assertEqual(r['statusCode'],200)
            value=json.loads(r['body'])
            self.assertEqual(value['selected_date'],'2025-11-20');self.assertIsNone(value['unchanged_threshold'])
            self.assertEqual(value['snapshots']['2025-11-20']['loaded_window_rows'],210)
            self.assertEqual(self.s3.calls,[{'Bucket':'INVENTED_BUCKET','Key':f'data/chips/daily/{DATASET}/{CODE}.json'}])
            self.assertIn('private',r['headers']['cache-control'])
        finally:self.s3.get_object=old
    def test_invalid_iso_date_returns_error_without_storage_access(self):
        r=self.request(f'/data/chips/daily/{DATASET}/{CODE}/history/2025-02-30.json')
        self.assertEqual(r['statusCode'],422);self.assertEqual(self.s3.calls,[])

    def test_batch_stays_authenticated_on_warm_cache_and_has_bounded_query(self):
        _,frame,_=fixture();old=self.s3.get_object
        self.s3.get_object=lambda **kw:{'Body':BytesIO(json.dumps(frame).encode())}
        event={'rawPath':f'/data/chips/daily/{DATASET}/{CODE}/history/2025-11-20.json','requestContext':{'http':{'method':'GET'}},'headers':{},'queryStringParameters':{'batch':'8'}}
        try:
            r=self.api.handler(event,None);self.assertEqual(r['statusCode'],200)
            self.assertEqual(json.loads(r['body'])['schema'],'stock.chips.history.batch.v1')
            self.api.current_user=lambda t:None
            self.assertEqual(self.api.handler(event,None)['statusCode'],401)
            self.api.current_user=lambda t:'INVENTED'
            event['queryStringParameters']={'batch':'500'}
            self.assertEqual(self.api.handler(event,None)['statusCode'],400)
        finally:self.s3.get_object=old
