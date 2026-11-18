/* oxlint-disable typescript/no-require-imports -- executable CommonJS browser check */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const zlib = require('node:zlib');
const os = require('node:os');
const { performance } = require('node:perf_hooks');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '../..');
const OUT = process.env.OUTPUT_DIR;
const VERIFY = process.argv.includes('--verify-only');
const VARIANTS = (process.env.VARIANTS || (VERIFY ? 'candidate' : 'baseline,candidate')).split(',');
const SCENARIOS = (process.env.SCENARIOS || 'session,password').split(',');
const CONTRACT = process.env.ASSERT_VARIANT || 'candidate';
const DRAIN_AUTH401 = process.env.DIAG_DRAIN_AUTH401 === '1';
const ROUNDS = VERIFY ? 1 : 5;
const HOST_AT_START = { loadAverage: os.loadavg(), cpuCount: os.cpus().length };
const API = 'https://api.stock.iteready.com';
const DELAY = Number(process.env.API_DELAY_MS || 250);
const DATE = '2026-10-09';
const NAMES = ['锂铝鑫甬示例', '锂铝鑫甬复核'];
const QUERY = '/?date=' + DATE + '&scope=all';
const NETWORK = { offline: false, latency: 150, downloadThroughput: 200000,
  uploadThroughput: 100000, connectionType: 'cellular4g' };
