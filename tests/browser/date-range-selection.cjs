/* Date range interaction regression: local server and isolated fixtures only. */
/* oxlint-disable typescript/no-require-imports -- executable CommonJS browser smoke */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const SITE = process.env.DATE_RANGE_UI_URL || 'http://127.0.0.1:3107';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(SITE).hostname));
const OUTPUT_DIR = process.env.DATE_RANGE_UI_OUTPUT_DIR;
const DAYS = [];
for (
  const day = new Date('2026-07-09T12:00:00Z');
  day <= new Date('2026-09-30T12:00:00Z');
  day.setUTCDate(day.getUTCDate() + 1)
) {
  if (day.getUTCDay() !== 0 && day.getUTCDay() !== 6)
    DAYS.push(day.toISOString().slice(0, 10));
}
const errors = [];
const blocked = [];
const checks = [];
function mark(name) {
  checks.push({ name, status: 'passed' });
}
async function setup(browser, mobile = false) {
  const context = await browser.newContext({
    viewport: mobile
      ? { width: 375, height: 812 }
      : { width: 1440, height: 960 },
    isMobile: mobile,
    hasTouch: mobile,
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    // Neither authenticated production resources nor a collector are used.
    if (
      ![new URL(SITE).origin, 'https://api.stock.iteready.com'].includes(
        url.origin,
      ) ||
      request.method() !== 'GET'
    ) {
      blocked.push(`${request.method()} ${url.origin}${url.pathname}`);
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
    if (url.pathname === '/auth/me') return json({ username: 'fixture' });
    if (url.pathname === '/data/stocks.json')
      return json({ rows: [{ code: '000002', name: '万科Ａ' }] });
    if (url.pathname === '/data/manifest.json')
      return json({
        generatedAt: '2026-09-30T08:00:00Z',
        source: 'isolated browser fixture',
        methodology: 'fixture',
        universeTotal: 1,
        tradingDates: DAYS,
        retentionStart: DAYS[0],
        dates: [...DAYS]
          .reverse()
          .map((date) => ({ date, file: `${date}.json`, status: 'full' })),
      });
    if (url.pathname === '/data/collection-status.json')
      return json({
        state: 'completed',
        latestDate: DAYS.at(-1),
        dataComplete: true,
      });
    const date = url.pathname.match(/^\/data\/(\d{4}-\d{2}-\d{2})\.json$/)?.[1];
    if (date)
      return json({
        date,
        generatedAt: 'fixture',
        source: 'fixture',
        methodology: 'fixture',
        scope: 'full',
        total: 1,
        rows: [
          {
            code: '000002',
            name: '万科Ａ',
            market: 'SZ',
            status: 'ok',
            first15Volume: 10,
            dailyVolume: 100,
            ratio: 10,
            priceStatus: 'missing',
          },
        ],
      });
    if (
      url.pathname.startsWith('/data/') ||
      url.origin !== new URL(SITE).origin
    ) {
      blocked.push(`unknown fixture ${url.pathname}`);
      return route.abort();
    }
    return route.continue();
  });
  await page.goto(
    `${SITE}/?from=2026-07-09&to=2026-08-27&scope=single&symbols=000002`,
  );
  await page.locator('.results tbody tr').first().waitFor({ timeout: 60000 });
  return { page, context };
}
const dateButton = (page, key) => {
  const [year, month, day] = key.split('-').map(Number);
  return page.locator(
    `.date-calendar button[data-day="${year}/${month}/${day}"]`,
  );
};
const done = (page) => page.getByRole('button', { name: '完成', exact: true });
const caption = (page) => page.locator('.calendar-range-summary output');
const dates = (page) => page.locator('.calendar-range-date strong');
const open = async (page) => {
  await page.getByRole('button', { name: '选择日期', exact: true }).click();
  await caption(page).waitFor();
  assert.equal(await caption(page).innerText(), '先点开始日期，再点结束日期');
};
const rangeParams = (page) => {
  const params = new URL(page.url()).searchParams;
  return [params.get('from'), params.get('to')];
};
async function applied(page, from, to) {
  await page.waitForFunction(
    ([from, to]) => {
      const p = new URLSearchParams(location.search);
      return p.get('from') === from && p.get('to') === to;
    },
    [from, to],
  );
  assert.deepEqual(await dates(page).allTextContents(), [from, to]);
  assert.equal(await done(page).isEnabled(), true);
  const count = DAYS.filter((date) => date >= from && date <= to).length;
  await page.waitForFunction(
    (count) => document.querySelectorAll('.results tbody tr').length === count,
    count,
  );
}
async function screenshot(page, name) {
  if (OUTPUT_DIR)
    await page.screenshot({
      path: path.join(OUTPUT_DIR, name),
      fullPage: true,
    });
}

void (async () => {
  if (OUTPUT_DIR) fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  let failure;
  let activePage;
  try {
    const desktop = await setup(browser);
    const page = (activePage = desktop.page);
    await open(page);
    assert.deepEqual(await dates(page).allTextContents(), [
      '2026-07-09',
      '2026-08-27',
    ]);
    await dateButton(page, '2026-08-17').click();
    assert.deepEqual(await dates(page).allTextContents(), [
      '2026-08-17',
      '待选择',
    ]);
    assert.deepEqual(rangeParams(page), ['2026-07-09', '2026-08-27']);
    assert.equal(await done(page).isDisabled(), true);
    assert.match(await caption(page).innerText(), /请选择结束日期/);
    mark(
      'existing range: first click starts a fresh draft without changing the query',
    );

    await dateButton(page, '2026-08-20').hover();
    assert.equal(
      await dateButton(page, '2026-08-20').getAttribute('data-range-end'),
      'true',
    );
    assert.deepEqual(await dates(page).allTextContents(), [
      '2026-08-17',
      '待选择',
    ]);
    assert.deepEqual(rangeParams(page), ['2026-07-09', '2026-08-27']);
    mark('hover previews the range without committing an end date');
    await dateButton(page, '2026-08-20').click();
    await applied(page, '2026-08-17', '2026-08-20');
    await page.mouse.move(0, 0);
    await screenshot(page, 'desktop-complete.png');
    if (OUTPUT_DIR)
      await page.locator('.date-popover').screenshot({
        path: path.join(OUTPUT_DIR, 'desktop-calendar.png'),
      });
    mark(
      'second click applies both new endpoints and the corresponding results',
    );

    await done(page).click();
    await open(page);
    await dateButton(page, '2026-08-24').click();
    assert.deepEqual(await dates(page).allTextContents(), [
      '2026-08-24',
      '待选择',
    ]);
    assert.deepEqual(rangeParams(page), ['2026-08-17', '2026-08-20']);
    await dateButton(page, '2026-08-27').click();
    await applied(page, '2026-08-24', '2026-08-27');
    mark('closing and reopening always restarts with a start-date click');

    await dateButton(page, '2026-08-31').click();
    assert.deepEqual(rangeParams(page), ['2026-08-24', '2026-08-27']);
    await page.getByRole('button', { name: '下个月', exact: true }).click();
    await dateButton(page, '2026-09-02').click();
    await applied(page, '2026-08-31', '2026-09-02');
    assert.match(
      await page.locator('.date-calendar .rdp-caption_label').innerText(),
      /九月/,
    );
    mark(
      'a third click begins another range; cross-month completion preserves the visible month',
    );

    await done(page).click();
    await open(page);
    await dateButton(page, '2026-09-04').click();
    await dateButton(page, '2026-09-01').click();
    await applied(page, '2026-09-01', '2026-09-04');
    mark('reverse endpoint clicks produce an ordered valid range');
    await dateButton(page, '2026-09-07').click();
    await dateButton(page, '2026-09-07').click();
    await applied(page, '2026-09-07', '2026-09-07');
    mark('clicking the same trading day twice completes a one-day range');
    assert.equal(await dateButton(page, '2026-09-05').isDisabled(), true);
    mark('non-trading days stay disabled');

    await dateButton(page, '2026-09-14').click();
    await page.keyboard.press('Escape');
    assert.deepEqual(rangeParams(page), ['2026-09-07', '2026-09-07']);
    await open(page);
    assert.equal(await done(page).isEnabled(), true);
    assert.deepEqual(await dates(page).allTextContents(), [
      '2026-09-07',
      '2026-09-07',
    ]);
    await page.getByRole('button', { name: '重新选择', exact: true }).click();
    assert.deepEqual(await dates(page).allTextContents(), ['待选择', '待选择']);
    assert.equal(await done(page).isDisabled(), true);
    await dateButton(page, '2026-09-21').focus();
    await page.keyboard.press('Enter');
    await dateButton(page, '2026-09-23').focus();
    await page.keyboard.press('Enter');
    await applied(page, '2026-09-21', '2026-09-23');
    mark(
      'Escape discards unfinished selection; reset and keyboard selection work',
    );

    await dateButton(page, '2026-09-28').click();
    await page.getByRole('button', { name: '近5日', exact: true }).click();
    await applied(page, '2026-09-24', '2026-09-30');
    await dateButton(page, '2026-09-14').click();
    assert.deepEqual(await dates(page).allTextContents(), [
      '2026-09-14',
      '待选择',
    ]);
    mark('a shortcut replaces the draft and the next click starts a new range');
    await page.getByRole('radio', { name: '多日', exact: true }).click();
    assert.equal(await done(page).isEnabled(), true);
    await page.getByRole('button', { name: '清空', exact: true }).click();
    await dateButton(page, '2026-09-21').click();
    await dateButton(page, '2026-09-23').click();
    await page.waitForFunction(
      () =>
        new URLSearchParams(location.search).get('dates') ===
        '2026-09-21,2026-09-23',
    );
    await page.getByRole('radio', { name: '单日', exact: true }).click();
    await dateButton(page, '2026-09-29').click();
    await page.waitForFunction(
      () => new URLSearchParams(location.search).get('date') === '2026-09-29',
    );
    await page.locator('.date-popover').waitFor({ state: 'detached' });
    assert.equal(await page.locator('.date-popover').count(), 0);
    mark(
      'leaving a pending range restores Done; multiple and single date modes still work',
    );
    await desktop.context.close();

    const mobile = await setup(browser, true);
    const phone = (activePage = mobile.page);
    await open(phone);
    await dateButton(phone, '2026-08-17').tap();
    assert.equal(await done(phone).isDisabled(), true);
    await screenshot(phone, 'mobile-pending.png');
    await dateButton(phone, '2026-08-20').tap();
    await applied(phone, '2026-08-17', '2026-08-20');
    const box = await phone.locator('.date-popover').boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= 375);
    assert.ok(box.y >= 0 && box.y + box.height <= 812);
    await screenshot(phone, 'mobile-complete.png');
    await done(phone).tap();
    await open(phone);
    await dateButton(phone, '2026-08-24').tap();
    assert.deepEqual(await dates(phone).allTextContents(), [
      '2026-08-24',
      '待选择',
    ]);
    assert.deepEqual(rangeParams(phone), ['2026-08-17', '2026-08-20']);
    mark(
      '375px touch viewport: two taps, visible popup, completion and reopen all work',
    );
    await mobile.context.close();
    assert.deepEqual(errors, []);
    assert.deepEqual(blocked, []);
  } catch (error) {
    failure = error;
    if (activePage && !activePage.isClosed())
      await screenshot(activePage, 'failure.png').catch(() => {});
  } finally {
    await browser.close();
    const result = {
      scope: 'local isolated browser fixtures; no production requests',
      status: failure ? 'failed' : 'passed',
      checks,
      errors,
      blocked,
      failure: failure?.stack || null,
    };
    if (OUTPUT_DIR)
      fs.writeFileSync(
        path.join(OUTPUT_DIR, 'browser-checks.json'),
        JSON.stringify(result, null, 2) + '\n',
      );
    console.log(JSON.stringify(result, null, 2));
  }
  if (failure) process.exitCode = 1;
})();
