from test_auth_gateway import AuthTests
from backend.golden.annotations import empty
import json,io


class GoldenAnnotationGateway(AuthTests):
    def calibration_fixture(self):
        import gzip
        self.auth.cognito.get_user.return_value={'Username':'alice'}
        example=dict(price_geometry='log-price.v1',schema='stock.golden.example.v1',symbol='600519',dataset_id='a'*16,as_of='2020-01-02',policy_sha256='f'*64,
            history=[dict(date='2020-01-01',high='20',low='15',open='16',close='17'),dict(date='2020-01-02',high='15',low='10',open='12',close='13')],result=dict(H={'price':'21'},L=None,price_groups=[]))
        def get(Bucket,Key):
            raw=json.dumps(empty('600519')).encode() if '/annotations/' in Key else gzip.compress(json.dumps(example).encode())
            return dict(Body=io.BytesIO(raw),ETag='"etag"')
        self.auth.s3.get_object.side_effect=get
        return dict(kind='calibration',revision=0,drawing=dict(price_geometry='log-price.v1',id='00000000-0000-0000-0000-000000000001',title='fictional',mode='HL',points=[dict(date='2020-01-01',field='high',price='20'),dict(date='2020-01-02',field='low',price='10')],note='',as_of='2020-01-02',dataset_id='a'*16),review=dict(status='H_ISSUE',note='round one',axis_id='',auto_mode='HL'))
    def test_calibration_is_one_atomic_cas_with_server_published_context(self):
        body=self.calibration_fixture()
        event=self.event('/data/golden-workbench/annotations/600519.json',method='POST',token=self.token(),origin=self.auth.ORIGIN,body=body)
        self.assertEqual(self.auth.handler(event,None)['statusCode'],200)
        self.assertEqual(self.auth.s3.put_object.call_count,1)
        args=self.auth.s3.put_object.call_args.kwargs;value=json.loads(args['Body'])
        self.assertEqual(args['IfMatch'],'"etag"');self.assertEqual(value['revision'],1)
        self.assertEqual(value['reviews'][0]['auto_context']['H']['price'],'21')
        self.assertEqual(value['reviews'][0]['drawing_snapshot']['points'][0]['price'],'20')
    def test_calibration_rejects_forged_source_fields_future_dates_and_axis(self):
        import copy
        body=self.calibration_fixture()
        for change in ('price','future','axis','extra'):
            value=copy.deepcopy(body)
            if change=='price':value['drawing']['points'][0]['price']='22'
            elif change=='future':value['drawing']['points'][0]['date']='2020-01-03'
            elif change=='axis':value['review']['axis_id']='invented'
            else:value['auto_context']={'H':'forged'}
            event=self.event('/data/golden-workbench/annotations/600519.json',method='POST',token=self.token(),origin=self.auth.ORIGIN,body=value)
            self.assertEqual(self.auth.handler(event,None)['statusCode'],400)
        self.auth.s3.put_object.assert_not_called()
    def setup_doc(self):
        self.auth.cognito.get_user.return_value={'Username':'alice'}
        def get(Bucket,Key):
            example=dict(schema='stock.golden.example.v1',symbol='600519',dataset_id='a'*16,as_of='2020-01-02')
            if getattr(self, 'example_geometry', None): example['price_geometry']=self.example_geometry
            value=empty('600519') if '/annotations/' in Key else example
            return {'Body':io.BytesIO(json.dumps(value).encode()),'ETag':'"etag"'}
        self.auth.s3.get_object.side_effect=get
    def test_geometry_is_bound_to_published_example_for_every_write_kind(self):
        for kind in ('drawing','review','calibration'):
            for example_log in (False,True):
                for request_log in (False,True):
                    with self.subTest(kind=kind,example_log=example_log,request_log=request_log):
                        body=self.calibration_fixture()
                        original_get=self.auth.s3.get_object.side_effect
                        def get(Bucket,Key):
                            obj=original_get(Bucket,Key)
                            if '/annotations/' not in Key:
                                import gzip
                                example=json.loads(gzip.decompress(obj['Body'].read()))
                                if not example_log: example.pop('price_geometry')
                                obj['Body']=io.BytesIO(gzip.compress(json.dumps(example).encode()))
                            return obj
                        self.auth.s3.get_object.side_effect=get
                        drawing=body['drawing']
                        if not request_log: drawing.pop('price_geometry')
                        if kind=='drawing': body=dict(kind=kind,revision=0,drawing=drawing)
                        elif kind=='review':
                            body=dict(kind=kind,revision=0,as_of=drawing['as_of'],dataset_id=drawing['dataset_id'],status='UNCERTAIN',note='',axis_id='',auto_mode='HL')
                            if request_log: body['price_geometry']='log-price.v1'
                        self.auth.s3.put_object.reset_mock()
                        event=self.event('/data/golden-workbench/annotations/600519.json',method='POST',token=self.token(),origin=self.auth.ORIGIN,body=body)
                        result=self.auth.handler(event,None)
                        self.assertEqual(result['statusCode'],200 if example_log==request_log else 409)
                        self.assertEqual(self.auth.s3.put_object.call_count,int(example_log==request_log))
                        if example_log!=request_log: self.assertIn('刷新',json.loads(result['body'])['error'])
                        else: self.assertEqual(json.loads(self.auth.s3.put_object.call_args.kwargs['Body'])['revision'],1)
                        example_reads=[c for c in self.auth.s3.get_object.call_args_list if '/annotations/' not in c.kwargs['Key']]
                        self.assertEqual(len(example_reads),1)
                        self.assertEqual(example_reads[0].kwargs['Key'],f"data/golden-workbench/{'a'*16}/600519.json")
                        self.auth.s3.get_object.reset_mock()
    def test_bad_dataset_and_unknown_example_geometry_never_write(self):
        body=self.calibration_fixture()
        body['drawing']['dataset_id']='../not-a-dataset'
        event=self.event('/data/golden-workbench/annotations/600519.json',method='POST',token=self.token(),origin=self.auth.ORIGIN,body=body)
        self.assertEqual(self.auth.handler(event,None)['statusCode'],400)
        self.assertEqual(self.auth.s3.get_object.call_count,1)
        body=self.calibration_fixture()
        original_get=self.auth.s3.get_object.side_effect
        def get(Bucket,Key):
            obj=original_get(Bucket,Key)
            if '/annotations/' not in Key:
                import gzip
                example=json.loads(gzip.decompress(obj['Body'].read()));example['price_geometry']='future.v2'
                obj['Body']=io.BytesIO(gzip.compress(json.dumps(example).encode()))
            return obj
        self.auth.s3.get_object.side_effect=get
        event=self.event('/data/golden-workbench/annotations/600519.json',method='POST',token=self.token(),origin=self.auth.ORIGIN,body=body)
        self.assertEqual(self.auth.handler(event,None)['statusCode'],409)
        self.auth.s3.put_object.assert_not_called()
    def test_mismatched_example_identity_never_writes(self):
        import gzip
        for field,value in (('schema','other.v1'),('symbol','000001'),('dataset_id','b'*16),('as_of','2020-01-03')):
            with self.subTest(field=field):
                body=self.calibration_fixture()
                original_get=self.auth.s3.get_object.side_effect
                def get(Bucket,Key):
                    obj=original_get(Bucket,Key)
                    if '/annotations/' not in Key:
                        example=json.loads(gzip.decompress(obj['Body'].read()));example[field]=value
                        obj['Body']=io.BytesIO(gzip.compress(json.dumps(example).encode()))
                    return obj
                self.auth.s3.get_object.side_effect=get
                event=self.event('/data/golden-workbench/annotations/600519.json',method='POST',token=self.token(),origin=self.auth.ORIGIN,body=body)
                self.assertEqual(self.auth.handler(event,None)['statusCode'],400)
                self.auth.s3.put_object.assert_not_called()
    def test_annotation_writes_require_login_and_origin(self):
        path='/data/golden-workbench/annotations/600519.json'
        for event in [self.event(path,method='POST',origin=self.auth.ORIGIN),self.event(path,method='POST',token=self.token(),origin='https://other.example')]:
            self.assertIn(self.auth.handler(event,None)['statusCode'],(401,403))
        self.auth.s3.put_object.assert_not_called()
    def test_save_has_etag_and_cognito_owner_and_no_arbitrary_key(self):
        self.example_geometry='log-price.v1';self.setup_doc()
        body=dict(kind='drawing',revision=0,drawing=dict(price_geometry='log-price.v1',id='00000000-0000-0000-0000-000000000001',title='QA fixture',mode='HL',points=[dict(date='2020-01-01',field='high',price='20'),dict(date='2020-01-02',field='low',price='10')],note='',as_of='2020-01-02',dataset_id='a'*16))
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
