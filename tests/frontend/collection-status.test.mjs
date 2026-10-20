import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { compileFunction } from 'node:vm';
import ts from 'typescript';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const code = ts.transpileModule(
  readFileSync(
    new URL('../../src/components/collection-status.tsx', import.meta.url),
    'utf8',
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const mod = { exports: {} };
compileFunction(code, ['require', 'module', 'exports'])(
  createRequire(
    new URL('../../src/components/collection-status.tsx', import.meta.url),
  ),
  mod,
  mod.exports,
);
const {
  parseCollectionState,
  collectionQueryMessage,
  startCollectionPolling,
  CollectionStatus,
} = mod.exports;
const status = {
  id: 'run1',
  state: 'running',
  completedStocks: 12,
  totalStocks: 5556,
  latestDate: '2026-09-04',
  updatedAt: '2026-09-06T20:00:00+08:00',
  message: '正在处理',
};
const statusText = (value) =>
  renderToStaticMarkup(
    createElement(CollectionStatus, { status: value }),
  ).replace(/<[^>]*>/g, '');
const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status });
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('compact header keeps publication date and sync time distinct and exposes active problems', () => {
  const render = (value) =>
    renderToStaticMarkup(
      createElement(CollectionStatus, {
        latestDate: '2026-09-04',
        status: value,
      }),
    );
  const completed = {
    ...status,
    state: 'completed',
    phase: 'close',
    dataComplete: true,
    priceDataComplete: true,
    publicationCommitted: true,
    lastSuccessfulUpdate: '2026-09-06T09:10:00Z',
  };
  const html = render(completed);
  assert.match(html, /行情截至 <time[^>]*>2026-09-04<\/time>/);
  assert.match(html, /最后成功同步：<time[^>]*>2026\/09\/06 17:10<\/time>/);
  assert.equal(
    (html.match(/sync-state /g) || []).length,
    1,
    'healthy label occurs only in closed details',
  );
  assert.equal(
    (render({ ...completed, state: 'failed' }).match(/sync-state /g) || [])
      .length,
    2,
  );
  assert.match(render(null), /状态暂不可用/);
});
async function until(predicate) {
  // Parallel TypeScript test compilation can briefly starve this local timer.
  // Keep the condition assertion, but do not confuse scheduler latency with failure.
  const deadline = Date.now() + 5000;
  while (!predicate()) {
    if (Date.now() > deadline) throw Error('poll condition not reached');
    await wait(2);
  }
}

test('unknown or invalid status cannot become visible progress', () => {
  assert.equal(parseCollectionState({ ...status, state: 'unknown' }), null);
  assert.equal(
    parseCollectionState({ ...status, completedStocks: 5557 }),
    null,
  );
  assert.equal(parseCollectionState({ ...status, totalStocks: -1 }), null);
  assert.equal(parseCollectionState({ ...status, completedStocks: 1.5 }), null);
  assert.deepEqual(parseCollectionState(status), status);
});

test('daily repair fields are optional, reject invalid counts and preserve unknown versus zero', () => {
  assert.deepEqual(parseCollectionState(status), status);
  const future = {
    ...status,
    pendingCount: 700056,
    lastSuccessfulUpdate: '2026-09-06T09:10:00Z',
    phase: 'daily',
  };
  assert.deepEqual(parseCollectionState(future), future);
  const empty = { ...status, pendingCount: null, lastSuccessfulUpdate: null };
  assert.deepEqual(parseCollectionState(empty), empty);
  for (const pendingCount of [-1, 1.5, '2', true, Number.MAX_SAFE_INTEGER + 1])
    assert.equal(parseCollectionState({ ...status, pendingCount }), null);
  for (const lastSuccessfulUpdate of ['', 'not-a-date', 123, true])
    assert.equal(
      parseCollectionState({ ...status, lastSuccessfulUpdate }),
      null,
    );
  assert.equal(
    parseCollectionState({ ...status, pendingCount: 0 }).pendingCount,
    0,
  );
});

