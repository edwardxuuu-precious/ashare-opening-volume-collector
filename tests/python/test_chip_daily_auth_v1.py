"""New daily namespace cases only, wholly invented, no AWS or credentials."""
import gzip,json,unittest
from io import BytesIO
from test_chips_auth_additions import ChipAuthNewCases

class DailyAuthV1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ChipAuthNewCases.setUpClass();cls.api=ChipAuthNewCases.module;cls.s3=ChipAuthNewCases.s3
    def setUp(self):self.s3.calls.clear();self.s3.missing=False;self.api.current_user=lambda token:'INVENTED'
    def request(self,path,method='GET'):
        return self.api.handler({'rawPath':path,'requestContext':{'http':{'method':method}},'headers':{'origin':'https://invented.invalid'}},None)
    def test_daily_auth_before_private_read(self):
        self.api.current_user=lambda t:None
        self.assertEqual(self.request('/data/chips/daily/status.json')['statusCode'],401);self.assertEqual(self.s3.calls,[])
    def test_daily_namespace_exact_generation_only(self):
        for p in ('manifest','status','0123456789abcdef/screening','0123456789abcdef/999999'):
            self.assertEqual(self.request('/data/chips/daily/'+p+'.json')['statusCode'],200)
        for p in ('../../collector/chips/state','0123456789abcdef/secrets','x/999999'):
            self.assertEqual(self.request('/data/chips/daily/'+p+'.json')['statusCode'],404)
        self.assertEqual(len(self.s3.calls),4)
    def test_compressed_private_object_decoded_without_exposing_a_write_route(self):
        old=self.s3.get_object
        self.s3.get_object=lambda **kw:{'Body':BytesIO(gzip.compress(b'{"all_values_invented":true}'))}
        try:
            r=self.request('/data/chips/daily/0123456789abcdef/999999.json');self.assertEqual(json.loads(r['body']),{'all_values_invented':True})
            self.assertEqual(self.request('/data/chips/daily/status.json','POST')['statusCode'],404)
        finally:self.s3.get_object=old
