/* Live, read-only query acceptance using the owner's existing account. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const SITE = 'https://stock.iteready.com';
const API = 'https://api.stock.iteready.com';
const DAYS = ['2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-28', '2026-09-29', '2026-09-30'];
const OUT = path.resolve(process.argv[2]);

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1024 } });
  const page = await context.newPage();
  const result = { startedAt: new Date().toISOString(), site: SITE, symbol: '300684', selectedDates: DAYS, requests: [], pageErrors: [] };
  let loggedIn = false;
  let activeDays = 0;
  let maxActiveDays = 0;
  const pendingDays = new Set();
  page.on('pageerror', e => result.pageErrors.push(e.message));
  page.on('request', request => {
    if (request.method() === 'GET' && /^\/data\/2026-\d\d-\d\d\.json$/.test(new URL(request.url()).pathname)) {
      pendingDays.add(request);
      maxActiveDays = Math.max(maxActiveDays, ++activeDays);
    }
  });
  const finished = request => { if (pendingDays.delete(request)) activeDays--; };
  page.on('requestfinished', finished);
  page.on('requestfailed', request => {
    finished(request);
    result.requests.push({ path: new URL(request.url()).pathname, method: request.method(), failure: request.failure()?.errorText });
  });
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.origin === API) result.requests.push({ path: url.pathname, method: response.request().method(), status: response.status(), requestId: response.headers()['apigw-requestid'] || null });
  });
  try {
    const anonymous = await context.request.get(API + '/data/manifest.json', { headers: { Origin: SITE } });
    result.anonymousDataStatus = anonymous.status();
    assert.equal(result.anonymousDataStatus, 401);
    await page.goto(SITE + '/?dates=' + DAYS.join(',') + '&scope=single&symbols=300684', { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: '登录开盘量', exact: true }).waitFor({ timeout: 30000 });
    const vault = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.config/stock-iteready/accounts.json'), 'utf8'));
    const password = vault.accounts?.edward?.password;
    assert.equal(typeof password, 'string');
    await page.locator('input[name="username"]').fill('edward');
    await page.locator('input[name="password"]').fill(password);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.getByText('已登录：edward', { exact: true }).waitFor({ timeout: 30000 });
    loggedIn = true;
    await page.waitForFunction(() => {
      const rows = [...document.querySelectorAll('.results tbody tr')];
      return rows.length === 7 && rows.every(row => /2026-09-\d\d/.test(row.textContent));
    }, null, { timeout: 90000 });
    result.rowText = await page.locator('.results tbody tr').allTextContents();
    result.rowDates = result.rowText.map(text => text.match(/2026-09-\d\d/)[0]).sort();
    assert.deepEqual(result.rowDates, DAYS);
    assert.ok((await page.locator('body').textContent()).includes('中石科技'));
    await page.locator('.trend-chart svg.recharts-surface').waitFor({ state: 'visible', timeout: 30000 });
    result.trendRendered = await page.locator('.trend-chart .recharts-line-curve').count() > 0;
    assert.equal(result.trendRendered, true);
    result.dailyRequests = result.requests.filter(r => /^\/data\/2026-\d\d-\d\d\.json$/.test(r.path) && r.method === 'GET');
    assert.equal(result.dailyRequests.length, 7);
    assert.ok(result.dailyRequests.every(r => r.status === 200));
    assert.equal(result.pageErrors.length, 0);
    result.maxConcurrentDailyReads = maxActiveDays;
    assert.ok(maxActiveDays <= 2);
    result.screenshot = path.join(OUT, 'production-query-300684.png');
    await page.screenshot({ path: result.screenshot, fullPage: true });
    const publicPath = await context.request.get(SITE + '/data/2026-09-30.json');
    const publicText = await publicPath.text();
    result.websiteDataPath = { status: publicPath.status(), containsRawRows: false };
    try { const value = JSON.parse(publicText); result.websiteDataPath.containsRawRows = Array.isArray(value.rows); } catch { /* SPA or access rejection is expected. */ }
    assert.equal(result.websiteDataPath.containsRawRows, false);
    result.passed = true;
  } catch (error) {
    result.passed = false;
    result.failure = error.message;
    await page.screenshot({ path: path.join(OUT, 'production-query-failure.png'), fullPage: true }).catch(() => {});
    process.exitCode = 1;
  } finally {
    if (loggedIn) {
      const logout = await context.request.post(API + '/auth/logout', { headers: { Origin: SITE } });
      result.logoutStatus = logout.status();
      const revoked = await context.request.get(API + '/data/manifest.json', { headers: { Origin: SITE } });
      result.loggedOutDataStatus = revoked.status();
      if (result.logoutStatus !== 200 || result.loggedOutDataStatus !== 401) { result.passed = false; process.exitCode = 1; }
    }
    result.completedAt = new Date().toISOString();
    fs.writeFileSync(path.join(OUT, 'production-browser-readback.json'), JSON.stringify(result, null, 2));
    await browser.close();
    console.log(JSON.stringify({ passed: result.passed, rowDates: result.rowDates, dailyRequests: result.dailyRequests?.length, maxConcurrentDailyReads: maxActiveDays, anonymousDataStatus: result.anonymousDataStatus, loggedOutDataStatus: result.loggedOutDataStatus, pageErrors: result.pageErrors.length, failure: result.failure }));
  }
})().catch(() => {
  console.error('Live readback failed before completion.');
  process.exitCode = 1;
});
