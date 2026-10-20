import test from 'node:test';
import assert from 'node:assert/strict';
import {
  filterRows,
  parseFilters,
  formatVolume,
  formatPct,
  pctTone,
  normalizeDownloadedRow,
  advanceLatestSelection,
  shouldFollowLatest,
  combineDays,
} from '../../src/lib/stock.ts';
const rows = [
  { code: '000001', name: '平安银行', market: 'SZ', ratio: 18.88569, first15Volume: 1888569, dailyVolume: 10000000 },
  { code: '600519', name: '贵州茅台', market: 'SH', ratio: 17.077289, first15Volume: 17077289, dailyVolume: 100000000 },
  { code: '920002', name: '万达轴承', market: 'BJ', ratio: 22.147523, first15Volume: 22147523, dailyVolume: 100000000 },
  { code: '000002', name: '万科A', market: 'SZ', ratio: null },
];
const base = parseFilters(new URLSearchParams());
test('latest view advances while explicit historical selections remain pinned', () => {
  const latest = {
    ...base,
    date: '2026-09-07',
    endDate: '2026-09-07',
  };
  assert.deepEqual(
    advanceLatestSelection(latest, '2026-09-07', '2026-09-08', true),
    { ...latest, date: '2026-09-08', endDate: '2026-09-08' },
  );
  assert.equal(
    advanceLatestSelection(latest, '2026-09-07', '2026-09-08', false),
    latest,
  );
  const range = { ...latest, mode: 'range', date: '2026-09-01' };
  assert.equal(
    advanceLatestSelection(range, '2026-09-07', '2026-09-08', true),
    range,
  );
});
test('only default or latest-mode URLs opt into following future dates', () => {
  assert.equal(shouldFollowLatest(new URLSearchParams()), true);
  assert.equal(shouldFollowLatest(new URLSearchParams('latest=1')), true);
  assert.equal(
    shouldFollowLatest(new URLSearchParams('date=2026-09-07')),
    false,
  );
  assert.equal(
    shouldFollowLatest(
      new URLSearchParams('latest=1&date=2026-09-07'),
    ),
    true,
  );
  assert.equal(
    shouldFollowLatest(
      new URLSearchParams('from=2026-09-01&to=2026-09-07'),
    ),
    false,
  );
});
test('shared filters reproduce selected market and minimum', () => {
  const f = parseFilters(
    new URLSearchParams('date=2026-09-04&market=BJ&min=20'),
  );
  assert.equal(f.date, '2026-09-04');
  assert.deepEqual(
    filterRows(rows, f).map((x) => x.code),
    ['920002'],
  );
});
test('missing values stay at bottom in both directions', () => {
  for (const order of ['asc', 'desc'])
    assert.equal(filterRows(rows, { ...base, order }).at(-1).code, '000002');
});
test('search and minimum combine without interpreting null as zero', () => {
  assert.deepEqual(
    filterRows(rows, { ...base, query: '平安', min: 18 }).map((x) => x.code),
    ['000001'],
  );
  assert.equal(filterRows(rows, { ...base, min: 1 }).length, 3);
});
test('invalid shared parameters are bounded', () => {
  const f = parseFilters(
    new URLSearchParams('min=Infinity&market=garbage&date=bad&order=no'),
  );
  assert.deepEqual(f, base);
  assert.equal(parseFilters(new URLSearchParams('min=101')).min, 100);
});
test('volume units and missing are separate', () => {
  assert.equal(formatVolume(null), '—');
  assert.equal(formatVolume(0), '0');
  assert.equal(formatVolume(10000), '1.00 万');
  assert.equal(formatVolume(100000000), '1.00 亿');
});
test('quote percentages keep sign, two decimals and a missing marker', () => {
  assert.equal(formatPct(null), '—');
  assert.equal(formatPct(undefined), '—');
  assert.equal(formatPct(Number.NaN), '—');
  assert.equal(formatPct(2.5), '+2.50%');
  assert.equal(formatPct(-1.234), '-1.23%');
  assert.equal(formatPct(0), '0.00%');
  assert.equal(pctTone(2.5), 'up');
  assert.equal(pctTone(-2.5), 'down');
  assert.equal(pctTone(0), 'flat');
  assert.equal(pctTone(null), 'flat');
});

