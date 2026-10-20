// Explicit live readback; root creates/removes the disposable account. No AWS writes.
/* oxlint-disable typescript/no-require-imports -- Executable CommonJS smoke script, matching existing release scripts. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');
const SITE = 'https://stock.iteready.com';
const API = 'https://api.stock.iteready.com';
const out = path.resolve(__dirname, '../../outputs/download-only/live');
const results = [];
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const compact = value => value.replace(/\s+/g, '');
const valid = row => /^\d{6}$/.test(row.code) && Number.isFinite(row.first15Volume) &&
  Number.isFinite(row.dailyVolume) && row.first15Volume >= 0 && row.dailyVolume > 0 &&
  row.first15Volume <= row.dailyVolume;
const displayRatio = row => valid(row) ? row.first15Volume / row.dailyVolume * 100 : null;
const calendarKey = date => date.split('-').map(Number).join('/');
async function eventually(check, label) {
  const deadline = Date.now() + 30000;
  let last;
  while (Date.now() < deadline) {
    try { await check(); return; } catch (error) { last = error; }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw Error(`${label}: ${last?.message || 'timed out'}`);
}
function validateRows(rows) {
  for (const row of rows) {
    if (valid(row)) {
      assert.ok(row.dailyVolume > 0 && row.first15Volume >= 0 && row.first15Volume <= row.dailyVolume);
      if (row.calculationPolicy === 'download_only')
        assert.ok(Math.abs(row.ratio - row.first15Volume / row.dailyVolume * 100) < 0.000001,
          `${row.code}: ratio formula differs`);
    } else {
      assert.equal(row.ratio, null, `${row.code}: incomplete record exposes a ratio`);
    }
  }
}

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const credentials = JSON.parse(execFileSync(path.resolve(__dirname, '../../.venv/bin/python'),
    ['-c', 'import json; from credential_store import load_credentials; print(json.dumps(load_credentials("stock-download-check")))'],
    { env: { ...process.env, PYTHONPATH: path.resolve(__dirname, '../../deploy/auth') }, encoding: 'utf8' }));
  assert.equal(credentials.username, 'stock-download-check');
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  let context;
  let signedIn = false;
  const errors = [], unexpectedWrites = [];
  try {
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const responses = new Map();
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (request.method() === 'POST' && request.url().includes('/api/query-jobs'))
        unexpectedWrites.push(request.url());
    });
    page.on('response', async response => {
      const url = new URL(response.url());
      if (url.origin !== API || !url.pathname.startsWith('/data/') || response.status() !== 200) return;
      try {
        const bytes = await response.body();
        responses.set(url.pathname, { data: JSON.parse(bytes), sha256: sha(bytes), encoding: response.headers()['content-encoding'] });
      } catch { /* Navigations may cancel unrelated old responses. */ }
    });
    for (const file of ['manifest.json', 'collection-status.json'])
      assert.equal((await context.request.get(`${API}/data/${file}`)).status(), 401);
    results.push({ name: 'anonymous manifest and collection status denied', passed: true });
    await page.goto(SITE);
    await page.getByRole('button', { name: '登录', exact: true }).waitFor();
    await page.getByLabel('账号', { exact: true }).fill(credentials.username);
    await page.getByLabel('密码', { exact: true }).fill(credentials.password);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.locator('.view-heading').waitFor({ timeout: 30000 });
    signedIn = true;
    const manifestResponse = await context.request.get(`${API}/data/manifest.json`);
    assert.equal(manifestResponse.status(), 200);
    const manifest = await manifestResponse.json();
    const days = manifest.dates.map(entry => entry.date).sort((a, b) => a.localeCompare(b));
    assert.ok(days.length >= 2, 'need at least two published dates to verify the range');
    const latest = days.at(-1), earlier = days.at(-2);
    const latestPath = `/data/${latest}.json`;
    responses.delete(latestPath);
    await page.goto(`${SITE}/?date=${latest}&scope=all`);
    await eventually(async () => {
      assert.ok(responses.has(latestPath));
      const rows = responses.get(latestPath).data.rows;
      const ranked = rows;
      assert.equal(await page.locator('.results tbody tr').count(), Math.min(50, ranked.length));
      const tableHeading = compact(await page.locator('.results').innerText());
      assert.ok(tableHeading.includes(`${ranked.length.toLocaleString('en-US')}条记录`));
      const summary = compact(await page.locator('.summary').innerText());
      assert.ok(summary.includes(`覆盖股票${new Set(ranked.map(row => row.code)).size.toLocaleString('en-US')}只`));
      assert.ok(summary.includes(`可计算记录${rows.filter(valid).length.toLocaleString('en-US')}/${rows.length.toLocaleString('en-US')}`));
    }, 'latest market summary matches the exact browser response');
    const latestData = responses.get(latestPath);
    validateRows(latestData.data.rows);
    assert.equal(latestData.encoding, 'gzip', 'full-market transport must be compressed');
    results.push({ name: 'latest market counts and ratio validation match private API', passed: true,
      date: latest, totalStocks: new Set(latestData.data.rows.map(row => row.code)).size,
      calculableRows: latestData.data.rows.filter(valid).length,
      missingRows: latestData.data.rows.filter(row => !valid(row)).length,
      sha256: latestData.sha256 });

    await eventually(async () => {
      const status = responses.get('/data/collection-status.json')?.data;
      assert.ok(status, 'authenticated status response not received');
      assert.ok(['running', 'paused', 'completed', 'failed'].includes(status.state));
      assert.ok(Number.isSafeInteger(status.completedStocks) && status.completedStocks >= 0);
      assert.ok(Number.isSafeInteger(status.totalStocks) && status.totalStocks >= status.completedStocks);
      const output = page.locator('output').filter({ hasText: '已处理' });
      assert.equal(await output.count(), 1);
      assert.ok(compact(await output.innerText()).includes(
        `已处理${status.completedStocks.toLocaleString('en-US')}/${status.totalStocks.toLocaleString('en-US')}只股票`));
      assert.doesNotMatch(await output.innerText(), /待核验|已核对|复核|口径差异/);
    }, 'cloud status counters are actually displayed');
    results.push({ name: 'real cloud collection status displayed', passed: true,
      status: responses.get('/data/collection-status.json').data });
    await page.screenshot({ path: path.join(out, 'market.png'), fullPage: true });

    responses.delete(latestPath);
    await page.goto(`${SITE}/?date=${latest}&scope=single&symbols=600519`);
    await eventually(async () => {
      assert.ok(responses.has(latestPath));
      assert.equal(await page.locator('.results tbody tr').count(), 1);
    }, 'single Maotai row loaded');
    const maotai = responses.get(latestPath).data.rows.find(row => row.code === '600519');
    assert.ok(maotai, 'latest snapshot must include 600519 for this regression');
    validateRows([maotai]);
    const row = page.locator('.results tbody tr');
    assert.doesNotMatch(await row.innerText(), /核验|核对|口径差异|参考占比/);
    assert.equal(await row.locator('.status-column').count(), 0);
    if (valid(maotai)) {
      assert.equal(await row.locator('.ratio-display').count(), 1);
      assert.ok(compact(await row.innerText()).includes(`${displayRatio(maotai).toFixed(2)}%`));
    } else {
      assert.equal(await row.locator('.ratio-display').count(), 0);
      assert.equal(await row.locator('.unavailable-value').innerText(), '—');
    }
    results.push({ name: 'single-stock ratio uses downloaded volumes without verification labels', passed: true,
      date: latest, ratio: displayRatio(maotai) });
    await page.screenshot({ path: path.join(out, 'maotai.png'), fullPage: true });

    // Exercise a real calendar selection, without depending on translated aria-label text.
    if (earlier.slice(0, 7) !== latest.slice(0, 7))
      await page.getByRole('button', { name: '上个月', exact: true }).click();
    await page.locator(`.date-calendar button[data-day="${calendarKey(earlier)}"]`).click();
    await eventually(async () => assert.equal(new URL(page.url()).searchParams.get('date'), earlier), 'calendar changes query date');
    results.push({ name: 'calendar selection updates the selected trading date', passed: true, date: earlier });

    if (process.env.ARCHIVED_DATE) {
      const archived = process.env.ARCHIVED_DATE;
      assert.match(archived, /^\d{4}-\d{2}-\d{2}$/);
      assert.ok(days.includes(archived), 'archived date stays in the published catalog');
      const archivedPath = `/data/${archived}.json`;
      responses.delete(archivedPath);
      await page.goto(`${SITE}/?date=${archived}&scope=all`);
      await eventually(async () => {
        assert.ok(responses.has(archivedPath));
        assert.ok(await page.locator('.results tbody tr').count() > 0);
        assert.equal(await page.locator(`.date-calendar button[data-day="${calendarKey(archived)}"]`).isEnabled(), true);
        assert.match(await page.locator('.calendar-selection').innerText(), /历史数据持续积累/);
      }, 'saved history outside the collection window remains selectable and visible');
      const data = responses.get(archivedPath);
      validateRows(data.data.rows);
      results.push({ name: 'archived historical date is queryable and selectable', passed: true,
        date: archived, totalStocks: data.data.rows.length, calculableRows: data.data.rows.filter(valid).length, sha256: data.sha256 });
      await page.screenshot({ path: path.join(out, 'archived-date.png'), fullPage: true });
    }

    const secondCode = latestData.data.rows.find(item => item.code !== '600519')?.code;
    assert.ok(secondCode);
    const selectedCodes = ['600519', secondCode];
    for (const day of [earlier, latest]) responses.delete(`/data/${day}.json`);
    await page.goto(`${SITE}/?from=${earlier}&to=${latest}&scope=multiple&symbols=${selectedCodes.join(',')}&unverified=1`);
    let expected;
    await eventually(async () => {
      assert.ok([earlier, latest].every(day => responses.has(`/data/${day}.json`)));
      expected = [earlier, latest].flatMap(day => responses.get(`/data/${day}.json`).data.rows)
        .filter(item => selectedCodes.includes(item.code));
      assert.equal(await page.locator('.results tbody tr').count(), expected.length);
      assert.equal(await page.locator('.results tbody .ratio-display').count(), expected.filter(valid).length);
      const url = new URL(page.url());
      assert.equal(url.searchParams.get('from'), earlier);
      assert.equal(url.searchParams.get('to'), latest);
      assert.deepEqual(url.searchParams.get('symbols').split(',').sort((a, b) => a.localeCompare(b)),
        [...selectedCodes].sort((a, b) => a.localeCompare(b)));
      assert.ok(compact(await page.locator('.calendar-selection-caption').innerText()).includes(`${earlier}至${latest}`));
    }, 'multiple stocks and range retain exact API records');
    validateRows(expected);
    results.push({ name: 'multi-stock calendar range follows private API data', passed: true,
      from: earlier, to: latest, symbols: selectedCodes, records: expected.length, calculableRows: expected.filter(valid).length });
    await page.screenshot({ path: path.join(out, 'range.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(out, 'range-mobile.png'), fullPage: true });
    assert.deepEqual(unexpectedWrites, [], 'viewing data must not enqueue jobs');
    assert.deepEqual(errors, [], 'uncaught browser exceptions');
    results.push({ name: 'no collection POSTs or uncaught browser errors', passed: true });
    const logout = await context.request.post(`${API}/auth/logout`, { headers: { Origin: SITE }, data: {} });
    assert.equal(logout.status(), 200);
    signedIn = false;
    assert.equal((await context.request.get(`${API}/data/collection-status.json`)).status(), 401);
    results.push({ name: 'logout invalidates collection status access', passed: true });
  } catch (error) {
    results.push({ name: 'live verification failed', passed: false, error: error.message });
    throw error;
  } finally {
    if (signedIn && context)
      await context.request.post(`${API}/auth/logout`, { headers: { Origin: SITE }, data: {} }).catch(() => {});
    await browser.close();
    fs.writeFileSync(path.join(out, 'verification.json'), JSON.stringify(results, null, 2));
  }
  console.log(JSON.stringify(results));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
