"""Invented exact-endpoint residual and source-bound migration contracts."""
import copy,math,unittest
from datetime import date,timedelta
from decimal import Decimal as D
from unittest.mock import patch
from backend.chips.eastmoney_model import Bar,MODEL,estimate_snapshots
from backend.chips.runner import compatible_ready_model,implementation_identity


def source():
    return [Bar('2026-10-12',D('56.70'),D('56.99'),D('56.50'),D('56.80'),D('1000'),D('56700'),D('2')),
        Bar('2026-10-13',D('21.00'),D('21.20'),D('20.81'),D('21.10'),D('1000'),D('21000'),D('2'))]


class Roundoff(unittest.TestCase):
    def test_only_the_final_proven_negative_endpoint_is_zeroed(self):
        s=estimate_snapshots(source(),['2026-10-13'],security_id='INVENTED')['2026-10-13']
        self.assertEqual(len(s['roundoff_repairs']),1);r=s['roundoff_repairs'][0]
        self.assertEqual(r['node_index'],149);self.assertLess(float(r['original_raw_mass']),0)
        self.assertEqual(D(s['raw_node_masses'][149]),0)
        self.assertEqual(r['events'][0]['grid_numerator'],int(D('56.99')*14900))
        self.assertTrue(all(D(v)>=0 for v in s['raw_node_masses']))

    def test_an_unproven_negative_shape_is_never_silently_clamped(self):
        original=math.floor
        with patch('backend.chips.eastmoney_model.math.floor',side_effect=lambda x:original(x)+1 if 0<x<5 else original(x)):
            with self.assertRaisesRegex(ValueError,'WITHOUT_EXACT_ENDPOINT_PROOF'):
                estimate_snapshots(source(),['2026-10-13'],security_id='INVENTED')

    def test_old_ready_requires_exact_source_pair_and_full_snapshot_replay(self):
        bars=[Bar((date(2026,1,1)+timedelta(days=i)).isoformat(),D('5.00'),D('5.00'),D('5.00'),D('5.00'),D('1000'),D('5000'),D('2')) for i in range(210)]
        targets=[b.source_date for b in bars[-2:]];snapshots=estimate_snapshots(bars,targets,security_id='INVENTED')
        for s in snapshots.values():s.pop('roundoff_repairs')
        current=implementation_identity();old=copy.deepcopy(current)
        old['model_implementation_sha256']='3f4e2515c261ea219b187c9803f73a3460c933c813de889c820cd82111956091'
        old['files']['eastmoney_model.py']='41f391357dc7b6f52136e137b561c4c795596abb58b8552eb8c7140f58064743'
        previous={'security_id':'INVENTED','snapshots':snapshots,'execution_identity':old,'history':[
            {'date':b.source_date,**{key:str(getattr(b,field)) for key,field in (('open','open'),('high','high'),('low','low'),('close','close'),('volume_shares','volume_shares'),('amount_cny','amount_cny'),('turnover_percent','turnover_pct'))}} for b in bars]}
        reused=copy.deepcopy(previous);self.assertTrue(compatible_ready_model(reused,current))
        self.assertEqual(reused['calculation_execution_identity'],old)
        self.assertTrue(reused['roundoff_compatibility']['all_snapshots_fully_replayed'])
        for mutate in ('source','input','raw','prefix'):
            bad=copy.deepcopy(previous)
            if mutate=='source':bad['execution_identity']['files']['eastmoney_model.py']='UNREVIEWED'
            if mutate=='input':bad['execution_identity']['files']['ohlc4_input.py']='UNREVIEWED'
            if mutate=='raw':bad['snapshots'][targets[0]]['raw_node_masses'][0]='-1e-30'
            if mutate=='prefix':bad['snapshots'][targets[0]]['normalized_input_prefix_sha256']='CHANGED'
            self.assertFalse(compatible_ready_model(bad,current),mutate)
