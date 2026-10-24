"""Reviewed prelisting rows complete without a quote request or invented zeros."""
import _bootstrap
import copy
from contextlib import ExitStack
from datetime import datetime
from unittest import TestCase
from unittest.mock import patch

from scripts import refresh
from scripts.exchange_status import valid_historical_no_trade
from scripts.no_trade_evidence import evidence, reviewed_listing_evidence, valid_notice
from scripts.reconciliation import special_status_explained, validate_price_row


class SourceForbidden(AssertionError):
    pass


class NoSource:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        raise SourceForbidden('Source access is forbidden in this regression')


def missing_row(code):
    # Same persisted shape as the actual Oct20/21/22 unprocessed-price gaps.
    return dict(code=code, name=code, market='SZ' if code.startswith('3') else 'BJ',
                status='missing', calculationPolicy='download_only', ratio=None,
                sourceProvider='sina', verificationVersion=3, reason='尚未取得目标日期数据',
                pctChange=None, amplitude=None, open=None, high=None, low=None,
                close=None, priceStatus='missing')


class ReviewedPrelistingTests(TestCase):
    def fetch_without_source(self, code, day, previous=None, opening=None, snapshot=None):
        previous = missing_row(code) if previous is None else previous
        before = copy.deepcopy(previous)
        provider = NoSource()
        with ExitStack() as stack:
            stack.enter_context(patch.object(refresh, 'AK', provider))
            network = [stack.enter_context(patch(target, side_effect=SourceForbidden('Network forbidden')))
                       for target in ('socket.socket.connect', 'socket.socket.connect_ex',
                                      'socket.create_connection', 'socket.getaddrinfo',
                                      'urllib.request.urlopen', 'urllib.request.OpenerDirector.open',
                                      'requests.sessions.Session.request')]
            task = (dict(code=code, name=code), day, opening, previous, 'catchup')
            if snapshot is not None:
                task += (snapshot,)
            try:
                result = refresh.fetch(task)
            finally:
                self.assertEqual(previous, before)
                self.assertEqual(provider.calls, [])
                self.assertTrue(all(call.call_count == 0 for call in network))
        return result

    def test_actual_prelisting_gap_completes_without_injected_previous_proof(self):
        result = self.fetch_without_source('301718', '2026-10-20')
        row = result['row']
        self.assertEqual(row['status'], 'suspended')
        self.assertTrue(refresh.complete(row, '2026-10-20'))
        self.assertEqual(row['noTradeEvidence']['kind'], 'not_yet_listed')
        self.assertEqual(row['noTradeEvidence']['listingDate'], '2026-10-22')
        self.assertEqual(validate_price_row(row), 'not_traded')
        self.assertTrue(special_status_explained(row))
        self.assertEqual(row['specialStatus']['type'], 'not_yet_listed')
        for field in ('ratio', 'first15Volume', 'dailyVolume', 'open', 'high', 'low', 'close'):
            self.assertIsNone(row[field])
        self.assertEqual(result['errors'], [])
        self.assertIsNone(result['firstDataRequestAt'])

    def test_reviewed_prelisting_proof_is_ready_without_resetting_future_retry(self):
        attempt = dict(committed=True, failures=2, nextRetryAt='2026-10-24T09:00:00+08:00')
        before = copy.deepcopy(attempt)
        self.assertTrue(refresh.ready('301718', '2026-10-20', 'catchup', attempt,
                                      datetime.fromisoformat('2026-10-24T08:00:00+08:00')))
        self.assertEqual(attempt, before)

    def test_all_six_exact_reviewed_stock_days_complete_through_public_fetch(self):
        cases = [('301718', day) for day in ('2026-10-20', '2026-10-21')]
        cases += [('920285', day) for day in ('2026-10-20', '2026-10-21', '2026-10-22')]
        cases += [('920296', '2026-10-20')]
        for code, day in cases:
            with self.subTest(code=code, day=day):
                result = self.fetch_without_source(code, day)
                row = result['row']
                self.assertTrue(refresh.complete(row, day))
                self.assertEqual(validate_price_row(row), 'not_traded')
                self.assertTrue(special_status_explained(row))
                self.assertIn(row['noTradeEvidence']['listingDate'], row['specialStatus']['description'])
                self.assertIn(day, row['specialStatus']['description'])
                self.assertEqual(row['specialStatus']['type'], 'not_yet_listed')
                for field in ('ratio', 'first15Volume', 'dailyVolume', 'open', 'high', 'low', 'close'):
                    self.assertIsNone(row[field])
                self.assertIsNone(result['firstDataRequestAt'])

    def test_listing_facts_keep_original_sha_and_do_not_change_company_notice_contract(self):
        expected = {'301718': ('2026-10-22', 'szse',
                    '1e06c3103208c398a227302a677c27de53e44b3f04b9a12fd256e2fce89ff193'),
                    '920285': ('2026-10-23', 'bse',
                    'eedd520158dd57b85931924a046e75f28d7813ccd08a6ee02f2256450013fa06'),
                    '920296': ('2026-10-21', 'bse',
                    'eedd520158dd57b85931924a046e75f28d7813ccd08a6ee02f2256450013fa06')}
        for code, (listed, provider, sha) in expected.items():
            with self.subTest(code=code):
                proof = reviewed_listing_evidence(code, '2026-10-20')
                self.assertEqual((proof['listingDate'], proof['provider'], proof['sourceSha256']),
                                 (listed, provider, sha))
                self.assertTrue(valid_historical_no_trade(proof, code, '2026-10-20'))
                self.assertFalse(valid_historical_no_trade(proof, code, '2026-10-21'))
                self.assertIsNone(evidence(code, '2026-10-20'))
                self.assertFalse(valid_notice(proof, code))

    def test_listing_day_other_days_and_codes_never_expand_the_registry_or_retry_gate(self):
        attempt = dict(committed=True, nextRetryAt='2026-10-24T09:00:00+08:00')
        now = datetime.fromisoformat('2026-10-24T08:00:00+08:00')
        for code, day in (('301718', '2026-10-22'), ('920285', '2026-10-23'),
                          ('920296', '2026-10-21'), ('301718', '2026-10-19'),
                          ('920285', '2026-10-19'), ('920296', '2026-10-22'),
                          ('920286', '2026-10-20'), ('0920285', '2026-10-20'),
                          ('920285', '20261020')):
            with self.subTest(code=code, day=day):
                self.assertIsNone(reviewed_listing_evidence(code, day))
                self.assertFalse(refresh.ready(code, day, 'catchup', attempt, now))
        self.assertFalse(refresh.ready('301718', '2026-10-20', 'opening', attempt, now))

    def test_committed_source_hold_stays_closed_and_unchanged(self):
        code, day = '301718', '2026-10-20'
        now = datetime.fromisoformat('2026-10-24T08:00:00+08:00')
        observation = dict(version=1, provider='sina', kind='target_opening_absent',
                           code=code, date=day, dailyVolume=100, targetTime='09:45:00')
        attempt = {}
        for _ in range(3):
            attempt = refresh.commit_attempt(attempt, now, False, observation)
        before = copy.deepcopy(attempt)
        self.assertTrue(refresh.source_held(attempt, code, day))
        self.assertFalse(refresh.ready(code, day, 'catchup', attempt, now))
        self.assertEqual(attempt, before)

    def test_positive_core_ratio_and_wrong_previous_status_fail_before_any_source_or_change(self):
        for changes, opening in ((dict(first15Volume=1), None), (dict(dailyVolume=1), None),
                                 (dict(ratio=1), None), (dict(status='ok'), None),
                                 (dict(status='suspended'), None), ({}, 1)):
            previous = dict(missing_row('301718'), **changes)
            with self.subTest(changes=changes, opening=opening), self.assertRaises(ValueError):
                self.fetch_without_source('301718', '2026-10-20', previous, opening)

    def test_any_saved_or_snapshot_price_observation_is_not_overwritten(self):
        for field in ('open', 'high', 'low', 'close'):
            previous = dict(missing_row('301718'), **{field: 1})
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.fetch_without_source('301718', '2026-10-20', previous)
        available = dict(missing_row('301718'), open=1, high=2, low=1, close=2,
                         priceStatus='available', priceSourceProvider='sina')
        with self.assertRaises(ValueError):
            self.fetch_without_source('301718', '2026-10-20', available)
        partial = missing_row('301718');partial.pop('high')
        with self.assertRaises(ValueError):
            self.fetch_without_source('301718', '2026-10-20', partial)
        for snapshot in (dict(dailyVolume=1), dict(close=1), available):
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                self.fetch_without_source('301718', '2026-10-20', snapshot=snapshot)