test('status shows only the successful sync time and current state', () => {
  const text = statusText({
    ...status,
    phase: 'daily',
    pendingCount: 700056,
    calculationPolicy: 'download_only',
    lastSuccessfulUpdate: '2026-09-06T09:10:00Z',
    historicalPendingCount: 3553,
    speedDegraded: true,
    nextRetryAt: '2026-09-06T09:20:00Z',
  });
  assert.equal(text, '最后同步：2026/09/06 17:10同步中');
  assert.equal(statusText(status), '最后同步：暂无记录同步中');
  assert.equal(statusText(null), '最后同步：暂无记录状态暂不可用');
  assert.equal(
    statusText({ ...status, state: 'paused' }),
    '最后同步：暂无记录同步已暂停',
  );
  assert.equal(
    statusText({ ...status, state: 'failed' }),
    '最后同步：暂无记录同步失败',
  );
});

test('no-op completion retains the successful publication date instead of the heartbeat date', () => {
  const text = statusText({
    ...status,
    state: 'completed',
    phase: 'daily',
    pendingCount: 0,
    calculationPolicy: 'download_only',
    lastSuccessfulUpdate: '2026-09-04T09:10:00Z',
    message: '当日无需更新，已保留上一轮发布结果。',
  });
  assert.equal(text, '最后同步：2026/09/04 17:10本轮同步结束');
});

test('deferred retry, failed work and manual pause have distinct visible states', () => {
  const deferred = {
    ...status,
    state: 'paused',
    phase: 'opening',
    outcome: 'incomplete_checkpointed',
    exitReason: 'source_retry_not_due',
    openingCachedCount: 5550,
    openingNoTradeCount: 0,
    openingMissingCount: 6,
    openingUnprocessedCount: 0,
    openingRetryableCount: 6,
    openingComplete: false,
    nextRetryAt: '2026-09-06T20:30:00+08:00',
  };
  assert.deepEqual(parseCollectionState(deferred), deferred);
  assert.match(statusText(deferred), /等待补采/);
  assert.match(
    statusText({ ...deferred, outcome: 'failed', exitReason: 'failed' }),
    /同步失败/,
  );
  assert.match(statusText({ ...status, state: 'paused' }), /同步已暂停/);
  assert.match(
    statusText({ ...deferred, exitReason: 'source_throttled' }),
    /等待数据源恢复/,
  );
});

test('opening completion and atomic price completeness are required before success labels', () => {
  const opening = {
    ...status,
    state: 'completed',
    phase: 'opening',
    dataComplete: false,
    openingCachedCount: 5550,
    openingNoTradeCount: 6,
    openingMissingCount: 0,
    openingUnprocessedCount: 0,
    openingRetryableCount: 0,
    openingComplete: true,
  };
  assert.match(statusText(opening), /等待收盘数据/);
  assert.match(
    statusText({ ...opening, openingComplete: false }),
    /开盘数据待补齐/,
  );
  assert.match(
    statusText({
      ...status,
      state: 'completed',
      phase: 'close',
      dataComplete: true,
      priceDataComplete: false,
      publicationCommitted: true,
    }),
    /部分数据待补齐/,
  );
});

test('opening counters and lifecycle markers reject malformed or contradictory values', () => {
  for (const field of [
    'openingNoTradeCount',
    'openingMissingCount',
    'openingUnprocessedCount',
    'openingRetryableCount',
  ])
    for (const value of [-1, 1.5, '2', true, 5557])
      assert.equal(parseCollectionState({ ...status, [field]: value }), null);
  assert.equal(parseCollectionState({ ...status, outcome: 3 }), null);
  assert.equal(parseCollectionState({ ...status, exitReason: false }), null);
  assert.equal(
    parseCollectionState({ ...status, priceDataComplete: 'true' }),
    null,
  );
  assert.equal(
    parseCollectionState({
      ...status,
      phase: 'opening',
      openingCachedCount: 5550,
      openingNoTradeCount: 6,
      openingMissingCount: 1,
    }),
    null,
  );
});

