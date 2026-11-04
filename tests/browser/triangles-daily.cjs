const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const SITE = process.env.TRIANGLE_UI_URL || 'http://127.0.0.1:3131';
const origin = new URL(SITE).origin;
assert.ok(['localhost', '127.0.0.1'].includes(new URL(SITE).hostname));
const output = process.env.TRIANGLE_UI_OUTPUT_DIR;
const checks = [];
const errors = [];

(async () => {
  const { trianglePublicationFixture, jsonBytes } =
    await import('../frontend/fixtures/triangle-publication.mjs');
  const fixture = trianglePublicationFixture();
  let failPublication = false;
  const requests = [];
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    serviceWorkers: 'block',
  });
  try {
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    await context.route('**/*', async (route) => {
      const request = route.request(),
        url = new URL(request.url());
      if (
        ![origin, 'https://api.stock.iteready.com'].includes(url.origin) ||
        request.method() !== 'GET'
      )
        return route.abort();
      const headers = {
        'access-control-allow-origin': origin,
        'access-control-allow-credentials': 'true',
        'content-type': 'application/json; charset=utf-8',
        'cache-control': 'no-store',
      };
      const reply = (value, status = 200) =>
        route.fulfill({ status, body: jsonBytes(value), headers });
      if (url.pathname === '/auth/me')
        return reply({ username: 'Isolated daily UI check' });
      if (url.pathname.startsWith('/data/')) requests.push(url.pathname);
      if (url.pathname === '/data/triangles/daily/status.json')
        return reply(
          failPublication
            ? {
                ...fixture.status,
                state: 'FAILED',
                message: 'Isolated failed update.',
              }
            : fixture.status,
        );
      if (url.pathname === '/data/triangles/daily/manifest.json')
        return failPublication
          ? reply({ error: 'Isolated unavailable publication.' }, 503)
          : reply(fixture.manifest);
      if (url.pathname.endsWith('/screening.json'))
        return reply(fixture.screen);
      if (url.pathname.endsWith('/bundle-000.json'))
        return reply(fixture.bundle);
      if (url.pathname === '/data/manifest.json')
        return reply({
          generatedAt: fixture.manifest.generated_at,
          universeTotal: 4,
          tradingDates: fixture.manifest.detail_sessions,
          dates: fixture.manifest.detail_sessions.map((date) => ({
            date,
            file: `${date}.json`,
            status: 'complete',
          })),
        });
      if (url.pathname === '/data/stocks.json')
        return reply({ rows: fixture.securities });
      if (url.pathname.startsWith('/data/'))
        throw Error('Unexpected market request: ' + url.pathname);
      return route.continue();
    });
    await page.goto(SITE + '/triangles?asOf=2026-11-03&history=140&scope=all');
    await page
      .locator('[data-triangle-count="golden"]')
      .waitFor({ timeout: 45000 });
    assert.equal(
      await page.locator('[data-triangle-count="golden"]').textContent(),
      '1',
    );
    assert.equal(
      await page.locator('[data-triangle-count="death"]').textContent(),
      '2',
    );
    assert.equal(
      await page.getByRole('button', { name: '开始筛选', exact: true }).count(),
      0,
    );
    assert.ok(
      requests.every((url) => url.startsWith('/data/triangles/daily/')),
    );
    assert.equal(
      requests.filter((url) => /bundle-\d+\.json$/.test(url)).length,
      0,
    );
    checks.push(
      'Old default URL opens precomputed results without a click, raw date reads or detail preload.',
    );

    await page
      .getByRole('combobox', { name: '选择一个股票', exact: true })
      .fill('000002');
    await page
      .getByRole('option', { name: '历史双向样例 000002', exact: true })
      .click();
    const detail = page.getByRole('region', {
      name: '个股三角全部历史与关键点位',
      exact: true,
    });
    await detail.locator('svg').waitFor({ timeout: 30000 });
    assert.equal(
      await detail
        .getByLabel('查看K线日期', { exact: true })
        .locator('option')
        .count(),
      180,
    );
    assert.ok(
      await detail.locator('[data-triangle-direction="golden"]').count(),
    );
    assert.ok(
      await detail.locator('[data-triangle-direction="death"]').count(),
    );
    assert.ok(
      (
        await detail
          .locator('[data-triangle-direction="golden"] text')
          .allTextContents()
      ).every((value) => /金三角 \d{4}-\d{2}-\d{2}/.test(value)),
    );
    assert.ok(
      (
        await detail
          .locator('[data-triangle-direction="death"] text')
          .allTextContents()
      ).every((value) => /死三角 \d{4}-\d{2}-\d{2}/.test(value)),
    );
    checks.push(
      'One symbol displays all saved history and visibly labels golden and death formations.',
    );
    const historicGolden = detail
      .locator('[data-triangle-direction="golden"]')
      .first();
    const id = await historicGolden.getAttribute('data-triangle-formation');
    await historicGolden.locator('text').click();
    assert.equal(
      await detail.getByLabel('选择历史形态', { exact: true }).inputValue(),
      id,
    );
    await detail
      .getByRole('region', { name: '选中历史三角依据', exact: true })
      .waitFor();
    assert.ok(await detail.getByText(/已失效|已到期/).count());
    checks.push(
      'Clicking an older chart triangle selects its frozen levels and retains its later inactive status.',
    );

    failPublication = true;
    await page.evaluate(() =>
      document.dispatchEvent(new Event('visibilitychange')),
    );
    await page.getByText(/本次后台更新未完成/).waitFor();
    assert.equal(
      await page.locator('[data-triangle-count="golden"]').textContent(),
      '1',
    );
    assert.ok(await detail.locator('svg').count());
    checks.push(
      'A failed next publication keeps the last screen and historical chart visible.',
    );
    failPublication = false;
    await page
      .getByRole('button', { name: '自定义参数研究', exact: true })
      .click();
    await page.getByRole('button', { name: '开始筛选', exact: true }).waitFor();
    await page
      .getByRole('button', { name: '返回每日自动结果', exact: true })
      .click();
    await page.locator('[data-triangle-count="golden"]').waitFor();
    checks.push(
      'Custom research remains explicit and can return to automatic daily results.',
    );
    await page.setViewportSize({ width: 375, height: 812 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    );
    checks.push('The daily view fits a 375px viewport.');
    assert.deepEqual(errors, []);
    if (output) {
      fs.mkdirSync(output, { recursive: true });
      await page.screenshot({
        path: path.join(output, 'daily-mobile.png'),
        fullPage: true,
      });
      fs.writeFileSync(
        path.join(output, 'daily-browser-verification.json'),
        JSON.stringify({ status: 'passed', checks, requests, errors }, null, 2),
      );
    }
    console.log(
      JSON.stringify({
        status: 'passed',
        checks,
        rawDayReads: requests.filter((url) => /^\/data\/\d{4}-/.test(url))
          .length,
        errors,
      }),
    );
  } finally {
    await context.close();
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
