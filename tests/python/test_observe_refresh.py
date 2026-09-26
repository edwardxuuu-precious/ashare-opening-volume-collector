import _bootstrap
import io
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from deploy.actions.observe_refresh import audit, missing_payload_report, observe
from test_refresh_worker import row, DAY, CODES

class ObserveTests(unittest.TestCase):
    def test_green_run_does_not_imply_data_complete(self):
        item = dict(row(CODES[0]), status='missing', ratio=None)
        status = dict(targetDate=DAY,dataComplete=True,calculableCount=0,noTradeCount=0)
        payload = dict(date=DAY,rows=[item],universeTotal=1,generatedAt='a')
        manifest = dict(dates=[dict(date=DAY,valid=0,suspended=0,generatedAt='a')])
        result = audit(status,payload,manifest)
        self.assertFalse(result['dataComplete'])
        self.assertIn('false_completion',result['publicationErrors'])

    def test_full_readback_and_next_day_not_same_day_slo(self):
        status = dict(targetDate=DAY,dataComplete=True,outcome='completed',
                      calculableCount=1,noTradeCount=0,
                      calendarDates=[DAY],calendarValidThrough=DAY,
                      firstPassCompletedAt='2026-09-08T09:00:00+08:00')
        payload = dict(date=DAY,rows=[row(CODES[0])],universeTotal=1,generatedAt='a')
        manifest = dict(dates=[dict(date=DAY,valid=1,suspended=0,generatedAt='a')])
        result = audit(status,payload,manifest, datetime.fromisoformat(DAY+'T17:00:00+08:00'))
        self.assertTrue(result['dataComplete'])
        self.assertFalse(result['sameDayFirstPassBy1700'])

    def test_expired_calendar_cannot_make_a_stale_publication_healthy(self):
        status = dict(targetDate=DAY,dataComplete=True,outcome='completed',
                      calculableCount=1,noTradeCount=0,
                      calendarDates=[DAY],calendarValidThrough=DAY)
        payload = dict(date=DAY,rows=[row(CODES[0])],universeTotal=1,generatedAt='a')
        manifest = dict(dates=[dict(date=DAY,valid=1,suspended=0,generatedAt='a')])
        checked_at = datetime.fromisoformat('2026-09-08T17:00:00+08:00')
        result = audit(status,payload,manifest,checked_at)
        self.assertEqual(result['health'], 'needs_attention')
        self.assertIn('calendar_expired', result['publicationErrors'])
        self.assertIsNone(result['expectedClosedDate'])
        missing = missing_payload_report(status, DAY, checked_at)
        self.assertIn('calendar_expired', missing['publicationErrors'])

    def test_missing_or_malformed_calendar_validity_is_unavailable(self):
        payload = dict(date=DAY,rows=[row(CODES[0])],universeTotal=1,generatedAt='a')
        manifest = dict(dates=[dict(date=DAY,valid=1,suspended=0,generatedAt='a')])
        for through in (None, 'tomorrow', '2026-9-30'):
            with self.subTest(through=through):
                status = dict(targetDate=DAY,dataComplete=True,outcome='completed',
                              calculableCount=1,noTradeCount=0,
                              calendarDates=[DAY],calendarValidThrough=through)
                result = audit(status,payload,manifest,
                               datetime.fromisoformat(DAY+'T17:00:00+08:00'))
                self.assertEqual(result['health'], 'needs_attention')
                self.assertIn('calendar_unavailable', result['publicationErrors'])

    def test_valid_holiday_calendar_can_confirm_previous_trading_day(self):
        status = dict(targetDate=DAY,dataComplete=True,outcome='completed',
                      calculableCount=1,noTradeCount=0,
                      calendarDates=[DAY, '2026-09-09'],calendarValidThrough='2026-09-09')
        payload = dict(date=DAY,rows=[row(CODES[0])],universeTotal=1,generatedAt='a')
        manifest = dict(dates=[dict(date=DAY,valid=1,suspended=0,generatedAt='a')])
        result = audit(status,payload,manifest,
                       datetime.fromisoformat('2026-09-08T17:00:00+08:00'))
        self.assertEqual(result['health'], 'healthy')
        self.assertEqual(result['expectedClosedDate'], DAY)

    def test_history_run_does_not_replace_latest_publication_audit(self):
        latest = '2026-09-08'
        status = dict(targetDate=DAY,dataComplete=False,outcome='incomplete_checkpointed',
                      phase='catchup',exitReason='source_retry_deferred',
                      calculableCount=0,noTradeCount=0,
                      calendarDates=[DAY, latest],calendarValidThrough=latest,
                      targets={latest: dict(dataComplete=True,calculableCount=1,noTradeCount=0,
                                            outcome='completed',phase='close')})
        objects = {
            'collector/refresh-status.json': status,
            'data/collection-status.json': status,
            'data/manifest.json': dict(dates=[dict(date=day,valid=1,suspended=0,generatedAt='a')
                                            for day in (DAY, latest)]),
            **{'data/'+day+'.json': dict(date=day,rows=[row(CODES[0])],universeTotal=1,generatedAt='a')
               for day in (DAY, latest)},
        }
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat(latest+'T17:00:00+08:00')
        s3 = SimpleNamespace(get_object=lambda **kw: {
            'Body': io.BytesIO(json.dumps(objects[kw['Key']]).encode())})
        clients = {
            'sts': SimpleNamespace(get_caller_identity=lambda: {'Account': '838775535952'}),
            's3': s3,
            'cloudformation': SimpleNamespace(describe_stacks=lambda **kw: {
                'Stacks': [{'StackStatus': 'UPDATE_COMPLETE'}]}),
        }
        boto3 = SimpleNamespace(Session=lambda **kw: SimpleNamespace(client=clients.__getitem__))
        requests = SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {'workflow_runs': []}))
        with patch.dict('sys.modules', boto3=boto3, requests=requests), \
                patch('deploy.actions.observe_refresh.datetime', Clock):
            result = observe()
            explicit = observe(target=DAY)
        self.assertEqual(result['date'], latest)
        self.assertEqual(result['health'], 'healthy')
        self.assertEqual(result['phase'], 'close')
        self.assertEqual(result['outcome'], 'completed')
        self.assertIsNone(result['exitReason'])
        self.assertEqual(explicit['date'], DAY)
        self.assertIn('target_not_latest_closed_day', explicit['publicationErrors'])

    def test_latest_target_summary_counts_are_still_verified_during_history_run(self):
        status = dict(targetDate='2026-09-04', phase='catchup',exitReason='source_retry_deferred',
                      calendarDates=[DAY],calendarValidThrough=DAY,
                      targets={DAY: dict(dataComplete=True,calculableCount=0,noTradeCount=0)})
        payload = dict(date=DAY,rows=[row(CODES[0])],universeTotal=1,generatedAt='a')
        manifest = dict(dates=[dict(date=DAY,valid=1,suspended=0,generatedAt='a')])
        checked_at = datetime.fromisoformat(DAY+'T17:00:00+08:00')
        result = audit(status,payload,manifest,checked_at)
        self.assertIn('status_coverage', result['publicationErrors'])
        self.assertIsNone(result['phase'])
        self.assertIsNone(result['exitReason'])
        missing = missing_payload_report(status, DAY, checked_at)
        self.assertIsNone(missing['phase'])
        self.assertIsNone(missing['exitReason'])

    def test_20260918_unexplained_no_trade_fails_the_publication_gate(self):
        day = '2026-09-18'
        evidence = dict(kind='explicit_zero_daily_volume', provider='sina',
                        date=day, symbol='sz000001', volume=0)
        item = dict(row(CODES[0]), status='suspended', ratio=None,
                    first15Volume=None, dailyVolume=0, noTradeEvidence=evidence,
                    pctChange=None, amplitude=None)
        status = dict(targetDate=day,dataComplete=True,outcome='completed',
                      calculableCount=0,noTradeCount=1,
                      specialStatusExplained=0,specialStatusUnexplained=1,
                      statusExplanationComplete=False,calendarDates=[day])
        payload = dict(date=day,rows=[item],universeTotal=1,generatedAt='a',
                       specialStatusExplained=0,specialStatusUnexplained=1,
                       statusExplanationComplete=False)
        manifest = dict(dates=[dict(date=day,valid=0,suspended=1,generatedAt='a',
            specialStatusExplained=0,specialStatusUnexplained=1,
            statusExplanationComplete=False)])
        result = audit(status,payload,manifest)
        self.assertFalse(result['dataComplete'])
        self.assertIn('special_status_unexplained',result['publicationErrors'])
