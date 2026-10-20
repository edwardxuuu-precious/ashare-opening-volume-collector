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
const fontResponses = [];
let fontLoads = [];
let brandMetadata;
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
    fontLoads = await page.evaluate(async () => {
      const probes = [
        { family: 'Inter', text: '123ABC ¥1,234.56 +16.49% ￥100' },
        { family: '"Noto Sans SC"', text: '量序登录账号密码锂铝鑫甬稀' },
      ];
      return Promise.all(
        probes.map(async ({ family, text }) => {
          const faces = await document.fonts.load('16px ' + family, text);
          return {
            family,
            text,
            count: faces.length,
            statuses: faces.map((face) => face.status),
          };
        }),
      );
    });
    for (const font of fontLoads) {
      assert.ok(
        font.count > 0,
        font.family + ' must have a registered font face',
      );
      assert.ok(
        font.statuses.every((status) => status === 'loaded'),
        font.family + ' must finish loading',
      );
    }
    assert.ok(
      new Set(
        fontResponses
          .filter((response) => response.path.startsWith('/fonts/'))
          .map((response) => response.path),
      ).size >= 2,
      'at least two distinct self-hosted brand font requests must complete',
    );
    assert.ok(
      fontResponses.every(
        (response) => response.origin === origin && response.status === 200,
      ),
      'all font responses must be local and return HTTP 200',
    );
    checks.push(
      'Inter financial figures and Noto Sans SC Chinese probes load locally',
    );
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
    assert.match(
      await page.locator('.account-controls').innerText(),
      /登录界面验收/,
    );
    await page.locator('.results tbody tr').first().waitFor();
    assert.ok(
      (await page.locator('.results tbody tr').first().innerText()).includes(
        FIXTURE_STOCK,
      ),
    );
    assert.ok(
      (await page.locator('.rail-brand-row .brand').innerText()).includes(
        BRAND,
      ),
    );
    assert.match(await page.title(), /^量序(?:[\s·—|]|$)/);
    if (OUT) await page.screenshot({ path: path.join(OUT, 'brand-app.png') });
    checks.push(
      'authenticated sidebar retains Liangxu and renders Chinese stock names',
    );
    assert.equal(await page.locator('.auth-page').count(), 0);
    assert.equal(counts.login, 3);
    checks.push(
      'mocked successful login reveals the protected query interface',
    );
    await page.reload();
    await page.locator('.account-controls').waitFor();
    assert.ok(counts.me >= 2);
    checks.push('a mocked authenticated session survives reload');
    await page.getByRole('button', { name: '退出登录', exact: true }).click();
    await page.locator('.auth-form-panel form').waitFor();
    assert.equal(counts.logout, 1);
    assert.equal(await page.locator('input[name="password"]').inputValue(), '');
    checks.push(
      'mocked logout restores the login form and clears the password',
    );

    assert.deepEqual(errors, []);
    assert.deepEqual(blocked, []);
    assert.ok(
      fontResponses.every(
        (response) => response.origin === origin && response.status === 200,
      ),
      'font requests during authenticated reload must also remain local and successful',
    );
    const result = {
      checks,
      errors,
      blocked,
      counts,
      fontLoads,
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
          fontLoads,
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
