/* Local login UI check with isolated responses; no production requests or credentials. */
/* oxlint-disable typescript/no-require-imports -- executable CommonJS browser check */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const SITE = process.env.LOGIN_CARD_URL || 'http://127.0.0.1:3111';
const OUT = process.env.LOGIN_CARD_OUTPUT_DIR;
const origin = new URL(SITE).origin;
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(SITE).hostname));
const API_ORIGIN = 'https://api.stock.iteready.com';
const DATE = '2026-10-09';
const BRAND = '量序';
const FIXTURE_USER = '登录界面验收';
const FIXTURE_STOCK = '锂铝鑫甬示例';
const ERROR_TEXT = '账号或密码错误，请重试。';
const checks = [];
const errors = [];
const blocked = [];
const fontRequests = [];
const fontResponses = [];
const fontStacks = [];
let brandMetadata;
let documentRequests = 0;
let routeRelogin;
let obsoleteSessionCheck;
const counts = { me: 0, login: 0, logout: 0 };
const rows = [
  {
    code: '600000',
    name: FIXTURE_STOCK,
    market: 'SH',
    date: DATE,
    status: 'ok',
    first15Volume: 20,
    dailyVolume: 100,
    ratio: 20,
    priceStatus: 'missing',
  },
];

async function assertOnScreen(locator, viewport) {
  assert.ok(await locator.isVisible(), 'login control must be visible');
  const bounds = await locator.boundingBox();
  assert.ok(bounds, 'login control must have bounds');
  assert.ok(
    bounds.x >= 0 && bounds.x + bounds.width <= viewport.width + 1,
    'login control must fit horizontally',
  );
  assert.ok(
    bounds.y >= 0 && bounds.y + bounds.height <= viewport.height + 1,
    'login control must fit vertically without scrolling',
  );
}

async function assertSystemFonts(page, phase, selectors) {
  for (const selector of selectors) {
    const family = await page
      .locator(selector)
      .first()
      .evaluate((element) => getComputedStyle(element).fontFamily);
    assert.match(family, /system-ui/, 'the UI must use the system font stack');
    assert.doesNotMatch(family, /Inter|Noto Sans SC/);
    fontStacks.push({ phase, selector, family });
  }
  assert.deepEqual(fontRequests, [], 'natural UI use must request no webfonts');
}

