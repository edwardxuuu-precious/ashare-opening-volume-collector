import test from 'node:test';
import assert from 'node:assert/strict';
import { candleReturn } from '../../src/lib/candle-quote.ts';
import { workbenchQuote } from '../../src/lib/chip-workbench.ts';

test('daily return uses the previous published close outside the displayed window without looking into future bars', () => {
  const history = [10, 11, 11, 9.9, 29].map((close, index) => ({ date: `2020-01-0${index + 1}`, close }));
  const displayed = history.slice(1, 4);
  assert.equal(candleReturn(history, displayed[0].date), 10);
  assert.equal(candleReturn(history, '2020-01-03'), 0);
  assert.ok(Math.abs(candleReturn(history, '2020-01-04') - (-10)) < 1e-10);
  assert.equal(candleReturn(history, '2020-01-01'), null);
  assert.equal(candleReturn(history, '2020-01-06'), null);
  assert.equal(candleReturn(history.slice(0, 4), '2020-01-04'), candleReturn(history, '2020-01-04'));
});

test('decimal price strings and numeric trend prices have the same rounding, including half-cent percentage boundaries', () => {
  for (const [previous, close, expected] of [[8, 8.01, '0.13'], [8, 7.99, '-0.13'], [40, 41.07, Number('2.675').toFixed(2)], [40, 38.93, Number('-2.675').toFixed(2)]]) {
    for (const stringPrices of [false, true]) {
      const history = [{ date: '2020-01-01', close: stringPrices ? String(previous) : previous }, { date: '2020-01-02', close: stringPrices ? String(close) : close }];
      assert.equal(candleReturn(history, '2020-01-02').toFixed(2), expected);
    }
  }
  assert.equal(candleReturn([{ date: '2020-01-01', close: '8e0' }, { date: '2020-01-02', close: '8.01e0' }], '2020-01-02').toFixed(2), '0.13');
});

test('missing, zero and invalid closes never become a known daily return', () => {
  assert.equal(candleReturn([], '2020-01-02'), null);
  for (const invalid of [0, -1, NaN, Infinity, '0', '', 'missing', 'Infinity']) {
    assert.equal(candleReturn([{ date: '2020-01-01', close: invalid }, { date: '2020-01-02', close: 10 }], '2020-01-02'), null);
    assert.equal(candleReturn([{ date: '2020-01-01', close: 10 }, { date: '2020-01-02', close: invalid }], '2020-01-02'), null);
  }
});

test('chip summary shares the exact daily-return rounding used in chart readouts and does not invent changes for carried snapshots', () => {
  const bar = (date, close) => ({ date, close, open: close, high: close, low: close, volume_shares: '1000', amount_cny: '10000', turnover_percent: '2' });
  const history = [bar('2026-10-14', '8'), bar('2026-10-15', '8.01')];
  assert.equal(workbenchQuote(history, '2026-10-15', null).percent.toFixed(2), '0.13');
  assert.deepEqual(workbenchQuote(history, '2026-10-16', { close_cny: '8.01' }), { close: 8.01, change: null, percent: null, hasCandle: false });
});
