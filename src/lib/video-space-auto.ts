import type { VideoBar } from './video-space.ts';

export const AUTO_RULE = 'platform-wick-overlap-v1';
type Pivot = {
  index: number;
  date: string;
  kind: 'H' | 'L';
  price: number;
  zone: [number, number];
  confirmed: string;
};
type Point = { date: string; field: 'high' | 'low'; price: number };
export type AutoVersion = {
  id: string;
  points: Pivot[];
  H: Point;
  L: Point;
  confirmationDate: string;
  activationDate: string | null;
  downBreak: string | null;
  retiredAt: string | null;
  candidateCount: number;
  containment: number;
  logHeight: number;
};
export type AutoResult =
  | { kind: 'pending'; reason: string; versions: AutoVersion[] }
  | { kind: 'ready'; version: AutoVersion; versions: AutoVersion[] };

function candidates(
  bars: readonly VideoBar[],
  pivots: Pivot[],
  t: number,
  after: number,
) {
  const pool = pivots.filter((p) => p.index > after && p.index >= t - 39);
  const found: {
    points: Pivot[];
    H: Point;
    L: Point;
    containment: number;
    logHeight: number;
  }[] = [];
  for (let a = 0; a < pool.length; a++)
    for (let b = a + 1; b < pool.length; b++)
      for (let c = b + 1; c < pool.length; c++)
        for (let d = c + 1; d < pool.length; d++) {
          const points = [pool[a], pool[b], pool[c], pool[d]];
          if (
            points[3].index - points[0].index < 10 ||
            !['HLHL', 'LHLH'].includes(points.map((p) => p.kind).join(''))
          )
            continue;
          const highs = points.filter((p) => p.kind === 'H'),
            lows = points.filter((p) => p.kind === 'L');
          const h = highs.reduce((x, y) => (y.price > x.price ? y : x)),
            l = lows.reduce((x, y) => (y.price < x.price ? y : x));
          const logHeight = Math.log(h.price / l.price);
          if (logHeight < 0.048790164169432 || logHeight > 0.470003629245736)
            continue;
          const overlap = (ps: Pivot[]) =>
            Math.max(...ps.map((p) => p.zone[0])) <=
            Math.min(...ps.map((p) => p.zone[1]));
          if (!overlap(highs) || !overlap(lows)) continue;
          const section = bars.slice(points[0].index, points[3].index + 1);
          const containment =
            section.filter(
              (x) => Number(x.close) >= l.price && Number(x.close) <= h.price,
            ).length / section.length;
          if (containment < 0.8) continue;
          found.push({
            points,
            H: { date: h.date, field: 'high', price: h.price },
            L: { date: l.date, field: 'low', price: l.price },
            containment,
            logHeight,
          });
        }
  return found;
}

export function observeAutoPlatform(
  bars: readonly VideoBar[],
  date: string,
): AutoResult {
  if (!bars.some((b) => b.date === date))
    return {
      kind: 'pending',
      reason: '所选交易日没有已保存行情，待核。',
      versions: [],
    };
  const prefix = bars.filter((b) => b.date < date),
    pivots: Pivot[] = [],
    versions: AutoVersion[] = [];
  let active: AutoVersion | null = null,
    after = -1,
    above = 0;
  for (let t = 0; t < prefix.length; t++) {
    const bar = prefix[t],
      j = t - 2;
    if (active && active.activationDate === null)
      active.activationDate = bar.date;
    if (j >= 2) {
      const p = prefix[j],
        window = prefix.slice(j - 2, j + 3);
      const isHigh = window.every(
        (x, i) => i === 2 || Number(p.high) > Number(x.high),
      );
      const isLow = window.every(
        (x, i) => i === 2 || Number(p.low) < Number(x.low),
      );
      if (isHigh !== isLow) {
        const price = Number(isHigh ? p.high : p.low),
          eps = price * 0.005;
        pivots.push({
          index: j,
          date: p.date,
          kind: isHigh ? 'H' : 'L',
          price,
          zone: isHigh
            ? [Math.max(Number(p.open), Number(p.close)) - eps, price + eps]
            : [price - eps, Math.min(Number(p.open), Number(p.close)) + eps],
          confirmed: bar.date,
        });
      }
    }
    if (active) {
      if (!active.downBreak && Number(bar.close) < active.L.price)
        active.downBreak = bar.date;
      above = Number(bar.close) > active.H.price * 1.01 ? above + 1 : 0;
      if (above >= 2) {
        active.retiredAt = bar.date;
        after = t;
        active = null;
        above = 0;
      }
    }
    if (!active) {
      const found = candidates(prefix, pivots, t, after);
      if (found.length) {
        const first = found[0];
        active = {
          ...first,
          id: `${AUTO_RULE}:${first.points.map((p) => p.date).join(':')}:${bar.date}`,
          confirmationDate: bar.date,
          activationDate: null,
          downBreak: null,
          retiredAt: null,
          candidateCount: found.length,
        };
        versions.push(active);
        above = 0;
      }
    }
  }
  if (!active)
    return {
      kind: 'pending',
      reason: '观察日前未形成有效自动平台，待核。',
      versions,
    };
  if (!active.activationDate) active.activationDate = date;
  return { kind: 'ready', version: active, versions };
}
