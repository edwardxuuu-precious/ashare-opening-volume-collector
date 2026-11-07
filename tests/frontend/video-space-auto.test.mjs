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

test('lower platforms lock after a completed break and stop at root retirement', async () => {
  const { observeAutoFamily } =
    await import('../../src/lib/video-space-auto.ts');
  const data = Array.from({ length: 65 }, (_, i) => ({
    date: new Date(Date.UTC(2025, 0, i + 1)).toISOString().slice(0, 10),
    open: i < 20 ? '95' : '76',
    high: i < 20 ? '96' : '77',
    low: i < 20 ? '94' : '75',
    close: i < 20 ? '95' : '76',
    volume_shares: '1',
  }));
  for (const [i, kind, price] of [
    [3, 'H', 100],
    [7, 'L', 90],
    [11, 'H', 100],
    [15, 'L', 90],
    [23, 'H', 80],
    [27, 'L', 72],
    [31, 'H', 80],
    [35, 'L', 72],
  ]) {
    data[i] =
      kind === 'H'
        ? {
            ...data[i],
            open: String(price - 1),
            close: String(price - 1),
            high: String(price),
          }
        : {
            ...data[i],
            open: String(price + 1),
            close: String(price + 1),
            low: String(price),
          };
  }
  assert.equal(observeAutoFamily(data, data[37].date).lowerPlatforms.length, 0);
  const result = observeAutoFamily(data, data[38].date);
  assert.equal(result.lowerPlatforms.length, 1);
  assert.equal(result.lowerPlatforms[0].triggerDate, data[20].date);
  assert.equal(result.lowerPlatforms[0].confirmationDate, data[37].date);
  assert.equal(result.lowerPlatforms[0].activationDate, data[38].date);
  const changed = data.map((b, i) =>
    i >= 38 ? { ...b, high: '1000', low: '1', open: '500', close: '500' } : b,
  );
  assert.deepEqual(observeAutoFamily(changed, data[38].date), result);
  data[36] = { ...data[36], high: '103', open: '102', close: '102' };
  data[37] = { ...data[37], high: '104', open: '103', close: '103' };
  assert.equal(observeAutoFamily(data, data[38].date).lowerPlatforms.length, 0);
});

test('structural candidates merge causal reasons and separately time high and close breaks', async () => {
  const { observeStructuralWaves } =
    await import('../../src/lib/video-space-auto.ts');
  const history = Array.from({ length: 22 }, (_, i) => ({
    date: new Date(Date.UTC(2025, 1, i + 1)).toISOString().slice(0, 10),
    open: '60',
    high: '61',
    low: '59',
    close: '60',
    volume_shares: '1',
  }));
  history[3] = { ...history[3], high: '100', open: '99', close: '99' };
  history[7] = { ...history[7], low: '40', open: '41', close: '41' };
  history[11] = { ...history[11], high: '90', open: '89', close: '89' };
  history[15] = { ...history[15], high: '91' };
  history[16] = { ...history[16], high: '93', open: '92', close: '92' };
  const get = (date) => {
    const r = observeStructuralWaves(history, date);
    assert.equal(r.kind, 'ready');
    return r.candidates;
  };
  assert.equal(observeStructuralWaves(history, '2099-01-01').kind, 'pending');
  assert.equal(get(history[13].date).length, 0);
  const active = get(history[14].date);
  assert.equal(active.length, 1);
  assert.deepEqual(
    active[0].reasons.map((r) => r.method),
    ['A', 'B'],
  );
  assert.equal(active[0].confirmationDate, history[13].date);
  assert.equal(active[0].activationDate, history[14].date);
  assert.equal(active[0].firstHighBreak, null);
  const beforeClose = get(history[16].date)[0];
  assert.equal(beforeClose.firstHighBreak.date, history[15].date);
  assert.equal(beforeClose.firstHighBreak.usableFrom, history[16].date);
  assert.equal(beforeClose.firstCloseBreak, null);
  const afterClose = get(history[17].date)[0];
  assert.equal(afterClose.firstCloseBreak.date, history[16].date);
  assert.equal(afterClose.firstCloseBreak.usableFrom, history[17].date);
  const mutated = history.map((b, i) =>
    i >= 16 ? { ...b, open: '500', high: '1000', low: '1', close: '500' } : b,
  );
  assert.deepEqual(
    observeStructuralWaves(mutated, history[16].date),
    observeStructuralWaves(history, history[16].date),
  );
  assert.deepEqual(afterClose.reasons, active[0].reasons);
  assert.equal(afterClose.id, active[0].id);
});

test('structural catalog explains a confirmed high rejected by both hypotheses', async () => {
  const { observeStructuralWaves } =
    await import('../../src/lib/video-space-auto.ts');
  const history = Array.from({ length: 9 }, (_, i) => ({
    date: new Date(Date.UTC(2025, 3, i + 1)).toISOString().slice(0, 10),
    open: '60',
    high: '61',
    low: '59',
    close: '60',
    volume_shares: '1',
  }));
  history[3] = { ...history[3], high: '80', open: '79', close: '79' };
  const result = observeStructuralWaves(history, history[6].date);
  assert.equal(result.kind, 'ready');
  assert.equal(result.candidates.length, 0);
  assert.deepEqual(
    result.rejections.map(({ method, code }) => [method, code]),
    [
      ['A', 'no_prior_confirmed_high_at_or_above_H'],
      ['B', 'NO_PRIOR_CONFIRMED_LOW'],
    ],
  );
  for (const rejected of result.rejections) {
    assert.equal(rejected.H.date, history[3].date);
    assert.equal(rejected.H.field, 'high');
    assert.equal(rejected.H.confirmationDate, history[5].date);
  }
  assert.deepEqual(result.rejections[1].reason.stop, {
    code: 'NO_PRIOR_CONFIRMED_LOW',
  });
  assert.equal(
    observeStructuralWaves(history, history[5].date).rejections.length,
    0,
  );
});
