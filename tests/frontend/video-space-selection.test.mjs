import test from 'node:test';
import assert from 'node:assert/strict';
import { observeRulerSelection } from '../../src/lib/video-space-selection.ts';
import { observeVideoLayers } from '../../src/lib/video-space.ts';

function fixture(length = 70) {
  const data = Array.from({ length }, (_, i) => ({
    date: new Date(Date.UTC(2025, 0, i + 1)).toISOString().slice(0, 10),
    open: '95',
    high: '96',
    low: '94',
    close: '95',
    volume_shares: '1',
  }));
  const pivot = (i, kind, price) => {
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
  };
  pivot(3, 'H', 120);
  pivot(7, 'L', 85);
  pivot(11, 'H', 100);
  pivot(15, 'L', 90);
  pivot(19, 'H', 100);
  pivot(23, 'L', 90);
  return data;
}
function close(data, i, value) {
  data[i] = {
    ...data[i],
    open: String(value),
    close: String(value),
    low: String(Math.min(value - 1, Number(data[i].low))),
    high: String(Math.max(value + 1, Number(data[i].high))),
  };
}
function state(data, i) {
  return observeRulerSelection(data, data[i].date).state;
}
function ready(data, i) {
  const s = state(data, i);
  assert.equal(s.kind, 'ready');
  return s;
}

test('confirmed background selects a common enclosing wave and keeps the real source', () => {
  const data = fixture();
  assert.equal(state(data, 25).kind, 'pending');
  const s = ready(data, 26);
  assert.equal(s.current.ruler.kind, 'WAVE');
  assert.equal(s.current.ruler.H.date, data[19].date);
  assert.equal(s.current.ruler.L.date, data[15].date);
  assert.equal(s.current.selectedOn, data[26].date);
  assert.equal(s.current.scale, 'PARENT');
  assert.equal(s.current.background.H, 100);
  assert.equal(s.current.background.L, 90);
  assert.equal(s.kept.length, 1);
});

test('completed close triggers next-session half, two recovered closes restore parent, then half can re-enter', () => {
  const data = fixture();
  close(data, 27, 89);
  close(data, 28, 89);
  close(data, 29, 90);
  close(data, 30, 90);
  close(data, 32, 89);
  assert.equal(ready(data, 27).current.scale, 'PARENT');
  const half = ready(data, 28);
  assert.equal(half.current.scale, 'HALF');
  assert.equal(half.current.fine.firstHalfDate, data[28].date);
  assert.equal(half.current.scaleSince, data[28].date);
  assert.equal(ready(data, 30).current.scale, 'HALF');
  assert.equal(ready(data, 31).current.scale, 'PARENT');
  assert.equal(ready(data, 33).current.scale, 'HALF');
  assert.equal(ready(data, 33).current.fine.firstHalfDate, data[28].date);
  assert.deepEqual(
    ready(data, 33)
      .events.filter((e) => e.kind === 'SCALE')
      .map((e) => [e.date, e.to]),
    [
      [data[28].date, 'HALF'],
      [data[31].date, 'PARENT'],
      [data[33].date, 'HALF'],
    ],
  );
});

test('equal geometry with newer dates cannot replace a held ruler', () => {
  const data = fixture();
  data[35] = { ...data[35], high: '100', open: '99', close: '99' };
  const original = ready(data, 26).current.ruler.id;
  const later = ready(data, 38);
  assert.equal(later.current.ruler.id, original);
  assert.equal(later.events.filter((e) => e.kind === 'MAIN').length, 1);
});

test('common outward expansion changes the main and preserves its predecessor without inherited fine counters', () => {
  const data = fixture();
  data[30] = { ...data[30], low: '84', open: '85', close: '85' };
  data[34] = { ...data[34], high: '110', open: '109', close: '109' };
  close(data, 35, 83);
  close(data, 36, 83);
  const original = ready(data, 26).current.ruler.id;
  const s = ready(data, 37);
  assert.equal(s.current.ruler.H.price, '110');
  assert.equal(s.current.ruler.L.price, '84');
  assert.equal(s.current.selectedOn, data[37].date);
  assert.equal(s.current.scale, 'PARENT');
  assert.equal(s.current.fine.recoveryCount, 0);
  assert.equal(s.current.fine.firstHalfDate, null);
  assert.ok(s.kept.some((v) => v.ruler.id === original));
  assert.equal(
    s.events.findLast((e) => e.kind === 'MAIN').reason,
    'OUTWARD_EXPANSION',
  );
});

test('a lower background changes the reference while retirement takes priority over further fine events', () => {
  const data = fixture();
  for (let i = 27; i < data.length; i++)
    data[i] = { ...data[i], open: '76', high: '77', low: '75', close: '76' };
  for (const i of [35, 43])
    data[i] = { ...data[i], open: '79', high: '80', close: '79' };
  for (const i of [39, 47])
    data[i] = { ...data[i], open: '71', low: '70', close: '71' };
  const before = ready(data, 26);
  const next = ready(data, 50);
  assert.notEqual(next.current.background.id, before.current.background.id);
  assert.equal(
    next.current.background.rootId,
    before.current.background.rootId,
  );
  assert.equal(next.current.background.H, 80);
  assert.equal(next.current.background.L, 70);
  assert.ok(next.kept.some((v) => v.ruler.id === before.current.ruler.id));
  close(data, 54, 102);
  close(data, 55, 102);
  const retired = state(data, 56);
  assert.equal(retired.kind, 'pending');
  assert.ok(retired.kept.length > 0);
  assert.equal(retired.events.at(-1).kind, 'RETIRED');
  assert.equal(retired.events.at(-1).date, data[56].date);
});

