import _bootstrap
import gc
import json
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

from scripts import backfill_quotes


def traded():
    return dict(code='000001', name='fixture', market='SZ', status='ok',
                first15Volume=10, dailyVolume=100, ratio=10,
                pctChange=1.0, amplitude=2.0, verificationVersion=3)


class PriceBackfillMemoryTests(unittest.TestCase):
    def test_scan_does_not_retain_every_historical_payload_in_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            days=['2026-09-18','2026-09-17','2026-09-16','2026-09-15']
            for day in days:
                (out/f'{day}.json').write_text(json.dumps(dict(date=day,rows=[traded()])))
            live=set()

            class Payload(dict):
                __slots__=('__weakref__',)

            def bounded_load(path, default=None):
                gc.collect()
                self.assertLessEqual(len(live),1)
                day=Path(path).stem
                payload=Payload(date=day,rows=[traded()])
                identity=id(payload);live.add(identity)
                weakref.finalize(payload,live.discard,identity)
                return payload

            with patch.object(backfill_quotes,'load_json',side_effect=bounded_load):
                needs,rows_seen,selected,incomplete=backfill_quotes.scan_needs(
                    out,{},False,min(days),max(days),1)

            self.assertEqual(selected,['2026-09-18'])
            self.assertEqual(incomplete,days)
            self.assertEqual(rows_seen,1)
            self.assertEqual(needs,{'000001':['2026-09-18']})


if __name__ == '__main__':
    unittest.main()