const DIRS = {
  baseline: path.resolve(process.env.BASELINE_DIR || path.join(ROOT, 'outputs/domestic-load-20261106/baseline/client')),
  candidate: path.resolve(process.env.CANDIDATE_DIR || path.join(ROOT, 'dist/client')),
};
assert.ok(VARIANTS.every(v => v in DIRS), 'VARIANTS must contain baseline and/or candidate');
assert.ok(SCENARIOS.every(s => ['session', 'password'].includes(s)));
assert.ok(!DRAIN_AUTH401 || VERIFY, 'diagnostic body draining is only permitted in verify-only runs');
assert.ok(Number.isFinite(DELAY) && DELAY >= 0 && DELAY <= 5000);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function serve(root) {
  assert.ok(fs.existsSync(path.join(root, 'index.html')), 'missing static build: ' + root);
  const cache = new Map();
  const mime = { '.html': 'text/html', '.rsc': 'text/x-component', '.js': 'text/javascript',
    '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };
  const server = http.createServer((req, res) => {
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    let file = path.resolve(root, '.' + pathname);
    if (!file.startsWith(root + path.sep) && file !== root) { res.writeHead(403).end(); return; }
    if (pathname.endsWith('/')) file = path.join(file, 'index.html');
    if (!fs.existsSync(file) && !path.extname(file)) file += '.html';
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404).end(); return; }
    const ext = path.extname(file);
    const encoding = /\.(html|rsc|js|css|json|svg)$/.test(file)
      ? (/\bbr\b/.test(req.headers['accept-encoding'] || '') ? 'br'
        : /\bgzip\b/.test(req.headers['accept-encoding'] || '') ? 'gzip' : '') : '';
    const key = file + ':' + encoding;
    if (!cache.has(key)) {
      const body = fs.readFileSync(file);
      cache.set(key, encoding === 'br' ? zlib.brotliCompressSync(body)
        : encoding === 'gzip' ? zlib.gzipSync(body) : body);
    }
    const body = cache.get(key);
    res.writeHead(200, { 'content-type': mime[ext] || 'application/octet-stream',
      'content-length': body.length, 'cache-control': 'no-store',
      ...(encoding ? { 'content-encoding': encoding, vary: 'Accept-Encoding' } : {}) });
    res.end(body);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { origin: 'http://127.0.0.1:' + server.address().port,
    close: () => new Promise(resolve => server.close(resolve)) };
}

async function sample(browser, site, variant, scenario, round) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, serviceWorkers: 'block' });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  const errors = [], blocked = [], calls = [], resources = new Map();
  const requests = new Map(), lifecycle = [];
  page.on('request', req => requests.set(req, { path: new URL(req.url()).pathname,
    type: req.resourceType(), method: req.method(), startedAt: performance.now(), state: 'pending' }));
  page.on('requestfinished', req => Object.assign(requests.get(req) || {}, { state: 'finished', endedAt: performance.now() }));
  page.on('requestfailed', req => Object.assign(requests.get(req) || {}, { state: 'failed',
    failure: req.failure()?.errorText, endedAt: performance.now() }));
  page.on('framenavigated', frame => { if (frame === page.mainFrame()) lifecycle.push({ event: 'framenavigated', path: new URL(frame.url()).pathname }); });
  let authenticated = scenario === 'session', phase = 0, documents = 0;
  const counts = { me: 0, login: 0, logout: 0, day: 0 };
  const row = () => ({ code: '600000', name: NAMES[phase], market: 'SH', date: DATE,
    status: 'ok', first15Volume: 20, dailyVolume: 100, ratio: 20, priceStatus: 'missing' });
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', response => {
    const p = new URL(response.url()).pathname;
    if (response.status() >= 400 && !(p === '/auth/me' && response.status() === 401))
      errors.push('HTTP ' + response.status() + ' ' + p);
  });
  await context.addInitScript(drain => {
    window.__loadDocumentMarker = Math.random().toString(36);
    window.__auth401Drained = 0;
    if (drain) {
      const original = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const response = await original(...args);
        const input = args[0] instanceof Request ? args[0].url : String(args[0]);
        if (new URL(input, location.href).pathname === '/auth/me' && response.status === 401) {
          await response.arrayBuffer();
          window.__auth401Drained++;
        }
        return response;
      };
    }
  }, DRAIN_AUTH401);
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), method = req.method();
    const local = url.origin === site.origin;
    const reject = () => { blocked.push(method + ' ' + url.origin + url.pathname); return route.abort(); };
    if (!local && url.origin !== API) return reject();
    const p = url.pathname;
    if (p.startsWith('/auth/') || p.startsWith('/data/')) {
      const headers = { 'access-control-allow-origin': site.origin,
        'access-control-allow-credentials': 'true', 'access-control-allow-methods': 'GET, POST, OPTIONS',
        'access-control-allow-headers': 'content-type', 'cache-control': 'no-store' };
      const json = async (value, status = 200) => {
        calls.push({ method, path: p, status });
        await sleep(DELAY);
        return route.fulfill({ status, json: value, headers });
      };
      if (method === 'OPTIONS' && ['/auth/login', '/auth/logout'].includes(p))
        return route.fulfill({ status: 204, headers });
      if (method === 'GET' && p === '/auth/me') {
        counts.me++; return json(authenticated ? { username: 'load-fixture' } : { error: 'fixture' }, authenticated ? 200 : 401);
      }
      if (method === 'POST' && p === '/auth/login') {
        counts.login++; authenticated = true; return json({ username: 'load-fixture' });
      }
      if (method === 'POST' && p === '/auth/logout') {
        counts.logout++; authenticated = false; return json({ ok: true });
      }
      if (method !== 'GET' || !authenticated) return reject();
      if (p === '/data/stocks.json') return json({ rows: [{ code: '600000', name: NAMES[phase] }] });
      if (p === '/data/manifest.json') return json({ generatedAt: 'fixture', source: 'fixture',
        methodology: 'fixture', universeTotal: 1, tradingDates: [DATE], retentionStart: DATE,
        dates: [{ date: DATE, file: DATE + '.json', status: 'full' }] });
      if (p === '/data/collection-status.json') return json({ id: 'fixture', state: 'completed', phase: 'close',
        completedStocks: 1, totalStocks: 1, latestDate: DATE, updatedAt: '2026-10-10T09:00:00+08:00',
        lastSuccessfulUpdate: '2026-10-10T08:17:00+08:00', dataComplete: true,
        priceDataComplete: true, publicationCommitted: true, message: 'fixture' });
      if (p === '/data/' + DATE + '.json') {
        counts.day++; return json({ date: DATE, generatedAt: 'fixture', source: 'fixture',
          methodology: 'fixture', scope: 'full', total: 1, rows: [row()] });
      }
      return reject();
    }
    if (!local || method !== 'GET') return reject();
    if (req.resourceType() === 'document') documents++;
    return route.continue();
  });
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Page.enable');
  await cdp.send('Page.setLifecycleEventsEnabled', { enabled: true });
  cdp.on('Page.lifecycleEvent', e => lifecycle.push({ event: e.name, timestamp: e.timestamp }));
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
  await cdp.send('Network.emulateNetworkConditions', NETWORK);
  cdp.on('Network.requestWillBeSent', e => {
    const u = new URL(e.request.url);
    if (u.origin === site.origin) resources.set(e.requestId, { path: u.pathname,
      font: u.pathname.startsWith('/fonts/') || e.type === 'Font', transferBytes: null });
  });
  cdp.on('Network.loadingFinished', e => {
    const resource = resources.get(e.requestId);
    if (resource) Object.assign(resource, { transferBytes: e.encodedDataLength, state: 'finished' });
  });
  cdp.on('Network.loadingFailed', e => {
    const resource = resources.get(e.requestId);
    if (resource) Object.assign(resource, { state: 'failed', failure: e.errorText, canceled: !!e.canceled });
  });
  const submit = async () => {
    await page.locator('input[name="username"]').fill('load-fixture');
    await page.locator('input[name="password"]').fill('fixture-only-password');
    await page.locator('.auth-form-panel button[type="submit"]').click();
  };
  const result = page.locator('.results tbody tr').first();
  const assertResult = async name => {
    await result.waitFor();
    await page.waitForFunction(name => document.querySelector('.results tbody tr')?.textContent.includes(name), name);
    assert.match(await result.innerText(), /20/);
  };
  const output = { variant, scenario, round, ok: false };
  try {
    const entryAt = performance.now();
    await page.goto(site.origin + QUERY, { waitUntil: 'domcontentloaded' });
    let startedAt = entryAt, beforeLoginDocument;
    if (scenario === 'password') {
      await page.locator('.auth-form-panel form').waitFor();
      beforeLoginDocument = await page.evaluate(() => window.__loadDocumentMarker);
      await page.locator('input[name="username"]').fill('load-fixture');
      await page.locator('input[name="password"]').fill('fixture-only-password');
      startedAt = performance.now();
      await page.locator('.auth-form-panel button[type="submit"]').click();
    }
    await assertResult(NAMES[0]);
    output.firstValidResultMs = +(performance.now() - startedAt).toFixed(2);
    output.entryToResultMs = +(performance.now() - entryAt).toFixed(2);
    await page.waitForLoadState('networkidle');
    const loaded = [...resources.values()], fonts = loaded.filter(r => r.font);
    Object.assign(output, { documentRequests: documents, authMeRequests: counts.me,
      apiCalls: [...calls], counts: { ...counts }, fontRequests: fonts.length,
      fontTransferBytes: fonts.reduce((n, r) => n + (r.transferBytes || 0), 0),
      staticTransferBytes: loaded.reduce((n, r) => n + (r.transferBytes || 0), 0),
      incompleteStaticResources: loaded.filter(r => !['finished', 'failed'].includes(r.state)).length,
      canceledStaticResources: loaded.filter(r => r.canceled).length,
      failedStaticResources: loaded.filter(r => r.state === 'failed' && !r.canceled).length,
      unknownTransferSizeResources: loaded.filter(r => r.transferBytes === null).length,
      transferBytesAreCompletedOnlyLowerBound: loaded.some(r => r.transferBytes === null),
      fonts, sameDocumentAfterLogin: scenario === 'password'
        ? beforeLoginDocument === await page.evaluate(() => window.__loadDocumentMarker) : null });
    assert.ok(counts.day > 0, 'a real mocked day read must produce the observed result');
    if (variant === CONTRACT) {
      assert.equal(fonts.length, 0, 'natural login/query must request no /fonts/ or font resources');
      assert.equal(documents, 1, 'login must retain the initial document');
      assert.equal(counts.me, 1, 'only the initial auth/me is permitted');
      if (scenario === 'password') assert.equal(output.sameDocumentAfterLogin, true);
    }
    if (scenario === 'password' && round === 1) {
      const retainedUrl = page.url(), dayReads = counts.day;
      phase = 1;
      await page.getByRole('button', { name: '账户菜单', exact: true }).click();
      await page.getByRole('menuitem', { name: '退出登录', exact: true }).click();
      await page.locator('.auth-form-panel form').waitFor();
      assert.equal(await page.locator('input[name="password"]').inputValue(), '');
      await submit();
      await assertResult(NAMES[1]);
      await page.waitForLoadState('networkidle');
      assert.equal(page.url(), retainedUrl, 'logout/relogin must retain the query URL');
      assert.ok(counts.day > dayReads, 'relogin must rebuild private query components and read new data');
      if (variant === CONTRACT) { assert.equal(documents, 1); assert.equal(counts.me, 1); }
      output.logoutRelogin = { passed: true, queryRetained: true, freshPrivateData: true };
    }
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
    output.ok = true;
  } catch (error) { output.error = error.message; }
  finally {
    Object.assign(output, { errors, blocked, diagnostics: { requests: [...requests.values()],
      pendingRequests: [...requests.values()].filter(r => r.state === 'pending'),
      staticResources: [...resources.values()], lifecycle,
      diagnosticAuth401Drained: await page.evaluate(() => window.__auth401Drained).catch(() => null),
      readyState: await page.evaluate(() => document.readyState).catch(() => 'unavailable') } });
    if (OUT && (round === 1 || !output.ok))
      await page.screenshot({ path: path.join(OUT, `${variant}-${scenario}-${round}.png`), fullPage: true }).catch(() => {});
    await context.close();
  }
  return output;
}

