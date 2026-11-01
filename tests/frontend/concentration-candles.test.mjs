import test from 'node:test';
import assert from 'node:assert/strict';
import { parseConcentrationCandles } from '../../src/lib/concentration-candles.ts';
import { candleReturn } from '../../src/lib/candle-quote.ts';
const target = {
  dataset_id: '0123456789abcdef',
  as_of: '2026-10-30',
  security_id: '999901',
  name: '虚构甲',
};
const bar = (date) => ({
  date,
  open: '10',
  high: '12',
  low: '9',
  close: '11',
  volume_shares: '123456',
});
const frame = () => ({
  schema: 'stock.chips.market.stock.v1',
  ...target,
  history: [bar('2026-10-29'), bar('2026-10-30')],
});
test('parses candle-only payload without chip distributions and preserves shares', () => {
  const result = parseConcentrationCandles(frame(), target);
  assert.equal(result.volume_unit, 'shares');
  assert.equal(result.history[0].volume, 123456);
  assert.deepEqual(result.history[0], {
    date: '2026-10-29',
    open: 10,
    high: 12,
    low: 9,
    close: 11,
    volume: 123456,
  });
  assert.deepEqual(
    parseConcentrationCandles({ ...frame(), history: [] }, target).history,
    [],
  );
});
test('requires exact schema, publication and security identity', () => {
  for (const field of ['schema', 'dataset_id', 'as_of', 'security_id', 'name'])
    assert.throws(() =>
      parseConcentrationCandles({ ...frame(), [field]: 'wrong' }, target),
    );
});
test('rejects blank, missing, nonfinite, negative-volume and invalid OHLC values', () => {
  for (const field of ['open', 'high', 'low', 'close', 'volume_shares']) {
    for (const value of [
      '',
      ' ',
      null,
      undefined,
      NaN,
      Infinity,
      'bad',
      true,
    ]) {
      const input = frame();
      input.history[0][field] = value;
      assert.throws(
        () => parseConcentrationCandles(input, target),
        field + ' ' + value,
      );
    }
  }
  for (const patch of [
    { low: '0' },
    { low: '-1' },
    { high: '8' },
    { open: '13' },
    { close: '8' },
    { volume_shares: '-1' },
  ]) {
    const input = frame();
    Object.assign(input.history[0], patch);
    assert.throws(() => parseConcentrationCandles(input, target));
  }
  const zero = frame();
  zero.history[0].volume_shares = '0';
  assert.equal(parseConcentrationCandles(zero, target).history[0].volume, 0);
});
test('requires real ordered unique dates no later than as-of and caps history', () => {
  for (const dates of [
    ['2026-02-30'],
    ['2026-10-31'],
    ['2026-10-30', '2026-10-29'],
    ['2026-10-29', '2026-10-29'],
    ['2026-1-01'],
  ])
    assert.throws(() =>
      parseConcentrationCandles(
        { ...frame(), history: dates.map(bar) },
        target,
      ),
    );
  assert.throws(() =>
    parseConcentrationCandles(
      { ...frame(), history: Array(501).fill(bar('2026-10-30')) },
      target,
    ),
  );
});
test('retains full history for previous-close outside a visible 120-bar window', () => {
  const history = Array.from({ length: 121 }, (_, i) =>
    bar(new Date(Date.UTC(2026, 5, 1 + i)).toISOString().slice(0, 10)),
  );
  history[0].close = '10';
  const stock = parseConcentrationCandles({ ...frame(), history }, target);
  assert.equal(stock.history.length, 121);
  assert.equal(candleReturn(stock.history, history[1].date), 10);
});
