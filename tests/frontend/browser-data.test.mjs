import test from 'node:test';
import assert from 'node:assert/strict';
import { projectDayForBrowser } from '../../src/lib/browser-data.ts';
import {
  combineDays,
  filterRows,
  parseFilters,
  providerLabel,
  formatVolume,
} from '../../src/lib/stock.ts';
import { buildTrend } from '../../src/lib/chart-data.ts';
import { loadQuery } from '../../src/lib/query-loader.ts';

const good = {
  code: '000001',
  name: '平安银行',
  market: 'SZ',
  first15Volume: 10,
  dailyVolume: 100,
  ratio: 10,
  status: 'ok',
  sourceProvider: 'sina',
  quality: 'matched',
};
const suspect = {
  code: '600519',
  name: '贵州茅台',
  market: 'SH',
  first15Volume: 20,
  dailyVolume: 100,
  ratio: 20,
  status: 'ok',
  quality: 'source_difference',
  qualityNote: '分钟合计差额 -1 股',
  dayVolumeDifference: -1,
  sourceProvider: 'eastmoney',
  reconciliationAttempts: [
    { source: 'sina', stage: 'retry', status: 'unverified', reason: 'gap' },
  ],
  verificationAudit: { details: 'server evidence' },
  verifiedAt: '2026-09-06T10:00:00',
};
const day = (date) => ({
  date,
  generatedAt: '2026-09-06',
  scope: 'full',
  source: 'fixture',
  methodology: 'fixture',
  total: 2,
  rows: [structuredClone(good), structuredClone(suspect)],
});
const visible = (rows) =>
  rows.map(
    ({
      reconciliationAttempts: _attempts,
      verificationAudit: _audit,
      verifiedAt: _at,
      quality: _quality,
      qualityNote: _note,
      referenceRatio: _reference,
      dayVolumeDifference: _difference,
      verificationVersion: _version,
      verificationState: _state,
      reason: _reason,
      ...r
    }) => r,
  );

test('an official no-trade row without an opening field loads with an explicit missing volume', async () => {
  const raw = day('2026-10-08');
  raw.total = 1;
  raw.rows = [{code:'000016',name:'*ST康佳A',market:'SZ',dailyVolume:0,ratio:null,
    status:'suspended',sourceProvider:'sina',priceStatus:'not_traded',
    open:null,high:null,low:null,close:null,
    specialStatus:{type:'suspension',label:'交易所公告停牌',startedAt:'2026-09-04',
      source:'深圳证券交易所停复牌提示',description:'已确认目标日全天无交易'}}];
  const original = structuredClone(raw);
  const result = await loadQuery({generatedAt:raw.generatedAt,source:'fixture',methodology:'fixture',
    dates:[{date:raw.date,file:raw.date+'.json',status:'complete'}]},
    parseFilters(new URLSearchParams('scope=single&date=2026-10-08&symbols=000016')),
    {dataBase:'https://fixture.test',apiBase:'',signal:new AbortController().signal,
      progress:()=>{},fetcher:async()=>new Response(JSON.stringify(raw))});
  const row = result.rows[0];
  assert.equal(row.first15Volume, null);
  assert.equal(formatVolume(row.first15Volume), '—');
  assert.equal(row.dailyVolume, 0);
  assert.equal(row.ratio, null);
  assert.equal(row.status, 'suspended');
  assert.deepEqual(row.specialStatus, original.rows[0].specialStatus);
  assert.deepEqual(raw, original);
});

test('a missing source reason survives the real query-loader projection without retaining audit history', async () => {
  const raw=day('2026-09-24');
  raw.rows=[{...good,code:'688184',status:'missing',first15Volume:null,dailyVolume:203691,ratio:null,
    reason:'新浪缺少目标09:45记录；待精确新证据复核',verificationAudit:{raw:'private'}}];
  const before=structuredClone(raw);
  const result=await loadQuery({generatedAt:raw.generatedAt,source:'fixture',methodology:'fixture',
    dates:[{date:raw.date,file:raw.date+'.json',status:'partial'}]},
    parseFilters(new URLSearchParams('scope=single&date=2026-09-24&symbols=688184')),
    {dataBase:'https://fixture.test',apiBase:'',signal:new AbortController().signal,progress:()=>{},
      fetcher:async()=>new Response(JSON.stringify(raw))});
  assert.equal(result.rows[0].reason,raw.rows[0].reason);
  assert.equal(result.rows[0].ratio,null);
  assert.equal(result.rows[0].dailyVolume,203691);
  assert.equal('verificationAudit' in result.rows[0],false);
  assert.deepEqual(raw,before);
});

test('provider labels explicitly distinguish BaoStock and unknown sources', () => {
  assert.equal(providerLabel('sina'), '新浪');
  assert.equal(providerLabel('eastmoney'), '东方财富');
  assert.equal(providerLabel('baostock'), 'BaoStock');
  for (const unknown of [
    undefined,
    '',
    'future-provider',
    'toString',
    '__proto__',
  ])
    assert.equal(providerLabel(unknown), '未知来源');
});

