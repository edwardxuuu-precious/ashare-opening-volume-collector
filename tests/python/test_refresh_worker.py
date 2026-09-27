"""Target-day worker integration with synchronous futures and private in-memory S3."""
import _bootstrap
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import cloud_worker
import refresh_worker as worker
from scripts import collect
from scripts.exchange_status import parse_sse_suspensions, parse_szse_suspensions
from scripts.universe_cache import validate_universe
from test_actions_runner import FakeS3


DAY = '2026-09-07'
OLDER = '2026-01-02'
TODAY = '2026-09-08'
CODES = ('000001', '600519', '920002')


def spot_frame():
    return pd.DataFrame([
        {'代码': 'sz000001', '名称': '平安银行', '最新价': 11.78, '昨收': 11.70, '今开': 11.72,
         '最高': 11.81, '最低': 11.65, '成交量': 74051597.0, '时间戳': '15:36:00'},
        {'代码': 'sh600519', '名称': '贵州茅台', '最新价': 1309.30, '昨收': 1316.01, '今开': 1318.00,
         '最高': 1323.00, '最低': 1309.05, '成交量': 1753404.0, '时间戳': '15:34:59'},
        {'代码': 'bj920002', '名称': '万达轴承', '最新价': 53.00, '昨收': 53.84, '今开': 53.70,
         '最高': 54.38, '最低': 52.74, '成交量': 729266.0, '时间戳': '15:30:02'},
    ])


def row(code, **overrides):
    value = dict(code=code, name='Fixture ' + code, market=collect.market(code),
                 verificationVersion=collect.VERIFICATION_VERSION,
                 calculationPolicy='download_only', sourceProvider='sina',
                 status='ok', first15Volume=10, dailyVolume=100, ratio=10,
                 quality='downloaded', verificationState='downloaded')
    value.update(overrides)
    return value


def result(code, **overrides):
    value = dict(code=code, row=row(code), opening=10, speedDegraded=False, errors=[])
    value.update(overrides)
    return value


class Clock:
    def __init__(self, stamp):
        self.start = datetime.fromisoformat(stamp)
        self.elapsed = 0.0

    def now(self):
        return self.start + timedelta(seconds=self.elapsed)

    def sleep(self, seconds):
        self.elapsed += max(seconds, .01)