test('published date and target date remain separate and an absent publication stays unknown', () => {
  const current = {
    ...status,
    phase: 'opening',
    targetDate: '2026-09-08',
    latestPublishedDate: '2026-09-04',
    latestPublishedAt: '2026-09-04T16:00:00+08:00',
  };
  assert.deepEqual(parseCollectionState(current), current);
  const empty = {
    ...current,
    latestPublishedDate: null,
    latestPublishedAt: null,
  };
  assert.deepEqual(parseCollectionState(empty), empty);
  assert.equal(
    parseCollectionState({ ...current, latestPublishedDate: 'not-a-date' }),
    null,
  );
  assert.equal(
    parseCollectionState({ ...current, latestPublishedAt: 'not-a-time' }),
    null,
  );
  assert.doesNotMatch(statusText(current), /2026\/09\/08/);
});

test('query messages distinguish active, paused and finished collection without masking transport errors', () => {
  const error = '2026-09-01 尚未下载。自动下载服务尚未接通';
  assert.match(collectionQueryMessage(error, status), /后台正在采集/);
  assert.match(
    collectionQueryMessage(error, { ...status, state: 'paused' }),
    /已暂停/,
  );
  assert.match(
    collectionQueryMessage(error, { ...status, state: 'completed' }),
    /可能不在本轮范围/,
  );
  assert.equal(collectionQueryMessage('读取失败', status), '读取失败');
  assert.equal(collectionQueryMessage(error, null), error);
});

test('missing status is silent and only a changed manifest triggers refresh', async () => {
  let count = 0;
  const statuses = [],
    generations = [],
    methods = [];
  const stop = startCollectionPolling({
    dataBase: 'https://data.test',
    pollMs: 3,
    manifestPollMs: 3,
    fetcher: async (url, init) => {
      methods.push(init.method);
      if (url.endsWith('collection-status.json')) return json({}, 404);
      count++;
      return json({ dates: [], generatedAt: count <= 2 ? 'a' : 'b' });
    },
    onStatus: (value) => statuses.push(value),
    onManifest: (value) => generations.push(value.generatedAt),
  });
  try {
    await until(() => count >= 4);
    assert.deepEqual(generations, ['a', 'b']);
    assert.ok(statuses.every((value) => value === null));
    assert.ok(methods.every((value) => value === undefined));
  } finally {
    stop();
  }
});

test('steady polling keeps status fresh without downloading the full manifest every tick', { timeout: 3000 }, async (t) => {
  // Windows may schedule five 2ms timers after the 50ms manifest interval.
  // Control elapsed time so this checks the request policy, not host latency.
  let clock = 1000;
  t.mock.method(Date, 'now', () => clock);
  let statusCalls = 0,
    manifestCalls = 0;
  const stop = startCollectionPolling({
    dataBase: '.',
    pollMs: 2,
    manifestPollMs: 50,
    initialManifestGeneration: 'v1',
    initialLatestDate: '2026-09-07',
    fetcher: async (url) => {
      if (url.endsWith('collection-status.json')) {
        statusCalls++;
        return json({ ...status, latestDate: '2026-09-07' });
      }
      manifestCalls++;
      return json({ dates: [], generatedAt: 'v1' });
    },
    onStatus: () => {},
    onManifest: () => {},
  });
  try {
    await until(() => statusCalls >= 5);
    assert.equal(manifestCalls, 0);
    clock += 51;
    await until(() => manifestCalls >= 1);
    assert.equal(manifestCalls, 1);
  } finally {
    stop();
  }
});

