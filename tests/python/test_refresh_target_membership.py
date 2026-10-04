"""Frozen membership corrections through the real target-day worker."""
import _bootstrap
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cloud_worker
import refresh_worker as worker
from scripts import collect, refresh
from scripts.universe_cache import validate_universe
from test_actions_runner import FakeS3
from test_refresh_worker import Clock, SyncContext, SyncPool, row


DAY = '2026-09-30'
OLD = '000001'
NEW = '001246'
UNSELECTED = '300123'


def priced(code, **overrides):
    value = row(code, open=10.1, high=10.5, low=10.0, close=10.3,
                pctChange=3.0, amplitude=5.0, priceStatus='available',
                priceSourceProvider='sina', dailyAdapter='fixture_daily',
                quoteTime='2026-09-30T15:00:00+08:00')
    value.update(overrides)
    return value


def repair_grant(**overrides):
    value = dict(date=DAY, code=NEW, listingDate=DAY,
                 sourceUrl='https://static.cninfo.com.cn/finalpage/2026-09-29/fixture.PDF',
                 sourceSha256='0' * 64)
    value.update(overrides)
    return value


class TargetMembershipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.out = self.root / 'data'
        self.out.mkdir()
        self.client = FakeS3()
        self.clock = Clock(DAY + 'T19:00:00+08:00')
        self.catalog = dict(asOf=DAY, total=2, historicalMembership=False,
                            rows=[dict(code=OLD, name='Renamed current member'),
                                  dict(code=NEW, name='New current member')])
        boundaries = ExitStack()
        self.addCleanup(boundaries.close)
        self.transport = boundaries.enter_context(patch(
            'requests.sessions.Session.request',
            side_effect=AssertionError('Unexpected external HTTP request')))
        self.socket = boundaries.enter_context(patch(
            'socket.create_connection',
            side_effect=AssertionError('Unexpected external socket connection')))
        self.addCleanup(self.transport.assert_not_called)
        self.addCleanup(self.socket.assert_not_called)

    def write(self, name, value):
        collect.write_json(self.out / name, value)

    def read(self, name):
        return json.loads((self.out / name).read_text())

    def seed(self):
        self.original = priced(OLD, name='Original frozen member',
                               originalReceipt='preserve-this-success')
        self.old_attempt = dict(committed=True, failures=0,
                                attemptedAt=DAY + 'T17:30:00+08:00',
                                observationReceipt='preserve-this-attempt')
        target = dict(date=DAY, universe=[dict(code=OLD, name=self.original['name'])],
                      openings={OLD: 10}, openingAttempts={OLD: dict(failures=2)},
                      attempts={OLD: self.old_attempt},
                      firstPassCompletedAt=DAY + 'T17:30:00+08:00',
                      fullyPublishedAt=DAY + 'T17:33:00+08:00',
                      summary=dict(dataComplete=True, priceDataComplete=True, pendingCount=0))
        self.write(DAY + '.json', dict(date=DAY, generatedAt=DAY + 'T17:33:00+08:00',
                                      scope='full', total=1, universeTotal=1,
                                      rows=[self.original]))
        self.write('refresh-state.json', dict(version=1, targets={DAY: target}))

    def run_worker(self, handler=None, repair_target=None, phase='close', explicit_day=DAY):
        def fetch(task):
            return dict(code=task[0]['code'], row=priced(task[0]['code']),
                        opening=10, speedDegraded=False, errors=[])

        self.pool = SyncPool(handler or fetch)
        context = SyncContext(self.pool)
        calendar = dict(calendarAsOf=self.clock.now().date().isoformat(),
                        calendarDates=[DAY], tradingDates=[DAY])

        def load_catalog(*_args, **_kwargs):
            self.write('universe.json', self.catalog)
            return self.catalog

        def load_calendar(*_args, **_kwargs):
            self.write('calendar.json', calendar)
            return calendar

        self.write('universe.json', self.catalog)
        args = SimpleNamespace(root=self.root, phase=phase,
                               target_date=explicit_day, max_minutes=10.1,
                               repair_target=repair_target)
        publisher = cloud_worker.Publisher(self.client, 'fixture', self.out)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, akshare=SimpleNamespace(
                tool_trade_date_hist_sina=lambda: None,
                stock_info_a_code_name=lambda: None)))
            stack.enter_context(patch.object(worker, 'now', side_effect=self.clock.now))
            stack.enter_context(patch.object(worker.time, 'monotonic',
                                            side_effect=lambda: self.clock.elapsed))
            stack.enter_context(patch.object(worker.time, 'sleep', side_effect=self.clock.sleep))
            stack.enter_context(patch.object(worker.signal, 'signal'))
            stack.enter_context(patch.object(worker.mp, 'get_context', return_value=context))
            stack.enter_context(patch.object(worker.SharedHTTPBudget, 'install'))
            stack.enter_context(patch.object(worker, 'load_calendar', side_effect=load_calendar))
            stack.enter_context(patch.object(worker, 'load_universe', side_effect=load_catalog))
            self.sse = stack.enter_context(patch.object(worker, 'load_sse_suspensions', return_value={}))
            self.szse = stack.enter_context(patch.object(worker, 'load_szse_suspensions', return_value={}))
            stack.enter_context(patch.object(worker, 'validate_universe', side_effect=lambda value, **kwargs:
                validate_universe(value, minimum_count=1)))
            self.snapshot = stack.enter_context(patch.object(worker.sina_spot, 'load_snapshot',
                return_value=dict(rows={NEW: priced(NEW)}, availableCount=1,
                                  missingCodes=[], source='fixture_sina')))
            stack.enter_context(patch.object(cloud_worker, 'now',
                                            side_effect=lambda: self.clock.now().isoformat()))
            return worker.run(args, publisher)

    def test_same_day_catalog_addition_is_dispatched_without_changing_old_success(self):
        self.seed()
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual([task[0]['code'] for task in self.pool.tasks], [NEW])
        target = self.read('refresh-state.json')['targets'][DAY]
        self.assertEqual(target['universe'], [dict(code=OLD, name=self.original['name']),
                                             self.catalog['rows'][1]])
        self.assertEqual(target['attempts'][OLD], self.old_attempt)
        self.assertEqual(target['openingAttempts'][OLD], dict(failures=2))
        saved = {value['code']: value for value in self.read(DAY + '.json')['rows']}
        self.assertEqual(saved[OLD], self.original)
        self.assertEqual(saved[NEW], priced(NEW))
        self.assertEqual(target['summary']['totalStocks'], 2)
        self.assertTrue(target['summary']['dataComplete'])
        self.assertTrue(target['summary']['priceDataComplete'])

    def test_explicit_historical_grant_adds_only_the_reviewed_code_day(self):
        self.seed()
        self.clock = Clock('2026-10-04T07:00:00+08:00')
        self.catalog['rows'].append(dict(code=UNSELECTED, name='Unselected current member'))
        self.catalog['total'] += 1
        grant = repair_grant()
        self.assertEqual(self.run_worker(repair_target=grant), 0)
        self.assertEqual([task[0]['code'] for task in self.pool.tasks], [NEW])
        self.assertEqual({task[1] for task in self.pool.tasks}, {DAY})
        self.snapshot.assert_not_called()
        target = self.read('refresh-state.json')['targets'][DAY]
        self.assertEqual([item['code'] for item in target['universe']], [OLD, NEW])
        saved = {value['code']: value for value in self.read(DAY + '.json')['rows']}
        self.assertEqual(saved[OLD], self.original)
        self.assertEqual(set(saved), {OLD, NEW})
        self.assertEqual(target['attempts'][OLD], self.old_attempt)
        correction = target['membershipCorrections'][-1]
        for field, value in grant.items():
            self.assertEqual(correction[field], value)
        self.assertEqual(correction['mode'], 'explicit_membership_repair')
        self.assertTrue(target['summary']['dataComplete'])

    def test_historical_target_never_merges_current_catalog_without_a_grant(self):
        self.seed()
        self.clock = Clock('2026-10-04T07:00:00+08:00')
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual(self.pool.tasks, [])
        target = self.read('refresh-state.json')['targets'][DAY]
        self.assertEqual([item['code'] for item in target['universe']], [OLD])
        self.assertNotIn('membershipCorrections', target)
        self.assertEqual(target['fullyPublishedAt'], DAY + 'T17:33:00+08:00')
        self.assertEqual(self.read(DAY + '.json')['rows'], [self.original])
        self.snapshot.assert_not_called()

    def test_automatic_catchup_reopens_completed_current_day_for_catalog_addition(self):
        self.seed()
        self.assertEqual(self.run_worker(phase='catchup', explicit_day=None), 0)
        self.assertEqual([task[0]['code'] for task in self.pool.tasks], [NEW])
        target = self.read('refresh-state.json')['targets'][DAY]
        self.assertEqual([item['code'] for item in target['universe']], [OLD, NEW])
        self.assertTrue(target['summary']['dataComplete'])
        self.assertEqual(target['attempts'][OLD], self.old_attempt)
        self.assertEqual(self.read(DAY + '.json')['rows'][0], self.original)

    def test_same_day_stale_catalog_does_not_expand_the_frozen_scope(self):
        self.seed()
        self.catalog['asOf'] = '2026-09-29'
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual(self.pool.tasks, [])
        self.assertEqual([item['code'] for item in self.read('refresh-state.json')['targets'][DAY]['universe']], [OLD])

    def test_catalog_removal_does_not_remove_an_original_frozen_member(self):
        self.seed()
        self.catalog['rows'] = self.catalog['rows'][1:]
        self.catalog['total'] = 1
        self.assertEqual(self.run_worker(), 0)
        target = self.read('refresh-state.json')['targets'][DAY]
        self.assertEqual([item['code'] for item in target['universe']], [OLD, NEW])
        self.assertEqual(self.read(DAY + '.json')['rows'][0], self.original)

    def test_completed_same_day_addition_is_idempotent(self):
        self.seed()
        self.assertEqual(self.run_worker(), 0)
        complete = self.read('refresh-state.json')['targets'][DAY]
        saved = self.read(DAY + '.json')['rows']
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual(self.pool.tasks, [])
        self.snapshot.assert_not_called()
        repeated = self.read('refresh-state.json')['targets'][DAY]
        for field in ('universe', 'attempts', 'openingAttempts', 'openings',
                      'membershipCorrections', 'firstPassCompletedAt', 'fullyPublishedAt'):
            self.assertEqual(repeated[field], complete[field])
        self.assertEqual(self.read(DAY + '.json')['rows'], saved)

    def test_failed_new_member_invalidates_old_completion_without_guessing_no_trade(self):
        self.seed()
        missing = priced(NEW, status='missing', first15Volume=None,
                         dailyVolume=None, ratio=None, reason='fixture source unavailable')
        self.assertEqual(self.run_worker(handler=lambda task: dict(
            code=task[0]['code'], row=missing, opening=None,
            speedDegraded=False, errors=[])), 0)
        self.assertEqual([task[0]['code'] for task in self.pool.tasks], [NEW])
        target = self.read('refresh-state.json')['targets'][DAY]
        self.assertFalse(target['summary']['dataComplete'])
        self.assertEqual(target['summary']['pendingCount'], 1)
        self.assertEqual(target['summary']['noTradeCount'], 0)
        self.assertNotIn('fullyPublishedAt', target)
        correction = target['membershipCorrections'][-1]
        self.assertEqual(correction['fullyPublishedAt'], DAY + 'T17:33:00+08:00')
        saved = {value['code']: value for value in self.read(DAY + '.json')['rows']}
        self.assertEqual(saved[OLD], self.original)
        self.assertEqual(saved[NEW]['status'], 'missing')
        self.assertNotIn('noTradeEvidence', saved[NEW])

    def test_explicit_grant_preserves_other_pending_attempts_and_source_holds(self):
        if not hasattr(refresh, 'source_held'):
            self.skipTest('This case requires the deployed finite-source-stop baseline')
        self.seed()
        self.clock = Clock('2026-10-04T07:00:00+08:00')
        held_code, pending_code = '600010', '000002'
        held = priced(held_code, status='missing', first15Volume=None, ratio=None,
                      reason='target opening absent')
        pending = priced(pending_code, status='missing', first15Volume=None, ratio=None,
                         reason='target opening pending')
        observation = dict(version=1, code=held_code, date=DAY, provider='sina',
                           kind='target_opening_absent', dailyVolume=100,
                           targetTime='09:45:00')
        held_attempt = {}
        for _ in range(refresh.SOURCE_OBSERVATION_LIMIT):
            held_attempt = refresh.commit_attempt(held_attempt, self.clock.now(), False, observation)
        self.assertTrue(refresh.source_held(held_attempt, held_code, DAY))
        pending_attempt = dict(committed=True, failures=1,
                               nextRetryAt='2026-10-04T07:00:00+08:00',
                               observationReceipt='unselected pending attempt')
        payload = self.read(DAY + '.json')
        payload.update(rows=[self.original, held, pending], total=3, universeTotal=3)
        self.write(DAY + '.json', payload)
        cache = self.read('refresh-state.json')
        target = cache['targets'][DAY]
        target['universe'].extend(dict(code=value['code'], name=value['name']) for value in (held, pending))
        target['attempts'].update({held_code: held_attempt, pending_code: pending_attempt})
        self.write('refresh-state.json', cache)
        self.assertEqual(self.run_worker(repair_target=repair_grant()), 0)
        self.assertEqual([task[0]['code'] for task in self.pool.tasks], [NEW])
        self.sse.assert_not_called()
        self.szse.assert_not_called()
        corrected = self.read('refresh-state.json')['targets'][DAY]
        self.assertEqual(corrected['attempts'][held_code], held_attempt)
        self.assertEqual(corrected['attempts'][pending_code], pending_attempt)
        self.assertEqual(corrected['summary']['sourceBlockedCodes'], [held_code])
        self.assertEqual(corrected['summary']['pendingCount'], 2)
        self.assertEqual(corrected['summary']['retryableCount'], 1)
        self.assertEqual(corrected['summary']['automaticActionableCount'], 1)
        saved = {value['code']: value for value in self.read(DAY + '.json')['rows']}
        self.assertEqual(saved[OLD], self.original)
        self.assertEqual(saved[held_code], held)
        self.assertEqual(saved[pending_code], pending)

    def test_explicit_grant_reconstructs_trimmed_membership_from_saved_scope_only(self):
        self.seed()
        self.clock = Clock('2026-10-04T07:00:00+08:00')
        cache = self.read('refresh-state.json')
        cache['targets'][DAY].pop('universe')
        self.write('refresh-state.json', cache)
        self.catalog['rows'].append(dict(code=UNSELECTED, name='Unselected member'))
        self.catalog['total'] += 1
        self.assertEqual(self.run_worker(repair_target=repair_grant()), 0)
        corrected = self.read('refresh-state.json')['targets'][DAY]
        self.assertEqual([item['code'] for item in corrected['universe']], [OLD, NEW])
        self.assertEqual(self.read(DAY + '.json')['rows'][0], self.original)

    def test_invalid_grants_fail_before_snapshot_state_or_publication_changes(self):
        cases = [
            ('listing day mismatch', repair_grant(listingDate='2026-10-01'), DAY, 'close', DAY),
            ('target mismatch', repair_grant(date='2026-09-29'), DAY, 'close', DAY),
            ('missing explicit date', repair_grant(), None, 'close', DAY),
            ('opening phase', repair_grant(), DAY, 'opening', DAY),
            ('unknown code', repair_grant(code='001999'), DAY, 'close', DAY),
            ('bad code', repair_grant(code='1246'), DAY, 'close', DAY),
            ('untrusted source', repair_grant(sourceUrl='https://example.com/source.pdf'), DAY, 'close', DAY),
            ('spoofed source', repair_grant(sourceUrl='https://static.cninfo.com.cn.evil.example/source.pdf'), DAY, 'close', DAY),
            ('no source fingerprint', repair_grant(sourceSha256='abc'), DAY, 'close', DAY),
            ('extra key', repair_grant(allStocks=True), DAY, 'close', DAY),
            ('stale catalog', repair_grant(), DAY, 'close', '2026-09-29'),
            ('future catalog', repair_grant(), DAY, 'close', '2026-10-05'),
        ]
        for label, grant, explicit_day, phase, as_of in cases:
            with self.subTest(label=label):
                self.seed()
                self.clock = Clock('2026-10-04T07:00:00+08:00')
                self.catalog['asOf'] = as_of
                state = (self.out / 'refresh-state.json').read_bytes()
                snapshot = (self.out / (DAY + '.json')).read_bytes()
                with self.assertRaises(ValueError):
                    self.run_worker(repair_target=grant, phase=phase, explicit_day=explicit_day)
                self.assertEqual((self.out / 'refresh-state.json').read_bytes(), state)
                self.assertEqual((self.out / (DAY + '.json')).read_bytes(), snapshot)
                self.assertEqual(self.pool.tasks, [])
                self.assertEqual(self.client.objects, {})

    def test_explicit_grant_cannot_create_a_new_historical_full_market_scope(self):
        self.clock = Clock('2026-10-04T07:00:00+08:00')
        self.write('refresh-state.json', dict(version=1, targets={}))
        state = (self.out / 'refresh-state.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'frozen or fully saved'):
            self.run_worker(repair_target=repair_grant())
        self.assertEqual((self.out / 'refresh-state.json').read_bytes(), state)
        self.assertEqual(self.pool.tasks, [])
        self.assertEqual(self.client.objects, {})


if __name__ == '__main__':
    unittest.main()