class Future:
    def __init__(self, value=None, ready=True):
        self.value, self.is_ready = value, ready

    def ready(self):
        return self.is_ready

    def get(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class SyncPool:
    def __init__(self, handler):
        self.handler = handler
        self.tasks = []
        self.closed = self.terminated = self.joined = False

    def apply_async(self, function, args):
        task = args[0]
        self.tasks.append(task)
        value = self.handler(task)
        return value if isinstance(value, Future) else Future(value)

    def close(self):
        self.closed = True

    def terminate(self):
        self.terminated = True

    def join(self):
        self.joined = True


class SyncContext:
    def __init__(self, pool):
        self.pool = pool
        self.processes = None

    def Lock(self):
        return nullcontext()

    def Value(self, _type, initial):
        return SimpleNamespace(value=initial)

    def Pool(self, processes, **kwargs):
        self.processes = processes
        return self.pool


class RefreshWorkerTests(unittest.TestCase):
    def setUp(self):
        self.reset_fixture()

    def reset_fixture(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / 'data'
        self.out.mkdir()
        self.client = FakeS3()
        self.clock = Clock('2026-09-08T07:00:00+08:00')
        self.codes = CODES[:1]

    def test_summary_separates_core_completion_from_ohlc_completion(self):
        item = {'code': CODES[0]}
        core = row(CODES[0])
        target = dict(date='2026-09-21', attempts={})

        missing = worker.metrics([item], {CODES[0]: core}, target, 'close')
        self.assertTrue(missing['dataComplete'])
        self.assertFalse(missing['priceDataComplete'])
        self.assertEqual(missing['priceMissing'], 1)

        priced = dict(core, open=9.8, high=10.3, low=9.7, close=10.0,
                      priceStatus='available', priceSourceProvider='sina')
        complete = worker.metrics([item], {CODES[0]: priced}, target, 'close')
        self.assertTrue(complete['dataComplete'])
        self.assertTrue(complete['priceDataComplete'])
        self.assertEqual(complete['priceAvailable'], 1)
        self.assertFalse(worker.target_complete(missing, 'close', '2026-09-21'))
        self.assertTrue(worker.target_complete(complete, 'close', '2026-09-21'))
        self.assertTrue(worker.target_complete(missing, 'close', '2026-09-18'))

    def test_20260918_no_trade_requires_a_specific_explanation(self):
        item = {'code': CODES[0]}
        evidence = dict(kind='explicit_zero_daily_volume', provider='sina',
                        date='2026-09-18', symbol='sz000001', volume=0)
        stopped = row(CODES[0], status='suspended', first15Volume=None,
                      dailyVolume=0, ratio=None, noTradeEvidence=evidence,
                      open=None, high=None, low=None, close=None,
                      priceStatus='not_traded', pctChange=None, amplitude=None)
        target = dict(date='2026-09-18', attempts={})

        unexplained = worker.metrics([item], {CODES[0]: stopped}, target, 'catchup')
        self.assertFalse(unexplained['dataComplete'])
        self.assertEqual(unexplained['specialStatusUnexplained'], 1)

        stopped['specialStatus'] = dict(type='suspension', label='交易所公告停牌',
            description='交易所公告确认当日停牌', startedAt='2026-09-18', source='交易所')
        explained = worker.metrics([item], {CODES[0]: stopped}, target, 'catchup')
        self.assertTrue(explained['dataComplete'])
        self.assertTrue(explained['statusExplanationComplete'])

    def write(self, name, value):
        collect.write_json(self.out / name, value)

    def read(self, name):
        return json.loads((self.out / name).read_text())

    def visible(self):
        return json.loads(self.client.objects['data/collection-status.json'])

    def run_worker(self, handler=None, phase='catchup', target=DAY, publisher=None,
                   calendar_dates=None, spot_fetch=None, max_minutes=10.1,
                   exchange_status=None, szse_status=None):
        pool = SyncPool(handler or (lambda task: result(task[0]['code'])))
        context = SyncContext(pool)
        catalog = dict(asOf=TODAY, total=len(self.codes), historicalMembership=False,
                       rows=[dict(code=code, name='Fixture ' + code) for code in self.codes])
        calendar_dates = calendar_dates or [OLDER, DAY, TODAY]
        calendar = dict(calendarAsOf=TODAY, calendarDates=calendar_dates,
                        tradingDates=[day for day in calendar_dates if day < TODAY])

        def load_catalog(*_args, **_kwargs):
            self.write('universe.json', catalog)
            return catalog

        def load_calendar(*_args, **_kwargs):
            self.write('calendar.json', calendar)
            return calendar

        self.pool = pool
        self.context = context
        self.publisher = publisher or cloud_worker.Publisher(self.client, 'fixture', self.out)
        args = SimpleNamespace(root=self.root, phase=phase, target_date=target,
                               max_minutes=max_minutes)
        with ExitStack() as stack:
            # Calendar/universe reads are fixture-owned; importing the real provider
            # would add unrelated native runtime initialization to this integration test.
            stack.enter_context(patch.dict(sys.modules, akshare=SimpleNamespace(
                tool_trade_date_hist_sina=lambda: None, stock_info_a_code_name=lambda: None,
                stock_zh_a_spot=spot_fetch or (lambda: (_ for _ in ()).throw(
                    AssertionError('Unexpected current-day snapshot request'))))))
            stack.enter_context(patch.object(worker, 'now', side_effect=self.clock.now))
            stack.enter_context(patch.object(worker.time, 'monotonic', side_effect=lambda: self.clock.elapsed))
            stack.enter_context(patch.object(worker.time, 'sleep', side_effect=self.clock.sleep))
            stack.enter_context(patch.object(worker.signal, 'signal'))
            stack.enter_context(patch.object(worker.mp, 'get_context', return_value=context))
            stack.enter_context(patch.object(worker.SharedHTTPBudget, 'install'))
            stack.enter_context(patch.object(worker, 'load_calendar', side_effect=load_calendar))
            stack.enter_context(patch.object(worker, 'load_universe', side_effect=load_catalog))
            stack.enter_context(patch.object(worker, 'load_sse_suspensions',
                                            return_value=exchange_status or {}))
            self.szse_lookup = stack.enter_context(patch.object(worker, 'load_szse_suspensions',
                                            return_value=szse_status or {}))
            stack.enter_context(patch.object(worker, 'validate_universe',
                                            side_effect=lambda value, **kwargs: validate_universe(value, minimum_count=1)))
            stack.enter_context(patch.object(cloud_worker, 'now', side_effect=lambda: self.clock.now().isoformat()))
            return worker.run(args, self.publisher)

    def test_exact_day_exchange_status_completes_gap_and_publishes_explanation(self):
        target = '2026-09-18'
        self.clock = Clock('2026-09-20T07:00:00+08:00')
        self.codes = ('600111',)
        exchange_status = parse_sse_suspensions([dict(
            productCode='600111', productName='北方稀土', controlType='TR',
            startStopDate='20260915', endStopDate='', type='LXTP',
            stopReason='拟筹划重大资产重组')], target)

        self.assertEqual(self.run_worker(
            handler=lambda task: worker.refresh.fetch(task), target=target,
            calendar_dates=[target], exchange_status=exchange_status), 0)

        self.assertEqual(len(self.pool.tasks), 1)
        self.assertEqual(len(self.pool.tasks[0]), 7)
        published = json.loads(self.client.objects['data/' + target + '.json'])['rows'][0]
        self.assertEqual(published['status'], 'suspended')
        self.assertEqual(published['specialStatus']['label'], '重大资产重组停牌')
        self.assertTrue(self.visible()['dataComplete'])

    def test_szse_reason_repairs_zero_volume_without_requerying_market_data(self):
        target = '2026-09-24'
        self.clock = Clock('2026-09-27T07:00:00+08:00')
        self.codes = ('300096',)
        saved = row('300096', status='suspended', first15Volume=None,
            dailyVolume=0, ratio=None, pctChange=None, amplitude=None,
            open=None, high=None, low=None, close=None, priceStatus='not_traded',
            noTradeEvidence=dict(kind='explicit_zero_daily_volume', provider='sina',
                                 date=target, symbol='sz300096', volume=0))
        self.write(target + '.json', dict(date=target, rows=[saved], universeTotal=1))
        evidence = parse_szse_suspensions([dict(zqdm='300096', zqjc='Fixture',
            tpkssj='2026-09-24 开市', fpkssj='2026-09-28 开市', tpsj='1天',
            tpyy='撤销其他风险警示')], target, code='300096')
        self.assertEqual(self.run_worker(handler=lambda task: worker.refresh.fetch(task),
            target=target, calendar_dates=[target], szse_status=evidence), 0)
        published = json.loads(self.client.objects['data/' + target + '.json'])['rows'][0]
        self.assertEqual(published['specialStatus']['source'], '深圳证券交易所停复牌提示')
        self.assertEqual(self.visible()['specialStatusUnexplained'], 0)
        self.assertTrue(self.visible()['dataComplete'])
        self.assertTrue(self.visible()['priceDataComplete'])
        self.assertEqual(self.read('refresh-state.json')['targets'][target]['summary']
                         ['lastAttemptedAt'], '2026-09-27T07:00:00+08:00')

    def test_szse_official_evidence_repairs_attempted_missing_rows_without_market_requests(self):
        target = '2026-09-15'
        self.clock = Clock('2026-09-27T07:00:00+08:00')
        self.codes = ('002731', '301139', '301390')
        saved = [row(code, status='missing', first15Volume=None, dailyVolume=None, ratio=None)
                 for code in self.codes]
        self.write(target + '.json', dict(date=target, rows=saved, universeTotal=3))
        self.write('refresh-state.json', dict(version=1, targets={target: dict(attempts={
            '002731': dict(committed=True, nextRetryAt='2026-09-28T07:00:00+08:00'),
            '301139': dict(committed=False, failures=1),
            '301390': dict(committed=True, failures=3),
        })}))
        evidence = {}
        for code, start in (('002731', '2026-09-01'), ('301139', '2026-08-31'),
                            ('301390', '2026-09-10')):
            evidence.update(parse_szse_suspensions([dict(zqdm=code, zqjc='Fixture',
                tpkssj=start + ' 开市', fpkssj='', tpsj='停牌', tpyy='重大事项')], target, code=code))
        with patch.object(worker.collect, 'minute_unadjusted') as minute, \
                patch('scripts.sina_daily.daily_unadjusted') as daily:
            result_code = self.run_worker(handler=lambda task: worker.refresh.fetch(task),
                target=target, calendar_dates=[target], szse_status=evidence)
        self.szse_lookup.assert_called_once_with(target, list(self.codes))
        self.assertEqual(result_code, 0)
        minute.assert_not_called()
        daily.assert_not_called()
        published = json.loads(self.client.objects['data/' + target + '.json'])['rows']
        self.assertEqual(len(published), 3)
        for value in published:
            self.assertEqual(value['status'], 'suspended')
            self.assertEqual(value['noTradeEvidence']['date'], target)
            self.assertEqual(value['noTradeEvidence']['provider'], 'szse')
            self.assertEqual(value['specialStatus']['source'], '深圳证券交易所停复牌提示')
        self.assertTrue(self.visible()['dataComplete'])

    def test_szse_missing_candidates_require_attempt_and_no_known_volume_and_are_bounded(self):
        target = '2026-09-15'
        self.clock = Clock('2026-09-27T07:00:00+08:00')
        untouched = tuple(f'300{index:03}' for index in range(90))
        excluded = tuple(f'300{index:03}' for index in range(90, 97))
        eligible = tuple(f'300{index:03}' for index in range(100, 130))
        self.codes = untouched + excluded + eligible
        saved = {code: row(code, status='missing', first15Volume=None, dailyVolume=None, ratio=None)
                 for code in self.codes}
        for code, field, value in (('300090', 'first15Volume', 10),
                                   ('300091', 'dailyVolume', 100),
                                   ('300092', 'first15Volume', 0),
                                   ('300093', 'dailyVolume', 0)):
            saved[code][field] = value
        attempts = {code: dict(committed=True) for code in excluded + eligible}
        attempts['300095'] = dict(committed=False, failures=True)
        attempts['300096'] = dict(committed=False, failures=0, dispatchedAt=self.clock.now().isoformat())
        self.write(target + '.json', dict(date=target, rows=list(saved.values()),
                                         universeTotal=len(self.codes)))
        self.write('refresh-state.json', dict(version=1, targets={target: dict(
            attempts=attempts, openings={'300094': 10})}))

        self.run_worker(target=target, calendar_dates=[target])

        self.szse_lookup.assert_called_once_with(target, list(eligible[:20]))

    def test_szse_no_match_keeps_attempted_empty_sina_response_missing(self):
        target = '2026-09-15'
        self.clock = Clock('2026-09-27T07:00:00+08:00')
        self.codes = ('301390',)
        saved = row('301390', status='missing', first15Volume=None, dailyVolume=None, ratio=None)
        self.write(target + '.json', dict(date=target, rows=[saved], universeTotal=1))
        self.write('refresh-state.json', dict(version=1, targets={target: dict(
            attempts={'301390': dict(committed=True, failures=1)})}))
        with patch.object(worker.refresh, 'AK', object(), create=True), \
                patch.object(worker.collect, 'minute_unadjusted', return_value=pd.DataFrame()), \
                patch('scripts.sina_daily.daily_unadjusted', return_value=pd.DataFrame()):
            self.run_worker(handler=lambda task: worker.refresh.fetch(task),
                            target=target, calendar_dates=[target])

        self.szse_lookup.assert_called_once_with(target, list(self.codes))
        published = json.loads(self.client.objects['data/' + target + '.json'])['rows'][0]
        self.assertEqual(published['status'], 'missing')
        self.assertNotIn('noTradeEvidence', published)
        self.assertNotIn('specialStatus', published)
        self.assertFalse(self.visible()['dataComplete'])

    def test_opening_only_persists_private_cache_and_never_publishes_daily_snapshot(self):
        self.clock = Clock('2026-09-08T09:50:00+08:00')
        self.codes = CODES
        old_payload = dict(date=DAY, generatedAt='old', rows=[row(CODES[0])])
        self.client.set('data/' + DAY + '.json', old_payload)
        self.client.set('data/manifest.json', dict(dates=[dict(date=DAY, file=DAY + '.json', generatedAt='old')]))
        before = dict(self.client.objects)
        self.assertEqual(self.run_worker(
            lambda task: dict(code=task[0]['code'], opening=10, speedDegraded=False),
            phase='opening', target=TODAY), 0)
        written = [entry['Key'] for entry in self.client.writes]
        self.assertFalse(any(key.startswith('data/2026-') for key in written))
        self.assertNotIn('data/manifest.json', written)
        self.assertFalse((self.out / (TODAY + '.json')).exists())
        self.assertEqual(self.client.objects['data/' + DAY + '.json'], before['data/' + DAY + '.json'])
        self.assertEqual(self.client.objects['data/manifest.json'], before['data/manifest.json'])
        cache = json.loads(self.client.objects['collector/refresh-state.json'])
        self.assertEqual(cache['targets'][TODAY]['openings'], dict.fromkeys(CODES, 10))
        status = self.visible()
        self.assertEqual(status['openingCachedCount'], 3)
        self.assertTrue(status['openingComplete'])
        self.assertFalse(status['publicationCommitted'])
        self.assertFalse(status['dataComplete'])
        self.assertIsNone(status['fullyPublishedAt'])
        self.assertEqual(self.context.processes, 4)
        self.assertTrue(self.pool.closed and self.pool.joined)

    def test_target_refresh_preserves_all_checkpoint_history_and_legacy_queue_bytes(self):
        old_row = row(CODES[0], first15Volume=5, dailyVolume=100, ratio=5)
        partial = row(CODES[0], status='missing', dailyVolume=None, ratio=None, reason='日线缺失', close=8.12)
        record = dict(code=CODES[0], days={OLDER: old_row, DAY: partial}, dailyAttempts={OLDER: {'primary': DAY}})
        self.write('checkpoint/' + CODES[0] + '.json', record)
        old_state = dict(dates=[OLDER, DAY], pending={CODES[0]: [OLDER, DAY]},
                         attempts={CODES[0]: {OLDER: dict(primary=DAY)}})
        self.write('daily-state.json', old_state)
        original_queue = (self.out / 'daily-state.json').read_bytes()
        self.write(OLDER + '.json', dict(date=OLDER, generatedAt='old', rows=[old_row]))
        old_snapshot = (self.out / (OLDER + '.json')).read_bytes()
        self.client.set('data/' + OLDER + '.json', old_snapshot)
        self.client.set('data/manifest.json', dict(dates=[dict(date=OLDER, file=OLDER + '.json', generatedAt='old')]))
        self.assertEqual(self.run_worker(), 0)
        saved = self.read('checkpoint/' + CODES[0] + '.json')
        self.assertEqual(saved['days'][OLDER], old_row)
        self.assertEqual(saved['dailyAttempts'], record['dailyAttempts'])
        self.assertEqual(saved['days'][DAY]['close'], 8.12)
        self.assertEqual(saved['days'][DAY]['dailyVolume'], 100)
        self.assertEqual((self.out / 'daily-state.json').read_bytes(), original_queue)
        self.assertEqual((self.out / (OLDER + '.json')).read_bytes(), old_snapshot)
        self.assertEqual(self.client.objects['data/' + OLDER + '.json'], old_snapshot)
        self.assertEqual(self.pool.tasks[0][2], 10)
        self.assertEqual(self.pool.tasks[0][3]['close'], 8.12)
        status = self.visible()
        self.assertEqual(status['targetDate'], DAY)
        self.assertEqual(status['historicalPendingCount'], 1)
        self.assertTrue(status['dataComplete'] and status['publicationCommitted'])
        self.assertIsNotNone(status['fullyPublishedAt'])

    def test_manifest_backlog_is_registered_when_legacy_queue_omits_date(self):
        omitted = '2026-08-31'
        day_payload = dict(date=DAY, generatedAt='fixture', rows=[row(CODES[0])])
        self.write(DAY + '.json', day_payload)
        missing = row(CODES[0], status='missing', first15Volume=None, dailyVolume=None,
                      ratio=None, reason='source missing')
        older_payload = dict(date=omitted, generatedAt='fixture', rows=[missing])
        self.write(omitted + '.json', older_payload)
        self.write('daily-state.json', dict(pending={}))
        manifest = dict(dates=[
            dict(date=DAY, file=DAY + '.json', generatedAt='fixture', total=1,
                 valid=1, suspended=0, missing=0, unverified=0),
            dict(date=omitted, file=omitted + '.json', generatedAt='fixture', total=1,
                 valid=0, suspended=0, missing=1, unverified=0),
        ])
        self.write('manifest.json', manifest)
        self.client.set('data/' + DAY + '.json', day_payload)
        self.client.set('data/' + omitted + '.json', older_payload)
        self.client.set('data/manifest.json', manifest)

        self.assertEqual(self.run_worker(target=None, calendar_dates=[omitted, DAY, TODAY]), 0)

        self.assertEqual([task[1] for task in self.pool.tasks], [omitted])
        self.assertEqual(self.visible()['targetDate'], omitted)
        targets = self.read('refresh-state.json')['targets']
        self.assertTrue(targets[DAY]['summary']['dataComplete'])
        self.assertTrue(targets[omitted]['summary']['dataComplete'])

    def test_manifest_zero_missing_does_not_hide_unproven_suspension(self):
        suspended = row(CODES[0], status='suspended', first15Volume=None,
                        dailyVolume=None, ratio=None, reason=None)
        self.write(DAY + '.json', dict(date=DAY, rows=[suspended]))
        self.write('daily-state.json', dict(pending={}))
        self.write('refresh-state.json', dict(version=1, targets={DAY: dict(
            summary=dict(pendingCount=0, retryableCount=0, dataComplete=True))}))
        self.write('manifest.json', dict(dates=[dict(
            date=DAY, file=DAY + '.json', generatedAt='fixture', total=1,
            valid=0, suspended=1, missing=0, unverified=0)]))

        self.assertEqual(self.run_worker(target=None), 0)

        self.assertEqual([task[1] for task in self.pool.tasks], [DAY])
        self.assertEqual(self.visible()['targetDate'], DAY)
        self.assertTrue(self.visible()['dataComplete'])

    def test_single_day_restore_does_not_let_legacy_downgrade_complete_history(self):
        old = '2026-09-04'
        payload = dict(date=DAY, generatedAt='fixture', rows=[row(CODES[0])])
        current_entry = dict(date=DAY, file=DAY+'.json', generatedAt='fixture', total=1,
                             valid=1, suspended=0, missing=0, unverified=0)
        old_entry = dict(current_entry, date=old, file=old+'.json')
        # The runner restored only DAY, but learned that old was complete from
        # the full remote catalog. The legacy queue still contains an old retry.
        self.write(DAY+'.json', payload)
        self.write('manifest.json', dict(dates=[current_entry]))
        self.client.set('data/'+DAY+'.json', payload)
        self.client.set('data/manifest.json', dict(dates=[old_entry, current_entry]))
        self.write('daily-state.json', dict(pending={CODES[0]: [old]}))
        self.write('refresh-state.json', dict(version=1, targets={
            old: dict(summary=dict(dataComplete=True, pendingCount=0)),
            DAY: dict(summary=dict(dataComplete=True, pendingCount=0))}))

        self.assertEqual(self.run_worker(target=DAY, calendar_dates=[old, DAY, TODAY]), 0)

        saved = self.read('refresh-state.json')['targets'][old]['summary']
        published = json.loads(self.client.objects['collector/refresh-status.json'])['targets'][old]
        self.assertTrue(saved['dataComplete'])
        self.assertEqual(saved['pendingCount'], 0)
        self.assertTrue(published['dataComplete'])
        self.assertTrue(self.visible()['dataComplete'])
        self.assertEqual(next(entry for entry in json.loads(self.client.objects['data/manifest.json'])['dates']
                              if entry['date'] == old), old_entry)

    def test_interrupted_uncommitted_request_is_reclaimed_next_day_without_waiting_for_retry(self):
        self.assertEqual(self.run_worker(lambda _task: Future(ready=False)), 0)
        interrupted = self.read('refresh-state.json')['targets'][DAY]['attempts'][CODES[0]]
        self.assertFalse(interrupted['committed'])
        self.assertTrue(self.pool.terminated and self.pool.joined)
        self.assertEqual(self.visible()['unprocessedCount'], 1)
        # A future retry timestamp must not hide a dispatch that never committed.
        cache = self.read('refresh-state.json')
        cache['targets'][DAY]['attempts'][CODES[0]]['nextRetryAt'] = '2026-09-09T20:00:00+08:00'
        self.write('refresh-state.json', cache)
        self.clock = Clock('2026-09-09T07:00:00+08:00')
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual(len(self.pool.tasks), 1)
        self.assertEqual(self.pool.tasks[0][1], DAY)
        committed = self.read('refresh-state.json')['targets'][DAY]['attempts'][CODES[0]]
        self.assertTrue(committed['committed'])
        self.assertIsNone(committed['nextRetryAt'])
        self.assertTrue(self.visible()['dataComplete'])

    def test_published_rows_repair_checkpoint_after_runner_dies_before_archive(self):
        self.write(DAY + '.json', dict(date=DAY, rows=[row(CODES[0])]))
        self.write('checkpoint/' + CODES[0] + '.json', dict(code=CODES[0], days={OLDER: row(CODES[0])}))
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual(self.pool.tasks, [])
        saved = self.read('checkpoint/' + CODES[0] + '.json')['days']
        self.assertEqual(saved[DAY], row(CODES[0]))
        self.assertEqual(saved[OLDER], row(CODES[0]))

    def test_partial_core_retry_preserves_published_complete_quote_frame(self):
        self.codes = CODES[:2]
        priced = row(CODES[0], open=9.8, high=10.3, low=9.7, close=10.0,
                     priceStatus='available', priceSourceProvider='tencent',
                     pctChange=1.1, amplitude=6.0,
                     dailyAdapter='tencent_daily_quote_backfill')
        missing = row(CODES[1], status='missing', first15Volume=None,
                      dailyVolume=None, ratio=None, reason='source missing')
        self.write(DAY + '.json', dict(date=DAY, generatedAt='fixture',
                                       universeTotal=2, rows=[priced, missing]))
        self.write('checkpoint/' + CODES[0] + '.json', dict(
            code=CODES[0], days={DAY: row(CODES[0])}))
        self.write('checkpoint/' + CODES[1] + '.json', dict(
            code=CODES[1], days={DAY: missing}))

        self.assertEqual(self.run_worker(), 0)

        published = json.loads(self.client.objects['data/' + DAY + '.json'])
        actual = {item['code']: item for item in published['rows']}[CODES[0]]
        for key in ('open', 'high', 'low', 'close', 'priceStatus',
                    'priceSourceProvider', 'pctChange', 'amplitude', 'dailyAdapter'):
            self.assertEqual(actual.get(key), priced[key], key)

    def test_status_counts_separate_first_pass_missing_explicit_no_trade_and_history(self):
        self.codes = CODES
        zero = row(CODES[1], status='suspended', first15Volume=None, dailyVolume=0, ratio=None,
                   noTradeEvidence=dict(kind='explicit_zero_daily_volume', provider='sina', date=DAY, volume=0))
        missing = row(CODES[2], status='missing', first15Volume=None, dailyVolume=None, ratio=None, reason='源超时')
        self.write(DAY + '.json', dict(date=DAY, rows=[row(CODES[0]), zero, missing]))
        self.write('daily-state.json', dict(pending={CODES[0]: [OLDER, DAY], CODES[1]: [OLDER]}))
        self.assertEqual(self.run_worker(lambda task: result(task[0]['code'], row=missing, opening=None, speedDegraded=True)), 0)
        self.assertEqual([task[0]['code'] for task in self.pool.tasks], [CODES[2]])
        status = self.visible()
        expected = dict(totalStocks=3, completedStocks=3, unprocessedCount=0, calculableCount=1,
                        noTradeCount=1, retryableCount=1, pendingCount=1, historicalPendingCount=2,
                        firstPassComplete=True, dataComplete=False, speedDegraded=True)
        for key, value in expected.items():
            self.assertEqual(status[key], value, key)
        self.assertEqual(status['state'], 'paused')
        self.assertEqual(status['nextRetryAt'], '2026-09-08T07:05:00.100000+08:00')
        self.assertIsNotNone(status['firstPassCompletedAt'])
        self.assertIsNone(status['fullyPublishedAt'])

    def test_status_uses_nearest_future_retry_instead_of_expired_retry(self):
        items = [dict(code=CODES[0]), dict(code=CODES[1])]
        target = dict(date=DAY, attempts={
            CODES[0]: dict(committed=True, nextRetryAt='2026-09-08T06:55:00+08:00'),
            CODES[1]: dict(committed=True, nextRetryAt='2026-09-08T07:15:00+08:00'),
        })
        with patch.object(worker, 'now', return_value=datetime.fromisoformat(
                '2026-09-08T07:00:00+08:00')):
            status = worker.metrics(items, {}, target, 'catchup')
        self.assertEqual(status['nextRetryAt'], '2026-09-08T07:15:00+08:00')

    def test_failed_publication_never_reports_complete_even_when_all_source_rows_are_ready(self):
        for mode in ('raise', 'zero'):
            with self.subTest(mode=mode):
                # Start with one published row and one outstanding row, ensuring
                # that a partial successful publication cannot bless a later failed one.
                self.reset_fixture()
                self.codes = CODES[:2]
                self.write(DAY + '.json', dict(date=DAY, rows=[row(CODES[0])]))
                publisher = cloud_worker.Publisher(self.client, 'fixture', self.out)
                actual_publish = publisher.publish
                calls = []

                def fail_final(*args, **kwargs):
                    calls.append(True)
                    if len(calls) == 1:
                        return actual_publish(*args, **kwargs)
                    if mode == 'raise':
                        raise OSError('fixture manifest upload failure')
                    return 0

                with patch.object(publisher, 'publish', side_effect=fail_final):
                    with self.assertRaises((OSError, RuntimeError)):
                        self.run_worker(publisher=publisher)
                statuses = [json.loads(entry['Body']) for entry in self.client.writes
                            if entry['Key'] in ('data/collection-status.json', 'collector/refresh-status.json')]
                self.assertTrue(statuses)
                self.assertTrue(all(not state['dataComplete'] for state in statuses))
                self.assertTrue(all(not state.get('fullyPublishedAt') for state in statuses))
                committed = json.loads(self.client.objects['data/' + DAY + '.json'])
                self.assertEqual(sum(item['status'] == 'ok' for item in committed['rows']), 1)
                self.assertTrue(worker.refresh.complete(self.read('checkpoint/' + CODES[1] + '.json')['days'][DAY]))

    def test_legacy_empty_response_is_republished_as_missing_not_unproven_no_trade(self):
        empty = row(CODES[0], status='suspended', first15Volume=None, dailyVolume=None, ratio=None)
        self.write(DAY + '.json', dict(date=DAY, generatedAt='old', rows=[empty]))
        self.write('checkpoint/' + CODES[0] + '.json', dict(code=CODES[0], days={DAY: empty}))
        missing = row(CODES[0], status='missing', first15Volume=None, dailyVolume=None, ratio=None,
                      reason='新浪未提供目标日期数据；等待补采')
        self.assertEqual(self.run_worker(lambda task: result(task[0]['code'], row=missing, opening=None)), 0)
        status = self.visible()
        self.assertFalse(status['dataComplete'])
        self.assertEqual(status['noTradeCount'], 0)
        self.assertEqual(status['retryableCount'], 1)
        self.assertEqual(self.read('checkpoint/' + CODES[0] + '.json')['days'][DAY]['status'], 'missing')
        payload = json.loads(self.client.objects['data/' + DAY + '.json'])
        self.assertEqual(payload['rows'][0]['status'], 'missing')
        manifest = json.loads(self.client.objects['data/manifest.json'])
        target = next(entry for entry in manifest['dates'] if entry['date'] == DAY)
        self.assertEqual(target['status'], 'partial')
        self.assertEqual(target['suspended'], 0)

    def test_repeat_completed_target_does_not_fetch_again_and_keeps_completion_receipt(self):
        self.assertEqual(self.run_worker(), 0)
        complete = self.visible()
        cached = self.read('refresh-state.json')['targets'][DAY]
        self.clock = Clock('2026-09-08T07:30:00+08:00')
        self.assertEqual(self.run_worker(lambda _task: self.fail('A complete target must not be fetched again')), 0)
        self.assertFalse(self.pool.tasks)
        second = self.visible()
        self.assertTrue(second['dataComplete'])
        self.assertEqual(second['attemptedThisRun'], 0)
        self.assertEqual(second['firstPassCompletedAt'], complete['firstPassCompletedAt'])
        self.assertEqual(second['fullyPublishedAt'], complete['fullyPublishedAt'])
        self.assertEqual(self.read('refresh-state.json')['targets'][DAY]['attempts'], cached['attempts'])

    def test_completed_current_day_without_quotes_downloads_snapshot_once(self):
        self.clock = Clock('2026-09-08T19:05:00+08:00')
        self.write(TODAY + '.json', dict(date=TODAY, rows=[row(CODES[0])]))

        self.assertEqual(self.run_worker(
            handler=lambda task: worker.refresh.fetch(task),
            phase='catchup', target=TODAY, spot_fetch=spot_frame), 0)

        self.assertEqual(len(self.pool.tasks), 1)
        published = json.loads(self.client.objects['data/' + TODAY + '.json'])
        self.assertEqual(published['rows'][0]['pctChange'], 0.6838)
        self.assertEqual(published['rows'][0]['amplitude'], 1.3675)
        self.assertTrue(self.visible()['dataComplete'])

    def test_completed_current_day_with_quotes_skips_another_snapshot(self):
        self.clock = Clock('2026-09-08T19:05:00+08:00')
        self.write(TODAY + '.json', dict(date=TODAY, rows=[
            row(CODES[0], pctChange=0.6838, amplitude=1.3675)]))

        self.assertEqual(self.run_worker(
            lambda _task: self.fail('A quote-complete target must not be fetched again'),
            phase='catchup', target=TODAY), 0)

        self.assertEqual(self.pool.tasks, [])
        self.assertEqual(self.visible()['attemptedThisRun'], 0)
        self.assertTrue(self.visible()['dataComplete'])

    def test_incomplete_current_day_with_saved_daily_quote_skips_snapshot(self):
        self.clock = Clock('2026-09-08T19:05:00+08:00')
        saved = row(CODES[0], status='missing', first15Volume=None, ratio=None,
                    pctChange=0.6838, amplitude=1.3675)
        self.write(TODAY + '.json', dict(date=TODAY, rows=[saved]))
        task_lengths = []

        def fill_opening(task):
            task_lengths.append(len(task))
            return result(task[0]['code'])

        self.assertEqual(self.run_worker(fill_opening, phase='catchup', target=TODAY), 0)

        self.assertEqual(task_lengths, [5])
        self.assertTrue(self.visible()['dataComplete'])

    def test_one_daily_gap_in_large_saved_market_skips_full_snapshot(self):
        self.clock = Clock('2026-09-08T19:05:00+08:00')
        self.codes = tuple(f'{number:06d}' for number in range(1, 102))
        saved = [row(code, pctChange=0.6838, amplitude=1.3675) for code in self.codes]
        saved[0].update(status='missing', dailyVolume=None, ratio=None, reason='daily:HTTPError')
        self.write(TODAY + '.json', dict(date=TODAY, rows=saved))

        self.assertEqual(self.run_worker(
            lambda task: result(task[0]['code']), phase='catchup', target=TODAY), 0)

        self.assertEqual(len(self.pool.tasks), 1)
        self.assertEqual(len(self.pool.tasks[0]), 5)
        self.assertTrue(self.visible()['dataComplete'])

    def test_repeated_source_http_errors_pause_for_a_fresh_runner(self):
        self.clock = Clock('2026-09-08T19:05:00+08:00')
        self.codes = CODES
        saved = [row(code, status='missing', first15Volume=None, ratio=None,
                     pctChange=0.6838, amplitude=1.3675) for code in self.codes]
        self.write(TODAY + '.json', dict(date=TODAY, rows=saved))

        def rejected(task):
            code = task[0]['code']
            return result(code, row=saved[self.codes.index(code)], opening=None,
                          errors=['opening:HTTPError'])

        with patch.object(worker, 'SOURCE_HTTP_FAILURE_LIMIT', 2):
            self.assertEqual(self.run_worker(rejected, phase='catchup', target=TODAY), 0)

        status = self.visible()
        self.assertTrue(status['sourceThrottled'])
        self.assertGreaterEqual(status['sourceHTTPFailureStreak'], 2)
        self.assertEqual(status['exitReason'], 'source_throttled')
        self.assertEqual(status['state'], 'paused')
        self.assertEqual(len(self.pool.tasks), len(self.codes))
        self.assertTrue(self.pool.closed and self.pool.joined)

    def test_source_http_errors_cool_all_dispatches_and_resume_one_at_a_time(self):
        self.codes = tuple(f'{number:06d}' for number in range(1, 7))
        dispatched = []

        def flaky_then_recovers(task):
            code = task[0]['code']
            dispatched.append(self.clock.elapsed)
            if len(dispatched) <= 4:
                missing = row(code, status='missing', first15Volume=None,
                              dailyVolume=None, ratio=None, reason='opening:HTTPError')
                return result(code, row=missing, opening=None,
                              errors=['opening:HTTPError'])
            return result(code, firstDataRequestAt='2026-09-07T00:00:00+00:00')

        with patch.object(worker, 'SOURCE_HTTP_FAILURE_LIMIT', 20):
            self.assertEqual(self.run_worker(
                flaky_then_recovers, phase='catchup', target=DAY,
                max_minutes=12), 0)

        self.assertEqual(len(dispatched), 6)
        self.assertLess(max(dispatched[:4]), 1)
        self.assertGreaterEqual(dispatched[4], 60)
        self.assertFalse(self.visible()['sourceThrottled'])

    def test_inflight_success_does_not_cancel_serial_source_recovery_probe(self):
        self.codes = tuple(f'{number:06d}' for number in range(1, 7))
        dispatched = []

        def one_failure_then_success(task):
            code = task[0]['code']
            dispatched.append(self.clock.elapsed)
            if len(dispatched) == 1:
                missing = row(code, status='missing', first15Volume=None,
                              dailyVolume=None, ratio=None, reason='opening:HTTPError')
                return result(code, row=missing, opening=None,
                              errors=['opening:HTTPError'])
            return result(code, firstDataRequestAt='2026-09-07T00:00:00+00:00')

        self.assertEqual(self.run_worker(
            one_failure_then_success, phase='catchup', target=DAY,
            max_minutes=12), 0)

        self.assertEqual(len(dispatched), 6)
        self.assertGreaterEqual(dispatched[4], 5)
        self.assertGreater(dispatched[5], dispatched[4])

    def test_new_run_clears_prior_source_throttle_status(self):
        collect.write_json(self.root / 'worker-state.json', dict(
            sourceThrottled=True, sourceHTTPFailureStreak=20))

        self.assertEqual(self.run_worker(), 0)

        status = self.visible()
        self.assertFalse(status['sourceThrottled'])
        self.assertEqual(status['sourceHTTPFailureStreak'], 0)

    def test_new_run_does_not_republish_previous_error_or_exchange_result(self):
        collect.write_json(self.root / 'worker-state.json', dict(
            error='ReadTimeout', errorDetail='previous invocation only',
            exchangeStatusError='HTTPError', exchangeStatusAvailable=True,
            exchangeStatusSource='sse', exchangeStatusMatchCount=6))

        self.assertEqual(self.run_worker(), 0)

        status = self.visible()
        for field in ('error', 'errorDetail', 'exchangeStatusError',
                      'exchangeStatusAvailable', 'exchangeStatusSource',
                      'exchangeStatusMatchCount'):
            self.assertNotIn(field, status)

    def test_suspended_current_day_clears_fabricated_quote_values(self):
        self.clock = Clock('2026-09-08T19:05:00+08:00')
        evidence = dict(kind='explicit_zero_daily_volume', provider='sina',
                        date=TODAY, symbol='sz000001', volume=0)
        self.write(TODAY + '.json', dict(date=TODAY, rows=[row(
            CODES[0], status='suspended', first15Volume=None, dailyVolume=0,
            ratio=None, noTradeEvidence=evidence, pctChange=-100.0, amplitude=0.0)]))
        zero_spot = lambda: pd.DataFrame([{
            '代码': 'sz000001', '最新价': 0.0, '昨收': 11.70, '今开': 0.0,
            '最高': 0.0, '最低': 0.0, '成交量': 0.0, '时间戳': '15:36:00',
        }])

        self.assertEqual(self.run_worker(
            handler=lambda task: worker.refresh.fetch(task),
            phase='catchup', target=TODAY, spot_fetch=zero_spot), 0)

        published = json.loads(self.client.objects['data/' + TODAY + '.json'])['rows'][0]
        self.assertIsNone(published['pctChange'])
        self.assertIsNone(published['amplitude'])
        self.assertEqual(published['dailyVolume'], 0)

    def test_close_reuses_opening_cache_and_schedules_missing_cache_stock_without_it(self):
        self.codes = CODES
        self.clock = Clock('2026-09-08T15:30:00+08:00')
        self.write('refresh-state.json', dict(version=1, targets={TODAY: dict(
            openings={CODES[0]: 10, CODES[1]: 10}, openingAttempts={CODES[2]: dict(
                committed=True, nextRetryAt='2026-09-08T16:00:00+08:00', failures=1)})}))
        self.assertEqual(self.run_worker(phase='close', target=TODAY,
                                         spot_fetch=spot_frame), 0)
        self.assertEqual({task[0]['code']: task[2] for task in self.pool.tasks},
                         {CODES[0]: 10, CODES[1]: 10, CODES[2]: None})
        self.assertTrue(all(task[1] == TODAY and task[4] == 'close' for task in self.pool.tasks))
        self.assertEqual({task[0]['code']: task[5]['dailyVolume'] for task in self.pool.tasks},
                         {CODES[0]: 74051597, CODES[1]: 1753404, CODES[2]: 729266})
        target = self.read('refresh-state.json')['targets'][TODAY]
        self.assertEqual(target['openingAttempts'][CODES[2]]['failures'], 1)
        self.assertEqual(set(target['attempts']), set(CODES))
        self.assertTrue(self.visible()['dataComplete'])

    def test_current_day_close_uses_one_market_snapshot_and_publishes_complete_rows(self):
        self.codes = CODES
        self.clock = Clock('2026-09-08T15:35:00+08:00')
        self.write('refresh-state.json', dict(version=1, targets={TODAY: dict(
            openings=dict.fromkeys(CODES, 10))}))
        calls = []

        def fetch_spot():
            calls.append(True)
            return spot_frame()

        self.assertEqual(self.run_worker(handler=lambda task: worker.refresh.fetch(task),
                                         phase='close', target=TODAY,
                                         spot_fetch=fetch_spot), 0)

        self.assertEqual(len(calls), 1)
        published = json.loads(self.client.objects['data/' + TODAY + '.json'])
        self.assertEqual([item['dailyVolume'] for item in published['rows']],
                         [74051597, 1753404, 729266])
        self.assertTrue(all(item['dailyAdapter'] == 'sina_market_snapshot'
                            for item in published['rows']))
        status = self.visible()
        self.assertEqual(status['closeSnapshotAvailableCount'], 3)
        self.assertEqual(status['closeSnapshotMissingCount'], 0)
        self.assertTrue(status['dataComplete'])

    def test_sparse_current_day_snapshot_stops_before_per_stock_fanout(self):
        self.codes = CODES
        self.clock = Clock('2026-09-08T15:35:00+08:00')
        sparse = spot_frame().iloc[:1].copy()

        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.run_worker(phase='close', target=TODAY,
                            spot_fetch=lambda: sparse)

        self.assertEqual(self.pool.tasks, [])
        self.assertNotIn('data/' + TODAY + '.json', self.client.objects)
        status = self.visible()
        self.assertEqual(status['state'], 'paused')
        self.assertEqual(status['unprocessedCount'], 3)
        self.assertFalse(status['dataComplete'])
        self.assertEqual(status['error'], 'ValueError')
        self.assertIn('coverage', status['errorDetail'])


if __name__ == '__main__':
    unittest.main()