test('terminal status uses a slower idle interval', async () => {
  let statusCalls = 0;
  const stop = startCollectionPolling({
    dataBase: '.',
    pollMs: 2,
    idlePollMs: 30,
    manifestPollMs: 60_000,
    initialManifestGeneration: 'v1',
    initialLatestDate: '2026-09-07',
    fetcher: async (url) => {
      if (url.endsWith('collection-status.json')) {
        statusCalls++;
        return json({ ...status, state: 'completed' });
      }
      return json({ dates: [], generatedAt: 'v1' });
    },
    onStatus: () => {},
    onManifest: () => {},
  });
  try {
    await until(() => statusCalls === 1);
    await wait(12);
    assert.equal(statusCalls, 1);
    await until(() => statusCalls === 2);
  } finally {
    stop();
  }
});

test('hidden tabs make no authenticated polling requests and resume when visible', async () => {
  let visible = false,
    calls = 0;
  const stop = startCollectionPolling({
    dataBase: '.',
    pollMs: 2,
    idlePollMs: 2,
    isVisible: () => visible,
    fetcher: async () => {
      calls++;
      return json({}, 404);
    },
    onStatus: () => {},
    onManifest: () => {},
  });
  try {
    await wait(12);
    assert.equal(calls, 0);
    visible = true;
    await until(() => calls > 0);
  } finally {
    stop();
  }
});

test('a newly committed target date bypasses the slow manifest interval once', async () => {
  let manifestCalls = 0;
  const generations = [];
  const stop = startCollectionPolling({
    dataBase: '.',
    pollMs: 2,
    manifestPollMs: 60_000,
    initialManifestGeneration: 'v1',
    initialLatestDate: '2026-09-07',
    fetcher: async (url) => {
      if (url.endsWith('collection-status.json'))
        return json({
          ...status,
          phase: 'close',
          targetDate: '2026-09-08',
          latestDate: '2026-09-08',
          publicationCommitted: true,
        });
      manifestCalls++;
      return json({
        dates: [{ date: '2026-09-08' }],
        generatedAt: 'v2',
      });
    },
    onStatus: () => {},
    onManifest: (value) => generations.push(value.generatedAt),
  });
  try {
    await until(() => generations.length === 1);
    await wait(10);
    assert.equal(manifestCalls, 1);
    assert.deepEqual(generations, ['v2']);
  } finally {
    stop();
  }
});

test('401 ends polling and lets authFetch own logout', async () => {
  let calls = 0;
  const statuses = [];
  const stop = startCollectionPolling({
    dataBase: '.',
    pollMs: 2,
    fetcher: async () => {
      calls++;
      return json({}, 401);
    },
    onStatus: (value) => statuses.push(value),
    onManifest: () => assert.fail('must not update'),
  });
  await wait(20);
  stop();
  assert.equal(calls, 1);
  assert.deepEqual(statuses, []);
});

test('transient 503 recovers within the same status tick', async () => {
  let calls = 0;
  const statuses = [];
  const stop = startCollectionPolling({
    dataBase: '.',
    idlePollMs: 60_000,
    initialManifestGeneration: 'v1',
    initialLatestDate: '2026-09-07',
    fetcher: async () => {
      calls++;
      return calls === 1
        ? json({}, 503)
        : json({ ...status, state: 'completed' });
    },
    onStatus: (value) => statuses.push(value),
    onManifest: () => assert.fail('must not read an unchanged manifest'),
  });
  try {
    await until(() => statuses.length === 1);
    assert.equal(calls, 2);
    assert.equal(statuses[0].state, 'completed');
  } finally {
    stop();
  }
});

test('stopping polling during retry backoff prevents more requests', async () => {
  let calls = 0;
  const statuses = [];
  const stop = startCollectionPolling({
    dataBase: '.',
    fetcher: async () => {
      calls++;
      return json({}, 503);
    },
    onStatus: (value) => statuses.push(value),
    onManifest: () => assert.fail('must not update after stopping'),
  });
  await until(() => calls === 1);
  stop();
  await wait(550);
  assert.equal(calls, 1);
  assert.deepEqual(statuses, []);
});

