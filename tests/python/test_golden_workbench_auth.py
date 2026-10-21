from test_auth_gateway import AuthTests
from backend.golden.annotations import empty
import json,types


class GoldenAnnotationGateway(AuthTests):
    def setup_doc(self):
        self.auth.cognito.get_user.return_value={'Username':'alice'}
        self.auth.s3.get_object.return_value={'Body':types.SimpleNamespace(read=lambda:json.dumps(empty('600519')).encode()),'ETag':'"etag"'}
    def test_annotation_writes_require_login_and_origin(self):
        path='/data/golden-workbench/annotations/600519.json'
        for event in [self.event(path,method='POST',origin=self.auth.ORIGIN),self.event(path,method='POST',token=self.token(),origin='https://other.example')]:
            self.assertIn(self.auth.handler(event,None)['statusCode'],(401,403))
        self.auth.s3.put_object.assert_not_called()
    def test_save_has_etag_and_cognito_owner_and_no_arbitrary_key(self):
        self.setup_doc()
        body=dict(kind='drawing',revision=0,drawing=dict(id='00000000-0000-0000-0000-000000000001',title='QA fixture',mode='HL',points=[dict(date='2020-01-01',field='high',price='20'),dict(date='2020-01-02',field='low',price='10')],note='',as_of='2020-01-02',dataset_id='a'*16))
        event=self.event('/data/golden-workbench/annotations/600519.json',method='POST',token=self.token(),origin=self.auth.ORIGIN,body=body)
        response=self.auth.handler(event,None);self.assertEqual(response['statusCode'],200)
        args=self.auth.s3.put_object.call_args.kwargs
        self.assertEqual(args['IfMatch'],'"etag"');self.assertEqual(json.loads(args['Body'])['drawings'][0]['owner'],'alice')
        self.auth.s3.reset_mock();event['rawPath']='/data/golden-workbench/annotations/999999.json'
        self.assertEqual(self.auth.handler(event,None)['statusCode'],404);self.auth.s3.put_object.assert_not_called()
    def test_s3_cas_conflict_is_not_retried(self):
        from test_auth_gateway import FakeClientError
        self.setup_doc();self.auth.s3.put_object.side_effect=FakeClientError('PreconditionFailed')
        event=self.event('/data/golden-workbench/annotations/600519.json',method='POST',token=self.token(),origin=self.auth.ORIGIN,body=dict(revision=0,kind='review',as_of='2020-01-02',dataset_id='a'*16,status='UNCERTAIN',note='',axis_id='',auto_mode='HL'))
        self.assertEqual(self.auth.handler(event,None)['statusCode'],409);self.assertEqual(self.auth.s3.put_object.call_count,1)
