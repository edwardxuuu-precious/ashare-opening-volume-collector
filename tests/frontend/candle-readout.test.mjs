import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { compileFunction } from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const modules = new Map();
function component(path) {
  const url = new URL(`../../src/${path}.tsx`, import.meta.url);
  if (modules.has(url.href)) return modules.get(url.href);
  const code = ts.transpileModule(readFileSync(url, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const nativeRequire = createRequire(url), mod = { exports: {} };
  compileFunction(code, ['require', 'module', 'exports'])(specifier => {
    if (specifier.endsWith('.css')) return {};
    if (specifier.startsWith('@/components/')) return component(specifier.slice(2));
    if (specifier.startsWith('@/lib/')) return nativeRequire(fileURLToPath(new URL(`../../src/${specifier.slice(2)}.ts`, import.meta.url)));
    return nativeRequire(specifier);
  }, mod, mod.exports);
  modules.set(url.href, mod.exports);
  return mod.exports;
}

const { ChipTradingChart } = component('components/chip-trading-chart');
const { GoldenCanvas } = component('components/golden-canvas');
const { TrendChart } = component('app/trend/trend-chart');
const { CandleChange } = component('components/candle-change');
const { ChartPeriodControl } = component('components/chart-period-control');
const { CandleInspectionControl } = component('components/candle-inspection-control');
const bar = (date, close) => ({ date, open: close, high: close, low: close, close, volume_shares: '1000', amount_cny: '10000', turnover_percent: '2' });
const history = [bar('2026-10-14', '4.95'), bar('2026-10-15', '4.78')];
const textOf = (html, className) => html.match(new RegExp(`<div class="[^"]*\\b${className}\\b[^"]*"[^>]*>(.*?)</div>`))?.[1].replace(/<[^>]*>/g, '');

test('chip readout shows the selected day return alongside its close', () => {
  const html = renderToStaticMarkup(createElement(ChipTradingChart, {
    history, current: null, previous: null, date: '2026-10-15', compareDate: '2026-10-14', appearance: 'workbench',
  }));
  assert.match(textOf(html, 'chip-candle-readout'), /2026-10-15.*收 4\.78.*涨跌幅 -3\.43%.*量/);
});

test('golden readout shows the daily return even when the previous close is outside its drawing window', () => {
  const html = renderToStaticMarkup(createElement(GoldenCanvas, { history, displayPeriod: 1 }));
  assert.match(textOf(html, 'gw-readout'), /2026-10-15.*收 4\.78.*涨跌幅 -3\.43%.*量/);
  assert.equal((html.match(/data-candle-date=/g) ?? []).length, 1);
});

test('trend readout uses the same return while its displayed window omits the previous candle', () => {
  const html = renderToStaticMarkup(createElement(TrendChart, {
    stock: { name: '测试样本', volume_unit: 'shares', history: history.map(row => ({ date: row.date, open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close), volume: Number(row.volume_shares) })) },
    date: '2026-10-15', screen: null, initialPeriod: 1,
  }));
  assert.match(textOf(html, 'tv-candle-readout'), /2026-10-15.*收 4\.78.*涨跌幅 -3\.43%.*量/);
  assert.equal((html.match(/data-candle-date=/g) ?? []).length, 1);
});

test('all chart readouts disclose missing previous closes instead of displaying a zero return', () => {
  const single = history.slice(0, 1);
  const chips = renderToStaticMarkup(createElement(ChipTradingChart, { history: single, current: null, previous: null, date: single[0].date, compareDate: '', appearance: 'workbench' }));
  const golden = renderToStaticMarkup(createElement(GoldenCanvas, { history: single }));
  for (const [html, name] of [[chips, 'chip-candle-readout'], [golden, 'gw-readout']]) {
    assert.match(textOf(html, name), /涨跌幅 待核实/);
    assert.doesNotMatch(textOf(html, name), /涨跌幅 0\.00%/);
  }
});

test('shared return label gives rises and falls a sign and semantic color while zero and unknown stay neutral', () => {
  for (const [percent, text, color] of [[10.04, '+10.04%', 'up'], [-3.43, '-3.43%', 'down'], [0, '0.00%', null], [null, '待核实', null]]) {
    const html = renderToStaticMarkup(createElement(CandleChange, { percent }));
    assert.ok(html.includes(`>${text}</strong>`));
    assert.ok(html.includes('涨跌幅 '));
    if (color) assert.ok(html.includes(`class="candle-change-${color}"`));
    else assert.doesNotMatch(html, /candle-change-(up|down)/);
  }
});

test('all three chart readouts show identical base fields and explicit share units', () => {
  const chip = renderToStaticMarkup(createElement(ChipTradingChart, { history, current: null, previous: null, date: '2026-10-15', compareDate: '2026-10-14', appearance: 'workbench' }));
  const golden = renderToStaticMarkup(createElement(GoldenCanvas, { history }));
  const trend = renderToStaticMarkup(createElement(TrendChart, {
    stock: { name: '测试样本', volume_unit: 'shares', history: history.map(row => ({ date: row.date, open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close), volume: Number(row.volume_shares) })) }, date: '2026-10-15', screen: null,
  }));
  const expected = '2026-10-15开 4.78高 4.78低 4.78收 4.78涨跌幅 -3.43%量 1,000股';
  assert.equal(textOf(chip, 'chip-candle-readout'), `${expected}换手 2.00%`);
  assert.equal(textOf(golden, 'gw-readout'), expected);
  assert.equal(textOf(trend, 'tv-candle-readout'), expected);
});

test('period control shows the selected bar count and discloses a short real history', () => {
  const html = renderToStaticMarkup(createElement(ChartPeriodControl, { period: 120, periods: [60, 120], availableCount: 35, onPeriodChange() {} }));
  assert.match(html, /<fieldset[^>]*aria-label="图表周期"/);
  assert.match(html, /aria-pressed="true">120根/);
  assert.match(html, /可用 35 根/);
  const full = renderToStaticMarkup(createElement(ChartPeriodControl, { period: 60, periods: [60, 120], availableCount: 60, onPeriodChange() {} }));
  assert.doesNotMatch(full, /可用/);
});

test('a controlled trend period remains the caller choice instead of resetting to the default', () => {
  const html = renderToStaticMarkup(createElement(TrendChart, { stock: { name: '测试样本', volume_unit: 'shares', history: history.map(row => ({ date: row.date, open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close), volume: Number(row.volume_shares) })) }, date: '2026-10-15', screen: null, initialPeriod: 1, periodValue: 2, lengths: [1, 2] }));
  assert.equal((html.match(/data-candle-date=/g) ?? []).length, 2);
  assert.match(html, /aria-pressed="true">2根/);
});

test('the shared date inspection is a labeled native range over actual available dates', () => {
  const html = renderToStaticMarkup(createElement(CandleInspectionControl, { dates: history.map(row=>row.date), date: '2026-10-15', onDateChange() {}, onBlur() {}, inputRef: { current: null } }));
  assert.match(html, /type="range" min="0" max="1" step="1"/);
  assert.match(html, /aria-label="查看K线日期" aria-valuetext="2026-10-15"/);
  assert.match(html, /value="1"/);
});
