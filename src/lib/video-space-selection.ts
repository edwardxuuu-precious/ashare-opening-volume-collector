import type { VideoBar } from './video-space.ts';
import {
  observeAutoFamily,
  observeStructuralWaves,
  type AutoFamily,
  type AutoVersion,
  type LowerPlatform,
  type WaveCandidate,
} from './video-space-auto.ts';

export const SELECTION_RULE = 'selection-envelope-v1';
type PricePoint = {
  readonly date: string;
  readonly field: 'high' | 'low';
  readonly price: string;
};
export type SelectionRuler = {
  readonly id: string;
  readonly kind: 'AUTO' | 'LOWER' | 'WAVE';
  readonly H: PricePoint;
  readonly L: PricePoint;
  readonly confirmationDate: string;
  readonly activationDate: string;
};
export type SelectionBackground = {
  readonly id: string;
  readonly rootId: string;
  readonly H: number;
  readonly L: number;
};
export type SelectionEvent =
  | {
      readonly kind: 'MAIN';
      readonly date: string;
      readonly fromId: string | null;
      readonly toId: string;
      readonly reason:
        | 'COMMON_ENVELOPE'
        | 'BACKGROUND_PLATFORM'
        | 'OUTWARD_EXPANSION';
      readonly backgroundId: string;
    }
  | {
      readonly kind: 'BACKGROUND';
      readonly date: string;
      readonly mainId: string;
      readonly fromId: string;
      readonly toId: string;
    }
  | {
      readonly kind: 'SCALE';
      readonly date: string;
      readonly mainId: string;
      readonly from: 'PARENT' | 'HALF';
      readonly to: 'PARENT' | 'HALF';
      readonly triggerDate: string;
      readonly reason: 'CLOSE_BELOW_L' | 'TWO_CLOSES_RECOVERED';
    }
  | {
      readonly kind: 'RETIRED';
      readonly date: string;
      readonly mainId: string;
      readonly rootId: string;
      readonly knownAtClose: string;
    };
type FineState = {
  readonly scale: 'PARENT' | 'HALF';
  readonly triggerDate: string | null;
  readonly effectiveDate: string;
  readonly firstHalfDate: string | null;
  readonly recoveryCount: number;
};
export type SelectedRuler = {
  readonly ruler: SelectionRuler;
  readonly background: SelectionBackground;
  readonly selectedOn: string;
  readonly scale: 'PARENT' | 'HALF';
  readonly scaleSince: string;
  readonly fine: FineState;
};
export type KeptRuler = {
  readonly ruler: SelectionRuler;
  readonly firstSelectedOn: string;
  readonly backgroundRootId: string;
};
export type RulerSelectionState = {
  readonly rule: typeof SELECTION_RULE;
  readonly events: readonly SelectionEvent[];
  readonly kept: readonly KeptRuler[];
} & (
  | { readonly kind: 'pending'; readonly reason: string }
  | {
      readonly kind: 'ready';
      readonly current: SelectedRuler;
      readonly disagreements: readonly {
        readonly highDate: string;
        readonly candidates: readonly WaveCandidate[];
      }[];
    }
);

