const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const SITE = process.env.CONCENTRATION_UI_URL || 'http://127.0.0.1:4230';
const OUT = process.env.CONCENTRATION_UI_OUT;
assert.ok(OUT, 'An explicit output directory is required');
assert.ok(['localhost', '127.0.0.1'].includes(new URL(SITE).hostname));
const API = 'https://api.stock.iteready.com';
const sessions = ['2026-09-24', '2026-09-28', '2026-09-29', '2026-09-30', '2026-10-08', '2026-10-09', '2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16', '2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23', '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30'];
const groups = [20, 'LONG'];
const streakSessions = [...Array.from({length: 100}, (_, i) => new Date(Date.UTC(2026, 5, 16 + i)).toISOString().slice(0, 10)), ...sessions];
const model = 'chip-estimate.eastmoney-nodes150.ohlc4.zero-start.decay2.window210.v1';
const rule = 'CENTRAL_90_NONINCREASING_INDEPENDENT_210_TWO_V1';
const row = (code, name, values, reasons = {}, short = 0) => {
  let streak = 0;
  let boundary = {kind: 'CALENDAR_LIMIT', session: sessions[0], reason: 'SUPPORTED_CALENDAR_START'};
  for (let i = 20; i > 0; i--) {
    const before = values[i - 1], after = values[i];
    if (reasons[i] === 'NO_TRADE') { boundary = {kind: 'NO_TRADE', session: sessions[i], reason: 'NO_TRADE'}; break; }
    if (before === null || after === null) { const missing = after === null ? i : i - 1; boundary = {kind: 'UNAVAILABLE', session: sessions[missing], reason: reasons[missing]}; break; }
    if (after > before) { boundary = {kind: 'INCREASE', previous_session: sessions[i - 1], previous_value: before, anchor_session: sessions[i], anchor_value: after}; break; }
    streak++;
  }
  const states = Object.fromEntries(groups.map(group => {
    const count = group === 'LONG' ? 21 : group;
    let unknown = count > 20;
    for (let i = Math.max(1, 21 - count); i <= 20; i++) {
      if (reasons[i] === 'NO_TRADE' || (values[i - 1] !== null && values[i] !== null && values[i] > values[i - 1])) return [group, 'FAIL'];
      if (values[i - 1] === null || values[i] === null) unknown = true;
    }
    return [group, unknown ? 'UNKNOWN' : 'PASS'];
  }));
  const result = {security_id: code, name, close_cny: '10.00', values, streak_days: streak, streak_boundary: boundary, streak_start_session: values[20] === null ? null : sessions[20 - streak], groups: states, point_reasons: reasons, short_history_days: short};
  if (states[20] === 'PASS') {
    const long = code === '999901';
    result.streak_days = long ? 66 : 21;
    result.streak_values = long ? [...Array(46).fill(values[0]), ...values] : [values[0], ...values];
    result.streak_start_session = streakSessions.at(-result.streak_values.length);
    result.streak_boundary = long
      ? {kind: 'INCREASE', previous_session: streakSessions.at(-68), previous_value: values[0] - 1, anchor_session: result.streak_start_session, anchor_value: values[0]}
      : {kind: 'UNAVAILABLE', session: streakSessions.at(-23), reason: 'SOURCE_SESSION_MISSING'};
    result.groups.LONG = 'PASS';
  }
  return result;
};
const rows = [
  row('999901', '虚构甲20日', Array.from({length: 21}, (_, i) => 40 - i)),
  row('999902', '虚构乙增加', [...Array(20).fill(19), 20]),
  row('999903', '虚构丙增加', [...Array(20).fill(9), 10]),
  row('999904', '虚构丁短历史', [...Array(15).fill(null), 20, 19, 18, 17, 16, 15], Object.fromEntries(Array.from({length: 15}, (_, i) => [i, 'SOURCE_SESSION_MISSING'])), 6),
  row('999905', '虚构戊有效零', Array(21).fill(0)),
  row('999906', '虚构己无交易', [...Array.from({length: 20}, (_, i) => 40 - i), null], {20: 'NO_TRADE'}),
  ...Array.from({length: 66}, (_, i) => row(String(998000+i), '虚构分页样本'+i, Array.from({length:21}, (_, j) => 50-j))),
];
function publication(dataset = '0123456789abcdef', empty = false) {
  const values = empty ? rows.map(r => row(r.security_id, r.name, [...Array(20).fill(10), 11])) : structuredClone(rows);
  const source = { schema: 'stock.chips.market.manifest.v1', dataset_id: dataset, active_slot: dataset, as_of: sessions.at(-1), generated_at: '2026-10-30T10:00:00Z', universe_total: values.length, scanned_count: values.length, coefficient: 2, model_version: model, universe_coverage: 'SITE_CATALOG_UNVERIFIED' };
  return {
    source,
    scan: {
      schema: 'stock.chips.concentration.two.v1', rule_version: rule, dataset_id: dataset, active_slot: dataset,
      as_of: source.as_of, coefficient: 2, model_version: model,
      window_policy: 'EACH_DATE_INDEPENDENT_TRAILING_210_OBSERVED_BARS', sessions, streak_sessions: streakSessions,
      universe_total: values.length, processed_count: values.length, source_universe_coverage: source.universe_coverage,
      input_fingerprint: 'f'.repeat(64), rows: values,
      groups: Object.fromEntries(groups.map(n => [n, Object.fromEntries(['PASS', 'FAIL', 'UNKNOWN'].map(s => [s, values.filter(r => r.groups[n] === s).length]))])),
    },
  };
}

