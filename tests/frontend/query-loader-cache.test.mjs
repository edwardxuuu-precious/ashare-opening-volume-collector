import test from 'node:test';
import assert from 'node:assert/strict';
import { loadQuery } from '../../src/lib/query-loader.ts';
import { parseFilters } from '../../src/lib/stock.ts';

const makeDay = (date, version = 'v1') => ({
  date,
  generatedAt: version,
  source: 'fixture',
  methodology: 'fixture',
  scope: 'full',
  total: 1,
  rows: [
    {
      code: '000001',
      name: version,
      market: 'SZ',
      ratio: 10,
      status: 'ok',
      first15Volume: 10,
      dailyVolume: 100,
    },
  ],
});
const makeManifest = (days, version = 'm1') => ({
  generatedAt: version,
  source: 'fixture',
  methodology: 'fixture',
  tradingDates: days.map((day) => day.date),
  retentionStart: '2026-03-06',
  dates: days.map((day) => ({
    date: day.date,
    generatedAt: day.generatedAt,
    file: day.date + '.json',
    status: 'full',
    total: day.total,
  })),
});
const filters = (dates) =>
  parseFilters(new URLSearchParams(`dates=${dates.join(',')}&scope=all`));
const response = (data) => new Response(JSON.stringify(data));
const opts = (cache, fetcher) => ({
  cache,
  fetcher,
  dataBase: 'https://fixture.test',
  apiBase: '',
  progress: () => {},
  signal: new AbortController().signal,
});
const dateFromUrl = (url) => url.slice(-15, -5);

test('global directory refresh fetches only the changed daily version', async () => {
  const cache = new Map(),
    calls = [];
  const days = [makeDay('2026-09-01'), makeDay('2026-09-04')];
  const options = opts(cache, async (url) => {
    calls.push(url);
    return response(days.find((day) => day.date === dateFromUrl(url)));
  });
  await loadQuery(
    makeManifest(days),
    filters(days.map((day) => day.date)),
    options,
  );
  days[1] = makeDay('2026-09-04', 'v2');
  const next = await loadQuery(
    makeManifest(days, 'm2'),
    filters(days.map((day) => day.date)),
    options,
  );
  assert.equal(calls.length, 3);
  assert.equal(
    calls.filter((url) => url.endsWith('2026-09-01.json')).length,
    1,
  );
  assert.equal(next.rows.find((row) => row.date === '2026-09-04').name, 'v2');
  assert.equal(cache.size, 2);
});

test('many versions of one full-market day retain only the current version', async () => {
  const cache = new Map();
  let current;
  const options = opts(cache, async () => response(current));
  for (let version = 0; version < 30; version++) {
    current = makeDay('2026-09-04', `v${version}`);
    const result = await loadQuery(
      makeManifest([current], `m${version}`),
      filters([current.date]),
      options,
    );
    assert.equal(result.rows[0].name, `v${version}`);
    assert.equal(cache.size, 1);
  }
});

test('126-day visible range stays complete with a 12-day cache and refreshes only the changed day', async () => {
  const days = Array.from({ length: 126 }, (_, index) => {
    const date = new Date(Date.UTC(2026, 2, 6 + index))
      .toISOString()
      .slice(0, 10);
    return makeDay(date);
  });
  const cache = new Map();
  let calls = 0;
  const options = opts(cache, async (url) => {
    calls++;
    return response(days.find((day) => day.date === dateFromUrl(url)));
  });
  const selection = filters(days.map((day) => day.date));
  const visible = await loadQuery(makeManifest(days), selection, options);
  assert.equal(visible.rows.length, 126);
  assert.equal(cache.size, 12);
  days[125] = makeDay(days[125].date, 'v2');
  const updated = await loadQuery(makeManifest(days, 'm2'), selection, options);
  assert.equal(
    calls,
    127,
    'unchanged days should reuse the result still held by the page',
  );
  assert.equal(updated.rows.length, 126);
  assert.equal(updated.rows.at(-1).name, 'v2');
  assert.equal(
    visible.rows.at(-1).name,
    'v1',
    'updating cannot mutate the visible old snapshot',
  );
  assert.equal(cache.size, 12);
});

test('manual cache clear forces a fresh read even while the old result is held', async () => {
  const cache = new Map(),
    day = makeDay('2026-09-04');
  let calls = 0;
  const options = opts(cache, async () => {
    calls++;
    return response(day);
  });
  const visible = await loadQuery(
    makeManifest([day]),
    filters([day.date]),
    options,
  );
  cache.clear();
  await loadQuery(makeManifest([day]), filters([day.date]), options);
  assert.equal(visible.rows.length, 1);
  assert.equal(calls, 2);
});

test('legacy date entries without generatedAt still invalidate on manifest version', async () => {
  const cache = new Map(),
    day = makeDay('2026-09-04'),
    manifest = makeManifest([day]);
  delete manifest.dates[0].generatedAt;
  let calls = 0;
  const options = opts(cache, async () => {
    calls++;
    return response(day);
  });
  await loadQuery(manifest, filters([day.date]), options);
  await loadQuery(
    { ...manifest, generatedAt: 'm2' },
    filters([day.date]),
    options,
  );
  assert.equal(calls, 2);
  assert.equal(cache.size, 1);
});

function hangingBody(signal, onBody) {
  return {
    ok: true,
    status: 200,
    json: () =>
      new Promise((_resolve, reject) => {
        const abort = () => reject(new DOMException('Aborted', 'AbortError'));
        signal.addEventListener('abort', abort, { once: true });
        if (signal.aborted) abort();
        onBody();
      }),
  };
}
test('a stalled JSON body times out after headers and cannot populate cache', async () => {
  const cache = new Map(),
    day = makeDay('2026-09-04');
  let signal,
    bodyStarted = false;
  await assert.rejects(
    loadQuery(makeManifest([day]), filters([day.date]), {
      ...opts(cache, async (_url, init) => {
        signal = init.signal;
        return hangingBody(signal, () => {
          bodyStarted = true;
        });
      }),
      requestTimeoutMs: 5,
    }),
    /响应超时/,
  );
  assert.equal(bodyStarted, true);
  assert.equal(signal.aborted, true);
  assert.equal(cache.size, 0);
});

test('external abort during body decoding returns AbortError without stale results', async () => {
  const cache = new Map(),
    controller = new AbortController(),
    day = makeDay('2026-09-04');
  await assert.rejects(
    loadQuery(makeManifest([day]), filters([day.date]), {
      ...opts(cache, async (_url, init) =>
        hangingBody(init.signal, () => controller.abort()),
      ),
      signal: controller.signal,
    }),
    { name: 'AbortError' },
  );
  assert.equal(cache.size, 0);
});

test('job response body uses the same bounded timeout', async () => {
  const day = makeDay('2026-09-04'),
    manifest = makeManifest([day]);
  manifest.dates = [];
  let posted = 0;
  await assert.rejects(
    loadQuery(manifest, filters([day.date]), {
      ...opts(new Map(), async (_url, init) => {
        assert.equal(init.method, 'POST');
        posted++;
        return hangingBody(init.signal, () => {});
      }),
      apiBase: 'https://fixture.test',
      requestTimeoutMs: 5,
    }),
    /响应超时/,
  );
  assert.equal(posted, 1);
});
