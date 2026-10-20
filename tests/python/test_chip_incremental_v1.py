"""New invented formula-equivalence and corrupt-cache cases; no market I/O."""
import copy,json,unittest
from decimal import Decimal as D
from backend.chips.model import Bar,estimate_snapshots

class IncrementalV1(unittest.TestCase):
    def test_exact_decimal_state_matches_full_recurrence_after_json_round_trip(self):
        bars=[Bar('2026-10-'+str(12+i),D('4.50'),D('4.52'),D('4.48'),D('4.50'),D('1000'),D('4500'),D(str(2+i))) for i in range(5)]
        direct=estimate_snapshots(bars,['2026-10-15','2026-10-16'],security_id='INVENTED')
        _,state=estimate_snapshots(bars[:4],['2026-10-14','2026-10-15'],security_id='INVENTED',export_state=True)
        restored=json.loads(json.dumps(state))
        warm,next_state=estimate_snapshots(bars,['2026-10-15','2026-10-16'],security_id='INVENTED',state=restored,export_state=True)
        self.assertEqual(warm,direct);self.assertEqual(next_state['rows_processed'],5)
        altered=list(bars);altered[0]=Bar('2026-10-12',D('4.50'),D('4.52'),D('4.48'),D('4.50'),D('1000'),D('4500'),D('9'))
        with self.assertRaisesRegex(ValueError,'MODEL_STATE_PREFIX_CHANGED'):
            estimate_snapshots(altered,['2026-10-15','2026-10-16'],security_id='INVENTED',state=restored)
        with self.assertRaisesRegex(ValueError,'MODEL_STATE_IDENTITY_MISMATCH'):
            estimate_snapshots(bars,['2026-10-15','2026-10-16'],security_id='OTHER_INVENTED',state=restored)
