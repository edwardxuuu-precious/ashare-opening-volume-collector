"""Complete concentration publication with independent scans and conditional writes."""
from copy import deepcopy
import gzip
import io
import unittest
from unittest.mock import patch

from test_concentration_runner import Store
from test_concentration import DATASET
from backend.chips import concentration_publication as publication
from backend.chips import concentration_two as engine
from backend.chips import concentration_two_runner as runner


class PublicationStore(Store):
    def __init__(self, dataset=DATASET, generated='2026-10-30T10:00:00Z'):
        super().__init__(1)
        self.objects = {key.replace(DATASET, dataset): value for key, value in self.objects.items()}
        for value in self.objects.values():
            if isinstance(value, dict) and 'dataset_id' in value:
                value['dataset_id'] = dataset
            if isinstance(value, dict) and 'active_slot' in value:
                value['active_slot'] = dataset
        self.objects[runner.MANIFEST]['generated_at'] = generated
        self.etag = None
        self.publication_writes = 0

    def get_publication(self):
        return self.get(publication.PUBLICATION), self.etag

    def replace_publication(self, value, etag):
        if self.etag != etag:
            raise ValueError('Simulated conditional conflict')
        self.objects[publication.PUBLICATION] = deepcopy(value)
        self.publication_writes += 1
        self.etag = str(self.publication_writes)


def complete(store):
    with patch.object(engine.EndpointSource, 'point', return_value=(20.0, None)):
        return runner.run(store, workers=1, publish=True, progress=lambda _: None)