function stockFrame(dataset, code) {
  const security = rows.find(row => row.security_id === code);
  assert.ok(security, 'Only selected synthetic stocks can be read');
  const count = code === '999904' ? 18 : 140;
  const history = Array.from({ length: count }, (_, i) => ({
    date: new Date(Date.UTC(2026, 9, 30 - (count - 1) + i)).toISOString().slice(0, 10),
    open: '10', high: '13', low: '9', close: i === 19 ? '10' : '12', volume_shares: String(123456 + i),
  }));
  return { schema: 'stock.chips.market.stock.v1', dataset_id: dataset, as_of: sessions.at(-1), security_id: code, name: security.name, history };
}

const results = [];
async function setup(browser, options = {}) {
  const context = await browser.newContext({ viewport: { width: options.width || 1440, height: 960 }, hasTouch: options.width === 375, serviceWorkers: 'block' });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  const state = { auth: options.auth !== false, authReads: 0, documents: 0, writes: [], unexpected: [], errors: [], reads: [], failSummary: options.failSummary || false, failManifest: false, failStock: options.failStock || false, emptyStock: options.emptyStock || false, staleStock: options.staleStock || false, heldStock: options.heldStock || false, releaseStock: null, held: options.held || false, release: null, data: publication(undefined, options.empty) };
  page.on('pageerror', error => state.errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin === new URL(SITE).origin) {
      if (request.isNavigationRequest()) state.documents++;
      return route.continue();
    }
    if (url.origin !== API) { state.unexpected.push(url.origin + url.pathname); return route.abort(); }
    if (request.method() !== 'GET') { state.writes.push(request.method() + ' ' + url.pathname); return route.abort(); }
    const json = (value, status = 200) => route.fulfill({ status, json: value, headers: { 'access-control-allow-origin': new URL(SITE).origin, 'access-control-allow-credentials': 'true' } });
    if (url.pathname === '/auth/me') {
      state.authReads++;
      return json(state.auth ? { username: '隔离验收' } : { error: 'expired' }, state.auth ? 200 : 401);
    }
    state.reads.push(url.pathname);
    if (!state.auth) return json({ error: 'expired' }, 401);
    if (url.pathname === '/data/chips/daily/manifest.json') return json(state.failManifest ? {} : state.data.source, state.failManifest ? 404 : 200);
    if (/^\/data\/chips\/daily\/[a-f0-9]{16}\/concentration-two-v1\.json$/.test(url.pathname)) {
      const snapshot = state.data.scan;
      if (state.held) await new Promise(resolve => { state.release = resolve; });
      try { return await json(state.failSummary ? {} : snapshot, state.failSummary ? 404 : 200); }
      catch (error) { if (!/closed|handled|aborted|canceled/i.test(error.message)) throw error; return; }
    }
    const stockPath = url.pathname.match(/^\/data\/chips\/daily\/([a-f0-9]{16})\/(\d{6})\.json$/);
    if (stockPath) {
      if (state.heldStock && stockPath[2] === '999901') await new Promise(resolve => { state.releaseStock = resolve; });
      try {
        const frame = stockFrame(stockPath[1], stockPath[2]);
        if (state.emptyStock) frame.history = [];
        if (state.staleStock) frame.history.pop();
        return await json(state.failStock ? {} : frame, state.failStock ? 503 : 200);
      } catch (error) { if (!/closed|handled|aborted|canceled/i.test(error.message)) throw error; return; }
    }
    return json({ error: 'No isolated fixture for this existing page' }, 404);
  });
  return { context, page, state };
}
async function ready(page, group = 20) {
  await page.getByRole('heading', { name: group === 'LONG' ? '连续不增大于20个交易日' : '至少连续不增' + group + '个交易日', exact: true }).waitFor();
}
const mainRows = page => page.locator('.conc-table-wrap').first().locator('tbody tr');
const sortKeys = ['code', 'current', 'streak', 'start', 'drop'];
const mainHeaders = page => page.locator('.conc-table-wrap').first().locator('thead th');
async function assertSort(page, key, direction) {
  const headers = mainHeaders(page);
  assert.deepEqual(await headers.evaluateAll(items => items.map(item => item.getAttribute('aria-sort'))),
    sortKeys.map(candidate => candidate === key ? (direction === 'asc' ? 'ascending' : 'descending') : 'none'));
}
async function chooseSort(page, key, direction) {
  const index = sortKeys.indexOf(key);
  assert.ok(index >= 0, 'Known sortable column');
  const header = mainHeaders(page).nth(index);
  const expected = direction === 'asc' ? 'ascending' : 'descending';
  for (let click = 0; click < 2 && await header.getAttribute('aria-sort') !== expected; click++) {
    await header.locator('button').click();
  }
  await assertSort(page, key, direction);
}
async function assertFocusedRowVisible(page, code, input) {
  const geometry = await mainRows(page).locator('[data-stock-key="'+code+'"]').evaluate(async element => {
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
    const list = element.closest('.conc-table-wrap');
    const bounds = list.getBoundingClientRect();
    const row = element.getBoundingClientRect();
    return {
      focused: element === document.activeElement,
      top: row.top,
      bottom: row.bottom,
      visibleTop: Math.max(0, bounds.top, ...[...list.querySelectorAll('thead th')].map(header => header.getBoundingClientRect().bottom)),
      visibleBottom: Math.min(window.innerHeight, bounds.bottom),
    };
  });
  assert.equal(geometry.focused, true, input+' focuses '+code);
  assert.ok(geometry.top >= geometry.visibleTop && geometry.bottom <= geometry.visibleBottom, input+' reveals '+code+' below the sticky header '+JSON.stringify(geometry));
}
const chooseGroup = (page, n) => page.getByRole('button', { name: n === 'LONG' ? /^大于20天/ : new RegExp('^连续' + n + '天') }).click();
async function check(browser, name, options, run) {
  const view = await setup(browser, options);
  try {
    await run(view.page, view.state);
    assert.deepEqual(view.state.errors, [], 'No browser runtime errors');
    assert.deepEqual(view.state.writes, [], 'Browser checks never write an API');
    assert.deepEqual(view.state.unexpected, [], 'No external network destinations');
    results.push({ name, status: 'passed' });
  } catch (error) {
    await view.page.screenshot({ path: path.join(OUT, name + '-failure.png'), fullPage: false }).catch(() => {});
    results.push({ name, status: 'failed', error: error.stack });
  } finally {
    if (view.state.release) view.state.release();
    if (view.state.releaseStock) view.state.releaseStock();
    await view.context.close();
  }
  console.log(JSON.stringify(results.at(-1)));
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    await check(browser, 'automatic-publication-waits-for-complete-summary', {}, async (page, state) => {
      await page.clock.install();
      await page.goto(SITE + '/concentration'); await ready(page);
      await mainRows(page).first().waitFor();
      const previousRows = await mainRows(page).count();
      assert.ok(previousRows > 0);
      state.data = publication('fedcba9876543210', true);
      state.data.source.generated_at = '2026-10-30T11:00:00Z';
      state.failSummary = true;
      await page.clock.runFor(60001);
      await page.getByRole('alert').filter({ hasText: '仍显示上次完整结果' }).waitFor();
      assert.equal(await mainRows(page).count(), previousRows);
      state.failSummary = false;
      await page.clock.runFor(60001);
      await page.waitForFunction(() => document.querySelectorAll('.conc-table-wrap tbody tr').length === 0);
      assert.equal(await page.getByRole('alert').count(), 0);
      assert.ok(state.reads.filter(path => path.includes('/fedcba9876543210/concentration-two-v1.json')).length >= 2);
    });
    await check(browser, 'raw-precision-and-null-last-both-directions', {}, async (page, state) => {
      const target = state.data.scan.rows;
      target[target.findIndex(r => r.security_id === '999902')] = row('999902','虚构乙增加',[...Array(20).fill(1),1.001]);
      target[target.findIndex(r => r.security_id === '999903')] = row('999903','虚构丙增加',[...Array(20).fill(1),1.002]);
      await page.goto(SITE + '/concentration'); await ready(page);
      await page.getByLabel('集中度名单状态').selectOption('FAIL');
      for (const direction of ['asc','desc']) {
        await chooseSort(page, 'current', direction);
        const codes = await mainRows(page).locator('[data-stock-key]').evaluateAll(items => items.map(item => item.dataset.stockKey));
        assert.deepEqual(codes, direction === 'asc' ? ['999902','999903','999906'] : ['999903','999902','999906']);
        assert.equal(await mainRows(page).nth(0).locator('td').nth(1).innerText(), '1.00%');
        assert.equal(await mainRows(page).nth(1).locator('td').nth(1).innerText(), '1.00%');
        await chooseSort(page, 'streak', direction);
        assert.equal(await mainRows(page).last().locator('[data-stock-key]').getAttribute('data-stock-key'), '999906');
      }
    });
    await check(browser, 'sortable-headers-selection-and-pagination', {}, async (page, state) => {
      await page.goto(SITE + '/concentration'); await ready(page);
      const headers = page.locator('.conc-table-wrap').first().locator('thead th');
      assert.equal(await headers.count(), 5);
      assert.equal(await page.getByLabel('集中度排序').count(), 0);
      assert.equal(await headers.locator('button').count(), 5);
      await assertSort(page, 'streak', 'desc');
      assert.equal(await headers.locator('[data-stock-key]').count(), 0);
      assert.match(await headers.nth(0).locator('button').getAttribute('title'), /六位股票代码/);
      await page.locator('.conc-candles').getByRole('button', {name:'60根',exact:true}).click();
      const selected = '999901';
      await mainRows(page).locator('[data-stock-key="'+selected+'"]').click();
      for (const [index, key] of ['code','current','streak','start','drop'].entries()) {
        if (key === 'streak') await chooseSort(page, 'code', 'desc');
        const button = headers.nth(index).locator('button');
        await button.click();
        await assertSort(page, key, 'asc');
        assert.equal(await headers.nth(index).getAttribute('aria-sort'), 'ascending');
        await button.focus(); await page.keyboard.press('Enter');
        await assertSort(page, key, 'desc');
        assert.equal(await headers.nth(index).getAttribute('aria-sort'), 'descending');
        assert.equal(await button.evaluate(el => el === document.activeElement), true);
        await page.keyboard.press('Space');
        await assertSort(page, key, 'asc');
        await page.locator('tr[aria-selected=true] [data-stock-key="'+selected+'"]').waitFor();
        assert.equal(await page.locator('.conc-candles').getByRole('button', {name:'60根',exact:true}).getAttribute('aria-pressed'), 'true');
        for (const direction of ['asc','desc']) {
          await chooseSort(page, key, direction);
          assert.equal(await headers.nth(index).getAttribute('aria-sort'), direction === 'asc' ? 'ascending' : 'descending');
          const previous = page.getByRole('button', {name:'上一页股票',exact:true});
          if (await previous.isEnabled()) await previous.click();
          const actual = await mainRows(page).locator('[data-stock-key]').evaluateAll(items => items.map(item => item.dataset.stockKey));
          await page.getByRole('button', {name:'下一页股票',exact:true}).click();
          actual.push(...await mainRows(page).locator('[data-stock-key]').evaluateAll(items => items.map(item => item.dataset.stockKey)));
          const metric = row => key === 'current' ? row.values[20] : key === 'streak' ? row.streak_days : key === 'start' ? row.values[0] : row.values[0]-row.values[20];
          const expected = state.data.scan.rows.filter(row => row.groups[20] === 'PASS').sort((a,b) => {
            const primary = key === 'code' ? a.security_id.localeCompare(b.security_id) : metric(a)-metric(b);
            return (direction === 'asc' ? primary : -primary) || a.security_id.localeCompare(b.security_id);
          }).map(row => row.security_id);
          assert.deepEqual(actual, expected, key+' '+direction+' sorts the full list before paging');
        }
      }
      await chooseSort(page, 'code', 'asc');
      assert.equal(await mainRows(page).count(), 8, 'Selected stock relocates to second page');
      await page.getByRole('button', {name:'上一页股票',exact:true}).click();
      assert.equal(await mainRows(page).count(), 60, 'Ordinary paging stays on requested page');
      assert.match(await mainRows(page).first().innerText(), /998000/);
      await mainRows(page).last().locator('button').focus();
      await mainRows(page).last().locator('button').click();
      await page.keyboard.press('ArrowDown');
      await page.locator('tr[aria-selected=true] [data-stock-key="998060"]').waitFor();
      await assertFocusedRowVisible(page, '998060', 'Cross-page ArrowDown');
      const before = await page.locator('.conc-detail h2').innerText();
      state.data = publication('fedcba9876543210');
      state.data.scan.rows.reverse();
      await Promise.all([page.waitForResponse(response => response.url().endsWith('/concentration-two-v1.json')), page.getByRole('button', {name:'刷新集中度筛选',exact:true}).click()]);
      await ready(page);
      assert.equal(await page.locator('.conc-detail h2').innerText(), before);
      await assertSort(page, 'code', 'asc');
      await page.locator('tr[aria-selected=true] [data-stock-key="998060"]').waitFor();
    });
    for (const headerHeight of [null, 44]) {
    await check(browser, 'sortable-header-visible-with-selected-row'+(headerHeight ? '-integer-height' : ''), {}, async page => {
      await page.goto(SITE + '/concentration'); await ready(page);
      if (headerHeight) await page.addStyleTag({content: '.conc-sort-table th {height:'+headerHeight+'px}'});
      const list = page.locator('.conc-table-wrap').first();
      const headers = list.locator('thead th');
      const button = headers.first().locator('button');
      if (headerHeight) assert.equal(await headers.first().evaluate(el => el.getBoundingClientRect().height), headerHeight);
      await mainRows(page).locator('[data-stock-key="998030"]').click();
      await chooseSort(page, 'code', 'asc');
      for (const input of ['click', 'Enter', 'Space']) {
        if (input === 'click') await button.click();
        else await page.keyboard.press(input);
        const direction = input === 'Enter' ? 'asc' : 'desc';
        await assertSort(page, 'code', direction);
        await list.locator('tr[aria-selected=true] [data-stock-key="998030"]').waitFor();
        const geometry = await button.evaluate(async element => {
          await new Promise(requestAnimationFrame);
          await new Promise(requestAnimationFrame);
          const header = element.getBoundingClientRect();
          const list = element.closest('.conc-table-wrap').getBoundingClientRect();
          return {
            focused: element === document.activeElement,
            top: header.top,
            bottom: header.bottom,
            listTop: list.top,
            listBottom: list.bottom,
            viewportHeight: window.innerHeight,
            scrollTop: element.closest('.conc-table-wrap').scrollTop,
          };
        });
        assert.equal(geometry.focused, true, input+' retains header focus');
        assert.ok(geometry.scrollTop > 0, input+' exposes the selected row below the top');
        assert.ok(geometry.top >= Math.max(0, geometry.listTop) && geometry.bottom <= Math.min(geometry.viewportHeight, geometry.listBottom), input+' keeps the focused sorting control visible '+JSON.stringify(geometry));
      }
      await mainRows(page).locator('[data-stock-key="998030"]').click();
      for (let index = 1; index <= 12; index++) {
        await page.keyboard.press('ArrowUp');
        await assertFocusedRowVisible(page, String(998030 + index), 'ArrowUp '+index);
      }
      await page.keyboard.press('Shift+Tab');
      await assertFocusedRowVisible(page, '998043', 'Native reverse Tab');
      await page.keyboard.press('Tab');
      await assertFocusedRowVisible(page, '998042', 'Native Tab');
      assert.equal(await list.locator('tr[aria-selected=true] [data-stock-key]').getAttribute('data-stock-key'), '998042', 'Tab focus preserves the explicit selection');
    });
    }
    await check(browser, 'groups-search-sort-pagination-and-detail', {}, async (page, state) => {
      await page.goto(SITE + '/concentration');
      await ready(page);
      assert.deepEqual(state.reads.filter(path => !/\/\d{6}\.json$/.test(path)), ['/data/chips/daily/manifest.json', '/data/chips/daily/manifest.json', '/data/chips/daily/0123456789abcdef/concentration-two-v1.json', '/data/chips/daily/manifest.json']);
      assert.equal(await mainRows(page).count(), 60);
      assert.match(await mainRows(page).first().innerText(), /虚构甲20日/);
      assert.match(await mainRows(page).first().innerText(), /66天/);
      for (const n of groups) {
        await chooseGroup(page, n);
        await ready(page, n);
        assert.equal(await page.getByRole('button', { name: n === 'LONG' ? /^大于20天/ : new RegExp('^连续' + n + '天') }).getAttribute('aria-pressed'), 'true');
        assert.equal(await mainRows(page).count(), 60);
        for (const removed of [3, 5, 10, 15]) assert.equal(await page.getByRole('button', {name: new RegExp('^连续'+removed+'天')}).count(), 0);
      }
      assert.equal(state.reads.filter(path => !/\/\d{6}\.json$/.test(path)).length, 4, 'Group changes do not refetch the market');
      await chooseGroup(page, 20);
      await chooseSort(page, 'current', 'asc');
      assert.match(await mainRows(page).first().innerText(), /虚构戊有效零/);
      await page.getByLabel('集中度名单状态').selectOption('UNKNOWN');
      await page.getByLabel('搜索集中度股票').fill('  999904  ');
      assert.equal(await mainRows(page).count(), 1);
      assert.match(await page.locator('.conc-detail').innerText(), /虚构丁短历史/);
      await page.getByText('展开21个交易日的精确读数与缺失说明', { exact: true }).click();
      assert.equal(await page.locator('.conc-series-table tbody tr').count(), 21);
      assert.match(await page.locator('.conc-series-table').innerText(), /缺少该交易日行情/);
      await page.getByRole('tab', { name: '口径与范围', exact: true }).click();
      assert.match(await page.getByRole('tabpanel').innerText(), /N\+1/);
      await page.getByLabel('搜索集中度股票').fill('');
      await page.getByLabel('集中度名单状态').selectOption('PASS');
      await chooseGroup(page, 20);
      await page.getByRole('button', { name: '下一页股票', exact: true }).click();
      assert.equal(await mainRows(page).count(), 8);
      await page.getByRole('button', { name: '上一页股票', exact: true }).click();
      await chooseSort(page, 'streak', 'desc');
      await page.getByRole('tab', { name: '逐日读数', exact: true }).click();
      await page.locator('.wb-workspace').evaluate(el => { el.scrollTop = 0; });
      await page.screenshot({ path: path.join(OUT, 'desktop-synthetic.png') });
    });
    await check(browser, 'unknown-zero-and-known-no-trade', {}, async page => {
      await page.goto(SITE + '/concentration'); await ready(page);
      await chooseGroup(page, 20);
      await page.getByLabel('集中度名单状态').selectOption('UNKNOWN');
      assert.equal(await mainRows(page).count(), 1);
      assert.match(await mainRows(page).innerText(), /虚构丁短历史/);
      assert.equal(await page.getByLabel('集中度名单状态').inputValue(), 'UNKNOWN');
      assert.match(await page.getByLabel('连续天数追溯边界').innerText(), /缺少该交易日行情/);
      await chooseGroup(page, 20);
      await page.getByLabel('集中度名单状态').selectOption('PASS');
      await chooseSort(page, 'current', 'asc');
      assert.match(await mainRows(page).first().innerText(), /虚构戊有效零/);
      assert.match(await mainRows(page).first().innerText(), /0\.00%/);
      await mainRows(page).first().locator('button').click();
      assert.match(await page.getByLabel('连续天数追溯边界').innerText(), /连续天数按已确认下界显示/);
      await page.getByLabel('集中度名单状态').selectOption('FAIL');
      await page.getByLabel('搜索集中度股票').fill('999906');
      await page.getByText('展开21个交易日的精确读数与缺失说明', { exact: true }).click();
      assert.match(await page.locator('.conc-series-table').innerText(), /确认无成交/);
    });
    await check(browser, 'long-complete-curve-and-exact-table', {}, async page => {
      await page.goto(SITE + '/concentration'); await ready(page);
      await chooseGroup(page, 'LONG'); await ready(page, 'LONG');
      assert.match(await mainRows(page).first().innerText(), /66天/);
      assert.equal(await page.locator('.conc-chart-point').count(), 67);
      assert.match(await page.locator('.conc-metrics').innerText(), new RegExp(streakSessions.at(-67)));
      await page.getByText('展开67个交易日的精确读数与缺失说明', {exact: true}).click();
      assert.equal(await page.locator('.conc-series-table tbody tr').count(), 67);
      assert.match(await page.locator('.conc-series-table tbody tr').first().innerText(), new RegExp(streakSessions.at(-67)));
      for (const width of [760, 375]) {
        await page.setViewportSize({width, height: 960});
        assert.equal(await page.locator('.conc-chart').evaluate(chart => {
          const bounds = chart.getBoundingClientRect();
          return [...chart.querySelectorAll('text')].every(label => {
            const rect = label.getBoundingClientRect();
            return rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1;
          });
        }), true, 'All mobile concentration axis labels fit inside their chart');
      }
      await chooseGroup(page, 20);
      assert.equal(await page.locator('.conc-chart-point').count(), 21);
    });
    await check(browser, 'mobile-axis-labels-fit-full-scale', {}, async (page, state) => {
      const row = state.data.scan.rows.find(row => row.security_id === '999901');
      row.values = Array.from({length: 21}, (_, i) => 100 - i * 5);
      row.streak_values = [...Array(46).fill(100), ...row.values];
      row.streak_boundary.previous_value = 99;
      row.streak_boundary.anchor_value = 100;
      await page.goto(SITE + '/concentration'); await ready(page);
      await chooseGroup(page, 'LONG'); await ready(page, 'LONG');
      for (const width of [760, 375]) {
        await page.setViewportSize({width, height: 960});
        assert.equal(await page.locator('.conc-chart').evaluate(chart => {
          const bounds = chart.getBoundingClientRect();
          return [...chart.querySelectorAll('text')].every(label => {
            const rect = label.getBoundingClientRect();
            return rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1;
          });
        }), true, 'Full-scale mobile labels fit inside the concentration chart');
      }
    });
    await check(browser, 'long-no-trade-before-boundary-retains-lower-bound', {}, async (page, state) => {
      const zero = state.data.scan.rows.find(row => row.security_id === '999905');
      zero.streak_boundary.reason = 'NO_TRADE';
      await page.goto(SITE + '/concentration'); await ready(page);
      await chooseGroup(page, 'LONG'); await ready(page, 'LONG');
      await page.getByLabel('搜索集中度股票').fill('999905');
      assert.match(await page.locator('.conc-metrics').innerText(), /≥21天/);
      const note = await page.getByLabel('连续天数追溯边界').innerText();
      assert.match(note, /较早交易日确认无成交，没有可比较读数/);
      assert.doesNotMatch(note, /中断/);
    });
    await check(browser, 'old-v2-is-rejected-without-fallback', {}, async (page, state) => {
      state.data.scan.schema = 'stock.chips.concentration.v2';
      await page.goto(SITE + '/concentration');
      await page.getByRole('alert').waitFor();
      assert.equal(await page.locator('.conc-detail').count(), 0);
      assert.equal(state.reads.some(path => path.endsWith('/concentration.json')), false);
    });
    await check(browser, 'manifest-pair-rollover-reloads-before-display', {held: true}, async (page, state) => {
      await page.clock.install();
      await page.goto(SITE + '/concentration');
      for (let i = 0; i < 100 && !state.release; i++) await page.waitForTimeout(10);
      assert.equal(typeof state.release, 'function');
      state.data = publication('fedcba9876543210'); state.held = false; state.release();
      await page.getByRole('alert').filter({ hasText: '筹码批次正在切换' }).waitFor();
      await page.clock.runFor(60001);
      await ready(page);
      assert.ok(state.reads.includes('/data/chips/daily/fedcba9876543210/concentration-two-v1.json'));
      assert.equal(state.reads.some(path => path === '/data/chips/daily/0123456789abcdef/999901.json'), false);
    });
    await check(browser, 'keyboard-selection-remains-visible', {}, async page => {
      await page.goto(SITE + '/concentration'); await ready(page);
      await mainRows(page).first().locator('button').focus();
      for (let i = 0; i < 18; i++) await page.keyboard.press('ArrowDown');
      const selected = page.locator('.conc-table-wrap').first().locator('button[aria-pressed=true]');
      await selected.waitFor();
      const visible = await selected.evaluate(el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; });
      assert.equal(visible, true, 'Selected keyboard row stays in the viewport');
      assert.equal(await selected.evaluate(el => el === document.activeElement), true);
    });
    await check(browser, 'refresh-recovery-retains-context', {}, async (page, state) => {
      await page.goto(SITE + '/concentration'); await ready(page);
      await chooseGroup(page, 20);
      await page.getByLabel('集中度名单状态').selectOption('UNKNOWN');
      await page.getByLabel('搜索集中度股票').fill('999904');
      await chooseSort(page, 'current', 'asc');
      state.data = publication('fedcba9876543210');
      state.failSummary = true;
      await page.getByRole('button', { name: '刷新集中度筛选', exact: true }).click();
      await page.getByRole('alert').waitFor();
      assert.match(await page.getByRole('alert').innerText(), /上次完整结果/);
      assert.equal(await page.getByLabel('搜索集中度股票').inputValue(), '999904');
      await assertSort(page, 'current', 'asc');
      assert.equal(await mainRows(page).count(), 1);
      state.failSummary = false;
      await page.getByRole('button', { name: '重新读取', exact: true }).click();
      await page.getByRole('alert').waitFor({ state: 'detached' });
      await ready(page, 20);
      assert.equal(await mainRows(page).count(), 1);
    });
    await check(browser, 'missing-summary-does-not-mean-empty-group', { failSummary: true }, async (page, state) => {
      await page.goto(SITE + '/concentration');
      await page.getByRole('alert').waitFor();
      assert.match(await page.getByRole('alert').innerText(), /尚未发布/);
      assert.equal(await page.getByText('暂无连续不增20天的股票', { exact: true }).count(), 0);
      state.failSummary = false;
      await page.getByRole('button', { name: '重新读取', exact: true }).click();
      await ready(page);
    });
    await check(browser, 'complete-empty-groups-are-explicit', { empty: true }, async page => {
      await page.goto(SITE + '/concentration'); await ready(page);
      await page.getByRole('heading', { name: '暂无连续不增20天的股票', exact: true }).waitFor();
      assert.equal(await page.locator('.conc-detail').count(), 0);
      await chooseGroup(page, 20);
      await page.getByRole('heading', { name: '暂无连续不增20天的股票', exact: true }).waitFor();
    });
    await check(browser, 'shared-auth-navigation-back-forward', {}, async (page, state) => {
      await page.goto(SITE + '/concentration'); await ready(page);
      for (const href of ['/chips', '/trend', '/golden/daily', '/', '/concentration']) {
        await page.locator('nav[aria-label="主要功能"] a[href="' + href + '"]').click();
        await page.waitForURL(SITE + href);
      }
      await ready(page);
      await page.goBack();
      await page.waitForURL(SITE + '/');
      await page.goForward();
      await page.waitForURL(SITE + '/concentration'); await ready(page);
      assert.equal(state.authReads, 1);
      assert.equal(state.documents, 1);
      assert.equal(state.reads.some(x => x.includes('_rsc')), false);
    });
    await check(browser, 'expired-session-unmounts-private-results', {}, async (page, state) => {
      await page.goto(SITE + '/concentration'); await ready(page);
      state.auth = false;
      await page.getByRole('button', { name: '刷新集中度筛选', exact: true }).click();
      await page.locator('input[type=password]').waitFor();
      assert.equal(await page.locator('.conc-table').count(), 0);
    });
    await check(browser, 'anonymous-direct-route-is-protected', { auth: false }, async (page, state) => {
      await page.goto(SITE + '/concentration');
      await page.locator('input[type=password]').waitFor();
      assert.deepEqual(state.reads, []);
    });
    await check(browser, 'candles-period-readout-and-order', {}, async (page, state) => {
      await page.goto(SITE + '/concentration'); await ready(page);
      const candles = page.locator('.conc-candles');
      await candles.locator('[data-candle-date]').first().waitFor();
      assert.equal(await candles.locator('[data-candle-date]').count(), 120);
      assert.equal(await candles.getByRole('button', { name: '120根', exact: true }).getAttribute('aria-pressed'), 'true');
      assert.equal(await candles.locator('.tv-chart-legend').count(), 0);
      for (const [field, value] of [['open', '开 10.00'], ['high', '高 13.00'], ['low', '低 9.00'], ['close', '收 12.00']]) assert.equal(await candles.locator('[data-field=' + field + ']').innerText(), value);
      assert.match(await candles.locator('[data-field=volume]').innerText(), /股/);
      assert.equal(await candles.locator('[data-volume-date]').count(), 120);
      assert.equal(await page.locator('.conc-detail').evaluate(el => {
        const plot = el.querySelector('.conc-chart'), candles = el.querySelector('.conc-candles'), tabs = el.querySelector('[role=tablist]');
        return !!(plot.compareDocumentPosition(candles) & Node.DOCUMENT_POSITION_FOLLOWING) && !!(candles.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING);
      }), true);
      const control = candles.getByRole('slider', { name: '查看K线日期' });
      await control.focus(); await page.keyboard.press('Home');
      assert.match(await candles.locator('.tv-candle-readout').innerText(), /20\.00%/);
      await page.keyboard.press('End');
      const lastDate = await control.getAttribute('aria-valuetext');
      const svg = candles.locator('svg'); await svg.scrollIntoViewIfNeeded();
      const box = await svg.boundingBox();
      await page.mouse.move(box.x + 40, box.y + 45);
      assert.notEqual(await control.getAttribute('aria-valuetext'), lastDate);
      await page.mouse.move(box.x + box.width - 3, box.y + 3);
      assert.equal(await control.getAttribute('aria-valuetext'), lastDate);
      await candles.getByRole('button', { name: '60根', exact: true }).click();
      assert.equal(await candles.locator('[data-candle-date]').count(), 60);
      await page.getByLabel('集中度名单状态').selectOption('UNKNOWN');
      await page.getByLabel('搜索集中度股票').fill('999904');
      await candles.getByText('可用 18 根', { exact: true }).waitFor();
      assert.equal(await candles.getByRole('button', { name: '60根', exact: true }).getAttribute('aria-pressed'), 'true');
      assert.equal(await candles.locator('[data-candle-date]').count(), 18);
      const before = state.reads.filter(path => path.endsWith('/999904.json')).length;
      await page.getByRole('button', { name: '刷新集中度筛选', exact: true }).click(); await ready(page);
      assert.equal(await candles.getByRole('button', { name: '60根', exact: true }).getAttribute('aria-pressed'), 'true');
      assert.equal(state.reads.filter(path => path.endsWith('/999904.json')).length, before);
      state.data = publication('fedcba9876543210');
      await Promise.all([page.waitForResponse(response => response.url().endsWith('/fedcba9876543210/999904.json')), page.getByRole('button', { name: '刷新集中度筛选', exact: true }).click()]);
      await candles.locator('[data-candle-date]').first().waitFor();
    });
    for (const width of [1440, 900, 880, 820, 761, 760, 375]) {
      await check(browser, 'candles-state-geometry-' + width, { width, heldStock: true }, async (page, state) => {
        await page.goto(SITE + '/concentration'); await ready(page);
        await page.locator('.conc-candle-state').waitFor();
        for (let i = 0; i < 100 && !state.releaseStock; i++) await page.waitForTimeout(10);
        assert.equal(typeof state.releaseStock, 'function');
        const measure = () => page.locator('.conc-candles').evaluate(el => ({
          section: el.getBoundingClientRect().height,
          tabs: document.querySelector('.conc-tabs').getBoundingClientRect().top,
        }));
        const loading = await measure();
        state.heldStock = false; state.releaseStock();
        await page.locator('.conc-candles [data-candle-date]').first().waitFor();
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const loaded = await measure();
        assert.ok(Math.abs(loaded.section - loading.section) <= 1, 'Candle section height matches loading at ' + width);
        assert.ok(Math.abs(loaded.tabs - loading.tabs) <= 1, 'Inspector tabs stay in place at ' + width);
      });
    }
    await check(browser, 'candles-delayed-selection-isolation', { heldStock: true }, async (page, state) => {
      await page.goto(SITE + '/concentration'); await ready(page);
      await page.waitForFunction(() => !!document.querySelector('.conc-candles'));
      for (let i = 0; i < 20 && !state.releaseStock; i++) await page.waitForTimeout(10);
      assert.equal(typeof state.releaseStock, 'function');
      await page.getByLabel('集中度名单状态').selectOption('FAIL');
      await mainRows(page).filter({ hasText: '虚构乙增加' }).locator('button').click();
      await page.locator('.conc-candles [data-candle-date]').first().waitFor();
      assert.match(await page.locator('.conc-candles svg title').first().textContent(), /虚构乙增加/);
      if (state.releaseStock) state.releaseStock();
      await page.waitForTimeout(100);
      assert.match(await page.locator('.conc-candles svg title').first().textContent(), /虚构乙增加/);
      assert.equal(await page.locator('.conc-candles').getByText('正在读取虚构甲20日日K线…').count(), 0);
    });
    await check(browser, 'candles-own-error-retry-preserves-context', { failStock: true }, async (page, state) => {
      await page.goto(SITE + '/concentration'); await ready(page);
      await chooseGroup(page, 20); await page.getByLabel('集中度名单状态').selectOption('UNKNOWN');
      await page.getByLabel('搜索集中度股票').fill('999904');
      await page.locator('.conc-candles [role=alert]').waitFor();
      state.failStock = false;
      await page.locator('.conc-candles').getByRole('button', { name: '重新读取', exact: true }).click();
      await page.locator('.conc-candles [data-candle-date]').first().waitFor();
      assert.equal(await page.getByLabel('搜索集中度股票').inputValue(), '999904');
      assert.equal(await page.getByRole('button', { name: /^连续20天/ }).getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('.conc-candles').getByRole('button', { name: '120根', exact: true }).getAttribute('aria-pressed'), 'true');
    });
    await check(browser, 'candles-valid-empty', { emptyStock: true }, async page => {
      await page.goto(SITE + '/concentration'); await ready(page);
      await page.getByText('本批次没有已保存的虚构甲20日日K线。', { exact: true }).waitFor();
      assert.equal(await page.locator('.conc-candles [role=alert]').count(), 0);
    });
    await check(browser, 'candles-last-saved-bar-is-explicit', { staleStock: true }, async page => {
      await page.goto(SITE + '/concentration'); await ready(page);
      await page.getByText('最后保存的行情为 2026-10-29，筛选数据截至 2026-10-30。', { exact: true }).waitFor();
      assert.equal(await page.locator('.conc-candles [data-candle-date="2026-10-30"]').count(), 0);
    });
    for (const width of [1440, 900, 760, 375]) {
      await check(browser, 'responsive-' + width, { width }, async page => {
        await page.goto(SITE + '/concentration'); await ready(page);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        assert.equal(await page.getByLabel('集中度排序').count(), 0);
        assert.equal(await mainHeaders(page).count(), 5);
        assert.equal(await mainHeaders(page).locator('button').count(), 5);
        await chooseSort(page, 'current', 'asc');
        await chooseSort(page, 'streak', 'desc');
        for (const n of groups) assert.equal(await page.getByRole('button', { name: n === 'LONG' ? /^大于20天/ : new RegExp('^连续' + n + '天') }).isVisible(), true);
        await page.screenshot({ path: path.join(OUT, 'width-' + width + '-synthetic.png') });
        await page.getByLabel('搜索集中度股票').fill('999901');
        await page.locator('.conc-detail').scrollIntoViewIfNeeded();
        await page.locator('.conc-candles [data-candle-date]').first().waitFor();
        const svg = page.locator('.conc-candles svg');
        assert.equal(await svg.evaluate(el => getComputedStyle(el).touchAction), 'pan-y');
        if (width === 375) {
          await svg.scrollIntoViewIfNeeded(); const bounds = await svg.boundingBox();
          await page.touchscreen.tap(bounds.x + 20, bounds.y + 50);
          assert.notEqual(await page.getByRole('slider', { name: '查看K线日期' }).getAttribute('aria-valuetext'), sessions.at(-1));
          assert.equal(Math.round(bounds.height), 310);
        }
        await page.screenshot({ path: path.join(OUT, 'detail-' + width + '-synthetic.png') });
      });
    }
  } finally { await browser.close(); }
  const report = { data: 'ALL CONCENTRATION VALUES AND STOCKS ARE SYNTHETIC', site: SITE, passed: results.every(r => r.status === 'passed'), checks: results };
  fs.writeFileSync(path.join(OUT, 'browser.json'), JSON.stringify(report, null, 2));
  if (!report.passed) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
