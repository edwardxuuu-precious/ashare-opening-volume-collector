"""New recovery fixtures; ALL_VALUES_INVENTED. No provider or AWS calls."""
import unittest
from decimal import Decimal as D
from backend.chips.model import Bar,estimate_snapshots
from backend.chips.runner import shared_universe
from backend.chips.screen import board

class BootstrapV2(unittest.TestCase):
    def test_current_catalog_accepts_302_growth_codes_and_keeps_all_markets(self):
        rows=[{'code':str(n).zfill(6),'name':'INVENTED'} for n in range(1,1001)]+[{'code':c,'name':'INVENTED'} for c in ('300001','302132','688001','920985')]
        class Store:
            def get(self,key):return {'asOf':'2026-10-16','historicalMembership':False,'total':len(rows),'rows':rows},'INVENTED_ETAG'
        stocks,receipts=shared_universe(Store(),'2026-10-16')
        self.assertEqual(len(stocks),1004);self.assertEqual(board('302132')[0],'CHINEXT');self.assertEqual(receipts[0]['cache_age_days'],0)
    def test_future_source_row_is_excluded_from_exported_state_and_empty_history_cannot_validate_cache(self):
        bars=[Bar('2026-10-'+str(12+i),D('5.00'),D('5.02'),D('4.98'),D('5.00'),D('1000'),D('5000'),D('3')) for i in range(5)]
        direct,state=estimate_snapshots(bars,['2026-10-14','2026-10-15'],security_id='INVENTED_V2',export_state=True)
        self.assertEqual(state['rows_processed'],4);self.assertEqual(state['last_date'],'2026-10-15')
        with self.assertRaisesRegex(ValueError,'MODEL_STATE_BOUNDARY_MISSING'):
            estimate_snapshots([],['2026-10-14','2026-10-15'],security_id='INVENTED_V2',state=state)
