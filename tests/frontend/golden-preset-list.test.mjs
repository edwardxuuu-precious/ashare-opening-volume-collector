import test from 'node:test';
import assert from 'node:assert/strict';
import { goldenPresetItems } from '../../src/lib/golden-preset-list.ts';

function row(code, category) {
  return {
    code, name: `虚构${code}`, as_of: '2026-11-02', source_status: category === 'UNKNOWN' ? 'UNKNOWN' : 'KNOWN', drawing_state: category,
    category, close: category === 'UNKNOWN' ? null : '10', H: null, M: null, L: null, direction: null, basis: 'NONE', near_tags: [], intraday_tags: [],
    new_crossing: 'UNKNOWN', distance_percent: { H: null, M: null, L: null }, reasons: ['仅用于名单软件检查'], structure_id: null,
  };
}

test('preset results keep user order and include abstentions, history and unknowns independently of market filters', () => {
  const historical = row('300308', 'HISTORY'), noDraw = row('600825', 'NO_DRAW'), unknown = row('301101', 'UNKNOWN');
  const items = goldenPresetItems([historical, unknown, noDraw]);
  assert.deepEqual(items.map(item => item.security.id), ['600825', '301101', '300722', '300684', '600851', '600199', '300580', '301413', '300969', '300434', '002229', '300308', '300502', '300394', '300857', 'sh000001', 'sz399006']);
  assert.equal(items[0].kind, 'published');
  assert.equal(items[0].row, noDraw);
  assert.equal(items[1].row, unknown);
  assert.equal(items[11].row, historical);
  assert.equal(items.filter(item => item.kind === 'published').length, 3);
});

test('indices never inherit bank or unqualified index-shaped stock rows', () => {
  const items = goldenPresetItems([row('000001', 'HISTORY'), row('399006', 'HISTORY')]);
  const indices = items.filter(item => item.security.kind === 'index');
  assert.deepEqual(indices.map(item => [item.security.name, item.kind, item.reason]), [['上证指数', 'missing', 'index-unavailable'], ['创业板指数', 'missing', 'index-unavailable']]);
  assert.equal(items.some(item => item.kind === 'published'), false);
});

test('published indices keep exact prefixed identities and the prescribed positions', () => {
  const shanghai = { ...row('sh000001', 'HISTORY'), name: '上证指数' };
  const growth = { ...row('sz399006', 'UNKNOWN'), name: '创业板指数' };
  const items = goldenPresetItems([row('000001', 'NO_DRAW'), growth, shanghai]);
  assert.equal(items[15].security.name, '上证指数');
  assert.equal(items[15].kind, 'published');
  assert.equal(items[15].row, shanghai);
  assert.equal(items[16].security.name, '创业板指数');
  assert.equal(items[16].kind, 'published');
  assert.equal(items[16].row, growth);
});

test('missing securities retain their identities without manufacturing price or drawing results', () => {
  const items = goldenPresetItems([]);
  assert.equal(items.length, 17);
  assert.equal(items[0].security.name, '新华传媒');
  assert.equal(items[0].kind, 'missing');
  assert.equal(items[0].reason, 'not-published');
  assert.equal('row' in items[0], false);
});

test('name and code search preserve preset ordering and still find unavailable indices', () => {
  assert.deepEqual(goldenPresetItems([], '指数').map(item => item.security.id), ['sh000001', 'sz399006']);
  assert.deepEqual(goldenPresetItems([], ' 300308 ').map(item => item.security.name), ['中际旭创']);
  assert.deepEqual(goldenPresetItems([], '明月').map(item => item.security.id), ['301101']);
  assert.deepEqual(goldenPresetItems([], '未指定股票'), []);
});
