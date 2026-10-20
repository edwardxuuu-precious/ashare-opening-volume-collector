import test from 'node:test';
import assert from 'node:assert/strict';
import { readResponse } from '../../src/lib/read-request.ts';
import { authRead } from '../../src/lib/auth-fetch.ts';

const url = 'https://fixture.test/data/manifest.json';
const consumeJson = (response) => response.json();
const options = (fetcher, extra = {}) => ({
  fetcher,
  retryBaseMs: 0,
  timeoutMs: 1000,
  ...extra,
});

for (const status of [429, 500, 502, 503, 504]) {
  test(`one transient HTTP ${status} is recovered before consuming JSON`, async () => {
    let calls = 0;
    let consumed = 0;
    const result = await readResponse(
      url,
      {},
      (response) => {
        consumed++;
        return response.json();
      },
      options(async () =>
        ++calls === 1
          ? new Response('temporary error', { status })
          : Response.json({ version: 'current' }),
      ),
    );
    assert.deepEqual(result, { version: 'current' });
    assert.equal(calls, 2);
    assert.equal(consumed, 1);
  });
}

test('a persistent server failure has a hard three-attempt limit', async () => {
  let calls = 0;
  const status = await readResponse(
    url,
    {},
    async (response) => response.status,
    options(
      async () => {
        calls++;
        return new Response('unavailable', { status: 503 });
      },
      { maxAttempts: 100 },
    ),
  );
  assert.equal(status, 503);
  assert.equal(calls, 3);
});

for (const status of [400, 401, 403, 404]) {
  test(`HTTP ${status} is returned without retry`, async () => {
    let calls = 0;
    assert.equal(
      await readResponse(
        url,
        {},
        async (response) => response.status,
        options(async () => {
          calls++;
          return new Response(null, { status });
        }),
      ),
      status,
    );
    assert.equal(calls, 1);
  });
}

test('a temporary network failure retries, while non-transport errors do not', async () => {
  let calls = 0;
  const data = await readResponse(
    url,
    {},
    consumeJson,
    options(async () => {
      if (++calls === 1) throw new TypeError('Failed to fetch');
      return Response.json({ recovered: true });
    }),
  );
  assert.equal(data.recovered, true);
  assert.equal(calls, 2);
  calls = 0;
  await assert.rejects(
    readResponse(
      url,
      {},
      consumeJson,
      options(async () => {
        calls++;
        throw Error('application failure');
      }),
    ),
    /application failure/,
  );
  assert.equal(calls, 1);
});

test('JSON syntax and consumer validation failures never retry', async () => {
  let calls = 0;
  const fetcher = async () => {
    calls++;
    return new Response('{broken-json');
  };
  await assert.rejects(readResponse(url, {}, consumeJson, options(fetcher)), {
    name: 'SyntaxError',
  });
  assert.equal(calls, 1);
  calls = 0;
  await assert.rejects(
    readResponse(
      url,
      {},
      async () => {
        throw new TypeError('manifest shape is invalid');
      },
      options(fetcher),
    ),
    /manifest shape is invalid/,
  );
  assert.equal(calls, 1);
});

test('server Retry-After is honored within the deadline', async () => {
  const started = Date.now();
  let secondAttemptAt;
  let calls = 0;
  await readResponse(
    url,
    {},
    consumeJson,
    options(async () => {
      if (++calls === 1)
        return new Response(null, {
          status: 429,
          headers: { 'Retry-After': '0.025' },
        });
      secondAttemptAt = Date.now();
      return Response.json({ ok: true });
    }),
  );
  assert.equal(calls, 2);
  assert.ok(secondAttemptAt - started >= 20);
});

test('a server wait longer than the read budget is not retried early', async () => {
  let calls = 0;
  const status = await readResponse(
    url,
    {},
    async (response) => response.status,
    options(
      async () => {
        calls++;
        return new Response(null, {
          status: 429,
          headers: { 'Retry-After': '120' },
        });
      },
      { timeoutMs: 50 },
    ),
  );
  assert.equal(status, 429);
  assert.equal(calls, 1);
});