async function verifyRouteRelogin(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
    serviceWorkers: 'block',
  });
  const state = {
    user: 'route-account-a',
    documents: 0,
    me: 0,
    login: 0,
    logout: 0,
    anonymousReads: 0,
    triangleReads: 0,
  };
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (request) => {
    if (request.isNavigationRequest() && request.frame() === page.mainFrame())
      state.documents++;
    const url = new URL(request.url());
    if (/\.(woff2?|ttf|otf)$/.test(url.pathname))
      fontRequests.push({ origin: url.origin, path: url.pathname });
  });
  await context.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const local = url.origin === origin;
    if (!local && url.origin !== API_ORIGIN) {
      blocked.push(request.method() + ' ' + url.origin + url.pathname);
      return route.abort();
    }
    const headers = {
      'access-control-allow-origin': origin,
      'access-control-allow-credentials': 'true',
      'access-control-allow-methods': 'GET, POST, OPTIONS',
      'access-control-allow-headers': 'content-type',
      'cache-control': 'no-store',
    };
    const json = (value, status = 200) =>
      route.fulfill({ status, json: value, headers });
    if (request.method() === 'OPTIONS')
      return route.fulfill({ status: 204, headers });
    if (url.pathname === '/auth/me' && request.method() === 'GET') {
      state.me++;
      return json(
        state.user ? { username: state.user } : {},
        state.user ? 200 : 401,
      );
    }
    if (url.pathname === '/auth/logout' && request.method() === 'POST') {
      state.logout++;
      if (state.logout === 1)
        return json({ error: 'isolated logout failure' }, 500);
      state.user = '';
      return json({ ok: true });
    }
    if (url.pathname === '/auth/login' && request.method() === 'POST') {
      state.login++;
      assert.equal(request.postDataJSON().username, 'route-account-b');
      state.user = 'route-account-b';
      return json({ username: state.user });
    }
    if (request.method() === 'GET' && url.pathname.startsWith('/data/')) {
      if (!state.user) {
        state.anonymousReads++;
        return json({ error: 'fixture session is anonymous' }, 401);
      }
      if (url.pathname === '/data/stocks.json')
        return json({ rows: rows.map(({ code, name }) => ({ code, name })) });
      if (url.pathname === '/data/manifest.json')
        return json({
          generatedAt: 'fixture',
          source: 'fixture',
          methodology: 'fixture',
          universeTotal: 1,
          tradingDates: [DATE],
          retentionStart: DATE,
          dates: [{ date: DATE, file: DATE + '.json', status: 'full' }],
        });
      if (url.pathname === '/data/' + DATE + '.json')
        return json({
          date: DATE,
          generatedAt: 'fixture',
          scope: 'full',
          total: 1,
          rows,
        });
      if (url.pathname.startsWith('/data/triangles/')) state.triangleReads++;
      return json(
        { error: 'No publication in this isolated route fixture' },
        404,
      );
    }
    if (local && request.method() === 'GET') return route.continue();
    blocked.push(request.method() + ' ' + url.origin + url.pathname);
    return route.abort();
  });
  try {
    await page.goto(SITE + '/?date=' + DATE + '&scope=all');
    await page.locator('.results tbody tr').first().waitFor();
    assert.match(
      await page.locator('.results').innerText(),
      new RegExp(FIXTURE_STOCK),
    );
    await page.locator('nav a[href="/triangles"]').click();
    await page
      .getByRole('heading', { name: '趋势三角与关键点位', exact: true })
      .waitFor();
    await page.locator('.triangle-error').waitFor();
    await page.evaluate((date) => {
      history.replaceState(
        null,
        '',
        '/triangles?scope=single&symbols=600000&asOf=' + date,
      );
      window.__loginRouteDocument = 'route-login-fixture';
    }, DATE);
    const targetUrl = page.url();
    const readsBeforeLogout = state.triangleReads;
    assert.ok(
      readsBeforeLogout > 0,
      'the first account must mount the selected private page',
    );
    await page.getByRole('button', { name: '账户菜单', exact: true }).click();
    await page.getByRole('menuitem', { name: '退出登录', exact: true }).click();
    const logoutError = page.locator('.account-controls').getByRole('alert');
    await logoutError.waitFor();
    assert.match(await logoutError.innerText(), /退出失败/);
    assert.equal(state.logout, 1, 'a failed logout must not retry itself');
    assert.equal(await page.locator('.auth-form-panel form').count(), 0);
    assert.ok(await page.locator('.triangle-site').isVisible());
    assert.equal(page.url(), targetUrl);
    assert.ok(
      await page
        .getByRole('button', { name: '账户菜单', exact: true })
        .isEnabled(),
    );
    await page.getByRole('button', { name: '账户菜单', exact: true }).click();
    await page.getByRole('menuitem', { name: '退出登录', exact: true }).click();
    await page.locator('.auth-form-panel form').waitFor();
    assert.equal(await page.locator('.triangle-site').count(), 0);
    assert.equal(await page.locator('.results').count(), 0);
    assert.doesNotMatch(
      await page.locator('body').innerText(),
      new RegExp(FIXTURE_STOCK),
    );
    await page.getByLabel('账号', { exact: true }).fill('route-account-b');
    await page
      .getByLabel('密码', { exact: true })
      .fill('route-fixture-password');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page
      .getByRole('heading', { name: '趋势三角与关键点位', exact: true })
      .waitFor();
    await page.locator('.triangle-error').waitFor();
    assert.equal(page.url(), targetUrl);
    assert.equal(
      await page.evaluate(() => window.__loginRouteDocument),
      'route-login-fixture',
    );
    assert.ok(
      state.triangleReads > readsBeforeLogout,
      'the second account mounts a fresh private page',
    );
    assert.equal(await page.locator('.results').count(), 0);
    assert.doesNotMatch(
      await page.locator('body').innerText(),
      new RegExp(FIXTURE_STOCK),
    );
    await page.getByRole('button', { name: '账户菜单', exact: true }).click();
    assert.match(await page.getByRole('menu').innerText(), /route-account-b/);
    assert.doesNotMatch(
      await page.getByRole('menu').innerText(),
      /route-account-a/,
    );
    await page.keyboard.press('Escape');
    await assertSystemFonts(page, 'relogin-subroute', ['.triangle-site h1']);
    assert.equal(state.documents, 1);
    assert.equal(state.me, 1);
    assert.equal(state.login, 1);
    assert.equal(state.logout, 2);
    assert.equal(state.anonymousReads, 0);
    return {
      ...state,
      logoutFailurePreservedPage: true,
      route: new URL(targetUrl).pathname + new URL(targetUrl).search,
    };
  } finally {
    await context.close();
  }
}

