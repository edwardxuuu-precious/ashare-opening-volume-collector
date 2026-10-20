// Explicit real-browser capacity acceptance. Root owns test-account lifecycle.
/* oxlint-disable typescript/no-require-imports -- Executable CommonJS browser smoke script. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const SITE = 'https://stock.iteready.com';
const API = 'https://api.stock.iteready.com';
const out = path.resolve(__dirname, '../../outputs/full-market-range-live');
const report = { realBrowser: true, synthetic: false, startedAt: new Date().toISOString(),
  limits: { runSeconds: 180, jsHeapBytes: 1500000000 }, checks: [], memory: [],
  dates: [], errors: [], responseErrors: [], unexpectedWrites: [],
  limitation: 'Current production includes many not-yet-collected placeholders. Passing this run does not establish capacity for 700k fully populated audit records or complete market data coverage.' };
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const compact = text => text.replace(/\s|,/g, '');

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const credentials = JSON.parse(fs.readFileSync(path.join(os.homedir(),
    '.config/stock-iteready/accounts/stock-full-market-check.json'), 'utf8'));
  assert.equal(credentials.username, 'stock-full-market-check');
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  let context, page, cdp, signedIn = false, capturing = false, monitorBusy = false;
  let deadlineTimer, memoryTimer;
  const start = Date.now(), responses = new Map(), responseTasks = new Set();
  let failure;
  try {
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    page = await context.newPage();
    cdp = await context.newCDPSession(page);
    await cdp.send('Performance.enable');
    report.browserVersion = browser.version();
    page.on('pageerror', error => report.errors.push(error.message));
    page.on('request', request => {
      if (request.method() === 'POST' && request.url().includes('/api/query-jobs'))
        report.unexpectedWrites.push(request.url());
    });
    page.on('response', response => {
      if (!capturing) return;
      const url = new URL(response.url());
      const match = url.pathname.match(/^\/data\/(\d{4}-\d{2}-\d{2})\.json$/);
      if (url.origin !== API || !match) return;
      const task = (async () => {
        try {
          assert.equal(response.status(), 200, `${match[1]} HTTP status`);
          const bytes = await response.body();
          const data = JSON.parse(bytes);
          assert.equal(data.date, match[1]);
          assert.ok(Array.isArray(data.rows));
          const statuses = {}, reasons = {};
          for (const row of data.rows) {
            statuses[row.status] = (statuses[row.status] || 0) + 1;
            if (row.reason === '尚未采集') reasons.notYetCollected = (reasons.notYetCollected || 0) + 1;
          }
          responses.set(data.date, { date: data.date, http: response.status(), rows: data.rows.length,
            bytes: bytes.length, generatedAt: data.generatedAt, sha256: sha(bytes), statuses, ...reasons });
          if (responses.size % 20 === 0) console.log(JSON.stringify({ receivedDates: responses.size }));
        } catch (error) {
          report.responseErrors.push({ date: match[1], error: error.message.slice(0, 300) });
        }
      })();
      responseTasks.add(task);
      void task.finally(() => responseTasks.delete(task));
    });
    const memory = async stage => {
      const metrics = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m => [m.name, m.value]));
      const sample = { stage, elapsedMs: Date.now() - start, jsHeapUsedSize: metrics.JSHeapUsedSize,
        jsHeapTotalSize: metrics.JSHeapTotalSize, nodes: metrics.Nodes, documents: metrics.Documents };
      report.memory.push(sample);
      if (sample.jsHeapUsedSize > report.limits.jsHeapBytes)
        throw Error('Browser JS heap exceeded the 1.5 GB stop limit');
      return sample;
    };
    const guard = new Promise((_, reject) => {
      deadlineTimer = setTimeout(() => reject(Error('Real-browser run exceeded 180 seconds')), 180000);
      memoryTimer = setInterval(async () => {
        if (monitorBusy) return;
        monitorBusy = true;
        try { await memory('monitor'); } catch (error) { reject(error); }
        finally { monitorBusy = false; }
      }, 750);
    });
    const run = async () => {
      assert.equal((await context.request.get(`${API}/data/manifest.json`)).status(), 401);
      await page.goto(SITE, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.getByRole('button', { name: '登录', exact: true }).waitFor();
      await page.getByLabel('账号', { exact: true }).fill(credentials.username);
      await page.getByLabel('密码', { exact: true }).fill(credentials.password);
      await page.getByRole('button', { name: '登录', exact: true }).click();
      await page.locator('.view-heading').waitFor({ timeout: 30000 });
      signedIn = true;
      const manifestResponse = await context.request.get(`${API}/data/manifest.json`);
      assert.equal(manifestResponse.status(), 200);
      const manifestBytes = await manifestResponse.body(), manifest = JSON.parse(manifestBytes);
      const days = [...new Set(manifest.dates.map(entry => entry.date))].sort((a, b) => a.localeCompare(b));
      assert.equal(days.length, 126, 'Published trading dates');
      report.manifest = { generatedAt: manifest.generatedAt, sha256: sha(manifestBytes),
        earliest: days[0], latest: days.at(-1), dates: days.length, universeTotal: manifest.universeTotal };
      const query = new URLSearchParams({ scope: 'all', from: days[0], to: days.at(-1), unverified: '1' });
      report.url = `${SITE}/?${query}`;
      capturing = true;
      await memory('before-range');
      const rangeStart = Date.now();
      await page.goto(report.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      let lastAssertion;
      for (;;) {
        try {
          assert.equal(days.filter(day => responses.has(day)).length, days.length);
          const expectedRows = days.reduce((sum, date) => sum + responses.get(date).rows, 0);
          const heading = compact(await page.locator('.results-heading').innerText({ timeout: 1000 }));
          assert.ok(heading.includes(`${expectedRows}条记录`), 'Table record count matches actual browser responses');
          assert.equal(await page.locator('.results tbody tr').count(), 50);
          assert.equal(expectedRows, 5556 * 126);
          report.rowCount = expectedRows;
          report.rangeReadyMs = Date.now() - rangeStart;
          break;
        } catch (error) { lastAssertion = error.message; }
        if (Date.now() - rangeStart > 165000) throw Error(lastAssertion || 'Range table was not ready');
        await wait(250);
      }
      await memory('range-ready');
      report.checks.push({ name: '126 authenticated daily responses sum to 700056 displayed rows', passed: true });
      const first = await page.locator('.results tbody tr').first().innerText();
      await page.screenshot({ path: path.join(out, 'page-1.png'), fullPage: true });
      await page.getByRole('button', { name: '下一页', exact: true }).click();
      await page.waitForFunction(previous => document.querySelector('.results tbody tr')?.innerText !== previous, first);
      assert.equal(await page.locator('.results tbody tr').count(), 50);
      assert.match(compact(await page.locator('.results').innerText()), /第51[–—-]100条/);
      await memory('page-two');
      await page.screenshot({ path: path.join(out, 'page-2.png'), fullPage: true });
      report.checks.push({ name: '50-row pagination advances to records 51–100', passed: true });
      assert.equal(report.errors.length, 0, 'No uncaught browser errors');
      assert.equal(report.unexpectedWrites.length, 0, 'Range read must not create collection jobs');
      report.checks.push({ name: 'No uncaught errors or collection-job writes', passed: true });
    };
    await Promise.race([run(), guard]);
    report.status = 'passed';
  } catch (error) {
    failure = error;
    report.status = 'failed';
    report.failure = error.message.slice(0, 1000);
    if (page && !page.isClosed()) {
      try { await page.screenshot({ path: path.join(out, 'failure.png'), timeout: 5000 }); } catch {}
    }
  } finally {
    clearTimeout(deadlineTimer); clearInterval(memoryTimer);
    capturing = false;
    if (context && signedIn) {
      try {
        const logout = await context.request.post(`${API}/auth/logout`,
          { headers: { Origin: SITE }, data: {}, timeout: 10000 });
        const after = await context.request.get(`${API}/data/manifest.json`, { timeout: 10000 });
        report.logout = { http: logout.status(), subsequentDataHttp: after.status(), passed: logout.status() === 200 && after.status() === 401 };
        if (!report.logout.passed) report.status = 'failed';
      } catch (error) { report.logout = { passed: false, error: error.message.slice(0, 300) }; report.status = 'failed'; }
    }
    await browser.close();
    await Promise.allSettled(responseTasks);
    report.dates = [...responses.values()].sort((a, b) => a.date.localeCompare(b.date));
    report.notYetCollectedRows = report.dates.reduce((sum, date) => sum + (date.notYetCollected || 0), 0);
    report.peakObservedJSHeapUsedSize = Math.max(0, ...report.memory.map(m => m.jsHeapUsedSize));
    report.completedAt = new Date().toISOString();
    report.elapsedMs = Date.now() - start;
    fs.writeFileSync(path.join(out, 'verification.json'), JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify({ status: report.status, dates: report.dates.length, rows: report.rowCount,
    notYetCollectedRows: report.notYetCollectedRows, peakObservedJSHeapUsedSize: report.peakObservedJSHeapUsedSize,
    elapsedMs: report.elapsedMs, logout: report.logout, failure: report.failure }));
  if (failure || report.status !== 'passed') process.exitCode = 1;
})().catch(error => { console.error(error.message); process.exitCode = 1; });