test('selection history is causal under exact prefixes and changes to observed-day or future prices', () => {
  const data = fixture();
  close(data, 27, 89);
  for (const i of [25, 26, 27, 28, 31, 40, 55]) {
    const expected = observeRulerSelection(
      data.slice(0, i + 1),
      data[i].date,
    ).state;
    assert.deepEqual(state(data, i), expected);
    const changed = data.map((b, j) =>
      j >= i ? { ...b, high: '1000', low: '1', open: '500', close: '500' } : b,
    );
    assert.deepEqual(state(changed, i), expected);
  }
  assert.equal(observeRulerSelection(data, '2099-01-01').state.kind, 'pending');
});

test('automatic layer reuses source prices and manual half remains available after automatic recovery', () => {
  const data = fixture();
  close(data, 27, 89);
  const stock = {
    code: '300502',
    history: data,
    autoHistory: { kind: 'ready', history: data },
  };
  const automatic = observeVideoLayers(
    stock,
    data[28].date,
    { kind: 'AUTO_PREVIOUS_CLOSE' },
    { kind: 'RULE' },
  );
  assert.equal(automatic.parent.kind, 'ready');
  assert.equal(automatic.observation.source.kind, 'HALF');
  assert.equal(
    automatic.observation.source.endpoints.parentId,
    automatic.selection.current.ruler.id,
  );
  const recovered = observeVideoLayers(
    stock,
    data[31].date,
    { kind: 'AUTO_PREVIOUS_CLOSE' },
    { kind: 'RULE' },
  );
  assert.equal(recovered.observation.source.kind, 'WAVE');
  const manual = observeVideoLayers(
    stock,
    data[31].date,
    { kind: 'AUTO_PREVIOUS_CLOSE' },
    { parentId: recovered.selection.current.ruler.id, scale: 'HALF' },
  );
  assert.equal(manual.observation.source.kind, 'HALF');
  assert.equal(manual.selection, null);
  const missing = observeVideoLayers(
    { ...stock, autoHistory: { kind: 'pending', reason: 'seed conflict' } },
    data[31].date,
    { kind: 'AUTO_PREVIOUS_CLOSE' },
    { kind: 'RULE' },
  );
  assert.equal(missing.parent.kind, 'pending');
  assert.equal(missing.selection, null);
});

test('different A/B valleys at a new high stay alternatives and cannot replace the main', () => {
  const data = fixture();
  data[31] = { ...data[31], low: '88', open: '89', close: '89' };
  data[35] = { ...data[35], high: '105', open: '104', close: '104' };
  data[39] = { ...data[39], low: '89', open: '90', close: '90' };
  data[43] = { ...data[43], high: '110', open: '109', close: '109' };
  const original = ready(data, 26).current.ruler.id;
  const s = ready(data, 46);
  assert.equal(s.current.ruler.id, original);
  const conflict = s.disagreements.find((v) => v.highDate === data[43].date);
  assert.equal(conflict.candidates.length, 2);
  assert.deepEqual(conflict.candidates.map((v) => v.L.price).sort(), [
    '85',
    '88',
  ]);
});

test('a new background selecting the same outer ruler does not restart its selected time or fine', () => {
  const data = fixture();
  data[30] = { ...data[30], low: '60', open: '61', close: '61' };
  data[34] = { ...data[34], high: '110', open: '109', close: '109' };
  for (let i = 37; i < data.length; i++)
    data[i] = { ...data[i], open: '76', high: '77', low: '75', close: '76' };
  data[39] = { ...data[39], open: '79', high: '80', close: '79' };
  data[47] = { ...data[47], open: '78.8', high: '79.8', close: '78.8' };
  for (const i of [43, 51])
    data[i] = { ...data[i], open: '71', low: '70', close: '71' };
  const original = ready(data, 37);
  const later = ready(data, 54);
  assert.equal(later.current.ruler.id, original.current.ruler.id);
  assert.notEqual(later.current.background.id, original.current.background.id);
  assert.equal(later.current.selectedOn, original.current.selectedOn);
  assert.equal(later.current.scaleSince, original.current.scaleSince);
  assert.equal(later.events.at(-1).kind, 'BACKGROUND');
  assert.equal(
    later.kept.filter((v) => v.ruler.id === original.current.ruler.id).length,
    1,
  );
});

test('missing common envelope falls back to the real background rather than an invented wave', () => {
  const data = fixture();
  for (const i of [3, 7])
    data[i] = { ...data[i], open: '95', close: '95', high: '96', low: '94' };
  data[19] = { ...data[19], high: '99.8', open: '98.8', close: '98.8' };
  const s = ready(data, 26);
  assert.equal(s.current.ruler.kind, 'AUTO');
  assert.equal(s.current.ruler.id, s.current.background.id);
  assert.equal(s.current.ruler.H.price, '100');
  assert.equal(
    s.events.find((e) => e.kind === 'MAIN').reason,
    'BACKGROUND_PLATFORM',
  );
});
