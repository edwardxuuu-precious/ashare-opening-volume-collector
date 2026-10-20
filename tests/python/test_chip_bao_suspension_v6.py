"""ALL_VALUES_INVENTED: confirmed suspension is distinct from missing trading data."""
import unittest
from unittest.mock import patch
from backend.chips.baostock_history import normalize,BaoHistory,BaoStockTransportError

class SuspensionV6(unittest.TestCase):
    def test_blank_confirmed_suspension_preserved_without_zero_filling(self):
        stock={'code':'688005','name':'INVENTED'}
        suspended=['2026-10-15','sh.688005','5','5','5','5','5','','','3','','0','0','0']
        traded=['2026-10-16','sh.688005','5','5','5','5','5','1000','5000','3','2','1','0','0']
        value,_=normalize(stock,[suspended,traded],'2026-10-16')
        self.assertEqual(value['confirmed_no_trade_sessions'],['2026-10-15'])
        self.assertEqual(len(value['data']['klines']),1)
        self.assertEqual(value['original_sdk_response']['rows'][0][7:9],['',''])
        self.assertEqual(value['confirmed_no_trade_missing_fields'][0]['missing_fields'],['volume','amount','turn'])
        bad=suspended.copy();bad[7]='10'
        with self.assertRaisesRegex(ValueError,'CONFLICT'):normalize(stock,[bad,traded],'2026-10-16')
        active=suspended.copy();active[11]='1'
        with self.assertRaises(ArithmeticError):normalize(stock,[active,traded],'2026-10-16')
    def test_transport_recovery_is_bounded_and_recorded(self):
        row=['2026-10-16','sh.688005','5','5','5','5','5','1000','5000','3','2','1','0','0']
        source=BaoHistory.__new__(BaoHistory)
        with patch.object(source,'_rows',side_effect=[BaoStockTransportError('INVENTED_TIMEOUT'),[row]]) as fetch,patch('backend.chips.baostock_history.time.sleep'):
            _,receipt,_=source.history({'code':'688005','name':'INVENTED'},'2026-10-16')
            self.assertEqual(fetch.call_count,2);self.assertEqual(len(receipt['transport_failures_before_success']),1)
        with patch.object(source,'_rows',side_effect=BaoStockTransportError('INVENTED_TIMEOUT')) as fetch,patch('backend.chips.baostock_history.time.sleep'):
            with self.assertRaises(BaoStockTransportError):source.history({'code':'688005','name':'INVENTED'},'2026-10-16')
            self.assertEqual(fetch.call_count,2)