test('unmount aborts in-flight response body and prevents callbacks or more requests', async () => {
  let calls = 0,
    signal;
  const callbacks = [];
  const stop = startCollectionPolling({
    dataBase: '.',
    pollMs: 2,
    timeoutMs: 50,
    fetcher: async (_url, init) => {
      calls++;
      signal = init.signal;
      return {
        status: 200,
        ok: true,
        json: () =>
          new Promise((_resolve, reject) =>
            signal.addEventListener(
              'abort',
              () => reject(new DOMException('aborted', 'AbortError')),
              { once: true },
            ),
          ),
      };
    },
    onStatus: (value) => callbacks.push(value),
    onManifest: (value) => callbacks.push(value),
  });
  await wait(2);
  stop();
  await wait(15);
  assert.equal(signal.aborted, true);
  assert.equal(calls, 1);
  assert.deepEqual(callbacks, []);
});

test('response body timeout is aborted and stale running status is not asserted', async () => {
  let signal;
  const statuses = [];
  const stop = startCollectionPolling({
    dataBase: '.',
    pollMs: 1000,
    timeoutMs: 5,
    fetcher: async (url, init) => {
      if (url.endsWith('manifest.json')) return json({}, 404);
      signal = init.signal;
      return {
        status: 200,
        ok: true,
        json: () =>
          new Promise((_resolve, reject) =>
            signal.addEventListener(
              'abort',
              () => reject(new DOMException('aborted', 'AbortError')),
              { once: true },
            ),
          ),
      };
    },
    onStatus: (value) => statuses.push(value),
    onManifest: () => assert.fail('invalid manifest'),
  });
  try {
    await until(() => statuses.length > 0);
    assert.deepEqual(statuses, [null]);
    assert.equal(signal.aborted, true);
  } finally {
    stop();
  }
});

test('backend diagnostics do not surface in the compact status bar', () => {
  for (const message of [
    '还有 32 条待核验，分钟合计尚未核对一致。',
    '仅复核既有历史问题记录，暂停新增当日数据',
    '目标交易日数据已补齐 目标交易日 2026-09-08。',
  ]) {
    assert.equal(
      statusText({ ...status, message }),
      '最后同步：暂无记录同步中',
    );
  }
});

test('pending counts stay in the data contract without adding status-bar detail', () => {
  for (const calculationPolicy of [
    undefined,
    'strict',
    'unknown',
    'download_only',
  ]) {
    for (const pendingCount of [0, 66000]) {
      const value = { ...status, pendingCount, calculationPolicy };
      assert.deepEqual(parseCollectionState(value), value);
      assert.equal(statusText(value), '最后同步：暂无记录同步中');
    }
  }
  assert.equal(
    parseCollectionState({ ...status, calculationPolicy: true }),
    null,
  );
});

