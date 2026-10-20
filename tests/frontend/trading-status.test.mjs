import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { compileFunction } from 'node:vm';
import ts from 'typescript';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const code = ts.transpileModule(
  readFileSync(
    new URL('../../src/components/trading-status.tsx', import.meta.url),
    'utf8',
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const mod = { exports: {} };
compileFunction(code, ['require', 'module', 'exports'])(
  createRequire(import.meta.url),
  mod,
  mod.exports,
);
const { TradingStatus } = mod.exports;

test('summary mode hides only calculable rows and retains unresolved or suspended records', () => {
  const row = {
    code: '600000',
    name: 'fixture',
    market: 'SH',
    first15Volume: null,
    dailyVolume: null,
    ratio: null,
  };
  const render = (status) =>
    renderToStaticMarkup(
      createElement(TradingStatus, {
        row: { ...row, status },
        hideNormal: true,
      }),
    );
  assert.equal(render('ok'), '');
  assert.match(render('missing'), /数据待补/);
  assert.match(render('suspended'), /确认无交易/);
  assert.match(render('unrecognized'), /记录状态尚未确认/);
  assert.doesNotMatch(render('unrecognized'), /status-icon-ok|正常交易/);
});

test('special suspension renders a compact graphical trigger with popover details', () => {
  const html = renderToStaticMarkup(
    createElement(TradingStatus, {
      row: {
        code: '601059',
        name: '信达证券',
        market: 'SH',
        status: 'suspended',
        first15Volume: null,
        dailyVolume: null,
        ratio: null,
        specialStatus: {
          type: 'merger_pending_delisting',
          label: '吸收合并停牌（拟终止上市）',
          description:
            '因中金公司换股吸收合并，股票连续停牌并进入主动终止上市程序。',
          startedAt: '2026-09-15',
          source: '上海证券交易所停复牌信息与公司公告',
          announcementTitle:
            '关于公司A股股票连续停牌直至终止上市、实施换股吸收合并的提示性公告',
          announcementUrl:
            'https://www.sse.com.cn/disclosure/dealinstruc/suspension/',
        },
      },
    }),
  );

  assert.match(html, /<button[^>]+aria-label="吸收合并停牌（拟终止上市）/);
  assert.match(html, /popover[Tt]arget="trading-status-601059-current"/);
  assert.match(html, /class="trading-status-popover"/);
  assert.match(html, /popover="auto"/);
  assert.doesNotMatch(html, /<details/);
  assert.match(html, /status-icon-merger/);
  assert.match(html, /<svg/);
  assert.match(html, /吸收合并停牌（拟终止上市）/);
  assert.match(html, /2026-09-15起/);
  assert.match(html, /中金公司换股吸收合并/);
  assert.match(html, /查看依据/);
  assert.match(html, /popover[Tt]arget[Aa]ction="hide"/);
});

test('ordinary and unresolved rows use distinct accessible icons instead of visible status text', () => {
  const ordinary = renderToStaticMarkup(
    createElement(TradingStatus, {
      row: {
        code: '600519',
        name: '贵州茅台',
        market: 'SH',
        status: 'ok',
        first15Volume: 1,
        dailyVolume: 10,
        ratio: 10,
      },
    }),
  );
  const unresolved = renderToStaticMarkup(
    createElement(TradingStatus, {
      row: {
        code: '600000',
        name: '浦发银行',
        market: 'SH',
        status: 'missing',
        first15Volume: null,
        dailyVolume: null,
        ratio: null,
      },
    }),
  );

  assert.match(ordinary, /aria-label="正常交易"/);
  assert.match(ordinary, /status-icon-ok/);
  assert.match(ordinary, /<svg/);
  assert.doesNotMatch(ordinary, />正常交易</);
  assert.doesNotMatch(ordinary, /停牌/);
  assert.match(unresolved, /aria-label="数据待补/);
  assert.match(unresolved, /status-icon-missing/);
  assert.match(unresolved, /<svg/);
  assert.doesNotMatch(unresolved, />数据待补</);
  assert.doesNotMatch(unresolved, /正常交易|退市|合并/);
});

test('delisting and ordinary suspension use different graphical states', () => {
  const delisting = renderToStaticMarkup(
    createElement(TradingStatus, {
      row: {
        code: '002731',
        name: '*ST萃华',
        market: 'SZ',
        status: 'suspended',
        first15Volume: null,
        dailyVolume: null,
        ratio: null,
        specialStatus: {
          type: 'pending_delisting',
          label: '规范类退市程序停牌',
          description: '等待深交所作出是否终止上市的决定。',
          startedAt: '2026-09-07',
          source: '深圳证券交易所信息披露公司公告',
        },
      },
    }),
  );
  const suspended = renderToStaticMarkup(
    createElement(TradingStatus, {
      row: {
        code: '600000',
        name: '浦发银行',
        market: 'SH',
        status: 'suspended',
        first15Volume: null,
        dailyVolume: null,
        ratio: null,
      },
    }),
  );

  assert.match(delisting, /status-icon-delisting/);
  assert.match(delisting, /aria-label="规范类退市程序停牌/);
  assert.match(suspended, /status-icon-suspended/);
  assert.match(suspended, /aria-label="确认无交易/);
  assert.doesNotMatch(suspended, />确认无交易</);
});

test('official Shenzhen disclosure evidence remains clickable', () => {
  const html = renderToStaticMarkup(
    createElement(TradingStatus, {
      row: {
        code: '002731',
        name: '*ST萃华',
        market: 'SZ',
        status: 'suspended',
        first15Volume: null,
        dailyVolume: null,
        ratio: null,
        specialStatus: {
          type: 'pending_delisting',
          label: '规范类退市程序停牌',
          description: '等待深交所作出是否终止上市的决定。',
          startedAt: '2026-09-07',
          source: '深圳证券交易所信息披露公司公告',
          announcementTitle: '关于公司股票停牌暨可能被终止上市的风险提示公告',
          announcementUrl:
            'https://disc.static.szse.cn/download/disc/example.PDF',
        },
      },
    }),
  );

  assert.match(html, /disc\.static\.szse\.cn/);
  assert.match(html, /查看依据/);
});

test('reviewed issuer disclosure in China Securities Journal remains clickable', () => {
  const html = renderToStaticMarkup(
    createElement(TradingStatus, {
      row: {
        code: '002743',
        name: '富煌钢构',
        market: 'SZ',
        status: 'suspended',
        first15Volume: null,
        dailyVolume: null,
        ratio: null,
        specialStatus: {
          type: 'risk_warning_transition',
          label: '风险警示切换停牌',
          description: '公司公告安排目标日停牌一天。',
          startedAt: '2026-09-07',
          source: '公司指定信息披露媒体：中国证券报',
          announcementUrl:
            'https://epaper.cs.com.cn/zgzqb/html/2026-09/05/nw.D110000zgzqb_20260905_4-B024.htm',
        },
      },
    }),
  );
  assert.match(html, /href="https:\/\/epaper\.cs\.com\.cn\//);
  assert.match(html, /查看依据/);
});

test('missing observations expose their own reason without claiming no trade', () => {
  const html = renderToStaticMarkup(
    createElement(TradingStatus, {
      row: {
        code: '688184',
        name: 'ST帕瓦',
        market: 'SH',
        status: 'missing',
        date: '2026-09-24',
        first15Volume: null,
        dailyVolume: 203691,
        ratio: null,
        reason: '新浪缺少目标09:45记录；等待新证据 <script>unsafe</script>',
      },
    }),
  );
  assert.match(html, /popover[Tt]arget="missing-status-688184-2026-09-24"/);
  assert.match(html, /aria-label="缺失数据说明"/);
  assert.match(html, /新浪缺少目标09:45记录/);
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|确认无交易|正常交易/);
});

test('untrusted and non-HTTPS disclosure links stay hidden', () => {
  for (const url of [
    'http://epaper.cs.com.cn/example',
    'https://epaper.cs.com.cn.evil.example/example',
    'javascript:alert(1)',
  ]) {
    const html = renderToStaticMarkup(
      createElement(TradingStatus, {
        row: {
          code: '002743',
          name: 'fixture',
          market: 'SZ',
          status: 'suspended',
          first15Volume: null,
          dailyVolume: null,
          ratio: null,
          specialStatus: {
            type: 'suspension',
            label: '停牌',
            description: '说明',
            startedAt: '2026-09-07',
            source: 'fixture',
            announcementUrl: url,
          },
        },
      }),
    );
    assert.doesNotMatch(html, /查看依据|href=/);
  }
});
