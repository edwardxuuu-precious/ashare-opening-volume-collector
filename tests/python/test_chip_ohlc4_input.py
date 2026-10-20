"""Invented off-book amount and strict basic validation boundaries."""
import hashlib,json,unittest
from backend.chips.ohlc4_input import prepare_eastmoney
from backend.chips.model import prepare_eastmoney as legacy_prepare
from backend.chips.current_quote import daily_line,renormalize
from test_chip_current_quote import quote


def bundle(line):return json.dumps({'rc':0,'data':{'code':'000573','name':'ALL_VALUES_INVENTED','klines':[line]}}).encode()


class Ohlc4Input(unittest.TestCase):
    def test_unused_vwap_is_audited_without_altering_any_source_value(self):
        line='2026-10-19,10.00,10.10,10.20,9.90,1000,900000,,0,0,2.5';raw=bundle(line)
        with self.assertRaisesRegex(ValueError,'VWAP_OUTSIDE_OHLC'):legacy_prepare(raw,expected_code='000573',cutoff='2026-10-19')
        bars,audit=prepare_eastmoney(raw,expected_code='000573',cutoff='2026-10-19')
        self.assertEqual(str(bars[0].volume_shares),'100000');self.assertEqual(str(bars[0].amount_cny),'900000')
        self.assertEqual(audit['invalid_source_rows'],[])
        self.assertEqual(audit['non_blocking_vwap_observations'][0]['original_row'],line)
        self.assertFalse(audit['non_blocking_vwap_observations'][0]['off_book_cause_authenticated'])

    def test_nonfinite_ohlc_and_quantity_conflicts_still_reject(self):
        line='2026-10-19,10.00,10.10,10.20,9.90,1000,900000,,0,0,2.5'
        for index,value in ((1,'NaN'),(3,'9.80'),(6,'-1'),(5,'0'),(10,'-1')):
            f=line.split(',');f[index]=value
            with self.assertRaises(ValueError):prepare_eastmoney(bundle(','.join(f)),expected_code='000573',cutoff='2026-10-19')

    def test_cached_quote_error_is_rebuilt_from_unchanged_original_body(self):
        q=quote();f=q['raw_quote_body'].split('~');f[35]='4.31/1000/300000';f[37]='30.00';q['raw_quote_body']='~'.join(f);q['raw_quote_sha256']=hashlib.sha256(q['raw_quote_body'].encode('gb18030')).hexdigest()
        q.pop('daily_line');q['daily_line_error']='AMOUNT_VOLUME_VWAP_OUTSIDE_OHLC'
        out=renormalize({'000573':q},'2026-10-19')['000573']
        self.assertNotIn('daily_line_error',out);self.assertEqual(out['raw_quote_body'],q['raw_quote_body'])
        self.assertEqual(out['daily_line'],daily_line('000573','2026-10-19',q['raw_quote_body']))
