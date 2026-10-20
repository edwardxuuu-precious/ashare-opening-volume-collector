import test from 'node:test';
import assert from 'node:assert/strict';
import {
  sixMonthStart,
  localDate,
  dateKey,
  shanghaiToday,
} from '../../src/lib/date-window.ts';
import {
  selectedDates,
  parseFilters,
  queryableDates,
} from '../../src/lib/stock.ts';
import { loadQuery } from '../../src/lib/query-loader.ts';
const base = {
  generatedAt: '2026-09-06',
  source: 'fixture',
  methodology: 'fixture',
  dates: [],
  tradingDates: ['2026-03-06', '2026-09-01', '2026-09-04'],
  retentionStart: '2026-03-06',
};
const f = parseFilters(
  new URLSearchParams('date=2026-09-04&scope=single&symbols=000001'),
);
const day = {
  date: '2026-09-04',
  generatedAt: '2026-09-06',
  scope: 'sample',
  source: 'fixture',
  methodology: 'fixture',
  total: 1,
  rows: [{ code: '000001', name: '平安银行', ratio: 10, first15Volume: 10, dailyVolume: 100, status: 'ok' }],
};
const response = (data, status = 200) =>
  new Response(JSON.stringify(data), { status });
test('six calendar months clamp month ends and preserve local date keys', () => {
  assert.equal(sixMonthStart('2026-08-31'), '2026-02-28');
  assert.equal(sixMonthStart('2024-08-31'), '2024-02-29');
  assert.equal(sixMonthStart('2026-09-06'), '2026-03-06');
  assert.equal(dateKey(localDate('2026-09-04')), '2026-09-04');
  assert.equal(shanghaiToday(new Date('2026-09-05T18:00:00Z')), '2026-09-06');
});
test('calendar trading dates may be queried before download; holidays are rejected', () => {
  assert.equal(selectedDates(base, f)[0].status, 'not_downloaded');
  assert.throws(() => selectedDates(base, { ...f, date: '2026-09-06' }));
  assert.throws(() => selectedDates(base, { ...f, date: '2026-02-27' }));
});
test('saved history stays selectable after leaving the collection calendar', () => {
  const oldDate = '2026-02-27';
  const oldEntry = {
    date: oldDate,
    file: `${oldDate}.json`,
    status: 'partial',
  };
  const m = { ...base, dates: [oldEntry] };
  assert.equal(queryableDates(m)[0], oldDate);
  assert.deepEqual(selectedDates(m, { ...f, date: oldDate }), [oldEntry]);
  assert.deepEqual(
    selectedDates(m, {
      ...f,
      mode: 'multiple',
      dates: [oldDate, '2026-09-04'],
    }).map((entry) => entry.date),
    [oldDate, '2026-09-04'],
  );
  assert.deepEqual(
    selectedDates(m, {
      ...f,
      mode: 'range',
      date: oldDate,
      endDate: '2026-03-06',
    }).map((entry) => entry.date),
    [oldDate, '2026-03-06'],
  );
  assert.throws(
    () => selectedDates(m, { ...f, date: '2026-02-26' }),
    /不在可查询/,
  );
});
test('the per-query limit applies to records regardless of historical age', () => {
  const dates = Array.from({ length: 187 }, (_, index) => {
    const date = new Date(Date.UTC(2025, 0, 1 + index));
    return date.toISOString().slice(0, 10);
  });
  const m = { ...base, tradingDates: dates };
  assert.equal(
    selectedDates(m, { ...f, mode: 'multiple', dates: dates.slice(0, 186) })
      .length,
    186,
  );
  assert.throws(
    () => selectedDates(m, { ...f, mode: 'multiple', dates }),
    /一次最多查询 186 个交易日，请分段查询/,
  );
});
test('archived daily data loads directly without restarting collection', async () => {
  const oldDate = '2026-02-27';
  const m = {
    ...base,
    dates: [{ date: oldDate, file: `${oldDate}.json`, status: 'partial' }],
  };
  const calls = [];
  const result = await loadQuery(
    m,
    { ...f, date: oldDate },
    {
      signal: new AbortController().signal,
      dataBase: 'https://data.test',
      apiBase: '',
      progress: () => {},
      fetcher: async (url, init) => {
        calls.push([url, init.method]);
        return response({ ...day, date: oldDate });
      },
    },
  );
  assert.equal(result.date, oldDate);
  assert.equal(calls.length, 1);
  assert.ok(calls[0][0].includes(`${oldDate}.json`));
  assert.notEqual(calls[0][1], 'POST');
});
test('an uncached date reports real queue/stock progress then returns downloaded data', async () => {
  const calls = [],
    progress = [];
  let polls = 0;
  const data = await loadQuery(base, f, {
    signal: new AbortController().signal,
    dataBase: 'https://data.test',
    apiBase: 'https://api.test',
    pollMs: 0,
    progress: (p) => progress.push(p),
    fetcher: async (url, init) => {
      calls.push([url, init.method]);
      if (init.method === 'POST')
        return response({
          id: 'job1',
          status: 'queued',
          completedStocks: 0,
          totalStocks: 1,
        });
      if (url.includes('/api/query-jobs/'))
        return response({
          id: 'job1',
          status: ++polls === 1 ? 'running' : 'completed',
          completedStocks: polls === 1 ? 0 : 1,
          totalStocks: 1,
        });
      return response(day);
    },
  });
  assert.equal(data.rows[0].ratio, 10);
  assert.ok(progress.some((p) => p.stage === 'queued'));
  assert.ok(progress.some((p) => p.stage === 'collecting'));
  assert.equal(calls.filter((c) => c[1] === 'POST').length, 1);
});
test('saved dates read directly without starting collection', async () => {
  const m = {
    ...base,
    dates: [{ date: '2026-09-04', file: '2026-09-04.json', status: 'partial' }],
  };
  let posted = false;
  const result = await loadQuery(m, f, {
    signal: new AbortController().signal,
    dataBase: 'https://data.test',
    apiBase: '',
    progress: () => {},
    fetcher: async (_, init) => {
      posted ||= init.method === 'POST';
      return response(day);
    },
  });
  assert.equal(posted, false);
  assert.equal(result.date, day.date);
});
test('missing collector configuration and failed jobs stop with clear errors', async () => {
  await assert.rejects(
    loadQuery(base, f, {
      signal: new AbortController().signal,
      dataBase: '.',
      apiBase: '',
      progress: () => {},
    }),
    /尚未接通/,
  );
  await assert.rejects(
    loadQuery(base, f, {
      signal: new AbortController().signal,
      dataBase: '.',
      apiBase: 'https://api.test',
      progress: () => {},
      fetcher: async () =>
        response({ id: 'bad', status: 'failed', message: '来源不可用' }),
    }),
    /来源不可用/,
  );
});
test('aborted waits stop polling and cannot return stale results', async () => {
  const c = new AbortController();
  let requests = 0;
  await assert.rejects(
    loadQuery(base, f, {
      signal: c.signal,
      dataBase: '.',
      apiBase: 'https://api.test',
      pollMs: 0,
      progress: (p) => {
        if (p.stage === 'queued' && p.total) c.abort();
      },
      fetcher: async () => {
        requests++;
        return response({
          id: 'j',
          status: 'queued',
          completedStocks: 0,
          totalStocks: 1,
        });
      },
    }),
    { name: 'AbortError' },
  );
  assert.equal(requests, 1);
});
test('changing stock filters reuses already loaded daily records', async () => {
  const m = {
      ...base,
      dates: [
        { date: '2026-09-04', file: '2026-09-04.json', status: 'partial' },
      ],
    },
    cache = new Map();
  let calls = 0;
  const options = {
    signal: new AbortController().signal,
    dataBase: 'https://data.test',
    apiBase: '',
    cache,
    progress: () => {},
    fetcher: async () => {
      calls++;
      return response(day);
    },
  };
  await loadQuery(m, f, options);
  await loadQuery(m, { ...f, stockScope: 'all', symbols: [] }, options);
  assert.equal(calls, 1);
});
