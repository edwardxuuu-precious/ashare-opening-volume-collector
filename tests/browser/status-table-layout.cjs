/* Desktop UI regression: run against the local development server. */
/* oxlint-disable typescript/no-require-imports -- executable CommonJS browser smoke */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const SITE = process.env.STATUS_UI_URL || 'http://localhost:3000';

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 900 },
    });
    await page.goto(`${SITE}/?date=2026-09-04&scope=all`);
    await page.locator('.results tbody tr').first().waitFor({ timeout: 30000 });

    const headers = (
      await page.locator('.results thead th').allTextContents()
    ).map((value) => value.replace(/\s+/g, ' ').trim());
    assert.deepEqual(headers, [
      '股票',
      '状态',
      '行情',
      '占比15分 / 全天',
      '成交量15分 / 全天',
      '涨跌涨幅 / 振幅',
    ]);

    const layouts = [];
    for (const width of [1280, 1024]) {
      await page.setViewportSize({ width, height: 900 });
      const layout = await page
        .locator('[data-slot="table-container"]')
        .evaluate((container) => ({
          clientWidth: container.clientWidth,
          scrollWidth: container.scrollWidth,
          tableRight: container.querySelector('table').getBoundingClientRect()
            .right,
          containerRight: container.getBoundingClientRect().right,
        }));
      assert.ok(
        layout.scrollWidth <= layout.clientWidth + 1,
        `${width}px desktop table overflowed by ${layout.scrollWidth - layout.clientWidth}px`,
      );
      assert.ok(
        layout.tableRight <= layout.containerRight + 1,
        `${width}px last desktop column is outside the visible table container`,
      );
      layouts.push({ width, ...layout });
    }

    const specialPage = await browser.newPage({
      viewport: { width: 1280, height: 900 },
    });
    await specialPage.route('**/data/2026-09-04.json', async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      const row = data.rows.find((item) => item.code === '000017');
      assert.ok(row, 'local UI fixture must include 000017');
      Object.assign(row, {
        status: 'suspended',
        first15Volume: null,
        dailyVolume: null,
        ratio: null,
        specialStatus: {
          type: 'merger_pending_delisting',
          label: '换股吸收合并停牌',
          description: '公司因实施换股吸收合并事项停牌。',
          startedAt: '2026-09-15',
          source: '交易所公告',
          announcementUrl:
            'https://www.sse.com.cn/disclosure/dealinstruc/suspension/',
        },
      });
      await route.fulfill({ response, json: data });
    });
    await specialPage.goto(
      `${SITE}/?date=2026-09-04&scope=single&symbols=000017`,
    );
    const specialRow = specialPage.locator('.results tbody tr');
    await specialRow.waitFor({ timeout: 30000 });
    const rowHeightBefore = await specialRow.evaluate(
      (element) => element.getBoundingClientRect().height,
    );
    await specialPage.getByRole('button', { name: /换股吸收合并停牌/ }).click();
    const popover = specialPage.locator('.trading-status-popover');
    await popover.waitFor({ state: 'visible' });
    const popoverLayout = await popover.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return {
        open: element.matches(':popover-open'),
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      };
    });
    const rowHeightAfter = await specialRow.evaluate(
      (element) => element.getBoundingClientRect().height,
    );
    assert.equal(popoverLayout.open, true);
    assert.ok(popoverLayout.left >= 0 && popoverLayout.right <= 1280);
    assert.ok(popoverLayout.top >= 0 && popoverLayout.bottom <= 900);
    assert.equal(rowHeightAfter, rowHeightBefore);
    assert.match(await popover.innerText(), /换股吸收合并停牌/);
    assert.match(await popover.innerText(), /查看依据/);
    if (process.env.STATUS_UI_SCREENSHOT)
      await specialPage.screenshot({
        path: process.env.STATUS_UI_SCREENSHOT,
        fullPage: false,
      });
    await specialPage.close();

    console.log(JSON.stringify({ headers, layouts, popoverLayout }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