test('phased refresh fields remain optional and preserve zero versus unknown', () => {
  const current = {
    ...status,
    phase: 'close',
    targetDate: '2026-09-07',
    unprocessedCount: 0,
    calculableCount: 5550,
    retryableCount: 6,
    noTradeCount: 0,
    historicalPendingCount: 700056,
    openingCachedCount: 5556,
    nextRetryAt: '2026-09-07T09:05:00Z',
    firstPassCompletedAt: '2026-09-07T09:00:00Z',
    fullyPublishedAt: null,
    firstPassComplete: true,
    dataComplete: false,
    openingComplete: true,
    speedDegraded: false,
  };
  assert.deepEqual(parseCollectionState(current), current);
  const unknown = { ...status, calculableCount: null, retryableCount: null };
  assert.deepEqual(parseCollectionState(unknown), unknown);
  const html = renderToStaticMarkup(
    createElement(CollectionStatus, { status: unknown }),
  );
  assert.doesNotMatch(html, /可计算 0|待重试 0|数据已补齐/);
  for (const field of [
    'unprocessedCount',
    'calculableCount',
    'retryableCount',
    'noTradeCount',
    'openingCachedCount',
  ]) {
    for (const value of [-1, 1.5, '2', true, 5557])
      assert.equal(
        parseCollectionState({ ...status, [field]: value }),
        null,
        `${field}=${value}`,
      );
  }
  for (const value of [-1, 1.5, '2', true])
    assert.equal(
      parseCollectionState({ ...status, historicalPendingCount: value }),
      null,
    );
  for (const field of [
    'nextRetryAt',
    'firstPassCompletedAt',
    'fullyPublishedAt',
  ]) {
    for (const value of ['', 'invalid', 123, true])
      assert.equal(parseCollectionState({ ...status, [field]: value }), null);
  }
  for (const field of [
    'firstPassComplete',
    'dataComplete',
    'openingComplete',
    'speedDegraded',
  ]) {
    for (const value of ['true', 1, null])
      assert.equal(parseCollectionState({ ...status, [field]: value }), null);
  }
  for (const value of ['20260907', '', 20260907, null])
    assert.equal(parseCollectionState({ ...status, targetDate: value }), null);
});

test('a completed first pass with missing records is not reported as synced', () => {
  const incomplete = {
    ...status,
    state: 'completed',
    phase: 'close',
    completedStocks: 5556,
    unprocessedCount: 0,
    calculableCount: 5550,
    noTradeCount: 2,
    retryableCount: 4,
    historicalPendingCount: 786,
    firstPassComplete: true,
    dataComplete: false,
    firstPassCompletedAt: '2026-09-07T09:00:00Z',
    nextRetryAt: '2026-09-07T09:05:00Z',
    message: '新浪尚未返回 4 只股票的目标日成交量。',
  };
  assert.equal(statusText(incomplete), '最后同步：暂无记录部分数据待补齐');
  assert.equal(
    statusText({ ...incomplete, dataComplete: undefined }),
    '最后同步：暂无记录本轮同步结束',
  );
  assert.equal(
    statusText({
      ...incomplete,
      dataComplete: true,
      publicationCommitted: false,
    }),
    '最后同步：暂无记录等待发布',
  );
});

test('opening precollection keeps the last published sync date without claiming daily completion', () => {
  const opening = {
    ...status,
    phase: 'opening',
    targetDate: '2026-09-08',
    openingCachedCount: 2843,
    openingComplete: false,
    dataComplete: true,
    firstPassComplete: true,
    fullyPublishedAt: '2026-09-07T09:00:00Z',
    lastSuccessfulUpdate: '2026-09-07T09:00:00Z',
    message: '全天数据已发布。',
  };
  assert.equal(statusText(opening), '最后同步：2026/09/07 17:00同步中');
  assert.equal(
    statusText({ ...opening, state: 'completed', openingComplete: true }),
    '最后同步：2026/09/07 17:00等待收盘数据',
  );
  assert.match(
    collectionQueryMessage('2026-09-08 尚未下载。', opening),
    /上午仅预采并缓存 09:45/,
  );
  assert.doesNotMatch(
    collectionQueryMessage('2026-09-01 尚未下载。', opening),
    /上午仅预采/,
  );
});

test('completed data uses the successful publication timestamp and hides collection details', () => {
  const complete = {
    ...status,
    state: 'completed',
    phase: 'catchup',
    latestDate: '2026-09-08',
    targetDate: '2026-09-07',
    firstPassComplete: true,
    dataComplete: true,
    fullyPublishedAt: '2026-09-08T00:30:00Z',
    publicationCommitted: true,
    speedDegraded: true,
  };
  assert.equal(statusText(complete), '最后同步：2026/09/08 08:30已同步');
  assert.equal(
    statusText({ ...complete, lastSuccessfulUpdate: '2026-09-08T09:54:00Z' }),
    '最后同步：2026/09/08 17:54已同步',
  );
});
