const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const SITE = process.env.UI_TEST_URL || 'http://localhost:4190';
const FIXTURES = process.env.UI_FIXTURE_DIR;
const OUT = process.env.UI_TEST_OUTPUT_DIR;
assert.ok(FIXTURES, 'UI_FIXTURE_DIR must point to isolated input snapshots');
assert.ok(['localhost', '127.0.0.1'].includes(new URL(SITE).hostname));
const meta = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'fixture-meta.json')));
const base = new URL(SITE).origin;
const results = [];
const only = process.env.UI_TEST_ONLY ? new RegExp(process.env.UI_TEST_ONLY) : null;
const readout = '.chip-candle-readout, .tv-candle-readout, .gw-readout';

async function setup(browser, options = {}) {
  const context = await browser.newContext({
    viewport: options.mobile ? { width: 375, height: 812 } : { width: 1440, height: 960 },
    hasTouch: Boolean(options.mobile),
    isMobile: Boolean(options.mobile),
    serviceWorkers: 'block',
  });
  const state = { fail: new Set(options.fail || []), authFailure: false, writes: [], errors: [] };
  await context.route('**/*', async (route) => {
    const request = route.request(), url = new URL(request.url());
    const headers = { 'Access-Control-Allow-Origin': base, 'Access-Control-Allow-Credentials': 'true' };
    if (url.origin === base && request.method() === 'GET') return route.continue();
    if (url.origin !== 'https://api.stock.iteready.com') return route.abort();
    const json = (value, status = 200) => route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(value) });
    if (request.method() !== 'GET') { state.writes.push(`${request.method()} ${url.pathname}`); return json({ error: 'Isolated check forbids API writes' }, 405); }
    if (url.pathname === '/auth/me') return state.authFailure ? json({ error: 'Session check unavailable' }, 404) : json({ username: '隔离界面核验' });
    if (state.fail.has(url.pathname)) return json({ error: 'Isolated read failure' }, 404);
    if (/^\/data\/golden-workbench\/annotations\/\w+\.json$/.test(url.pathname)) return json({ schema: 'stock.golden.annotations.v1', revision: 0, drawings: [], reviews: [] });
    const filename = path.resolve(FIXTURES, '.' + url.pathname);
    if (!filename.startsWith(path.resolve(FIXTURES) + path.sep) || !fs.existsSync(filename)) return json({ error: 'Snapshot not in isolated input' }, 404);
    return route.fulfill({ headers, contentType: 'application/json', body: fs.readFileSync(filename) });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  return { context, page, state };
}

async function open(page, route, selector) {
  await page.goto(SITE + route);
  await page.locator(selector).first().waitFor({ state: 'visible' });
}

async function check(browser, name, options, run) {
  if (only && !only.test(name)) return;
  const session = await setup(browser, options);
  try {
    await run(session.page, session.state);
    assert.deepEqual(session.state.writes, [], 'UI check must never write to an API');
    assert.deepEqual(session.state.errors, [], 'No uncaught browser errors');
    results.push({ name, status: 'passed' });
  } catch (error) {
    results.push({ name, status: 'failed', error: error.message });
    if (OUT) await session.page.screenshot({ path: path.join(OUT, name + '.png'), fullPage: true }).catch(() => {});
  } finally { await session.context.close(); }
  console.log(JSON.stringify(results.at(-1)));
}

async function tapCandle(page, date) {
  await page.locator(`[data-volume-date="${date}"]`).first().scrollIntoViewIfNeeded();
  const rect = await page.locator(`[data-volume-date="${date}"]`).first().boundingBox();
  assert.ok(rect, 'Target candle must be visible');
  await page.touchscreen.tap(rect.x + rect.width / 2, rect.y + Math.max(1, Math.min(3, rect.height / 2)));
}

