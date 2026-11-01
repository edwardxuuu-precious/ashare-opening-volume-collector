import test from 'node:test';
import assert from 'node:assert/strict';
import { candleIndexAtPoint } from '../../src/lib/chart-inspection.ts';
import { candleVolumeText, volumeText, volumeUnitLabel } from '../../src/lib/chart-readout.ts';

test('chart hit testing rejects points outside either axis and keeps the last column at its right edge', () => {
  const region = { left: 10, right: 110, top: 20, bottom: 220, count: 10 };
  assert.equal(candleIndexAtPoint({ ...region, x: 15, y: 30 }), 0);
  assert.equal(candleIndexAtPoint({ ...region, x: 110, y: 220 }), 9);
  for (const point of [{ x: 15, y: 19 }, { x: 105, y: 221 }, { x: 9, y: 100 }, { x: 111, y: 100 }]) assert.equal(candleIndexAtPoint({ ...region, ...point }), null);
  assert.equal(candleIndexAtPoint({ ...region, x: 50, y: 100, count: 0 }), null);
});

test('candle volume uses one numeric format while preserving the declared source unit', () => {
  assert.equal(candleVolumeText(1000, 'shares'), '1,000股');
  assert.equal(candleVolumeText(17660100, 'shares'), '1766.01万股');
  assert.equal(candleVolumeText(123456789, 'shares'), '1.23亿股');
  assert.equal(candleVolumeText(1000, 'hands_100_shares'), '1,000手（100股）');
  assert.equal(candleVolumeText(1000, 'UNKNOWN'), '1,000单位待核实');
  assert.equal(candleVolumeText(1000, 'provider_native_consistent_ratio_only'), '1,000原生量（仅比较比例）');
  assert.equal(candleVolumeText(0, 'shares'), '0股');
  assert.equal(candleVolumeText(null, 'shares'), '待核实');
  assert.equal(volumeText(null), '待核实');
  assert.equal(volumeUnitLabel(''), '单位待核实');
});