test('historic source labels survive projection while ratios use downloaded volumes', () => {
  const raw = day('2026-09-04');
  raw.rows = [
    {
      ...good,
      sourceProvider: 'baostock',
      verificationState: 'fallback_verified',
      reconciliationAttempts: [{ source: 'baostock', status: 'ok' }],
    },
    { ...suspect, sourceProvider: 'baostock', referenceRatio: 20 },
  ];
  const projected = projectDayForBrowser(raw);
  assert.ok(projected.rows.every((row) => row.sourceProvider === 'baostock'));
  assert.ok(projected.rows.every((row) => !('reconciliationAttempts' in row)));
  assert.ok(raw.rows.every((row) => row.reconciliationAttempts));
  assert.equal(projected.rows[0].ratio, 10);
  assert.equal('verificationState' in projected.rows[0], false);
  assert.equal(projected.rows[1].ratio, 20);
  assert.equal('referenceRatio' in projected.rows[1], false);
  assert.equal(projected.rows[1].status, 'ok');
  assert.deepEqual(
    filterRows(
      projected.rows,
      parseFilters(new URLSearchParams('scope=all')),
    ).map((row) => row.code),
    ['600519', '000001'],
  );
  const trend = buildTrend(projected.rows, [raw.date], null);
  assert.equal(trend.points[0].validCount, 2);
  assert.equal(trend.points[0].median, 15);
});

test('browser projection preserves all display data and leaves server audit input intact', () => {
  const original = day('2026-09-04');
  original.rows[0] = {
    ...original.rows[0],
    open: 10.1,
    high: 10.8,
    low: 9.9,
    close: 10.5,
    priceStatus: 'available',
    priceSourceProvider: 'sina',
    specialStatus: {
      type: 'major_restructuring',
      label: '重大资产重组停牌',
      description: '交易所公告确认停牌。',
      startedAt: '2026-09-04',
      source: '上海证券交易所',
    },
  };
  const before = structuredClone(original);
  const view = projectDayForBrowser(original);
  assert.deepEqual(original, before);
  assert.equal('reconciliationAttempts' in view.rows[1], false);
  assert.equal('verificationAudit' in view.rows[1], false);
  assert.equal('referenceRatio' in view.rows[1], false);
  assert.equal(view.rows[1].ratio, 20);
  assert.equal('dayVolumeDifference' in view.rows[1], false);
  assert.equal('qualityNote' in view.rows[1], false);
  assert.equal(view.rows[1].sourceProvider, 'eastmoney');
  assert.deepEqual(
    {
      open: view.rows[0].open,
      high: view.rows[0].high,
      low: view.rows[0].low,
      close: view.rows[0].close,
      priceStatus: view.rows[0].priceStatus,
      priceSourceProvider: view.rows[0].priceSourceProvider,
      specialStatus: view.rows[0].specialStatus,
    },
    {
      open: 10.1,
      high: 10.8,
      low: 9.9,
      close: 10.5,
      priceStatus: 'available',
      priceSourceProvider: 'sina',
      specialStatus: original.rows[0].specialStatus,
    },
  );
  assert.equal(projectDayForBrowser(view), view);
  const combined = combineDays([view]);
  assert.equal(combined.rows[0], view.rows[0]);
  assert.equal(combined.rows[1], view.rows[1]);
  assert.equal(
    projectDayForBrowser({ ...view, rows: combined.rows }).rows[0],
    view.rows[0],
  );
});

test('projected single, multiple and all-stock queries retain filtering, reference and trend results', () => {
  const days = [day('2026-09-03'), day('2026-09-04')];
  const baseline = combineDays(days);
  const projected = combineDays(days.map(projectDayForBrowser));
  for (const query of [
    'scope=all',
    'scope=all&unverified=1',
    'scope=all&min=15',
    'scope=single&symbols=600519',
    'scope=multiple&symbols=000001,600519&unverified=1',
    'scope=all&market=SH&unverified=1&order=date_asc',
  ]) {
    const filters = parseFilters(new URLSearchParams(query));
    assert.deepEqual(
      filterRows(projected.rows, filters),
      visible(filterRows(baseline.rows, filters)),
    );
  }
  for (const symbols of [null, ['600519'], ['000001', '600519']])
    assert.deepEqual(
      buildTrend(projected.rows, baseline.dates, symbols),
      buildTrend(baseline.rows, baseline.dates, symbols),
    );
  const wrongDay = { ...projected.rows[0], date: '1999-01-01' };
  assert.equal(
    combineDays([{ ...days[0], rows: [wrongDay] }]).rows[0].date,
    '2026-09-03',
  );
  assert.equal(wrongDay.date, '1999-01-01');
});

test('loader normalizes and projects downloaded and legacy cached records before retaining them', async () => {
  const manifest = {
    generatedAt: '2026-09-06',
    source: 'fixture',
    methodology: 'fixture',
    dates: [{ date: '2026-09-04', file: '2026-09-04.json', status: 'ready' }],
  };
  const filters = parseFilters(
    new URLSearchParams('scope=all&date=2026-09-04'),
  );
  for (const legacy of [false, true]) {
    const cache = new Map();
    const original = day('2026-09-04');
    if (legacy)
      cache.set('https://fixture.test|2026-09-06|2026-09-04', original);
    let calls = 0;
    const result = await loadQuery(manifest, filters, {
      dataBase: 'https://fixture.test',
      apiBase: '',
      signal: new AbortController().signal,
      cache,
      progress: () => {},
      fetcher: async () => {
        calls++;
        return new Response(JSON.stringify(original));
      },
    });
    assert.equal(calls, legacy ? 0 : 1);
    assert.equal(result.rows[1].ratio, 20);
    assert.equal('reconciliationAttempts' in result.rows[1], false);
    assert.equal(
      'reconciliationAttempts' in [...cache.values()][0].rows[1],
      false,
    );
    assert.ok(original.rows[1].reconciliationAttempts);
  }
});