test('abort during backoff stops before another network request', async () => {
  const controller = new AbortController();
  let calls = 0;
  const pending = readResponse(
    url,
    { signal: controller.signal },
    consumeJson,
    options(
      async () => {
        calls++;
        setTimeout(() => controller.abort(), 5);
        return new Response(null, {
          status: 503,
          headers: { 'Retry-After': '1' },
        });
      },
      { timeoutMs: 2000 },
    ),
  );
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(calls, 1);
});

test('a pre-aborted Request performs no network request', async () => {
  const controller = new AbortController();
  controller.abort();
  let calls = 0;
  await assert.rejects(
    readResponse(
      new Request(url, { signal: controller.signal }),
      {},
      consumeJson,
      options(async () => {
        calls++;
        return Response.json({});
      }),
    ),
    { name: 'AbortError' },
  );
  assert.equal(calls, 0);
});

for (const blockedStage of ['headers', 'body']) {
  test(`timeout stops waiting when ${blockedStage} ignore AbortSignal`, async () => {
    let receivedSignal;
    let calls = 0;
    const fetcher = async (_url, init) => {
      calls++;
      receivedSignal = init.signal;
      if (blockedStage === 'headers') return new Promise(() => {});
      return { status: 200, json: () => new Promise(() => {}) };
    };
    await assert.rejects(
      readResponse(url, {}, consumeJson, options(fetcher, { timeoutMs: 5 })),
      { name: 'TimeoutError', message: '数据服务响应超时，请重试。' },
    );
    assert.equal(calls, 1);
    assert.equal(receivedSignal.aborted, true);
  });
}

test('external cancellation stops a body reader that ignores AbortSignal', async () => {
  const controller = new AbortController();
  const pending = readResponse(
    url,
    { signal: controller.signal },
    consumeJson,
    options(async () => ({
      status: 200,
      json: () => {
        queueMicrotask(() => controller.abort());
        return new Promise(() => {});
      },
    })),
  );
  await assert.rejects(pending, { name: 'AbortError' });
});

test('retries and body reading share one deadline', async () => {
  let calls = 0;
  await assert.rejects(
    readResponse(
      url,
      {},
      consumeJson,
      options(
        async () => {
          if (++calls === 1)
            return new Response(null, {
              status: 503,
              headers: { 'Retry-After': '0.015' },
            });
          return { status: 200, json: () => new Promise(() => {}) };
        },
        { timeoutMs: 30 },
      ),
    ),
    { name: 'TimeoutError' },
  );
  assert.equal(calls, 2);
});

for (const failure of ['http', 'network']) {
  test(`POST ${failure} failure is never automatically retried`, async () => {
    let calls = 0;
    const fetcher = async () => {
      calls++;
      if (failure === 'network') throw new TypeError('Failed to fetch');
      return new Response(null, { status: 503 });
    };
    const pending = readResponse(
      url,
      { method: 'POST', body: '{}' },
      async (response) => response.status,
      options(fetcher),
    );
    if (failure === 'network') await assert.rejects(pending, /Failed to fetch/);
    else assert.equal(await pending, 503);
    assert.equal(calls, 1);
  });
}

test('authRead adds credentials and dispatches one expiration event without retrying 401', async (t) => {
  const windowTarget = new EventTarget();
  let expired = 0;
  windowTarget.addEventListener('stock-session-expired', () => expired++);
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
  Object.defineProperty(globalThis, 'window', {
    value: windowTarget,
    configurable: true,
  });
  t.after(() => {
    if (previousWindow)
      Object.defineProperty(globalThis, 'window', previousWindow);
    else delete globalThis.window;
  });
  const fetchMock = t.mock.method(globalThis, 'fetch', async (_input, init) => {
    assert.equal(init.credentials, 'include');
    return new Response(null, { status: 401 });
  });
  const status = await authRead(url, {}, async (response) => response.status, {
    retryBaseMs: 0,
  });
  assert.equal(status, 401);
  assert.equal(fetchMock.mock.callCount(), 1);
  assert.equal(expired, 1);
});

test('authRead uses one retry owner for a persistent GET failure', async (t) => {
  const fetchMock = t.mock.method(
    globalThis,
    'fetch',
    async () => new Response(null, { status: 503 }),
  );
  const status = await authRead(url, {}, async (response) => response.status, {
    retryBaseMs: 0,
  });
  assert.equal(status, 503);
  assert.equal(fetchMock.mock.callCount(), 3);
});