async function verifyObsoleteSessionCheck(browser) {
  const context = await browser.newContext({ serviceWorkers: 'block' });
  const page = await context.newPage();
  const state = { me: 0, login: 0, privateReads: 0, anonymousReads: 0 };
  let signedIn = false;
  let releaseMe;
  let markReleased;
  const released = new Promise((resolve) => {
    markReleased = resolve;
  });
  page.on('pageerror', (error) => errors.push(error.message));
  const isMe = (request) => new URL(request.url()).pathname === '/auth/me';
  const started = page.waitForRequest(isMe, { timeout: 12000 });
  const outcome = Promise.any([
    page.waitForEvent('requestfailed', { predicate: isMe }).then((request) => ({
      kind: 'cancelled',
      error: request.failure()?.errorText,
    })),
    page
      .waitForResponse((response) => isMe(response.request()))
      .then((response) => ({
        kind: 'response-received',
        status: response.status(),
      })),
  ]);
  void outcome.catch(() => {});
  await context.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin && url.origin !== API_ORIGIN) {
      blocked.push(request.method() + ' ' + url.origin + url.pathname);
      return route.abort();
    }
    const headers = {
      'access-control-allow-origin': origin,
      'access-control-allow-credentials': 'true',
      'access-control-allow-methods': 'GET, POST, OPTIONS',
      'access-control-allow-headers': 'content-type',
      'cache-control': 'no-store',
    };
    const json = (value, status = 200) =>
      route.fulfill({ status, json: value, headers });
    if (request.method() === 'OPTIONS')
      return route.fulfill({ status: 204, headers });
    if (url.pathname === '/auth/me' && request.method() === 'GET') {
      state.me++;
      await new Promise((resolve) => {
        releaseMe = resolve;
      });
      try {
        await json({ username: 'obsolete-check-account' });
      } catch (error) {
        if (!request.failure()) throw error;
      } finally {
        markReleased();
      }
      return;
    }
    if (url.pathname === '/auth/login' && request.method() === 'POST') {
      state.login++;
      signedIn = true;
      return json({ username: 'new-session-account' });
    }
    if (request.method() === 'GET' && url.pathname.startsWith('/data/')) {
      state.privateReads++;
      if (!signedIn) state.anonymousReads++;
      return json(
        { error: 'No publication in this isolated session fixture' },
        404,
      );
    }
    if (url.origin === origin && request.method() === 'GET')
      return route.continue();
    blocked.push(request.method() + ' ' + url.origin + url.pathname);
    return route.abort();
  });
  try {
    await page.goto(SITE + '/triangles?scope=single&symbols=600000', {
      waitUntil: 'domcontentloaded',
    });
    await started;
    assert.equal(
      state.privateReads,
      0,
      'a held session check cannot mount private pages',
    );
    await page.evaluate(() =>
      window.dispatchEvent(new Event('stock-session-expired')),
    );
    await page.locator('.auth-form-panel form').waitFor();
    assert.equal(state.privateReads, 0);
    await page.getByLabel('账号', { exact: true }).fill('new-session-account');
    await page
      .getByLabel('密码', { exact: true })
      .fill('obsolete-check-fixture');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page
      .getByRole('heading', { name: '趋势三角与关键点位', exact: true })
      .waitFor();
    await page.locator('.triangle-error').waitFor();
    releaseMe();
    releaseMe = undefined;
    await released;
    const oldCheckOutcome = await outcome;
    await page.getByRole('button', { name: '账户菜单', exact: true }).click();
    assert.match(
      await page.getByRole('menu').innerText(),
      /new-session-account/,
    );
    assert.doesNotMatch(
      await page.getByRole('menu').innerText(),
      /obsolete-check-account/,
    );
    assert.equal(await page.locator('.auth-form-panel form').count(), 0);
    assert.ok(await page.locator('.triangle-site').isVisible());
    assert.equal(state.me, 1);
    assert.equal(state.login, 1);
    assert.equal(state.anonymousReads, 0);
    return { ...state, oldCheckOutcome };
  } finally {
    if (releaseMe) releaseMe();
    await context.close();
    await outcome.catch(() => {});
  }
}