test('combined ranges report price completion independently from core data', () => {
  const baseDay = {
    date: '2026-09-17', generatedAt: 'v1', source: 'fixture', scope: 'full',
    total: 1, methodology: 'fixture', rows: [rows[0]], priceSchemaVersion: 1,
    priceFields: ['open', 'high', 'low', 'close'], priceAvailable: 1,
    priceNoTrade: 0, priceMissing: 0, priceDataComplete: true,
  };
  const incomplete = {
    ...baseDay, date: '2026-09-18', priceAvailable: 0, priceMissing: 1,
    priceDataComplete: false,
  };
  const result = combineDays([baseDay, incomplete]);
  assert.equal(result.priceAvailable, 1);
  assert.equal(result.priceMissing, 1);
  assert.equal(result.priceDataComplete, false);
});

import { selectedDates, queryParams, loadDateRange } from '../../src/lib/stock.ts';
import { createStockRangeFixture } from './fixtures/stock-range.mjs';
const { manifest, catalog, fetchDay } = createStockRangeFixture();
test('single date selects only one snapshot', () =>
  assert.deepEqual(
    selectedDates(
      manifest,
      parseFilters(new URLSearchParams('date=2026-09-04')),
    ).map((d) => d.date),
    ['2026-09-04'],
  ));
test('date range includes both boundaries and skips non-trading dates', () => {
  const f = parseFilters(new URLSearchParams('from=2026-08-29&to=2026-09-04'));
  assert.deepEqual(
    selectedDates(manifest, f).map((d) => d.date),
    ['2026-08-31', '2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04'],
  );
});
test('reversed, invalid and unavailable date ranges fail clearly', () => {
  for (const query of [
    'from=2026-09-04&to=2026-09-01',
    'from=2026-02-30&to=2026-09-04',
    'from=2020-01-01&to=2020-01-02',
  ])
    assert.throws(() =>
      selectedDates(manifest, parseFilters(new URLSearchParams(query))),
    );
});
test('range and stock share URL round-trip exactly', () => {
  const f = parseFilters(
    new URLSearchParams(
      'from=2026-09-01&to=2026-09-04&q=000001&market=SZ&min=10&order=date_asc',
    ),
  );
  assert.deepEqual(parseFilters(queryParams(f)), f);
});
test('all-stock range keeps each stock day separate', async () => {
  const f = parseFilters(new URLSearchParams('from=2026-09-01&to=2026-09-04'));
  const data = await loadDateRange(
    manifest,
    f,
    new AbortController().signal,
    fetchDay,
  );
  assert.equal(data.rows.length, 32 * 4);
  assert.equal(new Set(data.rows.map((r) => r.code + '-' + r.date)).size, 128);
  assert.equal(data.dates.length, 4);
  assert.equal(data.scope, 'sample');
});
test('one stock over 60 days has one independent ratio per date', async () => {
  const f = parseFilters(
    new URLSearchParams(
      'from=2026-06-12&to=2026-09-04&q=000001&order=date_asc',
    ),
  );
  const data = await loadDateRange(
    manifest,
    f,
    new AbortController().signal,
    fetchDay,
  );
  const selected = filterRows(data.rows, f);
  assert.equal(selected.length, 60);
  assert.equal(selected[0].date, '2026-06-12');
  assert.equal(selected.at(-1).date, '2026-09-04');
  assert.ok(Math.abs(selected.at(-1).ratio - 18.88569) < 0.000001);
  for (const row of selected)
    if (row.ratio !== null)
      assert.ok(
        Math.abs(row.ratio - (row.first15Volume / row.dailyVolume) * 100) <
          1e-5,
      );
});
test('date sort keeps missing observations on their dates', () => {
  const data = [
    { ...rows[0], date: '2026-09-02' },
    { ...rows[3], date: '2026-09-03' },
    { ...rows[1], date: '2026-09-01' },
  ];
  assert.deepEqual(
    filterRows(data, { ...base, order: 'date_desc' }).map((r) => r.date),
    ['2026-09-03', '2026-09-02', '2026-09-01'],
  );
});
test('one failed date rejects the entire query instead of silently omitting it', async () => {
  const f = parseFilters(new URLSearchParams('from=2026-09-01&to=2026-09-04'));
  await assert.rejects(
    loadDateRange(manifest, f, new AbortController().signal, async (entry) => {
      if (entry.date === '2026-09-02') throw Error('Unavailable');
      return fetchDay(entry);
    }),
    /Unavailable/,
  );
});
test('cancelled request cannot overwrite a newer query', async () => {
  const c = new AbortController();
  c.abort();
  await assert.rejects(
    loadDateRange(
      manifest,
      parseFilters(new URLSearchParams('date=2026-09-04')),
      c.signal,
      fetchDay,
    ),
    { name: 'AbortError' },
  );
});
test('range loading is bounded to four concurrent reads', async () => {
  let running = 0,
    maximum = 0;
  const f = parseFilters(new URLSearchParams('from=2026-08-01&to=2026-09-04'));
  await loadDateRange(
    manifest,
    f,
    new AbortController().signal,
    async (entry) => {
      running++;
      maximum = Math.max(maximum, running);
      await new Promise((r) => setTimeout(r, 2));
      const d = await fetchDay(entry);
      running--;
      return d;
    },
  );
  assert.ok(maximum <= 4);
});

