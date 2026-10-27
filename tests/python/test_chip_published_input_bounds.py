"""Invented bounded S3 responses; no cloud or market-data requests."""
import gzip
import io
import json
import unittest
from unittest.mock import Mock

from backend.chips.runner import Storage


class PublishedInputBounds(unittest.TestCase):
    def store(self,raw):
        body=io.BytesIO(raw)
        store=Storage.__new__(Storage)
        store.bucket='INVENTED'
        store.s3=Mock()
        store.s3.get_object.return_value={'Body':body,'ETag':'INVENTED_ETAG'}
        return store,body

    def test_plain_and_gzip_json_and_etag(self):
        raw=json.dumps({'date':'2026-10-27','rows':[]}).encode()
        for response in (raw,gzip.compress(raw)):
            with self.subTest(compressed=response!=raw):
                store,body=self.store(response)
                value,etag=store.get_bounded('data/2026-10-27.json',max_bytes=1000,max_decoded_bytes=1000)
                self.assertEqual(value,{'date':'2026-10-27','rows':[]})
                self.assertEqual(etag,'INVENTED_ETAG')
                self.assertTrue(body.closed)

    def test_oversized_encoded_response_is_rejected_and_closed(self):
        store,body=self.store(b' '*100)
        with self.assertRaisesRegex(ValueError,'ENCODED_BOUND'):
            store.get_bounded('INVENTED',max_bytes=20,max_decoded_bytes=1000)
        self.assertTrue(body.closed)

    def test_compressed_expansion_and_plain_decoded_bounds(self):
        raw=json.dumps({'value':'x'*10000}).encode()
        for response in (raw,gzip.compress(raw)):
            with self.subTest(compressed=response!=raw):
                store,body=self.store(response)
                with self.assertRaisesRegex(ValueError,'DECODED_BOUND'):
                    store.get_bounded('INVENTED',max_bytes=20000,max_decoded_bytes=100)
                self.assertTrue(body.closed)

    def test_missing_is_distinct_from_permission_denied(self):
        from botocore.exceptions import ClientError
        store,_=self.store(b'{}')
        for code in ('NoSuchKey','404','AccessDenied'):
            with self.subTest(code=code):
                store.s3.get_object.side_effect=ClientError({'Error':{'Code':code}},'GetObject')
                if code=='AccessDenied':
                    with self.assertRaises(ClientError):
                        store.get_bounded('INVENTED',max_bytes=100,max_decoded_bytes=100)
                else:
                    self.assertEqual(store.get_bounded('INVENTED',max_bytes=100,max_decoded_bytes=100),(None,None))


if __name__=='__main__':unittest.main()
