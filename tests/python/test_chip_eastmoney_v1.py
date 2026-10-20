"""Invented model-boundary and publication migration cases; no external I/O."""
import json
import unittest
from datetime import date,timedelta
from decimal import Decimal as D
from unittest.mock import patch
from backend.chips.eastmoney_model import Bar,MODEL,estimate_snapshots,compare_snapshots,display_price
from backend.chips.runner import run
from test_chip_daily_v1 import Memory,FakeSource,DAYS


def bars(count):
    return [Bar((date(2025,1,1)+timedelta(days=i)).isoformat(),D('5.00'),D('5.20'),D('4.80'),D('5.10'),D('1000'),D('5000'),D('2')) for i in range(count)]


class EastmoneyV1(unittest.TestCase):
    def test_window_and_prefix_no_synthetic_or_future_bars(self):
        source=bars(215);targets=[b.source_date for b in source[-2:]]
        result=estimate_snapshots(source,targets,security_id='INVENTED')
        self.assertEqual([result[d]['history_rows_processed'] for d in targets],[209,210])
        self.assertTrue(all(s['seed_source_date']==source[5].source_date and len(s['profile'])==150 for s in result.values()))
        changed=list(source);changed[0]=Bar(source[0].source_date,D('99'),D('99'),D('99'),D('99'),D('1000'),D('99000'),D('99'))
        self.assertEqual(result,estimate_snapshots(changed,targets,security_id='INVENTED'))
        future=source+[Bar('2030-01-01',D('99'),D('99'),D('99'),D('99'),D('1000'),D('99000'),D('99'))]
        self.assertEqual(result,estimate_snapshots(future,targets,security_id='INVENTED'))

    def test_flat_source_height_and_zero_initialization(self):
        b=Bar('2026-10-16',D('5'),D('5'),D('5'),D('5'),D('1000'),D('5000'),D('2'))
        s=estimate_snapshots([b],[b.source_date],security_id='INVENTED')[b.source_date]
        self.assertEqual(float(s['raw_total_mass']),.04*74.5)
        self.assertEqual(s['peak_prices_cny'],['5.00'])
        self.assertEqual(float(s['peak_bin_share_percent']),100)
        self.assertIsNone(s['unchanged_threshold'])

    def test_display_rounding_is_source_to_fixed_half_up(self):
        self.assertEqual(display_price(3.125),'3.13')

    def test_completed_legacy_generation_does_not_block_new_algorithm(self):
        store=Memory()
        with patch('backend.chips.runner.Source',FakeSource):
            first=run(store,as_of=DAYS[-1],max_minutes=2,workers=1)
            manifest,etag=store.get('data/chips/daily/manifest.json')
            manifest['execution_identity']={'sha256':'INVENTED_LEGACY_VERSION'}
            store.put('data/chips/daily/manifest.json',manifest,match=etag)
            second=run(store,as_of=DAYS[-1],max_minutes=2,workers=1)
        self.assertEqual(first['outcome'],second['outcome'])
        self.assertEqual(second['outcome'],'COMMITTED')
        self.assertEqual(store.get('data/chips/daily/manifest.json')[0]['model_version'],MODEL['version'])