test('four requested use cases and multi-stock ranges keep exact stock/date pairs', async () => {
  for (const [scope, symbols, dates, expected] of [
    ['single', '000001', 'date=2026-09-04', 1],
    ['single', '000001', 'from=2026-09-01&to=2026-09-04', 4],
    ['multiple', '000001,600519', 'date=2026-09-04', 2],
    ['multiple', '000001,600519', 'from=2026-09-01&to=2026-09-04', 8],
    ['all', '', 'date=2026-09-04', 32],
    ['all', '', 'dates=2026-09-01,2026-09-04', 64],
  ]) {
    const f = parseFilters(
      new URLSearchParams(
        `${dates}&scope=${scope}&symbols=${symbols}&unverified=1`,
      ),
    );
    const data = await loadDateRange(
      manifest,
      f,
      new AbortController().signal,
      fetchDay,
    );
    const selected = filterRows(data.rows, f);
    assert.equal(selected.length, expected);
    assert.equal(new Set(selected.map((r) => r.code + r.date)).size, expected);
    if (scope !== 'all')
      assert.deepEqual(
        [...new Set(selected.map((r) => r.code))].sort(),
        symbols.split(',').sort(),
      );
    assert.deepEqual(parseFilters(queryParams(f)), f);
    assert.equal(data.scope, 'sample');
  }
});
test('non-contiguous dates never include intervening days or duplicate observations', () => {
  const f = parseFilters(
    new URLSearchParams(
      'scope=multiple&symbols=000001,000001,600519&dates=2026-09-04,2026-09-01,2026-09-04',
    ),
  );
  assert.deepEqual(f.symbols, ['000001', '600519']);
  assert.deepEqual(
    selectedDates(manifest, f).map((d) => d.date),
    ['2026-09-01', '2026-09-04'],
  );
  assert.deepEqual(parseFilters(queryParams(f)), f);
});
test('empty and invalid explicit selections never silently become all stocks or dates', () => {
  for (const scope of ['single', 'multiple'])
    assert.equal(
      filterRows(rows, { ...base, stockScope: scope, symbols: [] }).length,
      0,
    );
  assert.equal(
    filterRows(rows, {
      ...base,
      stockScope: 'single',
      symbols: ['000001', '600519'],
    }).length,
    0,
  );
  for (const dates of ['', '2026-09-01,2026-09-06', 'bad'])
    assert.throws(() =>
      selectedDates(
        manifest,
        parseFilters(new URLSearchParams('dates=' + dates)),
      ),
    );
  const unknown = parseFilters(
    new URLSearchParams('scope=multiple&symbols=000001,999999'),
  );
  assert.deepEqual(unknown.symbols, ['000001', '999999']);
  assert.equal(filterRows(rows, unknown).length, 1);
});
test('legacy stock code links migrate to exact selection, explicit all scope ignores stale symbols', () => {
  const old = parseFilters(new URLSearchParams('q=000001'));
  assert.equal(old.stockScope, 'single');
  assert.deepEqual(old.symbols, ['000001']);
  assert.equal(old.query, '');
  const all = parseFilters(new URLSearchParams('scope=all&symbols=000001'));
  assert.deepEqual(all.symbols, []);
  assert.equal(filterRows(rows, all).length, rows.length);
});
test('synthetic picker catalog covers its test universe and preserves leading zeros', () => {
  assert.equal(catalog.rows.length, manifest.universeTotal);
  assert.equal(new Set(catalog.rows.map((r) => r.code)).size, catalog.total);
  assert.ok(catalog.rows.every((r) => /^\d{6}$/.test(r.code) && r.name));
  assert.ok(catalog.rows.some((r) => r.code === '000001'));
  assert.equal(catalog.historicalMembership, false);
});

