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
    new URL('../../src/components/daily-quote.tsx', import.meta.url),
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
const { DailyQuote } = mod.exports;

const base = {
  code: '000001',
  name: '平安银行',
  market: 'SZ',
  status: 'ok',
  first15Volume: 1,
  dailyVolume: 10,
  ratio: 10,
};

test('available daily quote renders atomic open close high low values', () => {
  const html = renderToStaticMarkup(
    createElement(DailyQuote, {
      row: {
        ...base,
        open: 10.1,
        high: 10.8,
        low: 9.9,
        close: 10.5,
        priceStatus: 'available',
      },
    }),
  );
  assert.match(html, /开.*10\.10/);
  assert.match(html, /收.*10\.50/);
  assert.match(html, /高.*10\.80/);
  assert.match(html, /低.*9\.90/);
});

test('confirmed no-trade and missing quotes stay visibly distinct', () => {
  const suspended = renderToStaticMarkup(
    createElement(DailyQuote, {
      row: {
        ...base,
        status: 'suspended',
        priceStatus: 'not_traded',
        specialStatus: {
          type: 'pending_delisting',
          label: '退市程序停牌',
          description: '退市',
          startedAt: '2026-09-18',
          source: '交易所',
        },
      },
    }),
  );
  const missing = renderToStaticMarkup(
    createElement(DailyQuote, {
      row: { ...base, status: 'missing', priceStatus: 'missing' },
    }),
  );
  assert.match(suspended, /退市无成交/);
  assert.match(missing, />—</);
});

test('compact price pairs preserve order and two-decimal precision', () => {
  const row = {
    ...base,
    open: 10.1,
    high: 10.8,
    low: 9.9,
    close: 10.5,
    priceStatus: 'available',
  };
  for (const [pair, first, second] of [
    ['open-close', '10.10', '10.50'],
    ['high-low', '10.80', '9.90'],
  ]) {
    const html = renderToStaticMarkup(createElement(DailyQuote, { row, pair }));
    assert.ok(html.indexOf(first) < html.indexOf(second));
    assert.match(html, /class="price-pair"/);
  }
});

test('compact price pairs never expose a partial or invalid price frame', () => {
  for (const fields of [
    { open: 10, high: 11, low: 9, close: null, priceStatus: 'available' },
    { open: 12, high: 11, low: 9, close: 10, priceStatus: 'available' },
    { open: 10, high: 11, low: 9, close: 10, priceStatus: 'missing' },
    {
      open: null,
      high: null,
      low: null,
      close: null,
      priceStatus: 'not_traded',
    },
  ]) {
    for (const pair of ['open-close', 'high-low']) {
      const html = renderToStaticMarkup(
        createElement(DailyQuote, { row: { ...base, ...fields }, pair }),
      );
      assert.match(html, /— \/ —/);
      assert.doesNotMatch(html, /[0-9]+\.[0-9]{2}|price-pair/);
    }
  }
});
