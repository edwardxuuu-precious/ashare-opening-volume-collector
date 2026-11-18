const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const SITE = process.env.UI_TEST_URL || 'http://localhost:4190';
const INPUT = process.env.UI_FIXTURE_DIR;
const OUT = process.env.UI_TEST_OUTPUT_DIR;
assert.ok(
  INPUT && OUT,
  'Use isolated snapshots and an explicit artifact directory',
);
assert.ok(['localhost', '127.0.0.1'].includes(new URL(SITE).hostname));
const origin = new URL(SITE).origin;
const meta = JSON.parse(fs.readFileSync(path.join(INPUT, 'fixture-meta.json')));
const results = [],
  geometry = {};
const only = process.env.UI_TEST_ONLY
  ? new RegExp(process.env.UI_TEST_ONLY)
  : null;

async function setup(browser, options = {}) {
  const context = await browser.newContext({
    viewport: { width: options.width || 1440, height: 960 },
    hasTouch: options.width === 375,
    serviceWorkers: 'block',
  });
  const state = {
    expireOn: '',
    auth: options.auth !== false,
    authReads: 0,
    documents: 0,
    marketReads: 0,
    errors: [],
    writes: [],
    blockedAssets: new Set(options.blockedAssets || []),
    fail: new Set(options.fail || []),
    holds: new Set(options.hold || []),
    releases: [],
  };
  state.release = (key) => {
    state.holds.delete(key);
    for (const release of state.releases.filter((item) => item.key === key))
      release.resolve();
    state.releases = state.releases.filter((item) => item.key !== key);
  };
  await context.route('**/*', async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    if (url.origin === origin) {
      if (state.blockedAssets.has(url.pathname)) return route.abort();
      if (request.isNavigationRequest()) state.documents++;
      return route.continue();
    }
    if (url.origin !== 'https://api.stock.iteready.com') return route.abort();
    const headers = {
      'Access-Control-Allow-Origin': origin,
      'Access-Control-Allow-Credentials': 'true',
    };
    const json = (value, status = 200) =>
      route.fulfill({
        status,
        headers,
        contentType: 'application/json',
        body: JSON.stringify(value),
      });
    if (request.method() !== 'GET') {
      state.writes.push(request.method() + ' ' + url.pathname);
      if (options.logout && url.pathname === '/auth/logout') {
        state.auth = false;
        return json({ ok: true });
      }
      if (options.login && url.pathname === '/auth/login') {
        state.auth = true;
        return json({ username: '隔离界面核验' });
      }
      return json({ error: 'Browser verification forbids API writes' }, 405);
    }
    if (url.pathname === '/auth/me') state.authReads++;
    else state.marketReads++;
    if (state.holds.has(url.pathname))
      await new Promise((resolve) =>
        state.releases.push({ key: url.pathname, resolve }),
      );
    if (url.pathname === state.expireOn) state.auth = false;
    if (!state.auth) return json({ error: 'Session expired' }, 401);
    if (url.pathname === '/auth/me') return json({ username: '隔离界面核验' });
    if (state.fail.has(url.pathname))
      return json({ error: 'Isolated read failure' }, 404);
    if (/^\/data\/golden-workbench\/annotations\/\w+\.json$/.test(url.pathname))
      return json({
        schema: 'stock.golden.annotations.v1',
        revision: 0,
        drawings: [],
        reviews: [],
      });
    const filename = path.resolve(INPUT, '.' + url.pathname);
    if (
      !filename.startsWith(path.resolve(INPUT) + path.sep) ||
      !fs.existsSync(filename)
    )
      return json({ error: 'No isolated input for this request' }, 404);
    if (options.running && url.pathname.endsWith('/daily/status.json')) {
      const snapshot = JSON.parse(fs.readFileSync(filename));
      return json({ ...snapshot, state: 'RUNNING', finished_at: null });
    }
    return route.fulfill({
      headers,
      contentType: 'application/json',
      body: fs.readFileSync(filename),
    });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  return { context, page, state };
}

async function check(browser, name, options, run) {
  if (only && !only.test(name)) return;
  const session = await setup(browser, options);
  try {
    await run(session.page, session.state);
    assert.deepEqual(session.state.errors, [], 'No browser runtime exceptions');
    assert.deepEqual(
      session.state.writes,
      options.login
        ? ['POST /auth/logout', 'POST /auth/login']
        : options.logout
          ? ['POST /auth/logout']
          : [],
      'Only explicitly mocked authentication writes are permitted',
    );
    results.push({ name, status: 'passed' });
  } catch (error) {
    results.push({ name, status: 'failed', error: error.message });
    await session.page
      .screenshot({
        path: path.join(OUT, name + '-failure.png'),
        fullPage: true,
      })
      .catch(() => {});
  } finally {
    for (const key of [...session.state.holds]) session.state.release(key);
    await session.context.close();
  }
  console.log(JSON.stringify(results.at(-1)));
}

async function ready(page, feature) {
  await page
    .locator(
      feature === 'chips'
        ? '.chip-candle-readout'
        : feature === 'golden'
          ? '.gw-readout'
          : '.tv-candle-readout',
    )
    .waitFor();
}
async function heading(page, text) {
  await page.locator('.wb-page-bar h1').filter({ hasText: text }).waitFor();
}
async function link(page, href, title) {
  await page
    .locator(`a[href="${href}"]`)
    .filter({ visible: true })
    .first()
    .click();
  await heading(page, title);
}
async function bounds(page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)),
    );
  });
  return page.evaluate(() => {
    const rect = (selector) => {
      const element = document.querySelector(selector);
      return element ? element.getBoundingClientRect().toJSON() : null;
    };
    return {
      sidebar: rect('.wb-sidebar'),
      workspace: rect('.wb-workspace'),
      header: rect('.wb-stock-header, .wb-loading-heading'),
      controls: rect(
        '.cw-chart-controls, .tv-chart-toolbar, .wb-loading-controls',
      ),
      chart: rect(
        '.chip-trading-scroll, .tv-chart-scroll, .wb-chart-placeholder',
      ),
      viewport: document.documentElement.clientWidth,
      body: document.body.scrollWidth,
    };
  });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    if (process.env.UI_BUILD_DIR)
      await check(
        browser,
        'page-module-failure-retains-context-and-retries',
        {
          blockedAssets: [
            '/' +
              JSON.parse(
                fs.readFileSync(
                  path.join(process.env.UI_BUILD_DIR, '.vite/manifest.json'),
                ),
              )['src/app/trend/page.tsx'].file,
          ],
        },
        async (page, state) => {
          await page.goto(SITE + '/chips');
          await ready(page, 'chips');
          await page.getByLabel('查找股票', { exact: true }).fill('000410');
          await page.locator('nav a[href="/trend"]').click();
          await page.locator('.site-navigation-error').waitFor();
          assert.equal(new URL(page.url()).pathname, '/chips');
          assert.equal(
            await page.getByLabel('查找股票', { exact: true }).inputValue(),
            '000410',
          );
          state.blockedAssets.clear();
          await page
            .locator('.site-navigation-error')
            .getByRole('button', { name: '重试', exact: true })
            .click();
          await heading(page, '趋势量价筛选');
          await ready(page, 'trend');
          assert.equal(state.documents, 1);
          assert.equal(state.authReads, 1);
        },
      );
    await check(
      browser,
      'relogin-keeps-current-route',
      { logout: true, login: true },
      async (page, state) => {
        await page.goto(SITE + '/chips');
        await ready(page, 'chips');
        await link(page, '/golden', '黄金画格核对');
        await ready(page, 'golden');
        await page.getByRole('button', { name: '账户菜单', exact: true }).click();
        await page
          .getByRole('menuitem', { name: '退出登录', exact: true })
          .click();
        await page
          .getByRole('heading', { name: '登录你的账户', exact: true })
          .waitFor();
        await page
          .getByLabel('账号', { exact: true })
          .fill('ui-isolated-account');
        await page
          .getByLabel('密码', { exact: true })
          .fill('isolated-test-only');
        await page.getByRole('button', { name: '登录', exact: true }).click();
        await heading(page, '黄金画格核对');
        await ready(page, 'golden');
        assert.equal(new URL(page.url()).pathname, '/golden');
        assert.equal(await page.locator('.chip-candle-readout').count(), 0);
        assert.equal(
          state.authReads,
          1,
          'Successful sign-in must reuse the current route without another session read',
        );
        assert.equal(state.documents, 1, 'Relogin must retain the document');
      },
    );

    await check(
      browser,
      'native-new-tab-and-download',
      {},
      async (page, state) => {
        await page.goto(SITE + '/chips');
        await ready(page, 'chips');
        const target = page.locator('nav a[href="/trend"]');
        const popup = page.waitForEvent('popup');
        await target.evaluate((anchor) =>
          anchor.setAttribute('target', '_blank'),
        );
        await target.click();
        const opened = await popup;
        await heading(opened, '趋势量价筛选');
        assert.equal(new URL(page.url()).pathname, '/chips');
        assert.equal(
          state.authReads,
          2,
          'A separate document must verify its own session',
        );
        await opened.close();
        await target.evaluate((anchor) => {
          anchor.removeAttribute('target');
          anchor.setAttribute('download', '');
        });
        const downloaded = page.waitForEvent('download');
        await target.click();
        await downloaded;
        assert.equal(new URL(page.url()).pathname, '/chips');
      },
    );

    await check(
      browser,
      'initial-auth-uses-workbench-and-blocks-data',
      { hold: ['/auth/me'] },
      async (page, state) => {
        await page.goto(SITE + '/chips');
        await page.locator('.app-session-loading .wb-loading').waitFor();
        assert.equal(
          state.marketReads,
          0,
          'Protected data cannot start before session verification',
        );
        assert.equal(
          await page.locator('.auth-backdrop, .auth-check').count(),
          0,
        );
        await page.screenshot({ path: path.join(OUT, 'initial-session.png') });
        state.release('/auth/me');
        await ready(page, 'chips');
      },
    );

    for (const width of [1440, 900, 375]) {
      geometry[width] = {};
      for (const feature of ['chips', 'trend']) {
        const manifest = `/data/${feature === 'chips' ? 'chips' : 'trend-volume'}/daily/manifest.json`;
        await check(
          browser,
          `${feature}-loading-result-${width}`,
          { width, hold: [manifest] },
          async (page, state) => {
            await page.goto(SITE + '/' + feature);
            await page.locator('.wb-list-tools').waitFor();
            await page.locator('.wb-workspace .wb-loading').waitFor();
            const loading = await bounds(page);
            assert.ok(loading.sidebar && loading.workspace && loading.chart);
            assert.ok(loading.body <= loading.viewport + 1);
            await page.screenshot({
              path: path.join(OUT, `${feature}-loading-${width}.png`),
              fullPage: width === 375,
            });
            state.release(manifest);
            await ready(page, feature);
            await page.waitForTimeout(300);
            const result = await bounds(page);
            assert.equal(
              result.workspace.x,
              loading.workspace.x,
              'Result cannot move the workspace sideways',
            );
            assert.equal(
              result.workspace.y,
              loading.workspace.y,
              'Result cannot insert a new outer heading or statistics block',
            );
            assert.equal(
              result.chart.y,
              loading.chart.y,
              'The chart must replace its reserved loading area',
            );
            assert.ok(result.body <= result.viewport + 1);
            geometry[width][feature] = { loading, result };
            await page.screenshot({
              path: path.join(OUT, `${feature}-result-${width}.png`),
              fullPage: width === 375,
            });
          },
        );
      }
      await check(
        browser,
        `chips-trend-layout-parity-${width}`,
        { width },
        async () => {
          const chips = geometry[width].chips,
            trend = geometry[width].trend;
          assert.ok(
            chips && trend,
            'Both actual pages must have been measured',
          );
          for (const phase of ['loading', 'result'])
            for (const key of [
              'sidebar',
              'workspace',
              'header',
              'controls',
              'chart',
            ]) {
              for (const field of ['x', 'y', 'width', 'height']) {
                if (key === 'header' && field === 'width') continue;
                if (
                  width === 375 &&
                  phase === 'result' &&
                  key === 'workspace' &&
                  field === 'height'
                )
                  continue;
                assert.ok(
                  Math.abs(
                    chips[phase][key][field] - trend[phase][key][field],
                  ) <= 1,
                  `${phase} ${key}.${field}: chips ${chips[phase][key][field]}, trend ${trend[phase][key][field]}`,
                );
              }
            }
        },
      );
    }

    for (const feature of ['chips', 'trend']) {
      const manifest = `/data/${feature === 'chips' ? 'chips' : 'trend-volume'}/daily/manifest.json`;
      await check(
        browser,
        `${feature}-running-keeps-loading-structure`,
        { running: true, hold: [manifest] },
        async (page, state) => {
          await page.goto(SITE + '/' + feature);
          await page.locator('.wb-list-tools').waitFor();
          await page.locator('.wb-workspace .wb-loading').waitFor();
          await page.waitForTimeout(100);
          assert.equal(
            await page.locator('.wb-workspace>.wb-update-note').count(),
            0,
            'Initial progress belongs in the same reserved state area',
          );
          state.release(manifest);
          await ready(page, feature);
        },
      );
      await check(
        browser,
        `${feature}-initial-error-and-retry`,
        { fail: [manifest] },
        async (page, state) => {
          await page.goto(SITE + '/' + feature);
          await page.locator('.wb-state[role="alert"]').waitFor();
          assert.equal(await page.locator('.wb-sidebar').count(), 1);
          assert.equal(
            await page.locator('.wb-workspace .wb-loading').count(),
            0,
            'Ended failures must stop loading',
          );
          const before = await page.locator('.wb-workspace').boundingBox();
          state.fail.clear();
          await page
            .getByRole('button', { name: '重新读取', exact: true })
            .click();
          await ready(page, feature);
          const after = await page.locator('.wb-workspace').boundingBox();
          assert.equal(after.y, before.y);
          assert.equal(after.x, before.x);
        },
      );
    }

    for (const width of [1440, 375])
      await check(
        browser,
        `all-routes-persistent-session-${width}`,
        { width },
        async (page, state) => {
          await page.goto(SITE + '/chips');
          await ready(page, 'chips');
          await page.evaluate(() => {
            window.__uiDocumentIdentity = 'persistent-workbench';
          });
          const flashes = await page.evaluate(() => {
            window.__uiAuthFlashes = 0;
            const observer = new MutationObserver(() => {
              if (
                document.querySelector(
                  '.app-session-loading,.auth-check,.auth-backdrop',
                )
              )
                window.__uiAuthFlashes++;
            });
            observer.observe(document.body, { subtree: true, childList: true });
            return true;
          });
          assert.equal(flashes, true);
          for (const [href, title] of [
            ['/trend', '趋势量价筛选'],
            ['/trend/research', '趋势历史研究'],
            ['/trend', '趋势量价筛选'],
            ['/golden', '黄金画格核对'],
            ['/chips', '每日筹码筛选'],
            ['/chips/calibration', '历史筹码校准'],
            ['/', '开盘量查询'],
            ['/chips', '每日筹码筛选'],
          ]) {
            await link(page, href, title);
            assert.equal(
              await page.evaluate(() => window.__uiDocumentIdentity),
              'persistent-workbench',
            );
            assert.ok(
              await page.evaluate(
                () =>
                  document.body.scrollWidth <=
                  document.documentElement.clientWidth + 1,
              ),
              href + ' cannot overflow after accumulated CSS',
            );
          }
          assert.equal(
            state.documents,
            1,
            'Internal routes cannot reload the document',
          );
          assert.equal(
            state.authReads,
            1,
            'Golden and all other routes must reuse the verified account',
          );
          assert.equal(await page.evaluate(() => window.__uiAuthFlashes), 0);
          await page.reload();
          await ready(page, 'chips');
          assert.equal(
            state.authReads,
            2,
            'Explicit refresh must still revalidate the real session',
          );
        },
      );

    await check(
      browser,
      'back-forward-and-home-query-restoration',
      {},
      async (page, state) => {
        const query = `/?scope=single&symbols=000410&date=${meta.latest}`;
        await page.goto(SITE + query);
        await page
          .getByRole('heading', { name: '沈阳机床', exact: true })
          .waitFor();
        await page.evaluate(() => {
          window.__uiDocumentIdentity = 'history';
        });
        await link(page, '/chips', '每日筹码筛选');
        await link(page, '/trend', '趋势量价筛选');
        await page.evaluate(() => history.back());
        await heading(page, '每日筹码筛选');
        await page.evaluate(() => history.back());
        await heading(page, '开盘量查询');
        await page
          .getByRole('heading', { name: '沈阳机床', exact: true })
          .waitFor();
        assert.equal(new URL(page.url()).searchParams.get('symbols'), '000410');
        await page.evaluate(() => history.forward());
        await heading(page, '每日筹码筛选');
        assert.equal(
          await page.evaluate(() => window.__uiDocumentIdentity),
          'history',
        );
        assert.equal(state.authReads, 1);
        assert.equal(state.documents, 1);
      },
    );

    await check(
      browser,
      'expired-session-unmounts-workspace',
      {},
      async (page, state) => {
        await page.goto(SITE + '/chips');
        await ready(page, 'chips');
        await link(page, '/trend', '趋势量价筛选');
        await ready(page, 'trend');
        state.expireOn = '/data/trend-volume/daily/status.json';
        await page
          .getByRole('button', { name: '刷新最新筛选', exact: true })
          .click();
        await page
          .getByRole('heading', { name: '登录你的账户', exact: true })
          .waitFor();
        assert.equal(
          await page
            .locator('.wb-shell,.chip-candle-readout,.tv-candle-readout')
            .count(),
          0,
        );
        await page.evaluate(() => history.back());
        await page
          .getByRole('heading', { name: '登录你的账户', exact: true })
          .waitFor();
        assert.equal(
          await page.locator('.chip-candle-readout,.tv-candle-readout').count(),
          0,
        );
      },
    );

    await check(
      browser,
      'logout-keeps-history-protected',
      { logout: true },
      async (page) => {
        await page.goto(SITE + '/chips');
        await ready(page, 'chips');
        await link(page, '/trend', '趋势量价筛选');
        await ready(page, 'trend');
        await page.getByRole('button', { name: '账户菜单', exact: true }).click();
        await page
          .getByRole('menuitem', { name: '退出登录', exact: true })
          .click();
        await page
          .getByRole('heading', { name: '登录你的账户', exact: true })
          .waitFor();
        await page.evaluate(() => history.back());
        await page
          .getByRole('heading', { name: '登录你的账户', exact: true })
          .waitFor();
        assert.equal(
          await page.locator('.chip-candle-readout,.tv-candle-readout').count(),
          0,
        );
      },
    );

    await check(
      browser,
      'golden-draft-retained-through-navigation',
      {},
      async (page, state) => {
        await page.goto(SITE + '/golden');
        await ready(page, 'golden');
        await page
          .getByRole('button', { name: '新增画线', exact: true })
          .click();
        const high = await page
          .locator('[data-candle-date="2026-10-14"] line')
          .first()
          .boundingBox();
        const low = await page
          .locator('[data-candle-date="2026-10-30"] line')
          .first()
          .boundingBox();
        await page.mouse.move(high.x, high.y);
        await page.mouse.down();
        await page.mouse.move(low.x, low.y + low.height, { steps: 8 });
        await page.mouse.up();
        assert.equal(await page.locator('[data-manual-anchor]').count(), 2);
        const draft = await page.evaluate(() =>
          localStorage.getItem('golden-draft-v1:隔离界面核验:002693'),
        );
        assert.ok(draft);
        await link(page, '/chips', '每日筹码筛选');
        await link(page, '/golden', '黄金画格核对');
        await ready(page, 'golden');
        assert.equal(await page.locator('[data-manual-anchor]').count(), 2);
        assert.equal(
          await page.evaluate(() =>
            localStorage.getItem('golden-draft-v1:隔离界面核验:002693'),
          ),
          draft,
        );
        assert.equal(
          await page.evaluate(() =>
            localStorage.getItem('golden-draft-v1::002693'),
          ),
          null,
        );
        assert.equal(state.authReads, 1);
      },
    );

    await check(
      browser,
      'active-navigation-keeps-selection',
      {},
      async (page, state) => {
        await page.goto(SITE + '/chips');
        await ready(page, 'chips');
        await page.getByLabel('查找股票', { exact: true }).fill('000410');
        await link(page, '/chips', '每日筹码筛选');
        assert.equal(
          await page.getByLabel('查找股票', { exact: true }).inputValue(),
          '000410',
        );
        assert.equal(state.authReads, 1);
      },
    );
  } finally {
    await browser.close();
  }
  const report = {
    scope:
      'Real local production/dev UI with isolated private snapshots; no real account session or API writes',
    results,
    geometry,
    passed: results.filter((result) => result.status === 'passed').length,
    failed: results.filter((result) => result.status === 'failed').length,
  };
  fs.writeFileSync(
    path.join(OUT, 'results.json'),
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify({ passed: report.passed, failed: report.failed }));
  if (report.failed) process.exitCode = 1;
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