import { buildTrend } from '../../src/lib/chart-data.ts';
import { changeFilters, dateQueryKey } from '../../src/lib/stock.ts';
test('stock drill-down selects the clicked stock while preserving dates', () => {
  const start = parseFilters(
    new URLSearchParams('scope=all&from=2026-09-01&to=2026-09-04'),
  );
  const next = changeFilters(
    start,
    { stockScope: 'single', symbols: ['600519'] },
    manifest,
  );
  assert.deepEqual(next.symbols, ['600519']);
  assert.equal(next.stockScope, 'single');
  assert.equal(dateQueryKey(next), dateQueryKey(start));
});
test('repeated quick-date choice does not require clearing current data', () => {
  const patch = { mode: 'range', date: '2026-08-31', endDate: '2026-09-04' };
  const first = changeFilters(base, patch, manifest);
  const repeat = changeFilters(first, patch, manifest);
  assert.equal(first.date, patch.date);
  assert.equal(first.endDate, patch.endDate);
  assert.equal(dateQueryKey(first), dateQueryKey(repeat));
});
test('individual chart series preserve missing days as gaps and keep exact ratios', () => {
  const data = [
    { ...rows[0], date: '2026-09-01', ratio: 12, first15Volume: 12, dailyVolume: 100 },
    { ...rows[0], date: '2026-09-03', ratio: 20, first15Volume: 20, dailyVolume: 100 },
    { ...rows[1], date: '2026-09-01', ratio: 8, first15Volume: 8, dailyVolume: 100 },
    { ...rows[1], date: '2026-09-02', ratio: null, first15Volume: null },
  ];
  const chart = buildTrend(
    data,
    ['2026-09-01', '2026-09-02', '2026-09-03'],
    ['000001', '600519'],
  );
  assert.equal(chart.individual, true);
  assert.deepEqual(
    chart.points.map((p) => p.s000001),
    [12, null, 20],
  );
  assert.deepEqual(
    chart.points.map((p) => p.s600519),
    [8, null, null],
  );
  assert.deepEqual(
    chart.points.map((p) => p.validCount),
    [2, 0, 1],
  );
});
test('whole-market chart computes each daily median independently without volume weighting', () => {
  const data = [
    { ...rows[0], date: '2026-09-01', ratio: 10, first15Volume: 0.1, dailyVolume: 1 },
    { ...rows[1], date: '2026-09-01', ratio: 30, first15Volume: 300, dailyVolume: 1000 },
    { ...rows[0], date: '2026-09-02', ratio: 60, first15Volume: 60, dailyVolume: 100 },
    { ...rows[1], date: '2026-09-02', ratio: null, first15Volume: null },
  ];
  const chart = buildTrend(
    data,
    ['2026-09-01', '2026-09-02', '2026-09-03'],
    null,
  );
  assert.equal(chart.individual, false);
  assert.deepEqual(
    chart.points.map((p) => p.median),
    [20, 60, null],
  );
  assert.deepEqual(
    chart.points.map((p) => p.validCount),
    [2, 1, 0],
  );
});
test('multiple-date trends omit intervening dates and median view avoids excessive overlapping lines', () => {
  const chart = buildTrend(
    [],
    ['2026-09-01', '2026-09-04'],
    ['1', '2', '3', '4', '5', '6'],
  );
  assert.equal(chart.individual, false);
  assert.deepEqual(
    chart.points.map((p) => p.date),
    ['2026-09-01', '2026-09-04'],
  );
  assert.ok(chart.points.every((p) => p.median === null));
});