function fromPlatform(v: AutoVersion | LowerPlatform): SelectionRuler | null {
  if (!v.activationDate) return null;
  const lower = 'realH' in v;
  const H = lower ? v.realH : v.H;
  const L = lower ? v.realL : v.L;
  return {
    id: v.id,
    kind: lower ? 'LOWER' : 'AUTO',
    H: { ...H, price: String(H.price) },
    L: { ...L, price: String(L.price) },
    confirmationDate: v.confirmationDate,
    activationDate: v.activationDate,
  };
}
function fromWave(v: WaveCandidate): SelectionRuler {
  return {
    id: v.id,
    kind: 'WAVE',
    H: { date: v.H.date, field: v.H.field, price: v.H.price },
    L: { date: v.L.date, field: v.L.field, price: v.L.price },
    confirmationDate: v.confirmationDate,
    activationDate: v.activationDate,
  };
}
function backgroundAt(family: AutoFamily, date: string) {
  const root = family.root.versions.findLast(
    (v) =>
      v.activationDate &&
      v.activationDate <= date &&
      (!v.retiredAt || v.retiredAt >= date),
  );
  if (!root) return null;
  let platform: AutoVersion | LowerPlatform = root;
  for (let i = 0; i < family.lowerPlatforms.length; i++) {
    const predecessor: string = platform.id;
    const next: LowerPlatform | undefined = family.lowerPlatforms.find(
      (v) =>
        v.rootId === root.id &&
        v.predecessorId === predecessor &&
        v.activationDate &&
        v.activationDate <= date,
    );
    if (!next) break;
    platform = next;
  }
  const ruler = fromPlatform(platform);
  return ruler
    ? {
        root,
        ruler,
        context: {
          id: ruler.id,
          rootId: root.id,
          H: Number(ruler.H.price),
          L: Number(ruler.L.price),
        },
      }
    : null;
}
function envelops(wave: WaveCandidate, H: number, L: number) {
  return Number(wave.H.price) >= H && Number(wave.L.price) <= L;
}
function newest(waves: readonly WaveCandidate[]) {
  return waves.reduce<WaveCandidate | null>(
    (best, v) => (!best || v.H.date > best.H.date ? v : best),
    null,
  );
}
function fineAt(
  bars: readonly VideoBar[],
  ruler: SelectionRuler,
  date: string,
): FineState {
  let scale: FineState['scale'] = 'PARENT';
  let triggerDate: string | null = null;
  let effectiveDate = ruler.activationDate;
  let firstHalfDate: string | null = null;
  let recoveryCount = 0;
  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i];
    if (bar.date < ruler.activationDate || bar.date >= date) continue;
    if (scale === 'PARENT') {
      if (Number(bar.close) < Number(ruler.L.price)) {
        scale = 'HALF';
        triggerDate = bar.date;
        effectiveDate = bars[i + 1]?.date ?? date;
        firstHalfDate ??= effectiveDate;
        recoveryCount = 0;
      }
    } else {
      recoveryCount =
        Number(bar.close) >= Number(ruler.L.price) ? recoveryCount + 1 : 0;
      if (recoveryCount >= 2) {
        scale = 'PARENT';
        triggerDate = bar.date;
        effectiveDate = bars[i + 1]?.date ?? date;
        recoveryCount = 0;
      }
    }
  }
  return { scale, triggerDate, effectiveDate, firstHalfDate, recoveryCount };
}

