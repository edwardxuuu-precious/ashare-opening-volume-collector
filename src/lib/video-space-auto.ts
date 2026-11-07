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

function pivotAt(prefix: readonly VideoBar[], t: number): Pivot | null {
  const j = t - 2;
  if (j < 2) return null;
  const p = prefix[j],
    window = prefix.slice(j - 2, j + 3);
  const isHigh = window.every(
    (x, i) => i === 2 || Number(p.high) > Number(x.high),
  );
  const isLow = window.every(
    (x, i) => i === 2 || Number(p.low) < Number(x.low),
  );
  if (isHigh === isLow) return null;
  const price = Number(isHigh ? p.high : p.low),
    eps = price * 0.005;
  return {
    index: j,
    date: p.date,
    kind: isHigh ? 'H' : 'L',
    price,
    zone: isHigh
      ? [Math.max(Number(p.open), Number(p.close)) - eps, price + eps]
      : [price - eps, Math.min(Number(p.open), Number(p.close)) + eps],
    confirmed: prefix[t].date,
  };
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
    const bar = prefix[t];
    if (active && active.activationDate === null)
      active.activationDate = bar.date;
    const pivot = pivotAt(prefix, t);
    if (pivot) pivots.push(pivot);
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

export type PlatformRelation =
  | { kind: 'CONTAINED_CELL'; cell: number }
  | { kind: 'CROSSES_CELLS' };
export type LowerPlatform = {
  id: string;
  rootId: string;
  predecessorId: string;
  realH: Point;
  realL: Point;
  fourConfirmedPoints: Pivot[];
  triggerDate: string;
  confirmationDate: string;
  activationDate: string | null;
  relationToRoot: PlatformRelation;
  rootLifetime: 'active' | 'archived';
  evidence: 'ENGINEERING_SUPPLEMENT';
};
export type FamilyEvent =
  | { kind: 'BREAK'; rootId: string; predecessorId: string; date: string }
  | { kind: 'LOCK'; rootId: string; platformId: string; date: string }
  | { kind: 'DUPLICATE'; rootId: string; existingId: string; date: string };
export type AutoFamily = {
  root: AutoResult;
  lowerPlatforms: LowerPlatform[];
  events: FamilyEvent[];
};

function relationToRoot(
  root: AutoVersion,
  H: Point,
  L: Point,
): PlatformRelation {
  const scale = Math.log(root.L.price / root.H.price);
  const a = Math.log(H.price / root.H.price) / scale;
  const b = Math.log(L.price / root.H.price) / scale;
  const cell = Math.floor(a + 1e-10);
  return b <= cell + 1 + 1e-10 &&
    Math.log(H.price / L.price) < Math.log(root.H.price / root.L.price) - 1e-10
    ? { kind: 'CONTAINED_CELL', cell }
    : { kind: 'CROSSES_CELLS' };
}
function endpointKey(H: Point, L: Point): string {
  return JSON.stringify([H.field, H.date, H.price, L.field, L.date, L.price]);
}
export function observeAutoFamily(
  bars: readonly VideoBar[],
  date: string,
): AutoFamily {
  const root = observeAutoPlatform(bars, date);
  const lowerPlatforms: LowerPlatform[] = [],
    events: FamilyEvent[] = [];
  const prefix = bars.filter((b) => b.date < date);
  const known = new Map<string, string>();
  for (const version of root.versions) {
    const pivots: Pivot[] = [];
    let predecessor = { id: version.id, L: version.L };
    let trigger = -1;
    for (let t = 0; t < prefix.length; t++) {
      const bar = prefix[t];
      if (version.retiredAt && bar.date >= version.retiredAt) break;
      const pivot = pivotAt(prefix, t);
      if (pivot) pivots.push(pivot);
      if (!version.activationDate || bar.date < version.activationDate)
        continue;
      if (trigger < 0 && Number(bar.close) < predecessor.L.price) {
        trigger = t;
        events.push({
          kind: 'BREAK',
          rootId: version.id,
          predecessorId: predecessor.id,
          date: bar.date,
        });
      }
      if (trigger < 0) continue;
      const found = candidates(prefix, pivots, t, trigger).filter(
        (c) => c.H.price < predecessor.L.price,
      );
      if (!found.length) continue;
      const first = found[0],
        key = endpointKey(first.H, first.L);
      const existing =
        known.get(key) ??
        root.versions.find(
          (v) =>
            v.confirmationDate <= bar.date && endpointKey(v.H, v.L) === key,
        )?.id;
      if (existing) {
        events.push({
          kind: 'DUPLICATE',
          rootId: version.id,
          existingId: existing,
          date: bar.date,
        });
        continue;
      }
      const id = `lower:${first.H.date}:${first.H.price}:${first.L.date}:${first.L.price}`;
      const node: LowerPlatform = {
        id,
        rootId: version.id,
        predecessorId: predecessor.id,
        realH: first.H,
        realL: first.L,
        fourConfirmedPoints: first.points,
        triggerDate: prefix[trigger].date,
        confirmationDate: bar.date,
        activationDate: bars[t + 1]?.date ?? null,
        relationToRoot: relationToRoot(version, first.H, first.L),
        rootLifetime: version.retiredAt ? 'archived' : 'active',
        evidence: 'ENGINEERING_SUPPLEMENT',
      };
      known.set(key, id);
      lowerPlatforms.push(node);
      events.push({
        kind: 'LOCK',
        rootId: version.id,
        platformId: id,
        date: bar.date,
      });
      predecessor = { id, L: first.L };
      trigger = -1;
    }
  }
  return { root, lowerPlatforms, events };
}

type WavePivotBase = {
  index: number;
  date: string;
  price: string;
  confirmationDate: string;
};
export type WaveHigh = WavePivotBase & { kind: 'H'; field: 'high' };
export type WaveLow = WavePivotBase & { kind: 'L'; field: 'low' };
export type WavePivot = WaveHigh | WaveLow;
type WaveAttempt = {
  olderLow: WaveLow;
  currentTrough: WaveLow;
  rightwardPeak: WaveHigh;
  interveningHighs: WaveHigh[];
  selectedHighestInterveningHigh: WaveHigh | null;
  highestPeakTieBreak: 'LATEST_DATE';
  checks: {
    olderLowStrictlyLower: boolean;
    hasInterveningStrictHigh: boolean;
    interveningPeakStrictlyLower: boolean;
  };
  action: 'EXTEND' | 'STOP';
  stopCodes: string[];
};
export type WaveReason =
  | {
      method: 'A';
      boundaryH: WaveHigh;
      consideredL: WaveLow[];
      selection: string;
      tieBreak: string;
      frozenOn: string;
    }
  | {
      method: 'B';
      rule: 'B_hh_hl_chain_origin';
      originalHigh: WaveHigh;
      initialTroughSelection: 'LATEST_CONFIRMED_STRICT_L_BEFORE_H';
      priorLowCount: number;
      initialTrough: WaveLow;
      attempts: WaveAttempt[];
      acceptedExtensions: number;
      stop:
        | {
            code: 'RELATION_FAILED';
            stopCodes: string[];
            atOlderLow: WaveLow;
            unvisitedEarlierLowCount: number;
          }
        | { code: 'NO_EARLIER_CONFIRMED_LOW' };
      finalTrough: WaveLow;
      finalRightwardPeak: WaveHigh;
      endpointFrozenBy: string;
      authorRuleClaimed: false;
    };
export type WaveBreak = {
  date: string;
  field: 'high' | 'close';
  price: string;
  knownAtClose: string;
  usableFrom: string | null;
};
export type WaveCandidate = {
  id: string;
  H: WaveHigh;
  L: WaveLow;
  confirmationDate: string;
  activationDate: string;
  reasons: WaveReason[];
  firstHighBreak: WaveBreak | null;
  firstCloseBreak: WaveBreak | null;
  evidence: 'ENGINEERING_HYPOTHESIS';
};

export type WaveRejection =
  | {
      method: 'A';
      code: 'no_prior_confirmed_high_at_or_above_H';
      H: WaveHigh;
      boundaryH: null;
      consideredL: [];
      frozenOn: string;
    }
  | {
      method: 'A';
      code: 'no_confirmed_low_strictly_between_boundary_and_H';
      H: WaveHigh;
      reason: Extract<WaveReason, { method: 'A' }>;
    }
  | {
      method: 'A';
      code: 'selected_low_not_below_H';
      H: WaveHigh;
      selectedL: WaveLow;
      reason: Extract<WaveReason, { method: 'A' }>;
    }
  | {
      method: 'B';
      code: 'NO_PRIOR_CONFIRMED_LOW';
      H: WaveHigh;
      reason: {
        rule: 'B_hh_hl_chain_origin';
        originalHigh: WaveHigh;
        initialTroughSelection: 'LATEST_CONFIRMED_STRICT_L_BEFORE_H';
        priorLowCount: 0;
        initialTrough: null;
        attempts: [];
        acceptedExtensions: 0;
        stop: { code: 'NO_PRIOR_CONFIRMED_LOW' };
        finalTrough: null;
        authorRuleClaimed: false;
      };
    }
  | {
      method: 'B';
      code: 'RESULTING_LOW_NOT_BELOW_HIGH';
      H: WaveHigh;
      L: WaveLow;
      reason: Extract<WaveReason, { method: 'B' }>;
    };

export function observeStructuralWaves(
  bars: readonly VideoBar[],
  date: string,
):
  | { kind: 'pending'; reason: string }
  | {
      kind: 'ready';
      candidates: WaveCandidate[];
      rejections: WaveRejection[];
    } {
  if (!bars.some((bar) => bar.date === date))
    return { kind: 'pending', reason: '所选交易日没有已保存行情，待核。' };
  const prefix = bars.filter((bar) => bar.date < date);
  const pivots: WavePivot[] = [];
  for (let t = 0; t < prefix.length; t++) {
    const pivot = pivotAt(prefix, t);
    if (!pivot) continue;
    const base = {
      index: pivot.index,
      date: pivot.date,
      confirmationDate: pivot.confirmed,
    };
    pivots.push(
      pivot.kind === 'H'
        ? { ...base, kind: 'H', field: 'high', price: prefix[pivot.index].high }
        : { ...base, kind: 'L', field: 'low', price: prefix[pivot.index].low },
    );
  }
  const highs = pivots.filter((p) => p.kind === 'H');
  const lows = pivots.filter((p) => p.kind === 'L');
  const result = new Map<string, WaveCandidate>();
  const rejections: WaveRejection[] = [];
  const add = (H: WaveHigh, L: WaveLow, reason: WaveReason) => {
    if (Number(L.price) >= Number(H.price)) return;
    const confirmationDate = H.confirmationDate;
    const confirmationIndex = bars.findIndex(
      (bar) => bar.date === confirmationDate,
    );
    const activationDate = bars[confirmationIndex + 1]?.date;
    if (!activationDate || activationDate > date) return;
    const id = `wave:${H.field}:${H.date}:${Number(H.price)}:${L.field}:${L.date}:${Number(L.price)}`;
    const existing = result.get(id);
    if (existing) {
      existing.reasons.push(reason);
      return;
    }
    const event = (field: 'high' | 'close'): WaveBreak | null => {
      const index = prefix.findIndex(
        (bar) =>
          bar.date > confirmationDate && Number(bar[field]) > Number(H.price),
      );
      if (index < 0) return null;
      const bar = prefix[index];
      return {
        date: bar.date,
        field,
        price: bar[field],
        knownAtClose: bar.date,
        usableFrom: bars[index + 1]?.date ?? null,
      };
    };
    result.set(id, {
      id,
      H,
      L,
      confirmationDate,
      activationDate,
      reasons: [reason],
      firstHighBreak: event('high'),
      firstCloseBreak: event('close'),
      evidence: 'ENGINEERING_HYPOTHESIS',
    });
  };
  for (const H of highs) {
    const boundary = highs
      .filter(
        (p) =>
          p.confirmationDate <= H.confirmationDate &&
          p.date < H.date &&
          Number(p.price) >= Number(H.price),
      )
      .at(-1);
    if (!boundary) {
      rejections.push({
        method: 'A',
        code: 'no_prior_confirmed_high_at_or_above_H',
        H,
        boundaryH: null,
        consideredL: [],
        frozenOn: H.confirmationDate,
      });
    } else {
      const consideredL = lows.filter(
        (p) =>
          p.confirmationDate <= H.confirmationDate &&
          boundary.date < p.date &&
          p.date < H.date,
      );
      const low = consideredL.reduce<WaveLow | null>(
        (best, p) => (!best || Number(p.price) < Number(best.price) ? p : best),
        null,
      );
      const reason: Extract<WaveReason, { method: 'A' }> = {
        method: 'A',
        boundaryH: boundary,
        consideredL,
        selection: 'minimum low price strictly between boundary H and H',
        tieBreak: 'earliest occurrence date',
        frozenOn: H.confirmationDate,
      };
      if (!low)
        rejections.push({
          method: 'A',
          code: 'no_confirmed_low_strictly_between_boundary_and_H',
          H,
          reason,
        });
      else if (Number(low.price) >= Number(H.price))
        rejections.push({
          method: 'A',
          code: 'selected_low_not_below_H',
          H,
          selectedL: low,
          reason,
        });
      else add(H, low, reason);
    }
    const priorLows = lows.filter(
      (p) => p.date < H.date && p.confirmationDate <= H.confirmationDate,
    );
    const initial = priorLows.at(-1);
    if (!initial) {
      rejections.push({
        method: 'B',
        code: 'NO_PRIOR_CONFIRMED_LOW',
        H,
        reason: {
          rule: 'B_hh_hl_chain_origin',
          originalHigh: H,
          initialTroughSelection: 'LATEST_CONFIRMED_STRICT_L_BEFORE_H',
          priorLowCount: 0,
          initialTrough: null,
          attempts: [],
          acceptedExtensions: 0,
          stop: { code: 'NO_PRIOR_CONFIRMED_LOW' },
          finalTrough: null,
          authorRuleClaimed: false,
        },
      });
      continue;
    }
    let trough = initial,
      peak = H;
    const attempts: WaveAttempt[] = [];
    let stop: Extract<WaveReason, { method: 'B' }>['stop'] = {
      code: 'NO_EARLIER_CONFIRMED_LOW',
    };
    let acceptedExtensions = 0;
    for (let i = priorLows.length - 2; i >= 0; i--) {
      const olderLow = priorLows[i];
      const interveningHighs = highs.filter(
        (p) =>
          olderLow.date < p.date &&
          p.date < trough.date &&
          p.confirmationDate <= H.confirmationDate,
      );
      const selected = interveningHighs.reduce<WaveHigh | null>(
        (best, p) =>
          !best || Number(p.price) >= Number(best.price) ? p : best,
        null,
      );
      const olderLowStrictlyLower =
        Number(olderLow.price) < Number(trough.price);
      const hasInterveningStrictHigh = interveningHighs.length > 0;
      const interveningPeakStrictlyLower =
        selected !== null && Number(selected.price) < Number(peak.price);
      const stopCodes = [
        !olderLowStrictlyLower ? 'OLDER_LOW_NOT_STRICTLY_LOWER' : null,
        !hasInterveningStrictHigh
          ? 'NO_INTERVENING_STRICT_HIGH'
          : !interveningPeakStrictlyLower
            ? 'INTERVENING_PEAK_NOT_STRICTLY_LOWER'
            : null,
      ].filter((code): code is string => code !== null);
      attempts.push({
        olderLow,
        currentTrough: trough,
        rightwardPeak: peak,
        interveningHighs,
        selectedHighestInterveningHigh: selected,
        highestPeakTieBreak: 'LATEST_DATE',
        checks: {
          olderLowStrictlyLower,
          hasInterveningStrictHigh,
          interveningPeakStrictlyLower,
        },
        action: stopCodes.length ? 'STOP' : 'EXTEND',
        stopCodes,
      });
      if (stopCodes.length || !selected) {
        stop = {
          code: 'RELATION_FAILED',
          stopCodes,
          atOlderLow: olderLow,
          unvisitedEarlierLowCount: i,
        };
        break;
      }
      trough = olderLow;
      peak = selected;
      acceptedExtensions++;
    }
    const reason: Extract<WaveReason, { method: 'B' }> = {
      method: 'B',
      rule: 'B_hh_hl_chain_origin',
      originalHigh: H,
      initialTroughSelection: 'LATEST_CONFIRMED_STRICT_L_BEFORE_H',
      priorLowCount: priorLows.length,
      initialTrough: initial,
      attempts,
      acceptedExtensions,
      stop,
      finalTrough: trough,
      finalRightwardPeak: peak,
      endpointFrozenBy: H.confirmationDate,
      authorRuleClaimed: false,
    };
    if (Number(trough.price) >= Number(H.price))
      rejections.push({
        method: 'B',
        code: 'RESULTING_LOW_NOT_BELOW_HIGH',
        H,
        L: trough,
        reason,
      });
    else add(H, trough, reason);
  }
  return { kind: 'ready', candidates: [...result.values()], rejections };
}
