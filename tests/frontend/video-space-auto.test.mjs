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