(async () => {
  if (OUT) fs.mkdirSync(OUT, { recursive: true });
  const sites = {}, samples = [];
  const browser = await chromium.launch({ headless: true, channel: 'chrome',
    args: ['--host-resolver-rules=MAP api.stock.iteready.com ~NOTFOUND'] });
  try {
    for (const v of VARIANTS) sites[v] = await serve(DIRS[v]);
    for (let round = 1; round <= ROUNDS; round++)
      for (const scenario of SCENARIOS)
        for (const v of (round % 2 ? VARIANTS : [...VARIANTS].reverse())) {
          const value = await sample(browser, sites[v], v, scenario, round);
          samples.push(value); console.log(JSON.stringify(value));
        }
    const summaries = [];
    for (const variant of VARIANTS) for (const scenario of SCENARIOS) {
      const values = samples.filter(x => x.variant === variant && x.scenario === scenario);
      const times = values.filter(x => x.ok).map(x => x.firstValidResultMs).sort((a, b) => a - b);
      summaries.push({ variant, scenario, attempted: values.length, successes: times.length,
        failures: values.length - times.length, minMs: times[0] ?? null,
        medianMs: times.length ? times[Math.floor(times.length / 2)] : null, maxMs: times.at(-1) ?? null });
    }
    const report = { passed: samples.every(x => x.ok), method: { mode: VERIFY ? 'verify-only' : 'measure',
      roundsPerSide: ROUNDS, order: 'round, scenario; variant order alternates each round', viewport: '1440x960',
      freshContextPerSample: true, cacheDisabled: true, staticCompression: 'Brotli/gzip; woff2 unchanged',
      network: NETWORK, mockApiDelayMs: DELAY, hostAtStart: HOST_AT_START,
      diagnosticDrainAuth401: DRAIN_AUTH401,
      fixture: 'one synthetic stock with rare Chinese characters; not the 5,579-stock production dataset',
      timing: 'session begins before navigation; password begins immediately before submit; bytes sampled after network idle',
      limits: ['API is route-fulfilled with fixed delay; not real mainland or cross-border network.',
        'CDP throttling applies to real local static transfers; API DNS is blocked and preconnect reuse is not measured.',
        'Subroute logout/relogin is outside this harness and is covered separately by login-card.',
        'Transfer bytes include completed static requests; canceled or unfinished transfers make the sum a lower bound.',
        'Failed or missing results are failures and excluded from successful timing statistics.'] }, summaries, samples };
    if (OUT) fs.writeFileSync(path.join(OUT, 'initial-load-performance.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify({ passed: report.passed, method: report.method, summaries }));
    if (!report.passed) process.exitCode = 1;
  } finally {
    await browser.close();
    for (const site of Object.values(sites)) await site.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