(async () => {
  if (OUT) fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    const pages = [
      ['opening', `/?scope=all&date=${meta.latest}`, 'table tbody tr'],
      ['chips', '/chips', '.chip-candle-readout'],
      ['trend', '/trend', '.tv-candle-readout'],
      ['research', '/trend/research', '.tv-candle-readout'],
      ['golden', '/golden', '.gw-readout'],
      ['calibration', '/chips/calibration', '.chip-candle-readout'],
    ];
    for (const mobile of [false, true]) for (const [name, route, selector] of pages) {
      await check(browser, `${name}-${mobile ? 'mobile' : 'desktop'}`, { mobile }, async (page) => {
        await open(page, route, selector);
        assert.equal(await page.locator('.stock-header').count(), 1);
        const geometry = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, body: document.body.scrollWidth }));
        assert.ok(geometry.body <= geometry.viewport + 1, `Page horizontal overflow ${JSON.stringify(geometry)}`);
        if (name !== 'opening') {
          const row = page.locator(readout).first();
          const text = await row.innerText();
          assert.match(text.replace(/\s+/g, ' '), /\d{4}-\d{2}-\d{2}.*开.*高.*低.*收.*涨跌幅.*量/s);
          for (const field of await row.locator(':scope > *').all()) {
            if (!(await field.isVisible())) continue;
            const bounds = await field.boundingBox();
            assert.ok(bounds.x >= -1 && bounds.x + bounds.width <= geometry.viewport + 1, 'Readout field must fit viewport');
          }
          const chart = page.locator('.chip-trading-scroll svg, .tv-chart-scroll svg, .gw-canvas svg').first();
          const bounds = await chart.boundingBox();
          assert.ok(bounds.width <= geometry.viewport + 1, 'Chart must fit the available viewport');
        }
        if (OUT) await page.screenshot({ path: path.join(OUT, `${name}-${mobile ? 'mobile' : 'desktop'}.png`), fullPage: true });
      });
    }

    await check(browser, 'chip-search-focus-and-hover', {}, async (page) => {
      await open(page, '/chips', '.chip-candle-readout');
      const search = page.getByLabel('查找股票', { exact: true });
      await search.fill(' 000410 ');
      assert.equal(await page.locator('.cw-stock-row').count(), 1, 'Whitespace must not hide a matching stock');
      await search.fill('');
      const rows = page.locator('.cw-stock-row');
      await rows.first().click();
      await page.keyboard.press('ArrowDown');
      const active = await page.evaluate(() => document.activeElement?.getAttribute('data-stock-key'));
      const code = await page.locator('.cw-stock-row[aria-pressed="true"]').getAttribute('data-stock-key');
      assert.equal(active, code, 'Keyboard selection must move row focus');
      await page.keyboard.press('Enter');
      assert.equal(await page.locator('.cw-stock-row[aria-pressed="true"]').getAttribute('data-stock-key'), code);
      await page.getByRole('button', { name: '沈阳机床 000410', exact: true }).click();
      await page.getByLabel('筹码日期', { exact: true }).selectOption('2026-10-15');
      await page.waitForFunction(() => document.querySelector('.chip-candle-readout b')?.textContent === '2026-10-15');
      const rect = await page.locator('[data-volume-date="2026-10-14"]').boundingBox();
      await page.mouse.move(rect.x + rect.width / 2, rect.y + 2);
      await page.waitForFunction(() => document.querySelector('.chip-candle-readout b')?.textContent === '2026-10-14');
      const next = await page.locator('[data-volume-date="2026-10-16"]').boundingBox();
      const svg = await page.locator('.chip-trading-scroll svg').boundingBox();
      await page.mouse.move(next.x + next.width / 2, svg.y + 1);
      const day = await page.locator('.chip-candle-readout b').innerText();
      assert.ok((await page.locator('.cw-quote').innerText()).includes(day.slice(5)), 'Readout and quote must have the same preview or fixed date');
    });

    await check(browser, 'golden-search-and-filtered-navigation', {}, async (page) => {
      await open(page, '/golden', '.gw-readout');
      const search = page.getByLabel('查找核对样本', { exact: true });
      await search.fill(' SH000001 ');
      assert.equal(await page.locator('.gw-stock').count(), 1);
      await page.locator('.gw-stock').click();
      await page.locator('.gw-detail-top h2').click();
      await page.keyboard.press('ArrowDown');
      assert.ok((await page.locator('.gw-detail-top h2').innerText()).includes('上证指数'), 'Filtered keyboard navigation cannot choose hidden samples');
      await search.fill('does-not-exist');
      assert.equal(await page.locator('.gw-stock').count(), 0);
      assert.match(await page.locator('.gw-sidebar').innerText(), /没有|暂无|无结果/);
      await page.keyboard.press('ArrowDown');
      assert.ok((await page.locator('.gw-detail-top h2').innerText()).includes('上证指数'));
    });

    await check(browser, 'chart-volume-format', {}, async (page) => {
      await open(page, '/golden', '.gw-readout');
      assert.match((await page.locator('.gw-readout').innerText()).replace(/\s/g, ''), /量1725\.55万股/);
      await open(page, '/chips', '.chip-candle-readout');
      await page.getByLabel('筹码日期', { exact: true }).selectOption('2026-10-15');
      await page.waitForFunction(() => document.querySelector('.chip-candle-readout b')?.textContent === '2026-10-15');
      assert.match((await page.locator('.chip-candle-readout').innerText()).replace(/\s/g, ''), /量1766\.01万股/);
      await open(page, '/trend', '.tv-candle-readout');
      const tonesMatch = () => page.evaluate(() => {
        const header = document.querySelector('.wb-stock-header .cw-quote strong.candle-change-up, .wb-stock-header .cw-quote strong.candle-change-down');
        const chart = document.querySelector('.tv-candle-readout .candle-change strong');
        return Boolean(header && chart && getComputedStyle(header).color === getComputedStyle(chart).color);
      });
      assert.equal(await tonesMatch(), true, 'Positive return colors must agree in the header and chart');
      await page.locator('.wb-filters').getByRole('button', { name: /^全部/ }).click();
      await page.getByLabel('搜索当日股票', { exact: true }).fill(meta.negative_code);
      await page.locator('.tv-candle-readout .candle-change-down').waitFor();
      assert.equal(await tonesMatch(), true, 'Negative return colors must agree in the header and chart');
    });

    await check(browser, 'opening-search-and-record-details', {}, async (page) => {
      await open(page, `/?scope=single&symbols=000410&date=${meta.latest}`, '.results tbody tr');
      await page.getByLabel('选择一个股票', { exact: true }).fill(' 000415 ');
      await page.getByRole('option', { name: '渤海租赁 000415', exact: true }).click();
      await page.getByRole('heading', { name: '渤海租赁', exact: true }).waitFor();
      await page.locator('.results tbody tr').first().getByRole('button', { name: /查看.*明细/ }).click();
      await page.getByRole('dialog').waitFor();
      const text = await page.getByRole('dialog').innerText();
      for (const label of ['开盘', '收盘', '最高', '最低', '涨跌幅']) assert.ok(text.includes(label), `Record detail must show ${label}`);
      await page.keyboard.press('Escape');
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
    });

    await check(browser, 'opening-query-refresh-recovery', {}, async (page, state) => {
      await open(page, `/?scope=single&symbols=000410&date=${meta.latest}`, '.results tbody tr');
      const before = await page.locator('.results tbody').innerText();
      state.fail.add(`/data/${meta.latest}.json`);
      await page.getByRole('button', { name: '重新读取当前查询', exact: true }).click();
      await page.getByText('刷新失败，仍显示上次结果。', { exact: true }).waitFor();
      assert.equal(await page.locator('.results tbody').innerText(), before);
      state.fail.clear();
      await page.getByRole('alert').getByRole('button', { name: '重新读取', exact: true }).click();
      await page.getByText('刷新失败，仍显示上次结果。', { exact: true }).waitFor({ state: 'hidden' });
      assert.equal(await page.locator('.results tbody').innerText(), before);
    });

    await check(browser, 'opening-initial-query-retry', { fail: [`/data/${meta.latest}.json`] }, async (page, state) => {
      await open(page, `/?scope=single&symbols=000410&date=${meta.latest}`, '.wb-state');
      await page.getByText('所选日期无法读取', { exact: true }).waitFor();
      state.fail.clear();
      await page.locator('.wb-state').getByRole('button', { name: '重新读取', exact: true }).click();
      await page.locator('.results tbody tr').first().waitFor();
    });

    await check(browser, 'opening-stock-catalog-retry', { fail: ['/data/stocks.json'] }, async (page, state) => {
      await open(page, `/?scope=single&symbols=000410&date=${meta.latest}`, '.catalog-error');
      state.fail.clear();
      await page.locator('.catalog-error').getByRole('button', { name: '重新读取', exact: true }).click();
      await page.getByRole('heading', { name: '沈阳机床', exact: true }).waitFor();
      await page.locator('.catalog-error').waitFor({ state: 'hidden' });
    });

    await check(browser, 'opening-multiple-stock-selection', {}, async (page) => {
      await open(page, `/?scope=all&date=${meta.latest}`, '.results tbody tr');
      await page.getByRole('radiogroup', { name: '股票范围', exact: true }).getByText('多只', { exact: true }).click();
      const search = page.getByLabel('选择多个股票', { exact: true });
      await search.fill(' 000410 ');
      await page.getByRole('option', { name: '沈阳机床 000410', exact: true }).click();
      await search.fill('000415');
      await page.getByRole('option', { name: '渤海租赁 000415', exact: true }).click();
      await page.waitForFunction(() => document.querySelectorAll('.results tbody tr').length === 2);
      await page.getByRole('button', { name: '移除 沈阳机床 000410', exact: true }).click();
      await page.waitForFunction(() => document.querySelectorAll('.results tbody tr').length === 1);
      assert.equal(new URL(await page.url()).searchParams.get('symbols'), '000415');
    });

    for (const mobile of [false, true]) await check(browser, `golden-overview-${mobile ? 'mobile' : 'desktop'}`, { mobile }, async (page) => {
      await open(page, '/golden', '.gw-readout');
      await page.locator('.gw-overview-link').click();
      await page.locator('.gw-preview').first().waitFor();
      assert.equal(await page.locator('.gw-preview').count(), 20);
      const bounds = await page.evaluate(() => ({ width: document.documentElement.clientWidth, body: document.body.scrollWidth }));
      assert.ok(bounds.body <= bounds.width + 1);
      if (OUT) await page.screenshot({ path: path.join(OUT, `golden-overview-${mobile ? 'mobile' : 'desktop'}.png`) });
      await page.locator('.gw-preview').first().click();
      await page.locator('.gw-detail-top h2').filter({ hasText: '双成药业' }).waitFor();
    });

    await check(browser, 'research-period-persists', {}, async (page) => {
      await open(page, '/trend/research', '.tv-candle-readout');
      await page.getByRole('button', { name: '60根', exact: true }).click();
      await page.locator('.wb-filters').getByRole('button', { name: /^全部/ }).click();
      await page.getByLabel('搜索研究样本', { exact: true }).fill('600000');
      await page.locator('.tv-row').first().click();
      await page.locator('.tv-candle-readout').waitFor();
      assert.equal(await page.getByRole('button', { name: '60根', exact: true }).getAttribute('aria-pressed'), 'true');
    });

    for (const route of ['/trend', '/trend/research']) await check(browser, 'unknown-return-' + route.replaceAll('/', '-'), {}, async (page) => {
      await open(page, route, '.tv-candle-readout');
      await page.locator('.wb-filters').getByRole('button', { name: /^数据不足/ }).click();
      const values = page.locator(route === '/trend' ? '.cw-quote small span' : '.tv-row b').filter({ hasText: /^待核实$/ });
      await values.first().waitFor();
      for (const value of await values.all()) assert.doesNotMatch((await value.getAttribute('class')) || '', /(?:tv|candle-change)-(?:up|down)/, 'Unknown returns must not be colored as a rise or fall');
    });

    for (const [name, route, rowSelector, selectedSelector, searchLabel] of [
      ['chips', '/chips', '.cw-stock-row', '.cw-stock-row[aria-pressed="true"]', '查找股票'],
      ['trend', '/trend', '.tv-row', '.tv-row[aria-pressed="true"]', '搜索当日股票'],
      ['research', '/trend/research', '.tv-row', '.tv-row[aria-pressed="true"]', '搜索研究样本'],
      ['golden', '/golden', '.gw-stock', '.gw-stock[aria-pressed="true"]', '查找核对样本'],
    ]) await check(browser, `list-keyboard-${name}`, {}, async (page) => {
      await open(page, route, rowSelector);
      const rows = page.locator(rowSelector);
      await rows.first().click();
      await page.keyboard.press('ArrowDown');
      const selected = await page.locator(selectedSelector).getAttribute('data-stock-key');
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-stock-key')), selected);
      await page.keyboard.press('Enter');
      assert.equal(await page.locator(selectedSelector).getAttribute('data-stock-key'), selected);
      await page.keyboard.press('Control+ArrowDown');
      assert.equal(await page.locator(selectedSelector).getAttribute('data-stock-key'), selected);
      await page.getByLabel(searchLabel, { exact: true }).focus();
      await page.keyboard.press('ArrowDown');
      assert.equal(await page.locator(selectedSelector).getAttribute('data-stock-key'), selected);
    });

    await check(browser, 'golden-period-persists-and-drawing', { mobile: true }, async (page) => {
      await open(page, '/golden', '.gw-readout');
      await page.getByRole('button', { name: '120根', exact: true }).click();
      await page.locator('.gw-stock[data-stock-key="300788"]').click();
      await page.locator('.gw-detail-top h2').filter({ hasText: '中信出版' }).waitFor();
      assert.equal(await page.getByRole('button', { name: '120根', exact: true }).getAttribute('aria-pressed'), 'true');
      await page.locator('.gw-stock[data-stock-key="002693"]').click();
      await page.locator('.gw-detail-top h2').filter({ hasText: '双成药业' }).waitFor();
      await page.getByRole('button', { name: '新增画线', exact: true }).click();
      const high = page.locator('[data-candle-date="2026-10-14"] line').first();
      await high.scrollIntoViewIfNeeded();
      const h = await high.boundingBox(), l = await page.locator('[data-candle-date="2026-10-30"] line').first().boundingBox();
      await page.mouse.move(h.x, h.y);
      await page.mouse.down();
      await page.mouse.move(l.x, l.y + l.height, { steps: 8 });
      await page.mouse.up();
      assert.equal(await page.locator('[data-manual-anchor]').count(), 2);
      assert.doesNotMatch(await page.locator('.gw-snap-readout').innerText(), /已取消/);
      await page.getByRole('button', { name: '撤销人工画线调整', exact: true }).click();
      assert.equal(await page.locator('[data-manual-anchor]').count(), 0);
    });

    await check(browser, 'daily-period-persists', {}, async (page) => {
      await open(page, '/trend', '.tv-candle-readout');
      await page.getByRole('button', { name: '60根', exact: true }).click();
      await page.locator('.tv-row').filter({ hasText: '000676' }).click();
      await page.locator('.tv-stock-heading h2').filter({ hasText: '智度股份' }).waitFor();
      await page.locator('.tv-candle-readout').waitFor();
      assert.equal(await page.getByRole('button', { name: '60根', exact: true }).getAttribute('aria-pressed'), 'true');
    });

    for (const route of ['/trend', '/trend/research', '/golden']) await check(browser, 'tabs-' + route.replaceAll('/', '-'), {}, async (page) => {
      await open(page, route, route === '/golden' ? '.gw-readout' : '.tv-candle-readout');
      const tabs = page.getByRole('tablist').getByRole('tab');
      await tabs.first().focus();
      await page.keyboard.press('End');
      assert.equal(await tabs.last().getAttribute('aria-selected'), 'true');
      assert.equal(await tabs.last().getAttribute('tabindex'), '0');
      await page.keyboard.press('Home');
      assert.equal(await tabs.first().getAttribute('aria-selected'), 'true');
      assert.equal(await tabs.last().getAttribute('tabindex'), '-1');
    });

    for (const route of ['/chips', '/trend', '/golden']) await check(browser, 'inspection-' + route.slice(1), { mobile: true }, async (page) => {
      await open(page, route, readout);
      const bars = await page.locator('[data-candle-date]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-candle-date')));
      const target = bars.at(-3);
      await tapCandle(page, target);
      assert.equal(await page.locator(readout).first().locator('b').first().innerText(), target);
      const dateControl = page.getByLabel('查看K线日期', { exact: true });
      await dateControl.focus();
      await page.keyboard.press('ArrowLeft');
      assert.equal(await page.locator(readout).first().locator('b').first().innerText(), bars.at(-4));
    });

    await check(browser, 'chip-detail-retry', { fail: [`/data/chips/daily/${meta.chip_dataset}/000596.json`] }, async (page, state) => {
      await open(page, '/chips', '.chip-candle-readout');
      await page.getByRole('button', { name: '古井贡酒 000596', exact: true }).click();
      const retry = page.locator('.cw-workspace').getByRole('button', { name: '重新读取', exact: true });
      await retry.waitFor({ state: 'visible' });
      state.fail.clear();
      await retry.click();
      await page.locator('.cw-stock-header h2').filter({ hasText: '古井贡酒' }).waitFor();
    });

    await check(browser, 'golden-initial-error-recovery', { fail: ['/data/golden-workbench/manifest.json'] }, async (page, state) => {
      await page.goto(SITE + '/golden');
      await page.getByRole('alert').first().waitFor();
      assert.equal(await page.getByText('正在读取20个每日样本…', { exact: true }).count(), 0);
      assert.equal(await page.getByText('正在读取K线与完整选点证据…', { exact: true }).count(), 0);
      state.fail.clear();
      await page.getByRole('button', { name: '重新读取', exact: true }).first().click();
      await page.locator('.gw-readout').waitFor();
    });

    await check(browser, 'golden-detail-retry', {}, async (page, state) => {
      await open(page, '/golden', '.gw-readout');
      state.fail.add(`/data/golden-workbench/${meta.golden_dataset}/300788.json`);
      await page.locator('.gw-stock').filter({ hasText: '300788' }).click();
      const retry = page.locator('.gw-workspace').getByRole('button', { name: '重新读取', exact: true });
      await retry.waitFor();
      state.fail.clear();
      await retry.click();
      await page.locator('.gw-detail-top h2').filter({ hasText: '中信出版' }).waitFor();
    });

    await check(browser, 'research-refresh-preserves-results', {}, async (page, state) => {
      await open(page, '/trend/research', '.tv-candle-readout');
      const before = await page.locator('.tv-candle-readout').innerText();
      state.fail.add('/data/trend-volume/manifest.json');
      await page.getByRole('button', { name: '刷新研究快照', exact: true }).click();
      await page.getByRole('alert').first().waitFor();
      assert.equal(await page.locator('.tv-candle-readout').innerText(), before);
      assert.equal(await page.getByLabel('统计时间段', { exact: true }).isVisible(), true);
      state.fail.clear();
      await page.getByRole('button', { name: '重新读取', exact: true }).first().click();
      await page.waitForFunction(() => !document.querySelector('[role="alert"]'));
      assert.equal(await page.locator('.tv-candle-readout').innerText(), before);
    });

    await check(browser, 'calibration-screen-retry-preserves-context', { fail: [`/data/chips/${meta.legacy_dataset}/screening.json`] }, async (page, state) => {
      await open(page, '/chips/calibration', '.chip-candle-readout');
      await page.getByLabel('筹码股票', { exact: true }).selectOption(meta.legacy_codes[1]);
      await page.locator('.chip-candle-readout').waitFor();
      const code = await page.getByLabel('筹码股票', { exact: true }).inputValue();
      const date = await page.getByLabel('筹码日期', { exact: true }).inputValue();
      await page.getByRole('button', { name: '样本数据检查', exact: true }).click();
      const retry = page.getByRole('button', { name: '重新读取', exact: true });
      await retry.waitFor();
      state.fail.clear();
      await retry.click();
      await page.locator('section[aria-label="筹码筛选结果"] tbody tr').first().waitFor();
      await page.getByRole('button', { name: '筹码分布', exact: true }).click();
      assert.equal(await page.getByLabel('筹码股票', { exact: true }).inputValue(), code);
      assert.equal(await page.getByLabel('筹码日期', { exact: true }).inputValue(), date);
    });

    await check(browser, 'login-session-check-retry', {}, async (page, state) => {
      state.authFailure = true;
      await page.goto(SITE + '/chips');
      await page.getByRole('alert').waitFor();
      const retry = page.getByRole('button', { name: '重新验证登录状态', exact: true });
      await retry.waitFor();
      state.authFailure = false;
      await retry.click();
      await page.locator('.chip-candle-readout').waitFor();
    });

    await check(browser, 'calibration-refresh-preserves-results', {}, async (page, state) => {
      await open(page, '/chips/calibration', '.chip-candle-readout');
      const before = await page.locator('.chip-candle-readout').innerText();
      const code = await page.getByLabel('筹码股票', { exact: true }).inputValue();
      await page.getByRole('button', { name: '样本数据检查', exact: true }).click();
      const table = page.locator('section[aria-label="筹码筛选结果"] tbody tr');
      await table.first().waitFor();
      const count = await table.count();
      state.fail.add(`/data/chips/${meta.legacy_dataset}/${code}.json`);
      state.fail.add(`/data/chips/${meta.legacy_dataset}/screening.json`);
      await page.getByRole('button', { name: '刷新历史筹码校准', exact: true }).click();
      await page.locator('section[aria-label="筹码筛选结果"]').getByRole('button', { name: '重新读取', exact: true }).waitFor();
      assert.equal(await table.count(), count, 'A failed refresh must retain the last complete table');
      await page.getByRole('button', { name: '筹码分布', exact: true }).click();
      assert.equal(await page.locator('.chip-candle-readout').innerText(), before, 'A failed refresh must retain the last complete chart');
    });

    await check(browser, 'calibration-period-persists', {}, async (page) => {
      await open(page, '/chips/calibration', '.chip-candle-readout');
      await page.getByRole('button', { name: '60根', exact: true }).click();
      await page.getByLabel('筹码股票', { exact: true }).selectOption(meta.legacy_codes[1]);
      await page.locator('.chip-candle-readout').waitFor();
      assert.equal(await page.getByRole('button', { name: '60根', exact: true }).getAttribute('aria-pressed'), 'true');
    });
  } finally { await browser.close(); }
  const summary = { passed: results.filter((r) => r.status === 'passed').length, failed: results.filter((r) => r.status === 'failed').length, results };
  if (OUT) fs.writeFileSync(path.join(OUT, 'results.json'), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify({ passed: summary.passed, failed: summary.failed }));
  process.exitCode = summary.failed ? 1 : 0;
})().catch((error) => { console.error(error); process.exitCode = 1; });