test('downloaded volumes determine ratios regardless of old reconciliation metadata', () => {
  const downloaded = { ...rows[1], date: '2026-09-04', first15Volume: 20, dailyVolume: 100,
    status: 'unverified', ratio: null, referenceRatio: 99, quality: 'source_difference', dayVolumeDifference: -1 };
  const visible = filterRows([downloaded], base);
  assert.equal(visible.length, 1);
  assert.equal(visible[0].ratio, 20);
  assert.equal(visible[0].status, 'ok');
  assert.equal(filterRows([downloaded], { ...base, min: 15 }).length, 1);
  const trend = buildTrend([downloaded], ['2026-09-04'], null);
  assert.equal(trend.points[0].median, 20);
  assert.equal(trend.points[0].validCount, 1);
});
test('missing or invalid downloaded inputs never reuse a reference ratio or fabricate zero', () => {
  for (const patch of [
    { first15Volume: null }, { first15Volume: undefined }, { dailyVolume: null },
    { dailyVolume: 0 }, { first15Volume: -1 }, { first15Volume: 101 },
    { first15Volume: NaN }, { dailyVolume: Infinity }, { code: 'bad' },
  ]) {
    const row = normalizeDownloadedRow({ ...rows[0], first15Volume: 20, dailyVolume: 100,
      status: 'unverified', ratio: 20, referenceRatio: 20, ...patch });
    assert.equal(row.ratio, null);
    assert.equal(row.status, 'missing');
    assert.equal(filterRows([row], { ...base, min: 1 }).length, 0);
  }
  assert.equal(normalizeDownloadedRow({ ...rows[0], first15Volume: 0, dailyVolume: 100 }).ratio, 0);
});
test('legacy visibility links are tolerated and cannot hide downloaded data', () => {
  const f = parseFilters(new URLSearchParams('scope=all&unverified=1'));
  assert.deepEqual(parseFilters(queryParams(f)), f);
  const input = [{ ...rows[0], status: 'unverified', ratio: null, dayVolumeDifference: 1 }];
  assert.deepEqual(filterRows(input, f), filterRows(input, base));
});
test('normalization is immutable and repeated projections preserve row identity', () => {
  const old = { ...rows[1], first15Volume: 20, dailyVolume: 100, status: 'unverified', ratio: null };
  const normalized = normalizeDownloadedRow(old);
  assert.notEqual(normalized, old);
  assert.equal(old.ratio, null);
  assert.equal(normalized.ratio, 20);
  assert.equal(normalizeDownloadedRow(normalized), normalized);
});