class CompletePublication(unittest.TestCase):
    def test_storage_uses_etag_conditions_and_reads_the_committed_pointer(self):
        value = {'schema': 'INVENTED'}
        class S3:
            def __init__(self):
                self.writes = []
            def get_object(self, **kwargs):
                return {'Body': io.BytesIO(gzip.compress(publication.encoded(value))), 'ETag': '"version-one"'}
            def put_object(self, **kwargs):
                self.writes.append(kwargs)
        store = publication.PublicationStorage.__new__(publication.PublicationStorage)
        store.bucket, store.deadline, store.s3 = 'INVENTED_BUCKET', None, S3()
        observed, etag = store.get_publication()
        self.assertEqual(observed, value)
        store.replace_publication(value, etag)
        store.replace_publication(value, None)
        self.assertEqual(store.s3.writes[0]['IfMatch'], '"version-one"')
        self.assertNotIn('IfNoneMatch', store.s3.writes[0])
        self.assertEqual(store.s3.writes[1]['IfNoneMatch'], '*')
        self.assertNotIn('IfMatch', store.s3.writes[1])
        self.assertEqual(gzip.decompress(store.s3.writes[0]['Body']), publication.encoded(value))

    def test_already_present_result_repairs_pointer_without_numerical_replay(self):
        store = PublicationStore()
        complete(store)
        with patch.object(engine.EndpointSource, 'point', side_effect=AssertionError('No endpoint replay')):
            result = publication.run_and_publish(store, workers=1, publish=True, progress=lambda _: None)
        self.assertEqual(result['outcome'], 'ALREADY_PRESENT')
        self.assertEqual(result['complete_publication']['state'], 'verified')
        pointer = store.objects[publication.PUBLICATION]
        self.assertEqual(pointer['source'], store.objects[runner.MANIFEST])
        self.assertEqual(pointer['payload_sha256'], runner.digest(store.objects[pointer['payload_key']]))
        publication.run_and_publish(store, workers=1, publish=True, progress=lambda _: None)
        self.assertEqual(store.publication_writes, 1)

    def test_incomplete_new_scan_leaves_the_complete_previous_result(self):
        store = PublicationStore()
        complete(store)
        publication.publish_complete(store, store.get(runner.MANIFEST))
        previous = store.get(publication.PUBLICATION)
        store.objects[runner.MANIFEST]['dataset_id'] = '1111111111111111'
        store.objects[runner.MANIFEST]['active_slot'] = '1111111111111111'
        with self.assertRaisesRegex(ValueError, 'SCREEN_DENOMINATOR'):
            publication.publish_complete(store, store.get(runner.MANIFEST))
        self.assertEqual(store.get(publication.PUBLICATION), previous)
        self.assertEqual(store.publication_writes, 1)

    def test_source_advancing_after_commit_does_not_invalidate_complete_result(self):
        store = PublicationStore()
        source = store.get(runner.MANIFEST)
        complete(store)
        store.objects[runner.MANIFEST]['dataset_id'] = '1111111111111111'
        result = publication.publish_complete(store, source)
        self.assertEqual(result['state'], 'verified')
        self.assertEqual(store.objects[publication.PUBLICATION]['dataset_id'], DATASET)

    def test_new_complete_result_replaces_old_but_old_worker_cannot_roll_back(self):
        old = PublicationStore()
        complete(old)
        old_source = old.get(runner.MANIFEST)
        publication.publish_complete(old, old_source)
        new = PublicationStore('1111111111111111', '2026-10-30T11:00:00Z')
        complete(new)
        new.objects[publication.PUBLICATION] = old.get(publication.PUBLICATION)
        new.etag = 'previous'
        self.assertEqual(publication.publish_complete(new, new.get(runner.MANIFEST))['state'], 'verified')
        old.objects[publication.PUBLICATION] = new.get(publication.PUBLICATION)
        self.assertEqual(publication.publish_complete(old, old_source)['state'], 'superseded')
        self.assertEqual(old.publication_writes, 1)

    def test_conflicting_generation_or_same_dataset_is_rejected(self):
        old = PublicationStore()
        complete(old)
        publication.publish_complete(old, old.get(runner.MANIFEST))
        other = PublicationStore('1111111111111111')
        complete(other)
        other.objects[publication.PUBLICATION] = old.get(publication.PUBLICATION)
        with self.assertRaisesRegex(ValueError, 'SAME_GENERATION_CONFLICT'):
            publication.publish_complete(other, other.get(runner.MANIFEST))
        old.objects[publication.PUBLICATION]['payload_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'SAME_DATASET_CONFLICT'):
            publication.publish_complete(old, old.get(runner.MANIFEST))

    def test_lost_write_acknowledgment_is_read_back_without_another_write(self):
        store = PublicationStore()
        complete(store)
        replace = store.replace_publication
        def lost(value, etag):
            replace(value, etag)
            raise TimeoutError('Lost PUT acknowledgment')
        store.replace_publication = lost
        self.assertEqual(publication.publish_complete(store, store.get(runner.MANIFEST))['state'], 'verified')
        self.assertEqual(store.publication_writes, 1)

    def test_unresolved_put_and_cas_competition_do_not_overwrite(self):
        store = PublicationStore()
        complete(store)
        def fail(*_):
            raise TimeoutError('Unknown PUT')
        store.replace_publication = fail
        with self.assertRaises(publication.PublicationUncertain):
            publication.publish_complete(store, store.get(runner.MANIFEST))
        self.assertEqual(store.publication_writes, 0)
        newer = publication.complete_publication(store, store.get(runner.MANIFEST))
        newer['dataset_id'] = newer['source']['dataset_id'] = newer['source']['active_slot'] = '2222222222222222'
        newer['generated_at'] = newer['source']['generated_at'] = '2026-10-30T12:00:00Z'
        newer['payload_key'] = 'data/chips/daily/2222222222222222/concentration-two-v1.json'
        def competitor(*_):
            store.objects[publication.PUBLICATION] = newer
            raise ValueError('Conditional conflict')
        store.replace_publication = competitor
        self.assertEqual(publication.publish_complete(store, store.get(runner.MANIFEST))['state'], 'superseded')
        self.assertEqual(store.publication_writes, 0)

    def test_partial_or_corrupt_summary_cannot_be_promoted(self):
        for mutation in ('missing', 'rows', 'fingerprint'):
            with self.subTest(mutation=mutation):
                store = PublicationStore()
                complete(store)
                key = f'data/chips/daily/{DATASET}/concentration-two-v1.json'
                if mutation == 'missing':
                    del store.objects[key]
                elif mutation == 'rows':
                    store.objects[key]['rows'] = []
                else:
                    store.objects[key]['input_fingerprint'] = '0' * 64
                with self.assertRaises(ValueError):
                    publication.publish_complete(store, store.get(runner.MANIFEST))
                self.assertNotIn(publication.PUBLICATION, store.objects)

    def test_checkpoint_and_draft_do_not_advance_complete_pointer(self):
        store = PublicationStore()
        with patch.object(engine.EndpointSource, 'point', return_value=(20.0, None)):
            result = publication.run_and_publish(store, workers=1, publish=False, progress=lambda _: None)
        self.assertEqual(result['outcome'], 'DRAFT_COMPLETE')
        self.assertNotIn(publication.PUBLICATION, store.objects)
        now = [0]
        create = store.create
        def expire(key, value):
            create(key, value)
            now[0] = 1200
        store = PublicationStore()
        create = store.create
        store.create = expire
        with patch.object(engine.EndpointSource, 'point', return_value=(20.0, None)):
            result = publication.run_and_publish(store, workers=1, publish=True, clock=lambda: now[0], progress=lambda _: None)
        self.assertEqual(result['outcome'], 'CHECKPOINT_SAVED')
        self.assertNotIn(publication.PUBLICATION, store.objects)


if __name__ == '__main__':
    unittest.main()
