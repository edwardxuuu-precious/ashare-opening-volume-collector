import _bootstrap
import gc
import json
import tempfile
import unittest
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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

    def test_provider_system_exit_becomes_checkpointed_source_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);day='2026-09-18'
            (out/f'{day}.json').write_text(json.dumps(dict(date=day,rows=[traded()])))
            (out/'manifest.json').write_text(json.dumps(dict(dates=[])))
            options=SimpleNamespace(
                out=str(out),cache=str(out/'price-backfill-cache.json'),
                backfill_id='ohlc-v1-20260920',from_date=day,to_date=day,batch_size=20,
                workers=1,interval=.35,cutoff='23:50',lookback=14,retries=0,
                save_every=200,force=False)
            scans=[({'000001':[day]},1,[day],[day]),({},1,[],[day])]
            provider=SimpleNamespace(
                stock_zh_a_hist_tx=Mock(side_effect=SystemExit(1)),
                stock_zh_a_daily=Mock(side_effect=SystemExit(1)))

            with patch.object(backfill_quotes,'scan_needs',side_effect=scans), \
                 patch.object(backfill_quotes.SharedHTTPBudget,'install'), \
                 patch.dict('sys.modules',{'akshare':provider}), \
                 patch.object(backfill_quotes,'patch_files',return_value=dict(
                     filledRows=0,unfilledRows=1,filesChanged=1)), \
                 patch.object(backfill_quotes,'touch_manifest'):
                result=backfill_quotes.run(options)

            self.assertEqual(result,1)
            state=json.loads((out/'price-backfill-state.json').read_text())
            self.assertEqual(state['exitReason'],'source_incomplete')
            cache=json.loads((out/'price-backfill-cache.json').read_text())
            self.assertIn('SystemExit',cache['quotes']['000001']['error'])


if __name__ == '__main__':
    unittest.main()
