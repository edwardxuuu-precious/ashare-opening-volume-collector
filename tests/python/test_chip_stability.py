"""Invented geometric/temporal counterexamples. No network or market inputs."""
import copy
import unittest
from decimal import Decimal as D
from fractions import Fraction as F
from unittest.mock import patch

from backend.chips.stability import (RULE_ID, cells, rebin, relative_distance, anchor_bands,
    band_mass, judge_snapshots, classify, text, calculation_complete, empty, InputError, ResourceBound)
from backend.chips.eastmoney_model import VERSION, estimate_snapshots
from backend.chips.model import Bar
from backend.chips.calendar import last_ten
from backend.chips.screen import apply_stability

DATES = last_ten('2026-10-23')
BASE, FIRST, END = DATES[-3:]


def snapshot(weights=None, *, low='90', step='1', day=BASE, code='INVENTED'):
    if weights is None:
        weights={10:'20', **{i:'8' for i in range(20,30)}}
    low,step=D(low),D(step)
    return {'model_version':VERSION,'source_date':day,'security_id':code,'seed_source_date':'2026-01-01',
        'price_grid':{'low_cny':str(low),'step_cny':str(step),'node_count':150},
        'profile':[{'price_cny':str(low+step*i),'share_percent':str(weights.get(i,'0'))} for i in range(150)]}


def series(current=None):
    return {BASE:snapshot(), FIRST:current or snapshot(day=FIRST), END:copy.deepcopy(current or snapshot(day=END))}


def judge(value):
    return judge_snapshots(value,BASE,[FIRST,END],first_limit_up_date=FIRST)


class ExactGeometry(unittest.TestCase):
    def test_mass_conservation_and_outside_anchor_mass(self):
        src=[(F(99),F(100),F(1,5)),(F(100),F(101),F(4,5))]
        top,audit=rebin(src,F('99.5'),F(1))
        self.assertEqual(F(audit['total_mass']),1)
        self.assertEqual(band_mass(src,(F('99.5'),F('100.5'))),F(1,2))
        self.assertTrue(top)

    def test_new_high_keeps_entire_domain(self):
        top,audit=rebin([(F(99),F(100),F(1,10)),(F(199),F(200),F(9,10))],F('98.5'),F(1))
        self.assertEqual(F(audit['total_mass']),1)
        self.assertGreater(max(top),190)
        self.assertGreater(audit['comparison_cells'],90)

    def test_zero_clipped_cell_has_positive_center(self):
        top,audit=rebin([(F(0),F('0.5'),F(1))],F('0.5'),F(1))
        self.assertEqual(top,[F('0.25')])
        self.assertEqual(F(audit['total_mass']),1)

    def test_relative_symmetric_distance_cannot_pick_favorable_peak(self):
        self.assertEqual(relative_distance([F(100)],[F(101)]),F('.01'))
        self.assertGreater(relative_distance([F(100),F(140)],[F(100)]),F('.01'))
        self.assertGreater(relative_distance([F(100)],[F(100),F(140)]),F('.01'))

    def test_neighbor_search_uses_relative_ratio_before_min(self):
        base=[F(1),F(3)];current=[F(2)]
        brute=max(max(min(abs(p-q)/q for p in current) for q in base),
                  max(min(abs(p-q)/q for q in base) for p in current))
        self.assertEqual(relative_distance(base,current),brute)

    def test_overlapping_anchor_bands_merge_once(self):
        self.assertEqual(len(anchor_bands([F(100),F(102)],F(1))),1)
        self.assertEqual(len(anchor_bands([F(100),F(140)],F(1))),2)

    def test_resource_bound_and_invalid_distribution(self):
        with self.assertRaises(ResourceBound):
            rebin([(F(1),F(30000),F(1))],F(1),F(1))
        bad=snapshot();bad['profile'][10]['share_percent']='NaN'
        with self.assertRaises(InputError):cells(bad)
        bad=snapshot();bad['profile'][10]['share_percent']='-1'
        with self.assertRaises(InputError):cells(bad)

    def test_summary_rounding_cannot_hide_tiny_threshold_violation(self):
        above=F(1,100)+F(1,10**100);below=F(9,10)-F(1,10**100)
        self.assertGreater(D(text(above,upper=True)),D('.01'))
        self.assertLess(D(text(below)),D('.9'))

    def test_terminal_unknown_is_computed_but_missing_evidence_can_be_repaired(self):
        row={'screen_status':'UNKNOWN','chip_stability':empty('NATIVE_RESOLUTION_INSUFFICIENT')}
        self.assertTrue(calculation_complete(row));self.assertEqual(row['screen_status'],'UNKNOWN')
        row['chip_stability']=empty('EARLIER_LIMIT_STATUS_UNKNOWN')
        self.assertFalse(calculation_complete(row))
        row['chip_stability']=empty('PRICE_BASIS_INCOMPARABLE')
        self.assertTrue(calculation_complete(row))
        row['chip_stability']['reasons'].append('CHIP_HISTORY_INCOMPLETE')
        self.assertFalse(calculation_complete(row))