export function observeRulerSelection(bars: readonly VideoBar[], date: string) {
  const family = observeAutoFamily(bars, date);
  const waveCatalog = observeStructuralWaves(bars, date);
  const events: SelectionEvent[] = [];
  const kept: KeptRuler[] = [];
  const common =
    waveCatalog.kind === 'ready'
      ? waveCatalog.candidates.filter(
          (w) =>
            w.reasons.some((r) => r.method === 'A') &&
            w.reasons.some((r) => r.method === 'B'),
        )
      : [];
  let current: SelectedRuler | null = null;
  const result = (state: RulerSelectionState) => ({
    family,
    waveCatalog,
    state,
  });
  if (!bars.some((b) => b.date === date))
    return result({
      kind: 'pending',
      reason: '所选交易日没有已保存行情，规则选尺待核。',
      rule: SELECTION_RULE,
      events,
      kept,
    });
  for (const bar of bars) {
    const day = bar.date;
    if (day > date) break;
    const background = backgroundAt(family, day);
    if (current && current.background.rootId !== background?.context.rootId) {
      const retired = family.root.versions.find(
        (v) => v.id === current?.background.rootId,
      )?.retiredAt;
      if (retired && retired < day)
        events.push({
          kind: 'RETIRED',
          date: day,
          mainId: current.ruler.id,
          rootId: current.background.rootId,
          knownAtClose: retired,
        });
      current = null;
    }
    if (!background) continue;
    const eligible = common.filter((w) => w.activationDate <= day);
    const previous: SelectedRuler | null = current;
    let ruler: SelectionRuler = previous ? previous.ruler : background.ruler;
    let reason: Extract<SelectionEvent, { kind: 'MAIN' }>['reason'] =
      'BACKGROUND_PLATFORM';
    if (!previous || previous.background.id !== background.context.id) {
      const wave = newest(
        eligible.filter((w) =>
          envelops(w, background.context.H, background.context.L),
        ),
      );
      if (wave) {
        ruler = fromWave(wave);
        reason = 'COMMON_ENVELOPE';
      } else ruler = background.ruler;
    } else {
      const held = previous.ruler;
      const H = Number(held.H.price),
        L = Number(held.L.price);
      const wave = newest(
        eligible.filter(
          (w) =>
            w.H.date > held.H.date &&
            envelops(w, H, L) &&
            (Number(w.H.price) > H || Number(w.L.price) < L),
        ),
      );
      if (wave) {
        ruler = fromWave(wave);
        reason = 'OUTWARD_EXPANSION';
      }
    }
    const changed: boolean = !previous || previous.ruler.id !== ruler.id;
    const fine = fineAt(bars, ruler, day);
    const selectedOn: string = !previous || changed ? day : previous.selectedOn;
    const scaleBefore: FineState['scale'] =
      !previous || changed ? 'PARENT' : previous.scale;
    const scaleSince: string =
      !previous || changed || scaleBefore !== fine.scale
        ? day
        : previous.scaleSince;
    current = {
      ruler,
      background: background.context,
      selectedOn,
      scale: fine.scale,
      scaleSince,
      fine,
    };
    if (changed) {
      events.push({
        kind: 'MAIN',
        date: day,
        fromId: previous?.ruler.id ?? null,
        toId: ruler.id,
        reason,
        backgroundId: background.context.id,
      });
      if (
        !kept.some(
          (v) =>
            v.ruler.id === ruler.id &&
            v.backgroundRootId === background.context.rootId,
        )
      )
        kept.push({
          ruler,
          firstSelectedOn: day,
          backgroundRootId: background.context.rootId,
        });
    } else if (previous && previous.background.id !== background.context.id) {
      events.push({
        kind: 'BACKGROUND',
        date: day,
        mainId: ruler.id,
        fromId: previous.background.id,
        toId: background.context.id,
      });
    }
    if (scaleBefore !== fine.scale && fine.triggerDate)
      events.push({
        kind: 'SCALE',
        date: day,
        mainId: ruler.id,
        from: scaleBefore,
        to: fine.scale,
        triggerDate: fine.triggerDate,
        reason:
          fine.scale === 'HALF' ? 'CLOSE_BELOW_L' : 'TWO_CLOSES_RECOVERED',
      });
  }
  if (!current)
    return result({
      kind: 'pending',
      reason: '当日没有存续且已启用的自动平台背景；保留原大格，规则主尺待核。',
      rule: SELECTION_RULE,
      events,
      kept,
    });
  const groups = new Map<string, WaveCandidate[]>();
  if (waveCatalog.kind === 'ready')
    for (const w of waveCatalog.candidates) {
      if (
        w.H.date < current.ruler.H.date ||
        !envelops(w, current.background.H, current.background.L)
      )
        continue;
      const group = groups.get(w.H.date) ?? [];
      group.push(w);
      groups.set(w.H.date, group);
    }
  const disagreements = [...groups.entries()]
    .filter(
      ([, candidates]) =>
        candidates.length > 1 &&
        candidates.some((w) => w.reasons.some((r) => r.method === 'A')) &&
        candidates.some((w) => w.reasons.some((r) => r.method === 'B')),
    )
    .map(([highDate, candidates]) => ({ highDate, candidates }));
  return result({
    kind: 'ready',
    rule: SELECTION_RULE,
    current,
    events,
    kept,
    disagreements,
  });
}
