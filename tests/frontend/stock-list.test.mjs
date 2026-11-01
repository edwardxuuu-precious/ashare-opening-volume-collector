import test from 'node:test';
import assert from 'node:assert/strict';
import { inspectorTabIndex, nextStockListItem, normalizeStockQuery, stockListMatches } from '../../src/lib/stock-list.ts';

test('all stock searches ignore outer whitespace and identifier case', () => {
  assert.equal(normalizeStockQuery(' SH000001 '), 'sh000001');
  assert.equal(stockListMatches(' SH000001 ', '上证指数', 'sh000001'), true);
  assert.equal(stockListMatches(' 000410 ', '沈阳机床', '000410'), true);
  assert.equal(stockListMatches(' 2026-10-15 ', '000410', '沈阳机床', '2026-10-15'), true);
  assert.equal(stockListMatches('missing', '沈阳机床', '000410'), false);
});

test('list navigation stays inside the filtered list without wrapping and handles an outside selection', () => {
  const keys = ['000410', '000596', '000716'];
  assert.deepEqual(nextStockListItem(keys, '000410', 1), { key: '000596', index: 1 });
  assert.equal(nextStockListItem(keys, '000410', -1), null);
  assert.equal(nextStockListItem(keys, '000716', 1), null);
  assert.deepEqual(nextStockListItem(keys, 'outside', 1), { key: '000410', index: 0 });
  assert.deepEqual(nextStockListItem(keys, 'outside', -1), { key: '000716', index: 2 });
  assert.equal(nextStockListItem([], '000410', 1), null);
  assert.equal(nextStockListItem(['000410'], '000410', 1), null);
});

test('research navigation distinguishes multiple observation dates of the same stock', () => {
  const keys = ['000410:2026-10-14', '000410:2026-10-15'];
  assert.deepEqual(nextStockListItem(keys, keys[0], 1), { key: keys[1], index: 1 });
});

test('inspector tabs share directional, first and last keyboard selection', () => {
  assert.equal(inspectorTabIndex('ArrowLeft', 0, 3), 2);
  assert.equal(inspectorTabIndex('ArrowRight', 2, 3), 0);
  assert.equal(inspectorTabIndex('Home', 2, 3), 0);
  assert.equal(inspectorTabIndex('End', 0, 3), 2);
  assert.equal(inspectorTabIndex('ArrowDown', 0, 3), null);
  assert.equal(inspectorTabIndex('ArrowRight', 0, 0), null);
});