(async () => {
  if (OUT) fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  let releaseLogin;
  let authenticated = false;
  let loginMode = 'failure';
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 960 },
      serviceWorkers: 'block',
    });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => {
      if (request.isNavigationRequest() && request.frame() === page.mainFrame())
        documentRequests++;
      const url = new URL(request.url());
      if (/\.(woff2?|ttf|otf)$/.test(url.pathname))
        fontRequests.push({ origin: url.origin, path: url.pathname });
    });
    page.on('response', (response) => {
      const url = new URL(response.url());
      if (/\.(woff2?|ttf|otf)$/.test(url.pathname))
        fontResponses.push({
          origin: url.origin,
          path: url.pathname,
          status: response.status(),
        });
    });
    await context.route('**/*', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const method = request.method();
      const local = url.origin === origin;
      const mockable = local || url.origin === API_ORIGIN;
      const headers = {
        'access-control-allow-origin': origin,
        'access-control-allow-credentials': 'true',
        'access-control-allow-methods': 'GET, POST, OPTIONS',
        'access-control-allow-headers': 'content-type',
        'cache-control': 'no-store',
      };
      const json = (value, status = 200) =>
        route.fulfill({ status, json: value, headers });
      const reject = () => {
        blocked.push(method + ' ' + url.origin + url.pathname);
        return route.abort();
      };
      if (!mockable) return reject();
      if (
        method === 'OPTIONS' &&
        ['/auth/login', '/auth/logout'].includes(url.pathname)
      )
        return route.fulfill({ status: 204, headers });
      if (url.pathname === '/auth/me' && method === 'GET') {
        counts.me += 1;
        return json(
          authenticated ? { username: FIXTURE_USER } : { error: 'fixture' },
          authenticated ? 200 : 401,
        );
      }
      if (url.pathname === '/auth/login' && method === 'POST') {
        counts.login += 1;
        assert.ok(
          counts.login <= 3,
          'only three isolated login attempts allowed',
        );
        if (loginMode === 'delayed')
          await new Promise((resolve) => {
            releaseLogin = resolve;
          });
        if (loginMode === 'success') {
          authenticated = true;
          return json({ username: FIXTURE_USER });
        }
        return json({ error: ERROR_TEXT }, 401);
      }
      if (url.pathname === '/auth/logout' && method === 'POST') {
        counts.logout += 1;
        assert.equal(counts.logout, 1, 'only one isolated logout allowed');
        authenticated = false;
        return json({ ok: true });
      }
      if (method !== 'GET') return reject();
      if (url.pathname === '/data/stocks.json')
        return json({ rows: rows.map(({ code, name }) => ({ code, name })) });
      if (url.pathname === '/data/manifest.json')
        return json({
          generatedAt: 'fixture',
          source: 'fixture',
          methodology: 'fixture',
          universeTotal: 1,
          tradingDates: [DATE],
          retentionStart: DATE,
          dates: [{ date: DATE, file: DATE + '.json', status: 'full' }],
        });
      if (url.pathname === '/data/collection-status.json')
        return json({
          id: 'fixture',
          state: 'completed',
          phase: 'close',
          completedStocks: 1,
          totalStocks: 1,
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
          total: 1,
          rows,
        });
      if (
        local &&
        ([
          '/',
          '/index.html',
          '/index.rsc',
          '/favicon.svg',
          '/favicon.ico',
        ].includes(url.pathname) ||
          url.pathname.startsWith('/_next/static/') ||
          url.pathname.startsWith('/fonts/') ||
          url.pathname.startsWith('/brand/'))
      )
        return route.continue();
      return reject();
    });
    await page.goto(SITE + '/?date=' + DATE + '&scope=all');
    const form = page.locator('.auth-form-panel form');
    const username = form.locator('input[name="username"]');
    const password = form.locator('input[name="password"]');
    const submit = form.locator('button[type="submit"]');
    await form.waitFor();
    assert.match(await page.locator('.auth-brand').innerText(), /量序/);
    assert.match(await page.title(), /^量序(?:[\s·—|]|$)/);
    brandMetadata = await page.evaluate(async () => {
      const icon = document.querySelector('link[rel="icon"]');
      if (!icon?.href) throw Error('brand favicon link is missing');
      const url = new URL(icon.href);
      if (url.origin !== window.location.origin)
        throw Error('brand favicon must be self-hosted');
      const response = await fetch(url.href, { cache: 'no-store' });
      return {
        title: document.title,
        favicon: url.pathname,
        faviconStatus: response.status,
        faviconType: response.headers.get('content-type'),
        faviconSvg: /<svg(?:\s|>)/.test(await response.text()),
      };
    });
    assert.equal(brandMetadata.faviconStatus, 200);
    assert.match(brandMetadata.faviconType || '', /image\/svg\+xml/);
    assert.equal(brandMetadata.faviconSvg, true);
    checks.push(
      'login brand, browser title and self-hosted SVG favicon use Liangxu',
    );
    await assertSystemFonts(page, 'login', [
      '.auth-form-panel h1',
      'input[name="username"]',
      'input[name="password"]',
      '.auth-form-panel button[type="submit"]',
    ]);
    checks.push('natural login uses system fonts without webfont downloads');
    assert.equal(await username.getAttribute('autocomplete'), 'username');
    assert.equal(
      await password.getAttribute('autocomplete'),
      'current-password',
    );
    assert.equal(await password.getAttribute('type'), 'password');
    checks.push('browser autofill semantics remain available');

    for (const viewport of [
      { width: 1440, height: 960, name: 'desktop' },
      { width: 375, height: 812, name: 'mobile' },
      { width: 320, height: 740, name: 'narrow' },
    ]) {
      await page.setViewportSize(viewport);
      await assertOnScreen(username, viewport);
      await assertOnScreen(password, viewport);
      await assertOnScreen(submit, viewport);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth + 1,
      );
      assert.equal(
        overflow,
        false,
        'login page must not overflow horizontally',
      );
      if (OUT)
        await page.screenshot({
          path: path.join(OUT, 'login-' + viewport.name + '.png'),
        });
      checks.push(
        viewport.name +
          ' form and primary button fit without horizontal overflow',
      );
    }

    await page.setViewportSize({ width: 1440, height: 960 });
    await username.focus();
    await page.keyboard.press('Tab');
    assert.ok(
      await password.evaluate((element) => element === document.activeElement),
    );
    await page.keyboard.press('Tab');
    assert.ok(
      await submit.evaluate((element) => element === document.activeElement),
    );
    checks.push('keyboard Tab proceeds from account to password to login');
    const help = form.locator('.auth-help');
    await help.locator('summary').click();
    assert.equal(await help.getAttribute('open'), '');
    assert.match(await help.innerText(), /联系管理员/);
    await help.locator('summary').click();
    checks.push('account help expands and closes');

    await username.fill('login-ui-fixture');
    await password.fill('fixture-only-password');
    await submit.click();
    const alert = form.getByRole('alert');
    await alert.waitFor();
    assert.equal(await alert.innerText(), ERROR_TEXT);
    assert.equal(counts.login, 1);
    assert.ok(await submit.isEnabled());
    if (OUT) await page.screenshot({ path: path.join(OUT, 'login-error.png') });
    checks.push('a mocked wrong-password response is visible and allows retry');

    loginMode = 'delayed';
    await submit.dblclick();
    await page.waitForFunction(
      () =>
        document.querySelector('.auth-form-panel button[type="submit"]')
          ?.disabled,
    );
    assert.equal(counts.login, 2, 'busy double-click must submit exactly once');
    assert.match(await submit.innerText(), /登录中/);
    assert.ok(releaseLogin, 'the isolated login response must be held');
    releaseLogin();
    releaseLogin = undefined;
    await alert.waitFor();
    assert.ok(await submit.isEnabled());
    checks.push(
      'busy state disables repeated submission during a mocked double-click',
    );

    loginMode = 'success';
    await submit.click();
    await page.locator('.account-controls').waitFor();
    await page.getByRole('button', { name: '账户菜单', exact: true }).click();
    assert.match(await page.getByRole('menu').innerText(), /登录界面验收/);
    await page.keyboard.press('Escape');
    await page.locator('.results tbody tr').first().waitFor();
    assert.ok(
      (await page.locator('.results tbody tr').first().innerText()).includes(
        FIXTURE_STOCK,
      ),
    );
    assert.ok(
      (
        await page
          .getByRole('link', { name: '量序首页', exact: true })
          .innerText()
      ).includes(BRAND),
    );
    assert.match(await page.title(), /^量序(?:[\s·—|]|$)/);
    if (OUT) await page.screenshot({ path: path.join(OUT, 'brand-app.png') });
    checks.push(
      'authenticated navigation retains Liangxu and renders Chinese stock names',
    );
    assert.equal(await page.locator('.auth-page').count(), 0);
    assert.equal(counts.login, 3);
    assert.equal(counts.me, 1, 'successful login must not recheck the session');
    assert.equal(
      documentRequests,
      1,
      'successful login must retain the document',
    );
    await assertSystemFonts(page, 'first-query', [
      '.account-controls',
      '.results tbody tr',
    ]);
    checks.push(
      'mocked login reveals the first query in the same document without webfonts',
    );
    await page.reload();
    await page.locator('.account-controls').waitFor();
    assert.ok(counts.me >= 2);
    checks.push('a mocked authenticated session survives reload');
    await page.getByRole('button', { name: '账户菜单', exact: true }).click();
    await page.getByRole('menuitem', { name: '退出登录', exact: true }).click();
    await page.locator('.auth-form-panel form').waitFor();
    assert.equal(counts.logout, 1);
    assert.equal(await page.locator('input[name="password"]').inputValue(), '');
    checks.push(
      'mocked logout restores the login form and clears the password',
    );

    routeRelogin = await verifyRouteRelogin(browser);
    checks.push(
      'another account reopens the selected subroute and query in the same document without old private content',
    );
    checks.push(
      'failed logout preserves the private page, exposes its error, and permits one explicit retry',
    );
    obsoleteSessionCheck = await verifyObsoleteSessionCheck(browser);
    checks.push(
      'an expired held session check cannot overwrite a newer login; its actual cancellation or response is recorded',
    );

    assert.deepEqual(errors, []);
    assert.deepEqual(blocked, []);
    assert.deepEqual(
      fontRequests,
      [],
      'authenticated reload must request no webfonts',
    );
    assert.deepEqual(fontResponses, []);
    const result = {
      checks,
      errors,
      blocked,
      counts,
      routeRelogin,
      obsoleteSessionCheck,
      documentRequests,
      fontStacks,
      fontRequests,
      fontResponses,
      brandMetadata,
      data: 'isolated fixtures; no production requests or real credentials',
    };
    if (OUT)
      fs.writeFileSync(
        path.join(OUT, 'login-browser-checks.json'),
        JSON.stringify(result, null, 2) + '\n',
      );
    console.log(JSON.stringify(result));
  } finally {
    if (releaseLogin) releaseLogin();
    await browser.close();
  }
})().catch((error) => {
  if (OUT)
    fs.writeFileSync(
      path.join(OUT, 'login-browser-failure.json'),
      JSON.stringify(
        {
          error: error.message,
          checks,
          errors,
          blocked,
          counts,
          routeRelogin,
          obsoleteSessionCheck,
          documentRequests,
          fontStacks,
          fontRequests,
          fontResponses,
          brandMetadata,
        },
        null,
        2,
      ) + '\n',
    );
  console.error(error);
  process.exitCode = 1;
});