class StableRegions(unittest.TestCase):
    def test_unchanged_daily_profile_passes_without_mutation(self):
        value=series();before=copy.deepcopy(value);s,e=judge(value)
        self.assertEqual(s['status'],'PASS');self.assertEqual(s['evaluated_sessions'],2)
        self.assertEqual(value,before);self.assertEqual(len(e['daily']),2)

    def test_one_percent_inclusive_and_larger_move_fails(self):
        for index,expected in ((11,'PASS'),(12,'FAIL')):
            current=snapshot({index:'20',**{i:'8' for i in range(20,30)}})
            s,_=judge(series(current));self.assertEqual(s['status'],expected)

    def test_mass_ratio_is_relative_not_percentage_points(self):
        for peak,other,expected in (('18','8.2','PASS'),('17.99','8.201','FAIL'),('12','8.8','FAIL')):
            current=snapshot({10:peak,**{i:other for i in range(20,30)}})
            s,_=judge(series(current));self.assertEqual(s['status'],expected)
            if expected=='FAIL':self.assertIn('ANCHOR_BAND_MASS_DECREASED',s['reasons'])

    def test_multiple_original_regions_all_survive(self):
        base=snapshot({10:'20',50:'20',**{i:'6' for i in range(20,30)}})
        s,e=judge({BASE:base,FIRST:copy.deepcopy(base),END:copy.deepcopy(base)})
        self.assertEqual(s['status'],'PASS');self.assertEqual(len(e['anchor_bands']),2)

    def test_new_distant_top_region_is_not_ignored(self):
        current=snapshot({10:'20',70:'19',**{i:'6.1' for i in range(20,30)}})
        s,_=judge(series(current));self.assertEqual(s['status'],'FAIL')
        self.assertIn('PEAK_REGION_MOVED',s['reasons'])

    def test_ninety_percent_top_height_is_inclusive(self):
        value=snapshot({10:'20',70:'18',**{i:'6.2' for i in range(20,30)}})
        s,_=judge(series(value));self.assertEqual(s['status'],'FAIL')
        value=snapshot({10:'20',70:'17.99',**{i:'6.201' for i in range(20,30)}})
        s,_=judge(series(value));self.assertEqual(s['status'],'PASS')

    def test_leaves_then_returns_still_fails(self):
        value=series();value[FIRST]=snapshot({15:'20',**{i:'8' for i in range(20,30)}})
        s,_=judge(value);self.assertEqual(s['status'],'FAIL')

    def test_small_daily_moves_cannot_hide_cumulative_displacement(self):
        days=DATES[-4:];base,one,two,three=days
        def at(index):return snapshot({index:'20',**{i:'8' for i in range(30,40)}},low='95',step='.5')
        value={base:at(10),one:at(11),two:at(12),three:at(13)}
        s,_=judge_snapshots(value,base,[one,two,three],first_limit_up_date=one)
        self.assertEqual(s['status'],'FAIL');self.assertGreater(D(s['max_shift_fraction']),D('.01'))

    def test_native_coarse_grid_stays_unknown(self):
        value=series(snapshot({10:'20',**{i:'8' for i in range(20,30)}},low='80',step='2'))
        s,_=judge(value);self.assertEqual(s['status'],'UNKNOWN')
        self.assertIn('NATIVE_RESOLUTION_INSUFFICIENT',s['reasons'])

    def test_missing_day_unknown_but_observed_clear_failure_excludes(self):
        value=series();del value[END];s,_=judge(value);self.assertEqual(s['status'],'UNKNOWN')
        value[FIRST]=snapshot({15:'20',**{i:'8' for i in range(20,30)}})
        s,_=judge(value);self.assertEqual(s['status'],'FAIL')

    def test_finer_shifted_grid_same_continuous_cells_pass(self):
        weights={19:'10',20:'10'}
        for price in range(110,120):
            for offset in (D('-.25'),D('.25')):
                i=int((D(price)+offset-D('90.25'))/D('.5'));weights[i]='4'
        current=snapshot(weights,low='90.25',step='.5')
        s,_=judge(series(current));self.assertEqual(s['status'],'PASS')

    def test_new_same_price_chips_can_pass_not_holder_retention(self):
        current=snapshot({10:'22',**{i:'7.8' for i in range(20,30)}})
        s,_=judge(series(current));self.assertEqual(s['status'],'PASS')


