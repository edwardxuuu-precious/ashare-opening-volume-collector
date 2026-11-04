/* oxlint-disable typescript/no-require-imports -- Standalone CommonJS browser harness. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const SITE = process.env.TRIANGLE_UI_URL || 'http://127.0.0.1:3115';
const origin = new URL(SITE).origin;
assert.ok(['localhost', '127.0.0.1'].includes(new URL(SITE).hostname));
const OUT = process.env.TRIANGLE_UI_OUTPUT_DIR;
const checks = [];
const errors = [];
const blocked = [];
const mark = (name) => checks.push(name);

(async () => {
  const { triangleFixtureBars: bars, triangleFixtureAsOf: asOf } =
    await import('../frontend/fixtures/triangles.mjs');
  const dates = bars.map((bar) => bar.date);
  const securities = [
    { code: '000001', name: '金三角样例', market: 'SZ' },
    { code: '600000', name: '死三角样例', market: 'SH' },
    { code: '000003', name: '短历史样例', market: 'SZ' },
    { code: '600004', name: '历史后缺口样例', market: 'SH' },
    { code: '000005', name: '小回撤样例', market: 'SZ' },
  ];
  if (OUT) fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  let currentPage;
  async function setup({
    mobile = false,
    calendarKnown = true,
    hold = false,
  } = {}) {
    const context = await browser.newContext({
      viewport: mobile
        ? { width: 375, height: 812 }
        : { width: 1440, height: 960 },
      isMobile: mobile,
      hasTouch: mobile,
      serviceWorkers: 'block',
    });
    const page = await context.newPage();
    currentPage = page;
    const dayReads = [];
    const pending = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await context.route('**/*', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (
        ![origin, 'https://api.stock.iteready.com'].includes(url.origin) ||
        request.method() !== 'GET'
      ) {
        blocked.push(request.method() + ' ' + url.origin + url.pathname);
        return route.abort();
      }
      const json = (value) =>
        route.fulfill({
          json: value,
          headers: {
            'access-control-allow-origin': origin,
            'access-control-allow-credentials': 'true',
            'cache-control': 'no-store',
          },
        });
      if (url.pathname === '/auth/me')
        return json({ username: '隔离样例验收' });
      if (url.pathname === '/data/stocks.json')
        return json({ rows: securities });
      if (url.pathname === '/data/manifest.json')
        return json({
          generatedAt: '2026-10-07T08:00:00Z',
          source: 'synthetic fixture only',
          methodology: 'fixture',
          universeTotal: securities.length,
          ...(calendarKnown ? { tradingDates: dates } : {}),
          dates: [...dates].reverse().map((date) => ({
            date,
            file: `${date}.json`,
            status: 'full',
            total: securities.length,
            valid: securities.length,
            missing: 0,
            suspended: 0,
          })),
        });
      if (url.pathname === '/data/collection-status.json')
        return json({
          state: 'completed',
          latestDate: asOf,
          dataComplete: true,
        });
      const date = url.pathname.match(
        /^\/data\/(\d{4}-\d{2}-\d{2})\.json$/,
      )?.[1];
      if (date) {
        dayReads.push(date);
        if (hold) await new Promise((resolve) => pending.push(resolve));
        const index = dates.indexOf(date);
        if (index < 0) return route.fulfill({ status: 404 });
        const rows = securities.map((security) => {
          let quote = bars[index];
          if (security.code === '600000') {
            const close =
              index < 80 ? 50 + index * 0.8 : 113 - (index - 80) * 1.5;
            quote = { open: close, high: close + 0.2, low: close - 0.2, close };
          }
          if (security.code === '000005') {
            const close =
              index < 80 ? 100 - index * 0.05 : 96 + (index - 80) * 0.3;
            quote = {
              open: close,
              high: close + 0.02,
              low: close - 0.02,
              close,
            };
          }
          const missing =
            (security.code === '000003' && index < 83) ||
            (security.code === '600004' && index === dates.length - 1);
          return {
            ...security,
            status: 'ok',
            first15Volume: 10,
            dailyVolume: 100,
            ratio: 10,
            priceStatus: missing ? 'missing' : 'available',
            open: missing ? null : quote.open,
            high: missing ? null : quote.high,
            low: missing ? null : quote.low,
            close: missing ? null : quote.close,
          };
        });
        return json({
          date,
          generatedAt: 'fixture',
          source: 'synthetic fixture only',
          methodology: 'fixture',
          scope: 'full',
          total: rows.length,
          rows,
        });
      }
      if (url.origin !== origin || url.pathname.startsWith('/data/')) {
        blocked.push('unknown fixture ' + url.pathname);
        return route.abort();
      }
      return route.continue();
    });
    await page.goto(SITE + '/triangles');
    await page
      .getByRole('button', { name: '开始筛选', exact: true })
      .waitFor({ timeout: 60000 });
    return {
      page,
      context,
      dayReads,
      release: () => {
        hold = false;
        pending.splice(0).forEach((resolve) => resolve());
      },
    };
  }
  const screen = (page) =>
    page.getByRole('region', { name: '三角筛选结果', exact: true });
  const detail = (page) =>
    page.getByRole('region', { name: '个股三角与关键点位', exact: true });
  async function run(page) {
    await page
      .getByRole('button', { name: /^(开始筛选|应用参数并筛选)$/ })
      .click();
    await screen(page).waitFor({ timeout: 60000 });
  }
  async function noOverflow(page) {
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
      'page must fit viewport; chart and tables scroll inside their containers',
    );
  }
  try {
    const desktop = await setup();
    const page = desktop.page;
    assert.equal(
      await page.getByLabel('短均线', { exact: true }).inputValue(),
      '5',
    );
    assert.equal(
      await page
        .getByLabel('金三角前最小跌幅 (%)', { exact: true })
        .inputValue(),
      '20',
    );
    assert.equal(desktop.dayReads.length, 0);
    mark('页面按默认参数展示，点击前不读取历史行情');
    await run(page);
    await page.waitForFunction(
      () =>
        document.querySelectorAll('.triangle-results tbody tr').length === 2,
    );
    assert.match(await screen(page).innerText(), /金三角样例/);
    assert.match(await screen(page).innerText(), /死三角样例/);
    assert.match(await screen(page).innerText(), /数据不足 2 只/);
    assert.equal(desktop.dayReads.length, dates.length);
    assert.equal(
      await page.locator('.triangle-chart-svg path[stroke="#b77e20"]').count(),
      1,
    );
    assert.equal(await detail(page).locator('.triangle-zone-card').count(), 2);
    assert.match(
      await detail(page).locator('.triangle-zone-card').first().innerText(),
      /参考价 80.00/,
    );
    assert.match(
      await detail(page).locator('.triangle-zone-card').last().innerText(),
      /参考价 130.00/,
    );
    assert.match(
      await detail(page).locator('.triangle-paths').innerText(),
      new RegExp(dates[90] + ' 确认'),
    );
    assert.match(
      await detail(page).locator('.triangle-paths').innerText(),
      new RegExp(dates[92] + ' 确认'),
    );
    assert.match(
      await detail(page).locator('.triangle-thresholds').innerText(),
      /132.6097/,
    );
    mark('默认规则筛出金三角和死三角，并显示两条独立确认、冻结价区及具体阈值');
    await page
      .getByLabel('查看K线日期', { exact: true })
      .selectOption(dates[89]);
    assert.match(
      await page.locator('.triangle-chart-readout').innerText(),
      /低 80.80/,
    );
    await noOverflow(page);
    if (OUT) {
      await detail(page).screenshot({
        path: path.join(OUT, 'desktop-detail.png'),
      });
      await page.screenshot({
        path: path.join(OUT, 'desktop-page.png'),
        fullPage: true,
      });
    }
    mark('桌面价格图、交叉与事件标记可见，日期可用键盘控件切换');

    const readsBefore = desktop.dayReads.length;
    await page.getByLabel('金三角前最小跌幅 (%)', { exact: true }).fill('95');
    assert.match(
      await page.locator('output.triangle-notice').innerText(),
      /上一次应用/,
    );
    await run(page);
    await page.waitForFunction(
      () =>
        document.querySelectorAll('.triangle-results tbody tr').length === 1,
    );
    assert.equal(desktop.dayReads.length, readsBefore);
    assert.ok(new URL(page.url()).searchParams.has('minDeclinePct'));
    mark('调整参数重新筛选复用已读行情，并明确区分参数草稿与已应用结果');
    await page
      .getByRole('button', { name: '恢复默认参数', exact: true })
      .click();
    await page.locator('.triangle-advanced summary').click();
    await page.getByLabel('形成后有效交易日', { exact: true }).fill('1');
    await run(page);
    await page.getByLabel('结果状态', { exact: true }).selectOption('expired');
    await page.getByLabel('信号更新范围', { exact: true }).selectOption('0');
    await page.waitForFunction(() =>
      document
        .querySelector('.triangle-results')
        ?.textContent.includes('历史后缺口样例'),
    );
    assert.equal(desktop.dayReads.length, readsBefore);
    mark('最新缺口不会删除已有到期历史，历史状态仍可筛选');

    await page
      .getByRole('button', { name: '恢复默认参数', exact: true })
      .click();
    await page
      .getByLabel('历史截止日', { exact: true })
      .selectOption(dates[90]);
    const prefixStart = desktop.dayReads.length;
    await run(page);
    assert.ok(
      desktop.dayReads.slice(prefixStart).every((date) => date <= dates[90]),
    );
    await page
      .getByLabel('切换已读取股票', { exact: true })
      .selectOption('000001');
    assert.match(
      await detail(page).locator('.triangle-paths').innerText(),
      /突破压力路径\s*等待触发/,
    );
    assert.doesNotMatch(
      await detail(page).innerText(),
      new RegExp(dates[92] + ' 确认'),
    );
    mark('历史截止日截断读取与标记，不提前显示未来突破确认');
    await page.getByLabel('短均线', { exact: true }).fill('10');
    await page
      .getByRole('button', { name: '应用参数并筛选', exact: true })
      .click();
    assert.match(await page.getByRole('alert').innerText(), /短均线 < 中均线/);
    mark('无效均线顺序被明确拒绝');
    await desktop.context.close();

    const mobile = await setup({ mobile: true });
    await run(mobile.page);
    await noOverflow(mobile.page);
    await mobile.page.waitForFunction(
      () => document.querySelector('.triangle-chart-scroll').scrollLeft > 0,
    );
    await mobile.page
      .getByLabel('股票范围', { exact: true })
      .selectOption('single');
    await mobile.page
      .getByRole('combobox', { name: '选择一个股票', exact: true })
      .fill('000001');
    await mobile.page
      .getByRole('option', { name: /金三角样例 000001/ })
      .click();
    await mobile.page.getByRole('listbox').waitFor({ state: 'hidden' });
    await run(mobile.page);
    assert.match(await screen(mobile.page).innerText(), /1只已检查/);
    await noOverflow(mobile.page);
    if (OUT)
      await mobile.page.screenshot({
        path: path.join(OUT, 'mobile-page.png'),
        fullPage: true,
      });
    mark('手机可选择单只股票筛选，页面无整体横向溢出');
    await mobile.page
      .getByLabel('股票范围', { exact: true })
      .selectOption('multiple');
    await mobile.page
      .getByRole('combobox', { name: '选择多个股票', exact: true })
      .fill('600000');
    await mobile.page
      .getByRole('option', { name: /死三角样例 600000/ })
      .click();
    await run(mobile.page);
    assert.match(await screen(mobile.page).innerText(), /2只已检查/);
    await noOverflow(mobile.page);
    mark('指定多只股票使用相同规则，不混入其余样本');
    await mobile.page
      .getByRole('link', { name: '开盘量查询', exact: true })
      .click();
    await mobile.page
      .getByRole('link', { name: /^趋势三角(?:与关键点位)?$/, exact: true })
      .waitFor({ timeout: 10000 });
    await mobile.page
      .getByRole('link', { name: /^趋势三角(?:与关键点位)?$/, exact: true })
      .click();
    await mobile.page
      .getByRole('heading', { name: /^趋势三角(?:与关键点位)?$/, exact: true })
      .waitFor();
    mark('正式静态产物的首页入口和页面导航可用');
    await mobile.context.close();

    const unknown = await setup({ calendarKnown: false });
    await run(unknown.page);
    await unknown.page
      .getByRole('button', { name: '查看全部状态与历史', exact: true })
      .click();
    assert.match(await screen(unknown.page).innerText(), /当前有效观察 0 只/);
    assert.doesNotMatch(
      await detail(unknown.page).innerText(),
      /突破压力已确认|回踩支撑已确认/,
    );
    assert.equal(
      await detail(unknown.page).locator('.triangle-paths').count(),
      0,
    );
    assert.equal(
      await detail(unknown.page).locator('svg path[stroke="#b77e20"]').count(),
      1,
    );
    assert.equal(
      await detail(unknown.page)
        .locator('svg path[stroke="#b77e20"]')
        .getAttribute('d'),
      '',
    );
    mark('没有交易日历时，列表和个股详情均保留待判定，不生成均线确认');
    await unknown.context.close();

    const cancelled = await setup({ hold: true });
    await cancelled.page
      .getByRole('button', { name: '开始筛选', exact: true })
      .click();
    await cancelled.page
      .getByRole('button', { name: '取消', exact: true })
      .click();
    cancelled.release();
    await cancelled.page.waitForTimeout(200);
    assert.equal(await screen(cancelled.page).count(), 0);
    assert.equal(cancelled.dayReads.length <= 2, true);
    mark('取消中止日线读取，迟到响应不会生成结果');
    await cancelled.context.close();
    assert.deepEqual(errors, []);
    assert.deepEqual(blocked, []);
    mark('浏览器没有运行错误，全部数据请求由隔离样例响应且无采集写请求');
    const result = {
      status: 'passed',
      fixtureOnly: true,
      checks,
      errors,
      blocked,
    };
    if (OUT)
      fs.writeFileSync(
        path.join(OUT, 'browser-verification.json'),
        JSON.stringify(result, null, 2) + '\n',
      );
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    if (currentPage && !currentPage.isClosed())
      console.error(
        JSON.stringify(
          {
            url: currentPage.url(),
            errors,
            blocked,
            headings: await currentPage.locator('h1,h2').allTextContents(),
            links: await currentPage
              .locator('a')
              .evaluateAll((links) =>
                links.map((link) => [
                  link.textContent,
                  link.getAttribute('href'),
                ]),
              ),
          },
          null,
          2,
        ),
      );
    throw error;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
