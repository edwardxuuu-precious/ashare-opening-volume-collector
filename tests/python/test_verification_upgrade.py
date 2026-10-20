import _bootstrap
import unittest
from scripts.upgrade_verification import upgrade_row

class UpgradeTest(unittest.TestCase):
    def test_old_matched_small_gap_is_not_grandfathered(self):
        old = dict(status='ok', quality='matched', ratio=20, minuteDayVolume=999, dailyVolume=1000, dayVolumeDifference=-1)
        new = upgrade_row(old)
        self.assertEqual(new['status'], 'unverified')
        self.assertIsNone(new['ratio'])
        self.assertEqual(new['referenceRatio'], 20)
        self.assertEqual(old['ratio'], 20)
        self.assertNotIn('verifiedAt', new)
        self.assertNotIn('verificationVersion', new)
    def test_missing_saved_evidence_cannot_be_certified(self):
        self.assertEqual(upgrade_row(dict(status='ok', ratio=20))['status'], 'unverified')
    def test_fallback_provenance_and_reference_remain_intact(self):
        old = dict(status='ok', quality='matched', ratio=20, minuteDayVolume=1000, dailyVolume=1000,
                   sourceProvider='eastmoney', verificationState='fallback_matched', reconciliationAttempts=[{'stage':'fallback'}])
        new = upgrade_row(old)
        self.assertEqual(new['status'], 'ok')
        self.assertEqual(new['sourceProvider'], 'eastmoney')
        self.assertEqual(new['reconciliationAttempts'], old['reconciliationAttempts'])
    def test_repeated_migration_preserves_reference(self):
        old = dict(status='ok', quality='source_difference', ratio=17.08, minuteDayVolume=990, dailyVolume=1000)
        first = upgrade_row(old)
        self.assertEqual(upgrade_row(first), first)
