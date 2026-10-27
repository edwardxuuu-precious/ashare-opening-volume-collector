"""Pure time/calendar tests; no AWS, quotes, dispatches, or market inputs."""
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from backend.chips import runner
from tooling import chip_followup_guard as guard


def at(value):
    return datetime.fromisoformat(value)


class TargetDay(unittest.TestCase):
    def choose(self, value, explicit=None):
        moment=at(value)
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):
                return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)
        with patch.object(runner,'datetime',Clock):
            return runner.target_day(explicit)

    def test_current_session_becomes_eligible_at_1540(self):
        self.assertEqual(self.choose('2026-10-26T15:39:59+08:00'),'2026-10-23')
        self.assertEqual(self.choose('2026-10-26T15:40:00+08:00'),'2026-10-26')

    def test_explicit_current_session_respects_same_boundary(self):
        with self.assertRaisesRegex(ValueError,'SESSION_NOT_COMPLETED'):
            self.choose('2026-10-26T15:39:59+08:00','2026-10-26')
        self.assertEqual(self.choose('2026-10-26T15:40:00+08:00','2026-10-26'),'2026-10-26')

    def test_utc_boundary_matches_guard(self):
        for value,expected,action in (
            ('2026-10-26T07:39:59+00:00','2026-10-23','WAIT'),
            ('2026-10-26T07:40:00+00:00','2026-10-26','READY')):
            with self.subTest(value=value):
                self.assertEqual(self.choose(value),expected)
                self.assertEqual(guard.time_gate(at(value))['action'],action)

    def test_weekend_and_exchange_holiday_choose_last_session(self):
        self.assertEqual(self.choose('2026-10-25T15:40:00+08:00'),'2026-10-23')
        self.assertEqual(self.choose('2026-10-05T15:40:00+08:00'),'2026-09-30')
        for value in ('2026-10-25T15:40:00+08:00','2026-10-05T15:40:00+08:00'):
            self.assertEqual(guard.time_gate(at(value))['action'],'SKIP')

    def test_older_explicit_date_allowed_and_future_rejected(self):
        self.assertEqual(self.choose('2026-10-26T09:00:00+08:00','2026-10-23'),'2026-10-23')
        with self.assertRaisesRegex(ValueError,'SESSION_NOT_COMPLETED'):
            self.choose('2026-10-26T15:40:00+08:00','2026-10-27')

    def test_explicit_non_session_and_unsupported_year_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'TEN_COMPLETED_SESSIONS_REQUIRED'):
            self.choose('2026-10-26T15:40:00+08:00','2026-10-25')
        with self.assertRaisesRegex(ValueError,'OFFICIAL_CALENDAR_YEAR_UNAVAILABLE'):
            self.choose('2027-01-04T15:40:00+08:00')

    def test_workflow_primary_and_original_catchup_times(self):
        workflow=(Path(__file__).resolve().parents[2]/'.github/workflows/chips.yml').read_text(encoding='utf-8')
        self.assertIn("- cron: '40 7 * * *'",workflow)
        self.assertIn("- cron: '20 13,18 * * *'",workflow)
        self.assertNotIn("- cron: '20 8,13,18 * * *'",workflow)


if __name__=='__main__':
    unittest.main()
