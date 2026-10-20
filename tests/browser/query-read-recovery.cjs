/* Query recovery regression: local server plus isolated browser fixtures. */
/* oxlint-disable typescript/no-require-imports -- executable CommonJS browser smoke */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const SITE =
  process.env.QUERY_UI_URL || process.env.BASE_URL || 'http://localhost:3107';
const DAYS = [
  '2026-09-21',
  '2026-09-22',
  '2026-09-23',
  '2026-09-24',
  '2026-09-28',
  '2026-09-29',
  '2026-09-30',
];
const LAST = DAYS.at(-1);
const OUTPUT_DIR = process.env.QUERY_UI_OUTPUT_DIR;
const stocks = [
  { code: '000001', name: '甲股票' },
  { code: '000002', name: '乙股票' },
];

function daily(date, version, includeSecond = false) {
  return {
    date,
    generatedAt: version,
    source: 'browser fixture',
    methodology: 'fixture',
    scope: 'full',
    total: includeSecond ? 2 : 1,
    priceDataComplete: false,
    rows: [
      {
        code: '000001',
        name: '甲股票',
        market: 'SZ',
        status: 'ok',
        first15Volume: Number(date.slice(-2)),
        dailyVolume: 100,
        ratio: Number(date.slice(-2)),
        priceStatus: 'missing',
      },
      ...(includeSecond
        ? [
            {
              code: '000002',
              name: '乙股票',
              market: 'SZ',
              status: 'ok',
              first15Volume: 12,
              dailyVolume: 100,
              ratio: 12,
              priceStatus: 'missing',
            },
          ]
        : []),
    ],
  };
}
function manifest(backend) {
  return {
    generatedAt: backend.version,
    source: 'browser fixture',
    methodology: 'fixture',
    universeTotal: 2,
    tradingDates: backend.fixtureDates,
    retentionStart: backend.fixtureDates[0],
    dates: [...backend.fixtureDates].reverse().map((date) => ({
      date,
      file: date + '.json',
      status: 'full',
      total: 1,
      valid: 1,
      missing: 0,
      suspended: 0,
      generatedAt: date === LAST ? backend.version : 'v1',
    })),
  };
}
async function setup(browser, settings = {}) {
  const page = await browser.newPage({
    viewport: { width: 1280, height: 900 },
  });
  const backend = {
    version: 'v1',
    failManifest: false,
    failCatalog: false,
    failDate: '',
    announceVersion: false,
    includeSecond: false,
    heldDate: '',
    hold: Promise.resolve(),
    fixtureDates: DAYS,
    ...settings,
  };
  const counts = new Map();
  const blocked = [];
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  // Only collection polling's idle interval is accelerated. Request deadlines
  // and retry waits still use their production values.
  await page.addInitScript(() => {
    const timeout = window.setTimeout.bind(window);
    window.setTimeout = (callback, delay, ...args) =>
      timeout(callback, delay === 60000 ? 100 : delay, ...args);
  });
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (value, status = 200) =>
      route.fulfill({
        status,
        json: value,
        headers: {
          'access-control-allow-origin': new URL(SITE).origin,
          'access-control-allow-credentials': 'true',
          'access-control-allow-methods': 'GET, POST, OPTIONS',
          'access-control-allow-headers': 'content-type',
        },
      });
    if (path.startsWith('/auth/')) return json({ username: 'fixture' });
    if (path.startsWith('/data/')) {
      counts.set(path, (counts.get(path) || 0) + 1);
      if (path === '/data/stocks.json')
        return json(
          backend.failCatalog
            ? { error: 'fixture unavailable' }
            : { rows: stocks },
          backend.failCatalog ? 503 : 200,
        );
      if (path === '/data/manifest.json')
        return json(
          backend.failManifest
            ? { error: 'fixture unavailable' }
            : manifest(backend),
          backend.failManifest ? 503 : 200,
        );
      if (path === '/data/collection-status.json')
        return json({
          id: 'fixture',
          state: 'completed',
          completedStocks: 2,
          totalStocks: 2,
          latestDate: backend.announceVersion ? '2026-10-02' : LAST,
          targetDate: backend.announceVersion ? '2026-10-02' : LAST,
          updatedAt: '2026-10-03T00:00:00Z',
          message: 'fixture',
          publicationCommitted: true,
          dataComplete: true,
          phase: 'daily',
        });
      const date = path.match(/^\/data\/(\d{4}-\d{2}-\d{2})\.json$/)?.[1];
      if (date) {
        if (backend.heldDate === date && backend.hold) await backend.hold;
        if (backend.failDate === date)
          return json({ error: 'fixture unavailable' }, 503);
        return json(
          daily(
            date,
            date === LAST ? backend.version : 'v1',
            backend.includeSecond,
          ),
        );
      }
      return json({ error: 'unknown fixture resource' }, 404);
    }
    if (url.origin === new URL(SITE).origin) return route.continue();
    // A local regression must never fall through to a production endpoint.
    blocked.push(url.origin + path);
    return route.abort();
  });
  return { page, backend, counts, blocked, errors };
}
async function loaded(page) {
  await page.locator('.results tbody tr').first().waitFor({ timeout: 30000 });
}
async function selectStock(page, code) {
  await page.locator('#stock-picker').fill(code);
  await page
    .getByRole('option', { name: `乙股票 ${code}`, exact: true })
    .click();
}
function count(snapshot, path) {
  return snapshot.get('/data/' + path) || 0;
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const results = [];
  try {
    const stockCase = await setup(browser);
    const { page, backend, counts } = stockCase;
    await page.goto(
      `${SITE}/?dates=${DAYS.join(',')}&scope=single&symbols=000001`,
    );
    await loaded(page);
    assert.equal(await page.locator('.results tbody tr').count(), 7);
    assert.equal(await page.locator('.price-backfill-notice').count(), 1);
    const initialCounts = new Map(counts);

    // A new publication changes just one daily version. A's background read
    // fails, then changing to B on the same dates must discard A's notices.
    backend.version = 'v2';
    backend.failDate = LAST;
    backend.announceVersion = true;
    await page
      .locator('.range-caption[role="alert"]')
      .waitFor({ timeout: 30000 });
    const refreshText = await page
      .locator('.range-caption[role="alert"]')
      .innerText();
    assert.match(refreshText, /2026-09-30.*HTTP 503/);
    assert.match(refreshText, /上次已保存/);
    assert.equal(await page.locator('.results tbody tr').count(), 7);
    await selectStock(page, '000002');
    await page
      .getByText('这些日期暂时无法查询', { exact: true })
      .waitFor({ timeout: 30000 });
    assert.match(
      await page.locator('.result-empty').innerText(),
      /2026-09-30.*HTTP 503/,
    );
    assert.equal(await page.locator('.results tbody tr').count(), 0);
    assert.equal(await page.locator('.selection-warning').count(), 0);
    assert.equal(await page.locator('.price-backfill-notice').count(), 0);
    assert.equal(await page.locator('.range-caption[role="alert"]').count(), 0);
    if (OUTPUT_DIR) {
      fs.mkdirSync(OUTPUT_DIR, { recursive: true });
      await page.screenshot({
        path: path.join(OUTPUT_DIR, 'browser-query-failure.png'),
        fullPage: true,
      });
    }

    backend.failDate = '';
    backend.includeSecond = true;
    const failedCounts = new Map(counts);
    await page.getByRole('button', { name: '重新读取', exact: true }).click();
    await loaded(page);
    assert.match(await page.locator('.view-heading h2').innerText(), /乙股票/);
    for (const date of DAYS.slice(0, -1))
      assert.equal(
        count(counts, date + '.json'),
        count(initialCounts, date + '.json'),
        'unchanged successful dates stay cached',
      );
    assert.equal(
      count(counts, LAST + '.json'),
      count(failedCounts, LAST + '.json') + 1,
    );
    assert.equal(await page.locator('.result-empty').count(), 0);
    if (OUTPUT_DIR)
      await page.screenshot({
        path: path.join(OUTPUT_DIR, 'browser-query-recovery.png'),
        fullPage: true,
      });
    results.push({
      scenario: 'stock switch, background failure, cached manual recovery',
      passed: true,
    });
    assert.deepEqual(stockCase.errors, []);
    assert.deepEqual(stockCase.blocked, []);
    await page.close();

    const longDates = [
      '2026-09-11',
      '2026-09-14',
      '2026-09-15',
      '2026-09-16',
      '2026-09-17',
      '2026-09-18',
      '2026-09-21',
      '2026-09-22',
      '2026-09-23',
      '2026-09-24',
      '2026-09-25',
      '2026-09-28',
      '2026-09-29',
      '2026-09-30',
    ];
    const longCase = await setup(browser, {
      fixtureDates: longDates,
      includeSecond: true,
    });
    await longCase.page.goto(
      `${SITE}/?dates=${longDates.join(',')}&scope=single&symbols=000001`,
    );
    await loaded(longCase.page);
    assert.equal(await longCase.page.locator('.results tbody tr').count(), 14);
    assert.match(
      await longCase.page.locator('.results tbody tr').first().innerText(),
      /30\.00/,
    );
    const longCounts = new Map(longCase.counts);
    // Record committed DOM states through the actual stock-picker interaction.
    // No frame may combine B's title with A's ratio or an A-only warning.
    await longCase.page.evaluate(() => {
      new MutationObserver(() => {
        const title = document.querySelector('.view-heading h2')?.textContent;
        const row = document.querySelector('.results tbody tr')?.textContent;
        if (title === '乙股票' && row && !row.includes('12.00'))
          document.body.dataset.staleQuery = 'true';
      }).observe(document.body, {
        childList: true,
        characterData: true,
        subtree: true,
      });
    });
    await selectStock(longCase.page, '000002');
    await longCase.page
      .getByRole('heading', { name: '乙股票', exact: true })
      .waitFor();
    await longCase.page.waitForFunction(() =>
      document
        .querySelector('.results tbody tr')
        ?.textContent?.includes('12.00'),
    );
    assert.equal(await longCase.page.locator('.results tbody tr').count(), 14);
    for (const text of await longCase.page
      .locator('.results tbody tr')
      .allTextContents())
      assert.match(text, /12\.00/);
    assert.equal(
      await longCase.page.locator('body').getAttribute('data-stale-query'),
      null,
    );
    assert.equal(await longCase.page.locator('.selection-warning').count(), 0);
    for (const date of longDates)
      assert.equal(
        count(longCase.counts, date + '.json'),
        count(longCounts, date + '.json'),
        '14-day stock switch preserves the complete verified range',
      );
    assert.deepEqual(longCase.errors, []);
    assert.deepEqual(longCase.blocked, []);
    results.push({
      scenario:
        '14-day stock switch preserves complete cache and query identity',
      passed: true,
      dailyReadsBefore: 14,
      extraDailyReads: 0,
    });
    await longCase.page.close();

    const metadataCase = await setup(browser, { failManifest: true });
    await metadataCase.page.goto(
      `${SITE}/?date=${LAST}&scope=single&symbols=000001`,
    );
    await metadataCase.page
      .getByText('数据目录暂时无法读取', { exact: true })
      .waitFor({ timeout: 30000 });
    assert.match(
      await metadataCase.page.locator('.result-empty').innerText(),
      /HTTP 503/,
    );
    assert.equal(count(metadataCase.counts, 'manifest.json'), 3);
    metadataCase.backend.failManifest = false;
    await metadataCase.page
      .getByRole('button', { name: '重新读取', exact: true })
      .click();
    await loaded(metadataCase.page);
    assert.equal(count(metadataCase.counts, 'stocks.json'), 1);
    assert.match(
      await metadataCase.page.locator('.view-heading h2').innerText(),
      /甲股票/,
    );
    assert.deepEqual(metadataCase.errors, []);
    assert.deepEqual(metadataCase.blocked, []);
    results.push({
      scenario: 'manifest exhaustion and scoped recovery',
      passed: true,
    });
    await metadataCase.page.close();

    const catalogCase = await setup(browser, { failCatalog: true });
    await catalogCase.page.goto(
      `${SITE}/?date=${LAST}&scope=single&symbols=000001`,
    );
    await loaded(catalogCase.page);
    await catalogCase.page
      .getByRole('button', { name: '重新读取名单', exact: true })
      .waitFor({ timeout: 30000 });
    assert.match(
      await catalogCase.page
        .locator('.stock-scope-control [role="alert"]')
        .innerText(),
      /HTTP 503/,
    );
    assert.equal(count(catalogCase.counts, 'stocks.json'), 3);
    const catalogCounts = new Map(catalogCase.counts);
    catalogCase.backend.failCatalog = false;
    await catalogCase.page
      .getByRole('button', { name: '重新读取名单', exact: true })
      .click();
    await catalogCase.page
      .getByRole('heading', { name: '甲股票', exact: true })
      .waitFor();
    assert.equal(
      count(catalogCase.counts, 'manifest.json'),
      count(catalogCounts, 'manifest.json'),
    );
    assert.equal(
      count(catalogCase.counts, LAST + '.json'),
      count(catalogCounts, LAST + '.json'),
    );
    assert.deepEqual(catalogCase.errors, []);
    assert.deepEqual(catalogCase.blocked, []);
    results.push({
      scenario: 'catalog exhaustion and independent recovery',
      passed: true,
    });
    await catalogCase.page.close();

    const directoryCase = await setup(browser);
    await directoryCase.page.goto(
      `${SITE}/?date=${LAST}&scope=single&symbols=000001`,
    );
    await loaded(directoryCase.page);
    const directoryCounts = new Map(directoryCase.counts);
    directoryCase.backend.failManifest = true;
    directoryCase.backend.announceVersion = true;
    await directoryCase.page
      .getByRole('button', { name: '重新读取目录', exact: true })
      .waitFor({ timeout: 30000 });
    assert.match(
      await directoryCase.page
        .locator('.range-caption[role="alert"]')
        .innerText(),
      /数据目录.*HTTP 503.*上次已保存的数据目录/,
    );
    assert.equal(
      await directoryCase.page.locator('.results tbody tr').count(),
      1,
    );
    assert.equal(await directoryCase.page.locator('.result-empty').count(), 0);
    directoryCase.backend.announceVersion = false;
    directoryCase.backend.failManifest = false;
    const directoryRead = directoryCase.page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === '/data/manifest.json' &&
        response.status() === 200,
    );
    await directoryCase.page
      .getByRole('button', { name: '重新读取目录', exact: true })
      .click();
    await (await directoryRead).finished();
    await directoryCase.page
      .getByRole('button', { name: '重新读取目录', exact: true })
      .waitFor({ state: 'hidden' });
    assert.match(
      await directoryCase.page.locator('.view-dates').innerText(),
      /2026-09-30/,
    );
    assert.equal(
      count(directoryCase.counts, LAST + '.json'),
      count(directoryCounts, LAST + '.json'),
    );
    assert.deepEqual(directoryCase.errors, []);
    assert.deepEqual(directoryCase.blocked, []);
    results.push({
      scenario: 'background directory failure and preserved query recovery',
      passed: true,
    });
    await directoryCase.page.close();

    const cancellationCase = await setup(browser);
    await cancellationCase.page.goto(
      `${SITE}/?date=2026-09-21&scope=single&symbols=000001`,
    );
    await loaded(cancellationCase.page);
    let release;
    cancellationCase.backend.heldDate = '2026-09-23';
    cancellationCase.backend.hold = new Promise((resolve) => {
      release = resolve;
    });
    await cancellationCase.page.locator('[data-day="2026/9/23"]').click();
    await cancellationCase.page
      .locator('[aria-label="正在读取行情数据"]')
      .waitFor();
    assert.equal(
      await cancellationCase.page.locator('.price-backfill-notice').count(),
      0,
    );
    await cancellationCase.page.locator('[data-day="2026/9/24"]').click();
    await loaded(cancellationCase.page);
    release();
    await cancellationCase.page.waitForTimeout(150);
    assert.match(
      await cancellationCase.page.locator('.view-dates').innerText(),
      /2026-09-24/,
    );
    assert.match(
      await cancellationCase.page.locator('.results tbody tr').innerText(),
      /24\.00/,
    );
    assert.equal(
      await cancellationCase.page.locator('.result-empty').count(),
      0,
    );
    assert.deepEqual(cancellationCase.errors, []);
    assert.deepEqual(cancellationCase.blocked, []);
    results.push({
      scenario: 'date cancellation and late response isolation',
      passed: true,
    });
    await cancellationCase.page.close();
    const report = {
      site: SITE,
      checkedAt: new Date().toISOString(),
      results,
      productionRequests: 0,
    };
    if (OUTPUT_DIR)
      fs.writeFileSync(
        path.join(OUTPUT_DIR, 'browser-query-recovery-results.json'),
        JSON.stringify(report, null, 2) + '\n',
      );
    console.log(JSON.stringify(report));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.stack);
  process.exitCode = 1;
});
