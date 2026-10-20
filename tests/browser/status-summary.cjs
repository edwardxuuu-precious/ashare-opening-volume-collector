/* Local UI check with isolated data; no requests reach the production API. */
/* oxlint-disable typescript/no-require-imports -- executable CommonJS browser check */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const SITE = process.env.STATUS_SUMMARY_URL || 'http://127.0.0.1:3110';
const OUT = process.env.STATUS_SUMMARY_OUTPUT_DIR;
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(SITE).hostname));
const DATE = '2026-10-09';
const rows = [
  {
    code: '600000',
    name: '可计算示例',
    status: 'ok',
    first15Volume: 20,
    dailyVolume: 100,
    ratio: 20,
  },
  {
    code: '600001',
    name: '待补示例',
    status: 'missing',
    first15Volume: null,
    dailyVolume: 100,
    ratio: null,
    reason: '隔离样本缺少首15分钟记录',
  },
  {
    code: '600002',
    name: '停牌示例',
    status: 'suspended',
    first15Volume: null,
    dailyVolume: null,
    ratio: null,
  },
  {
    code: '600003',
    name: '合并示例',
    status: 'suspended',
    first15Volume: null,
    dailyVolume: null,
    ratio: null,
    specialStatus: {
      type: 'merger_pending_delisting',
      label: '合并停牌示例',
      description: '隔离样本：合并停牌说明',
      startedAt: DATE,
      source: '隔离样本',
      announcementUrl:
        'https://www.sse.com.cn/disclosure/dealinstruc/suspension/',
    },
  },
].map((row) => ({ market: 'SH', date: DATE, priceStatus: 'missing', ...row }));

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const checks = [];
  const errors = [];
  const blocked = [];
  let failed = false;
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 960 },
    });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const local = url.origin === new URL(SITE).origin;
      if (
        (!local && url.origin !== 'https://api.stock.iteready.com') ||
        request.method() !== 'GET'
      ) {
        blocked.push(request.method() + ' ' + url.origin + url.pathname);
        return route.abort();
      }
      const json = (value) =>
        route.fulfill({
          json: value,
          headers: {
            'access-control-allow-origin': new URL(SITE).origin,
            'access-control-allow-credentials': 'true',
          },
        });
      if (url.pathname === '/auth/me') return json({ username: '界面示例' });
      if (url.pathname === '/data/stocks.json')
        return json({ rows: rows.map(({ code, name }) => ({ code, name })) });
      if (url.pathname === '/data/manifest.json')
        return json({
          generatedAt: 'fixture',
          source: 'fixture',
          methodology: 'fixture',
          universeTotal: 4,
          tradingDates: [DATE],
          retentionStart: DATE,
          dates: [{ date: DATE, file: DATE + '.json', status: 'full' }],
        });
      if (url.pathname === '/data/collection-status.json')
        return json({
          id: 'fixture',
          state: failed ? 'failed' : 'completed',
          phase: 'close',
          completedStocks: 4,
          totalStocks: 4,
          latestDate: DATE,
          updatedAt: '2026-10-10T09:00:00+08:00',
          lastSuccessfulUpdate: '2026-10-10T08:17:00+08:00',
          dataComplete: true,
          priceDataComplete: true,
          publicationCommitted: true,
          message: 'fixture',
        });
      if (url.pathname === '/data/' + DATE + '.json')
        return json({
          date: DATE,
          generatedAt: 'fixture',
          source: 'fixture',
          methodology: 'fixture',
          scope: 'full',
          total: 4,
          rows,
        });
      if (!local || url.pathname.startsWith('/data/')) {
        blocked.push('unknown fixture ' + url.pathname);
        return route.abort();
      }
      return route.continue();
    });
    await page.goto(SITE + '/?date=' + DATE + '&scope=all');
    await page.locator('.results tbody tr').first().waitFor();
    const headers = await page.locator('.results thead th').allTextContents();
    assert.ok(!headers.some((text) => text.includes('状态')));
    assert.equal(await page.locator('.results .status-icon-ok').count(), 0);
    assert.equal(
      await page.locator('.results .status-icon-suspended').count(),
      1,
    );
    assert.equal(
      await page.locator('.research-topbar > .saved-date').count(),
      0,
    );
    assert.equal(await page.locator('.sync-status > .sync-state').count(), 0);
    checks.push('normal rows and the dedicated status column are hidden');

    await page.locator('.sync-details summary').click();
    const detail = page.locator('.sync-detail-panel');
    assert.match(await detail.innerText(), /行情日期：2026-10-09/);
    assert.match(await detail.innerText(), /最后成功同步：2026\/10\/10 08:17/);
    await page.locator('.sync-details summary').click();
    checks.push(
      'saved trading date differs from successful sync date without ambiguity',
    );

    await page
      .getByRole('button', { name: '数据待补，点击查看缺失原因' })
      .click();
    const missing = page.getByRole('dialog', { name: '缺失数据说明' });
    assert.match(await missing.innerText(), /隔离样本缺少首15分钟记录/);
    await missing.getByRole('button', { name: '关闭缺失说明' }).click();
    await page
      .getByRole('button', { name: '合并停牌示例，点击查看具体说明' })
      .click();
    const merger = page.getByRole('dialog', { name: '合并停牌示例' });
    assert.match(await merger.innerText(), /合并停牌说明/);
    assert.equal(
      await merger.getByRole('link', { name: '查看依据' }).count(),
      1,
    );
    await merger.getByRole('button', { name: '关闭状态说明' }).click();
    checks.push('missing and special suspension explanations remain usable');
    if (OUT) await page.screenshot({ path: OUT + '/table-summary.png' });

    await page.goto(SITE + '/?date=' + DATE + '&scope=single&symbols=600001');
    await page.locator('.results tbody tr').first().waitFor();
    assert.equal(await page.locator('.results td.stock-column').count(), 0);
    assert.equal(
      await page
        .locator(
          '.results td.trade-date .record-identity > .trading-status-trigger .status-icon-missing',
        )
        .count(),
      1,
    );
    await page
      .getByRole('button', { name: '数据待补，点击查看缺失原因' })
      .click();
    assert.match(
      await page.getByRole('dialog', { name: '缺失数据说明' }).innerText(),
      /隔离样本缺少/,
    );
    await page.getByRole('button', { name: '关闭缺失说明' }).click();
    checks.push('single-stock mode retains the exception next to its date');

    failed = true;
    await page.setViewportSize({ width: 375, height: 812 });
    await page.reload();
    await page.locator('.sync-status > .sync-state').waitFor();
    assert.match(
      await page.locator('.sync-status > .sync-state').innerText(),
      /同步失败/,
    );
    await page.locator('.sync-details summary').click();
    const bounds = await detail.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 375);
    checks.push(
      'mobile update details fit the viewport and failed sync remains visible',
    );
    if (OUT)
      await page.screenshot({ path: OUT + '/mobile-update-details.png' });
    assert.deepEqual(errors, []);
    assert.deepEqual(blocked, []);
    const result = {
      checks,
      errors,
      blocked,
      data: 'isolated fixtures; no production requests',
      headers,
    };
    if (OUT)
      fs.writeFileSync(
        OUT + '/browser-checks.json',
        JSON.stringify(result, null, 2) + '\n',
      );
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
