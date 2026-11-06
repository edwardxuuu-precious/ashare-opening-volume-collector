import test from 'node:test';
import assert from 'node:assert/strict';
import { observeAutoPlatform } from '../../src/lib/video-space-auto.ts';

function bars() {
  const result = Array.from({ length: 35 }, (_, i) => ({
    date: `2025-01-${String(i + 1).padStart(2, '0')}`,
    open: '95',
    high: '96',
    low: '94',
    close: '95',
    volume_shares: '1',
  }));
  for (const [i, kind] of [
    [3, 'H'],
    [7, 'L'],
    [11, 'H'],
    [15, 'L'],
  ]) {
    if (kind === 'H')
      result[i] = { ...result[i], open: '99', close: '99', high: '100' };
    else result[i] = { ...result[i], open: '91', close: '91', low: '90' };
  }
  return result;
}
test('confirmation requires two right bars and the next actual date, with no future input influence', () => {
  const data = bars();
  assert.equal(observeAutoPlatform(data, data[17].date).kind, 'pending');
  const result = observeAutoPlatform(data, data[18].date);
  assert.equal(result.kind, 'ready');
  assert.equal(result.version.confirmationDate, data[17].date);
  assert.equal(result.version.activationDate, data[18].date);
  const changed = data.map((b, i) =>
    i >= 18 ? { ...b, high: '1000', low: '1', open: '500', close: '500' } : b,
  );
  assert.deepEqual(observeAutoPlatform(changed, data[18].date), result);
  assert.equal(observeAutoPlatform(data, '2099-01-01').kind, 'pending');
});
test('new lows keep the ruler, while two later closes above the margin retire it', () => {
  const data = bars();
  data[19] = { ...data[19], low: '80', open: '89', close: '89' };
  const below = observeAutoPlatform(data, data[20].date);
  assert.equal(below.kind, 'ready');
  assert.equal(below.version.L.price, 90);
  assert.equal(below.version.downBreak, data[19].date);
  data[20] = { ...data[20], high: '103', open: '102', close: '102' };
  data[21] = { ...data[21], high: '104', open: '103', close: '103' };
  assert.equal(observeAutoPlatform(data, data[21].date).kind, 'ready');
  const retired = observeAutoPlatform(data, data[22].date);
  assert.equal(retired.kind, 'pending');
  assert.equal(retired.versions[0].retiredAt, data[21].date);
});
test('ambiguous dual pivot cannot form a platform', () => {
  const data = bars();
  data[3] = { ...data[3], low: '80' };
  assert.equal(observeAutoPlatform(data, data[18].date).kind, 'pending');
});

test('refresh preserves accepted auto history on pending, revision, missing dates and rollback', async () => {
  const { reconcileVideoRefresh } =
    await import('../../src/lib/video-space.ts');
  const history = bars().slice(0, 3);
  const stock = (items, asOf = items.at(-1).date) => ({
    code: '300502',
    asOf,
    autoHistory: { kind: 'ready', history: items },
  });
  const initial = stock(history);
  const pending = {
    ...initial,
    autoHistory: { kind: 'pending', reason: 'seed 404' },
  };
  assert.equal(reconcileVideoRefresh(null, pending).stock, pending);
  assert.deepEqual(reconcileVideoRefresh(initial, pending), {
    kind: 'error',
    previous: initial,
    message: '自动历史待核：seed 404。请重试。',
  });
  const appended = stock([...history, { ...history[2], date: '2025-01-04' }]);
  assert.equal(reconcileVideoRefresh(initial, appended).stock, appended);
  const revision = stock([
    ...history,
    { ...appended.autoHistory.history[3], close: '96' },
  ]);
  assert.equal(reconcileVideoRefresh(appended, revision).previous, appended);
  assert.equal(
    reconcileVideoRefresh(appended, stock(history)).previous,
    appended,
  );
  assert.equal(
    reconcileVideoRefresh(
      appended,
      stock([history[0], history[2], appended.autoHistory.history[3]]),
    ).previous,
    appended,
  );
  assert.equal(
    reconcileVideoRefresh(
      appended,
      stock(appended.autoHistory.history, '2025-01-03'),
    ).previous,
    appended,
  );
});
