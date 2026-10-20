"""New ALL_VALUES_INVENTED fallback-unit and suspension cases; no I/O."""
import unittest,json
from backend.chips.baostock_history import normalize
from backend.chips.runner import frame
from backend.chips.calendar import last_ten

class BaoAdapterV1(unittest.TestCase):
    def test_share_units_percent_and_no_synthetic_suspension_candle(self):
        rows=[]
        for d in last_ten('2026-10-16'):
            rows.append([d,'sz.000001','5.00','5.02','4.98','5.00','5.00','1000','5000','3','2.5','1','0','0'])
        rows[1]=[rows[1][0],'sz.000001','5.00','5.00','5.00','5.00','5.00','0','0','3','','0','0','0']
        stock={'code':'000001','name':'ALL_VALUES_INVENTED'};v,raw=normalize(stock,rows,'2026-10-16')
        self.assertEqual(len(v['data']['klines']),9);self.assertEqual(v['data']['klines'][0].split(',')[5],'10')
        self.assertEqual(v['confirmed_no_trade_sessions'],[rows[1][0]])
        r,out=frame(stock,raw,{'provider':'BAOSTOCK_0.9.3'},last_ten('2026-10-16'),{},'ffffffffffffffff')
        self.assertEqual(r['chip_status'],'READY');self.assertEqual(out['history'][-1]['volume_shares'],'1000')
        self.assertEqual(out['snapshots']['2026-10-16']['turnover_percent'],'2.5')
        self.assertFalse(out['source_audit']['original_raw_preserved'])
        self.assertTrue(out['source_audit']['original_sdk_field_strings_preserved'])
        bad=[list(r) for r in rows];bad[0][9]='2'
        with self.assertRaisesRegex(ValueError,'ADJUSTMENT'):normalize(stock,bad,'2026-10-16')
