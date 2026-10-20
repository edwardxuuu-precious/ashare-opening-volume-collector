"""ALL_VALUES_INVENTED: both failed reads remain inspectable in the missing frame."""
import time,unittest
from unittest.mock import patch
from backend.chips.baostock_history import BaoHistory,BaoStockTransportError
from backend.chips.runner import _calculate,implementation_identity
from backend.chips.calendar import last_ten
from test_chip_daily_v1 import Memory

class TransportV7(unittest.TestCase):
    def test_terminal_transport_failure_list_is_saved_with_missing_stock(self):
        bao=BaoHistory.__new__(BaoHistory)
        class Source:
            def history(self,stock,as_of):return bao.history(stock,as_of)
        store=Memory();owner='INVENTED'
        store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+600})
        ctx={'slot':'7777777777777777','dataset':'7777777777777777','days':last_ten('2026-10-16'),'as_of':'2026-10-16',
            'owner':owner,'pools':{},'actual_limits':{},'execution_identity':implementation_identity()}
        with patch.object(bao,'_rows',side_effect=BaoStockTransportError('INVENTED_TIMEOUT')) as fetch,patch('backend.chips.baostock_history.time.sleep'):
            row=_calculate(store,Source(),{'code':'688005','name':'INVENTED'},ctx)
        frame=store.get('data/chips/daily/7777777777777777/688005.json')[0]
        self.assertEqual(row['chip_status'],'MISSING');self.assertEqual(fetch.call_count,2)
        self.assertEqual([r['attempt'] for r in frame['source_failures']],[1,2])
        self.assertTrue(all(r['recorded_at'] for r in frame['source_failures']))