class TemporalIntegration(unittest.TestCase):
    def setup_history(self):
        history=[{'date':d,'open':'100','high':'100','low':'100','close':'100',
            'volume_shares':'1000','amount_cny':'100000','turnover_percent':'.01'} for d in DATES]
        row={'prefilter_status':'PASS','price_position_status':'PASS','chip_status':'READY',
            'recent_limit_up_dates':[END],'limit_evidence':[{'date':d,'status':'PASS' if d==END else 'FAIL'} for d in DATES]}
        return row,history

    def test_full_history_classifies_and_summary_joins_three_conditions(self):
        row,history=self.setup_history();s,proof=classify(row,history,DATES,security_id='INVENTED')
        self.assertEqual(s['status'],'PASS');self.assertEqual(s['baseline_date'],FIRST)
        self.assertEqual(s['required_sessions'],1);self.assertTrue(proof)
        apply_stability(row,s);self.assertEqual(row['screen_status'],'PASS')
        row['prefilter_status']='FAIL';apply_stability(row,s);self.assertEqual(row['screen_status'],'FAIL')

    def test_earliest_limit_date_does_not_reset_to_last(self):
        row,history=self.setup_history();row['recent_limit_up_dates']=[FIRST,END]
        row['limit_evidence'][-2]['status']='PASS'
        s,_=classify(row,history,DATES,security_id='INVENTED')
        self.assertEqual(s['first_limit_up_date'],FIRST);self.assertEqual(s['baseline_date'],BASE)
        self.assertEqual(s['required_sessions'],2)

    def test_unknown_before_event_is_not_silently_ignored(self):
        row,history=self.setup_history();row['limit_evidence'][0]['status']='UNKNOWN'
        s,_=classify(row,history,DATES,security_id='INVENTED')
        self.assertEqual(s['status'],'UNKNOWN');self.assertIn('EARLIER_LIMIT_STATUS_UNKNOWN',s['reasons'])

    def test_absent_baseline_never_passes(self):
        row,history=self.setup_history();history=[h for h in history if h['date']!=FIRST]
        s,_=classify(row,history,DATES,security_id='INVENTED')
        self.assertEqual(s['status'],'UNKNOWN');self.assertIn('BASELINE_NOT_AVAILABLE',s['reasons'])

    def test_reference_close_discontinuity_unknown(self):
        row,history=self.setup_history()
        ref={'rows':{END:{'preclose':'90','open':'100','high':'100','low':'100','close':'100'}}}
        s,_=classify(row,history,DATES,security_id='INVENTED',reference_evidence=ref)
        self.assertEqual(s['status'],'UNKNOWN');self.assertEqual(s['reasons'],['PRICE_BASIS_INCOMPARABLE'])

    def test_missing_prior_session_is_not_a_terminal_price_basis_conflict(self):
        row,history=self.setup_history();event,gap,after=DATES[-6:-3]
        row['recent_limit_up_dates']=[event]
        row['limit_evidence']=[{'date':d,'status':'PASS' if d==event else 'FAIL'} for d in DATES]
        history=[h for h in history if h['date']!=gap]
        s,_=classify(row,history,DATES,security_id='INVENTED',reference_evidence={'rows':{after:{'preclose':'101','open':'100','high':'100','low':'100','close':'100'}}})
        self.assertEqual(s['status'],'UNKNOWN')
        self.assertIn('CHIP_HISTORY_INCOMPLETE',s['reasons'])
        self.assertNotIn('PRICE_BASIS_INCOMPARABLE',s['reasons'])
        self.assertFalse(calculation_complete({'screen_status':'UNKNOWN','chip_stability':s}))

    def test_conflicting_trade_status_never_creates_a_carry_pass(self):
        for observed,traded in ((False,'1'),(True,'0')):
            with self.subTest(observed=observed):
                row,history=self.setup_history();event,day=DATES[-4:-2]
                row['recent_limit_up_dates']=[event]
                row['limit_evidence']=[{'date':d,'status':'PASS' if d==event else 'FAIL'} for d in DATES]
                if not observed:history=[h for h in history if h['date']!=day]
                s,_=classify(row,history,DATES,security_id='INVENTED',no_trade=[day] if not observed else [],
                    reference_evidence={'rows':{day:{'tradestatus':traded,'preclose':'100'}}})
                self.assertEqual(s['status'],'UNKNOWN');self.assertIn('DATA_TRADE_STATUS_CONFLICT',s['reasons'])
                self.assertFalse(calculation_complete({'screen_status':'UNKNOWN','chip_stability':s}))

    def test_conflicting_reference_price_is_repairable_not_a_corporate_action(self):
        row,history=self.setup_history()
        ref={'rows':{END:{'preclose':'90','open':'99','high':'100','low':'99','close':'100'}}}
        s,_=classify(row,history,DATES,security_id='INVENTED',reference_evidence=ref)
        self.assertEqual(s['status'],'UNKNOWN');self.assertIn('DATA_PRICE_EVIDENCE_CONFLICT',s['reasons'])
        self.assertNotIn('PRICE_BASIS_INCOMPARABLE',s['reasons'])
        self.assertFalse(calculation_complete({'screen_status':'UNKNOWN','chip_stability':s}))

    def test_future_bar_is_ignored(self):
        row,history=self.setup_history();expected=classify(row,history,DATES,security_id='INVENTED')[0]
        history.append({'date':'2026-10-26','open':'BROKEN_FUTURE_DATA'})
        self.assertEqual(classify(row,history,DATES,security_id='INVENTED')[0],expected)

    def test_known_other_failure_skips_unnecessary_replay(self):
        row,history=self.setup_history();row['price_position_status']='FAIL'
        with patch('backend.chips.stability.estimate_snapshots') as replay:
            s,_=classify(row,history,DATES,security_id='INVENTED')
        replay.assert_not_called();self.assertEqual(s['status'],'UNKNOWN')
        self.assertEqual(s['reasons'],['OTHER_CONDITION_FAILED_NOT_EVALUATED'])

    def test_confirmed_no_trade_carries_model_not_a_fabricated_bar(self):
        row,history=self.setup_history();row['recent_limit_up_dates']=[FIRST];row['limit_evidence'][-2]['status']='PASS'
        history=history[:-1];before=copy.deepcopy(history)
        s,proof=classify(row,history,DATES,security_id='INVENTED',no_trade=[END])
        self.assertEqual(s['status'],'PASS');self.assertEqual(s['evaluated_sessions'],2)
        self.assertEqual(history,before);self.assertEqual(proof['observed_anchor_dates'][END],FIRST)

    def test_later_missing_session_does_not_hide_earlier_supported_failure(self):
        row,history=self.setup_history();row['recent_limit_up_dates']=[FIRST];row['limit_evidence'][-2]['status']='PASS'
        history=history[:-1];history[-1].update(open='110',high='110',low='110',close='110',amount_cny='110000',turnover_percent='50')
        s,_=classify(row,history,DATES,security_id='INVENTED')
        self.assertEqual(s['status'],'FAIL');self.assertEqual(s['evaluated_sessions'],1)
        self.assertEqual(s['required_sessions'],2)


if __name__=='__main__':unittest.main()
