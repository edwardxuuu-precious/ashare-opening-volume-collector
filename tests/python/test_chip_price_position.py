"""Synthetic policy fixtures; no market data or network."""
import copy
import unittest
from backend.chips.screen import apply_price_position

class PricePosition(unittest.TestCase):
    def project(self,close='5',peaks=('5.2',),mean='5.3',base='PASS',ready=True):
        row={'close_cny':close,'prefilter_status':base,'chip_status':'READY' if ready else 'MISSING',
            'recent_limit_up_dates':['2001-01-02'],'peak_prices_cny':list(peaks),
            'original_peak_changes':[{'unchanged':'audit'}]}
        snapshot={'close_cny':close,'peak_prices_cny':list(peaks),'model_mean_cost_cny':mean}
        before=copy.deepcopy(snapshot)
        apply_price_position(row,snapshot if ready else None)
        self.assertEqual(snapshot,before)
        self.assertEqual(row['prefilter_status'],base)
        self.assertEqual(row['original_peak_changes'],[{'unchanged':'audit'}])
        return row

    def test_either_branch_and_both_are_sufficient(self):
        for peaks,mean,states in [(('4.9',),'5.1',('PASS','FAIL')),(('5.1',),'4.9',('FAIL','PASS')),(('4.9',),'4.8',('PASS','PASS'))]:
            with self.subTest(states=states):
                row=self.project(peaks=peaks,mean=mean)
                self.assertEqual((row['above_peak_status'],row['above_mean_cost_status']),states)
                self.assertEqual(row['screen_status'],'PASS')

    def test_equal_to_both_is_not_above(self):
        self.assertEqual(self.project(peaks=('5',),mean='5')['screen_status'],'FAIL')
        self.assertEqual(self.project()['screen_status'],'FAIL')

    def test_mean_uses_full_precision_and_tied_peaks_use_any(self):
        self.assertEqual(self.project(mean='4.999999999999999999')['screen_status'],'PASS')
        self.assertEqual(self.project(peaks=('4.8','5.5'))['above_peak_status'],'PASS')

    def test_unknown_never_becomes_a_match_but_known_or_pass_survives(self):
        for operand in (None,'NaN','Infinity','-1','broken'):
            with self.subTest(operand=operand):
                self.assertEqual(self.project(mean=operand)['screen_status'],'UNKNOWN')
                self.assertEqual(self.project(mean=operand,peaks=('4.9',))['screen_status'],'PASS')
        self.assertEqual(self.project(peaks=(),mean='4.9')['screen_status'],'PASS')
        self.assertEqual(self.project(peaks=(),mean='5.1')['screen_status'],'UNKNOWN')
        self.assertEqual(self.project(ready=False)['screen_status'],'UNKNOWN')

    def test_original_conjunction_and_snapshot_identity_are_retained(self):
        self.assertEqual(self.project(base='FAIL',mean='4')['screen_status'],'FAIL')
        self.assertEqual(self.project(base='UNKNOWN',mean='4')['screen_status'],'UNKNOWN')
        self.assertEqual(self.project(base='UNKNOWN')['screen_status'],'FAIL')
        row={'close_cny':'5','prefilter_status':'PASS','chip_status':'READY'}
        apply_price_position(row,{'close_cny':'4','peak_prices_cny':['3'],'model_mean_cost_cny':'3'})
        self.assertEqual(row['screen_status'],'UNKNOWN')
        self.assertIsNone(row['model_mean_cost_cny'])

if __name__=='__main__':unittest.main()
