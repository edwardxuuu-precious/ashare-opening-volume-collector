import test from 'node:test';
import assert from 'node:assert/strict';
import { loadQuery } from '../../src/lib/query-loader.ts';
import { parseFilters } from '../../src/lib/stock.ts';

const days = Array.from({ length: 7 }, (_, index) => ({
  date: `2026-09-${String(index + 21).padStart(2, '0')}`,
  generatedAt: 'fixture-v1',
  source: 'fixture',
  methodology: 'fixture',
  scope: 'full',
  total: 1,
  rows: [
    { code: '300684', name: 'fixture', market: 'SZ', status: 'ok', ratio: 10 },
  ],
}));
const manifest = {
  generatedAt: 'fixture-v1',
  source: 'fixture',
  methodology: 'fixture',
  tradingDates: days.map((day) => day.date),
  dates: days.map((day) => ({
    date: day.date,
    file: `${day.date}.json`,
    generatedAt: day.generatedAt,
    status: 'full',
  })),
};
const filters = parseFilters(
  new URLSearchParams(
    `mode=multiple&dates=${days.map((day) => day.date).join(',')}&scope=all`,
  ),
);
const dateFromUrl = (url) => url.match(/(\d{4}-\d{2}-\d{2})\.json$/)?.[1];
const options = (fetcher, extra = {}) => ({
  dataBase: 'https://fixture.test',
  apiBase: '',
  progress: () => {},
  signal: new AbortController().signal,
  fetcher,
  ...extra,
});

test('seven-day query survives one 503 and uses at most two concurrent daily reads', async () => {
  const calls = new Map();
  let active = 0;
  let peak = 0;
  const result = await loadQuery(
    manifest,
    filters,
    options(async (url) => {
      const date = dateFromUrl(url);
      const count = (calls.get(date) ?? 0) + 1;
      calls.set(date, count);
      active++;
      peak = Math.max(peak, active);
      await new Promise((resolve) => setTimeout(resolve, 2));
      active--;
      if (date === days.at(-1).date && count === 1)
        return new Response(null, { status: 503 });
      return Response.json(days.find((day) => day.date === date));
    }),
  );
  assert.equal(result.rows.length, 7);
  assert.equal(calls.size, 7);
  assert.equal(calls.get(days.at(-1).date), 2);
  assert.equal(
    [...calls.values()].reduce((a, b) => a + b, 0),
    8,
  );
  assert.equal(peak, 2);
});

test('one failed date cancels remaining work and retains successful dates for the next query', async () => {
  const cache = new Map();
  const calls = [];
  let blockedSignal;
  await assert.rejects(
    loadQuery(
      manifest,
      filters,
      options(
        async (url, init) => {
          const date = dateFromUrl(url);
          calls.push(date);
          if (date === days[0].date) return Response.json(days[0]);
          if (date === days[1].date) {
            blockedSignal = init.signal;
            return new Promise(() => {});
          }
          return new Response(null, { status: 403 });
        },
        { cache },
      ),
    ),
    /2026-09-23 读取失败（HTTP 403）/,
  );
  assert.deepEqual(
    calls,
    days.slice(0, 3).map((day) => day.date),
  );
  assert.equal(blockedSignal.aborted, true);
  assert.equal(cache.size, 1);
  assert.equal([...cache.values()][0].date, days[0].date);
  const nextCalls = [];
  const result = await loadQuery(
    manifest,
    filters,
    options(
      async (url) => {
        const date = dateFromUrl(url);
        nextCalls.push(date);
        return Response.json(days.find((day) => day.date === date));
      },
      { cache },
    ),
  );
  assert.equal(result.rows.length, 7);
  assert.equal(nextCalls.length, 6);
  assert.equal(nextCalls.includes(days[0].date), false);
});

test('persistent daily 503 stops after three calls with date and HTTP status', async () => {
  let calls = 0;
  const oneDay = { ...manifest, dates: manifest.dates.slice(0, 1) };
  const oneFilter = { ...filters, dates: [days[0].date] };
  await assert.rejects(
    loadQuery(
      oneDay,
      oneFilter,
      options(async () => {
        calls++;
        return new Response(null, { status: 503 });
      }),
    ),
    /2026-09-21 读取失败（HTTP 503）/,
  );
  assert.equal(calls, 3);
});

test('a daily schema error is not retried or cached', async () => {
  let calls = 0;
  const cache = new Map();
  await assert.rejects(
    loadQuery(
      { ...manifest, dates: manifest.dates.slice(0, 1) },
      { ...filters, dates: [days[0].date] },
      options(
        async () => {
          calls++;
          return Response.json({ ...days[0], date: 'wrong-date' });
        },
        { cache },
      ),
    ),
    /行情数据格式不正确/,
  );
  assert.equal(calls, 1);
  assert.equal(cache.size, 0);
});

test('late JSON completion after the deadline cannot populate the daily cache', async () => {
  const cache = new Map();
  await assert.rejects(
    loadQuery(
      { ...manifest, dates: manifest.dates.slice(0, 1) },
      { ...filters, dates: [days[0].date] },
      options(
        async () => ({
          ok: true,
          status: 200,
          json: () =>
            new Promise((resolve) => setTimeout(() => resolve(days[0]), 25)),
        }),
        { cache, requestTimeoutMs: 5 },
      ),
    ),
    /响应超时/,
  );
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(cache.size, 0);
});

test('download-job creation is not retried after a 503', async () => {
  let posted = 0;
  await assert.rejects(
    loadQuery(
      { ...manifest, dates: [] },
      { ...filters, dates: [days[0].date] },
      options(
        async (_url, init) => {
          assert.equal(init.method, 'POST');
          posted++;
          return Response.json({ error: 'try later' }, { status: 503 });
        },
        { apiBase: 'https://fixture.test' },
      ),
    ),
    /try later（HTTP 503）/,
  );
  assert.equal(posted, 1);
});
