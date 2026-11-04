export type PriceBar =
  | {
      readonly kind: 'bar';
      readonly date: string;
      readonly open: number;
      readonly high: number;
      readonly low: number;
      readonly close: number;
    }
  | {
      readonly kind: 'gap';
      readonly date: string;
      readonly reason:
        | 'not-traded'
        | 'missing-row'
        | 'missing-day'
        | 'invalid-price';
    };

export type TriangleParams = {
  fastPeriod: number;
  middlePeriod: number;
  slowPeriod: number;
  pretrendSessions: number;
  minDeclinePct: number;
  minAdvancePct: number;
  formationSessions: number;
  trendSessions: number;
  slopeSessions: number;
  zoneLookbackSessions: number;
  pivotRadius: number;
  zoneTolerancePct: number;
  minTouches: number;
  touchSeparationSessions: number;
  minTurnPct: number;
  breakoutBufferPct: number;
  confirmSessions: number;
  expirySessions: number;
};

export type ParameterField = {
  readonly key: keyof TriangleParams;
  readonly label: string;
  readonly min: number;
  readonly max: number;
  readonly step: number;
  readonly advanced: boolean;
};

export const parameterFields: readonly ParameterField[] = [
  {
    key: 'fastPeriod',
    label: '短均线',
    min: 1,
    max: 60,
    step: 1,
    advanced: false,
  },
  {
    key: 'middlePeriod',
    label: '中均线',
    min: 2,
    max: 120,
    step: 1,
    advanced: false,
  },
  {
    key: 'slowPeriod',
    label: '长均线',
    min: 3,
    max: 250,
    step: 1,
    advanced: false,
  },
  {
    key: 'pretrendSessions',
    label: '前置趋势回看交易日',
    min: 3,
    max: 250,
    step: 1,
    advanced: false,
  },
  {
    key: 'minDeclinePct',
    label: '金三角前最小跌幅 (%)',
    min: 0,
    max: 95,
    step: 0.5,
    advanced: false,
  },
  {
    key: 'minAdvancePct',
    label: '死三角前最小涨幅 (%)',
    min: 0,
    max: 500,
    step: 0.5,
    advanced: false,
  },
  {
    key: 'formationSessions',
    label: '三次交叉完成窗口',
    min: 1,
    max: 60,
    step: 1,
    advanced: false,
  },
  {
    key: 'trendSessions',
    label: '此前反向排列持续交易日',
    min: 1,
    max: 30,
    step: 1,
    advanced: true,
  },
  {
    key: 'slopeSessions',
    label: '此前长均线斜率回看交易日',
    min: 1,
    max: 60,
    step: 1,
    advanced: true,
  },
  {
    key: 'zoneLookbackSessions',
    label: '压力支撑回看交易日',
    min: 5,
    max: 250,
    step: 1,
    advanced: true,
  },
  {
    key: 'pivotRadius',
    label: '局部高低点左右交易日',
    min: 1,
    max: 10,
    step: 1,
    advanced: true,
  },
  {
    key: 'zoneTolerancePct',
    label: '价格区域半宽 (%)',
    min: 0.1,
    max: 10,
    step: 0.1,
    advanced: true,
  },
  {
    key: 'minTouches',
    label: '区域最少有效触点',
    min: 2,
    max: 10,
    step: 1,
    advanced: true,
  },
  {
    key: 'touchSeparationSessions',
    label: '触点最小间隔交易日',
    min: 1,
    max: 60,
    step: 1,
    advanced: true,
  },
  {
    key: 'minTurnPct',
    label: '触点之间最小转折 (%)',
    min: 0.1,
    max: 30,
    step: 0.1,
    advanced: true,
  },
  {
    key: 'breakoutBufferPct',
    label: '突破确认缓冲 (%)',
    min: 0,
    max: 10,
    step: 0.1,
    advanced: true,
  },
  {
    key: 'confirmSessions',
    label: '连续确认交易日',
    min: 1,
    max: 10,
    step: 1,
    advanced: true,
  },
  {
    key: 'expirySessions',
    label: '形成后有效交易日',
    min: 1,
    max: 250,
    step: 1,
    advanced: true,
  },
];

export const defaultTriangleParams: TriangleParams = {
  fastPeriod: 5,
  middlePeriod: 10,
  slowPeriod: 20,
  pretrendSessions: 60,
  minDeclinePct: 20,
  minAdvancePct: 25,
  formationSessions: 10,
  trendSessions: 3,
  slopeSessions: 5,
  zoneLookbackSessions: 60,
  pivotRadius: 2,
  zoneTolerancePct: 1.5,
  minTouches: 2,
  touchSeparationSessions: 5,
  minTurnPct: 3,
  breakoutBufferPct: 0.5,
  confirmSessions: 2,
  expirySessions: 30,
};

export type MaPoint = {
  readonly date: string;
  readonly bar: PriceBar;
  readonly fast: number | null;
  readonly middle: number | null;
  readonly slow: number | null;
};
export type MaPair = 'fast-middle' | 'fast-slow' | 'middle-slow';
export type TriangleDirection = 'golden' | 'death';
export type MaCross = {
  readonly id: string;
  readonly pair: MaPair;
  readonly direction: 'up' | 'down';
  readonly date: string;
  readonly value: number;
};
export type PriceAnchor = {
  readonly date: string;
  readonly price: number;
  readonly source: 'high' | 'low';
};
export type PretrendEvidence = {
  readonly kind: 'decline' | 'advance';
  readonly requiredSessions: number;
  readonly observedSessions: number;
  readonly complete: boolean;
  readonly movePct: number | null;
  readonly from: PriceAnchor | null;
  readonly to: PriceAnchor | null;
  readonly qualified: boolean;
  readonly reason: string;
};
export type ZoneTouch = {
  readonly occurredAt: string;
  readonly knownAt: string;
  readonly price: number;
};
export type PriceZone = {
  readonly kind: 'support' | 'resistance';
  readonly center: number;
  readonly lower: number;
  readonly upper: number;
  readonly frozenAt: string;
  readonly knownAt: string;
  readonly touches: readonly ZoneTouch[];
};
export type PathStatus =
  | { readonly kind: 'waiting' }
  | {
      readonly kind: 'testing';
      readonly startedAt: string;
      readonly observedSessions: number;
      readonly requiredSessions: number;
    }
  | {
      readonly kind: 'confirmed';
      readonly occurredAt: string;
      readonly knownAt: string;
      readonly price: number;
    };
export type TriangleState =
  | {
      readonly kind: 'active';
      readonly ageSessions: number;
      readonly breakout: PathStatus;
      readonly retest: PathStatus;
    }
  | { readonly kind: 'observing'; readonly ageSessions: number }
  | { readonly kind: 'rejected'; readonly reason: string }
  | {
      readonly kind: 'invalidated' | 'expired' | 'coverage-ended';
      readonly date: string;
      readonly reason: string;
    };
export type TriangleEvent = {
  readonly id: string;
  readonly kind:
    | 'formed'
    | 'resistance-breakout-observed'
    | 'support-touch'
    | 'resistance-breakout'
    | 'support-retest'
    | 'invalidated'
    | 'expired'
    | 'coverage-ended';
  readonly occurredAt: string;
  readonly knownAt: string;
  readonly price: number | null;
  readonly reason: string;
};
export type TriangleFormation = {
  readonly id: string;
  readonly direction: TriangleDirection;
  readonly startedAt: string;
  readonly formedAt: string;
  readonly price: number;
  readonly crosses: readonly MaCross[];
  readonly qualified: boolean;
  readonly reasons: readonly string[];
  readonly pretrend: PretrendEvidence;
  readonly support: PriceZone | null;
  readonly resistance: PriceZone | null;
  readonly thresholds: {
    readonly breakout: number | null;
    readonly retestRecovery: number | null;
    readonly zoneInvalidation: number | null;
    readonly anchorInvalidation: number | null;
  };
  readonly zoneReasons: {
    readonly support: string;
    readonly resistance: string;
  };
  readonly events: readonly TriangleEvent[];
  readonly state: TriangleState;
};
export type TriangleAnalysis = {
  readonly asOf: string;
  readonly params: TriangleParams;
  readonly points: readonly MaPoint[];
  readonly crosses: readonly MaCross[];
  readonly formations: readonly TriangleFormation[];
  readonly availability: {
    readonly kind: 'ready' | 'insufficient';
    readonly reason: string;
    readonly consecutiveBars: number;
  };
};

export function validateTriangleParams(value: TriangleParams): TriangleParams {
  if (!value || typeof value !== 'object')
    throw new Error('三角形参数必须是有效对象');
  for (const field of parameterFields) {
    const number = value[field.key];
    if (
      typeof number !== 'number' ||
      !Number.isFinite(number) ||
      number < field.min ||
      number > field.max
    ) {
      throw new Error(
        `${field.label}必须是 ${field.min} 至 ${field.max} 之间的有限数字`,
      );
    }
    if (field.step === 1 && !Number.isInteger(number))
      throw new Error(`${field.label}必须是整数`);
  }
  if (
    !(
      value.fastPeriod < value.middlePeriod &&
      value.middlePeriod < value.slowPeriod
    )
  ) {
    throw new Error('均线周期必须满足短均线 < 中均线 < 长均线');
  }
  if (value.zoneLookbackSessions < value.pivotRadius * 2 + 1)
    throw new Error('压力支撑回看交易日必须覆盖局部高低点左右窗口');
  return { ...value };
}

export function requiredTriangleHistory(params: TriangleParams): number {
  return (
    Math.max(
      params.slowPeriod + params.slopeSessions,
      params.slowPeriod + params.trendSessions - 1,
      params.pretrendSessions,
      params.zoneLookbackSessions,
    ) + 1
  );
}

export function analyzeTriangles(input: {
  readonly bars: readonly PriceBar[];
  readonly params: TriangleParams;
  readonly asOf: string;
}): TriangleAnalysis {
  const params = validateTriangleParams(input.params);
  validateDate(input.asOf, '观察截止日期');
  const bars = input.bars
    .filter((bar) => bar.date <= input.asOf)
    .map((bar) => ({ ...bar }));
  validateBars(bars);
  const points: MaPoint[] = [];
  const crosses: MaCross[] = [];
  const formations: TriangleFormation[] = [];
  const lives = new Map<string, Lifecycle>();
  let segment: TradedBar[] = [];
  let segmentPoints: MaPoint[] = [];
  let golden: Candidate | null = null;
  let death: Candidate | null = null;

  for (const bar of bars) {
    if (bar.kind === 'gap') {
      for (let f = 0; f < formations.length; f++) {
        const formation = formations[f];
        if (formation && isLive(formation.state)) {
          formations[f] = terminate(
            formation,
            'coverage-ended',
            bar.date,
            bar.date,
            null,
            `数据覆盖中断（${gapLabels[bar.reason]}），不据此判断价格失效`,
          );
        }
      }
      points.push({
        date: bar.date,
        bar,
        fast: null,
        middle: null,
        slow: null,
      });
      segment = [];
      segmentPoints = [];
      golden = null;
      death = null;
      continue;
    }

    const index = segment.length;
    segment.push(bar);
    const average = (period: number): number | null =>
      segment.length < period
        ? null
        : segment.slice(-period).reduce((sum, row) => sum + row.close, 0) /
          period;
    const point: MaPoint = {
      date: bar.date,
      bar,
      fast: average(params.fastPeriod),
      middle: average(params.middlePeriod),
      slow: average(params.slowPeriod),
    };
    const previous = segmentPoints.at(-1);
    const todayCrosses = previous ? findCrosses(previous, point) : [];
    crosses.push(...todayCrosses);

    for (let f = 0; f < formations.length; f++) {
      const formation = formations[f];
      const life = formation ? lives.get(formation.id) : undefined;
      if (formation && life && isLive(formation.state))
        formations[f] = advanceLifecycle(
          formation,
          life,
          bar,
          previous,
          index,
          todayCrosses,
          params,
        );
    }

    golden = advanceCandidate(
      golden,
      'golden',
      todayCrosses,
      segment,
      segmentPoints,
      index,
      params,
    );
    death = advanceCandidate(
      death,
      'death',
      todayCrosses,
      segment,
      segmentPoints,
      index,
      params,
    );
    for (const candidate of [golden, death]) {
      if (
        !candidate ||
        candidate.crosses.size !== 3 ||
        !strictStack(point, candidate.direction)
      )
        continue;
      const formation = completeFormation(
        candidate,
        bar,
        index,
        segment,
        params,
      );
      formations.push(formation);
      lives.set(formation.id, {
        formedIndex: index,
        supportLoss: null,
        anchorLoss: null,
      });
      if (candidate.direction === 'golden') golden = null;
      else death = null;
    }
    segmentPoints.push(point);
    points.push(point);
  }

  const required = requiredTriangleHistory(params);
  const ready = segment.length >= required;
  return freeze({
    asOf: input.asOf,
    params,
    points,
    crosses,
    formations,
    availability: {
      kind: ready ? 'ready' : 'insufficient',
      consecutiveBars: segment.length,
      reason: ready
        ? `连续 ${segment.length} 个交易日可用于当前规则回放`
        : `当前仅有 ${segment.length} 个连续有效交易日，当前规则完整评估至少需 ${required} 日；历史已形成记录仍保留`,
    },
  });
}

type TradedBar = Extract<PriceBar, { kind: 'bar' }>;
type Candidate = {
  direction: TriangleDirection;
  startedAt: string;
  startedIndex: number;
  crosses: Map<MaPair, MaCross>;
  pretrend: PretrendEvidence;
  reasons: string[];
  support: PriceZone | null;
  resistance: PriceZone | null;
  zoneReasons: { support: string; resistance: string };
};
type LossRun = { startedAt: string; sessions: number };
type Lifecycle = {
  formedIndex: number;
  supportLoss: LossRun | null;
  anchorLoss: LossRun | null;
};
type Pivot = ZoneTouch & { index: number };
const gapLabels = {
  'not-traded': '当日未成交',
  'missing-row': '行情行缺失',
  'missing-day': '交易日缺失',
  'invalid-price': '价格不可用',
};
const pairs: readonly {
  pair: MaPair;
  left: 'fast' | 'middle';
  right: 'middle' | 'slow';
}[] = [
  { pair: 'fast-middle', left: 'fast', right: 'middle' },
  { pair: 'fast-slow', left: 'fast', right: 'slow' },
  { pair: 'middle-slow', left: 'middle', right: 'slow' },
];

function freeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

function validateDate(value: string, label: string): void {
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(value) ||
    !Number.isFinite(Date.parse(`${value}T00:00:00Z`)) ||
    new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) !== value
  ) {
    throw new Error(`${label}必须是有效的 YYYY-MM-DD 日期`);
  }
}

function validateBars(bars: readonly PriceBar[]): void {
  let previous = '';
  for (const bar of bars) {
    validateDate(bar.date, '行情日期');
    if (bar.date <= previous)
      throw new Error(`行情日期必须严格递增且不能重复（${bar.date}）`);
    previous = bar.date;
    if (bar.kind === 'gap') {
      if (!(bar.reason in gapLabels))
        throw new Error(`行情缺口原因无效（${bar.date}）`);
      continue;
    }
    if (bar.kind !== 'bar') throw new Error('行情类型无效');
    if (
      [bar.open, bar.high, bar.low, bar.close].some(
        (price) =>
          !Number.isFinite(price) ||
          price <= 0 ||
          price > Number.MAX_SAFE_INTEGER / 1000,
      )
    ) {
      throw new Error(`OHLC 价格必须为安全范围内的有限正数（${bar.date}）`);
    }
    if (
      bar.low > Math.min(bar.open, bar.close) ||
      bar.high < Math.max(bar.open, bar.close) ||
      bar.low > bar.high
    ) {
      throw new Error(`OHLC 高低价关系无效（${bar.date}）`);
    }
  }
}

function findCrosses(previous: MaPoint, today: MaPoint): MaCross[] {
  const result: MaCross[] = [];
  for (const { pair, left, right } of pairs) {
    const beforeLeft = previous[left];
    const beforeRight = previous[right];
    const nowLeft = today[left];
    const nowRight = today[right];
    if (
      beforeLeft === null ||
      beforeRight === null ||
      nowLeft === null ||
      nowRight === null
    )
      continue;
    const before = beforeLeft - beforeRight;
    const now = nowLeft - nowRight;
    const direction =
      before <= 0 && now > 0 ? 'up' : before >= 0 && now < 0 ? 'down' : null;
    if (direction)
      result.push({
        id: `${today.date}:${pair}:${direction}`,
        pair,
        direction,
        date: today.date,
        value: (nowLeft + nowRight) / 2,
      });
  }
  return result;
}

function strictStack(point: MaPoint, direction: TriangleDirection): boolean {
  if (point.fast === null || point.middle === null || point.slow === null)
    return false;
  return direction === 'golden'
    ? point.fast > point.middle && point.middle > point.slow
    : point.fast < point.middle && point.middle < point.slow;
}

function contextReasons(
  prior: readonly MaPoint[],
  direction: TriangleDirection,
  params: TriangleParams,
): string[] {
  const recent = prior.slice(-params.trendSessions);
  const orderValid =
    recent.length === params.trendSessions &&
    recent.every((point) => {
      if (point.fast === null || point.middle === null || point.slow === null)
        return false;
      return direction === 'golden'
        ? point.fast <= point.middle && point.middle <= point.slow
        : point.fast >= point.middle && point.middle >= point.slow;
    });
  const latest = prior.at(-1)?.slow;
  const earlier = prior.at(-1 - params.slopeSessions)?.slow;
  const slopeValid =
    latest != null &&
    earlier != null &&
    (direction === 'golden' ? latest < earlier : latest > earlier);
  const reasons: string[] = [];
  if (!orderValid)
    reasons.push(
      `首个交叉前未满足连续 ${params.trendSessions} 日的${direction === 'golden' ? '空头' : '多头'}排列（相等允许，最终排列须严格）`,
    );
  if (!slopeValid)
    reasons.push(
      `首个交叉前长均线未满足 ${params.slopeSessions} 日${direction === 'golden' ? '下降' : '上升'}方向，或均线历史不足`,
    );
  return reasons;
}

function pretrendEvidence(
  prior: readonly TradedBar[],
  direction: TriangleDirection,
  params: TriangleParams,
): PretrendEvidence {
  const rows = prior.slice(-params.pretrendSessions);
  const common = {
    kind: direction === 'golden' ? 'decline' : 'advance',
    requiredSessions: params.pretrendSessions,
    observedSessions: rows.length,
  } satisfies Pick<
    PretrendEvidence,
    'kind' | 'requiredSessions' | 'observedSessions'
  >;
  if (rows.length < params.pretrendSessions) {
    return {
      ...common,
      complete: false,
      movePct: null,
      from: null,
      to: null,
      qualified: false,
      reason: `前置趋势需要完整 ${params.pretrendSessions} 日，首个交叉前仅有 ${rows.length} 日，不缩短窗口`,
    };
  }
  let from: PriceAnchor | null = null;
  let to: PriceAnchor | null = null;
  let extreme: TradedBar | null = null;
  let best = -Infinity;
  for (const row of rows) {
    if (extreme) {
      const move =
        direction === 'golden'
          ? ((extreme.high - row.low) / extreme.high) * 100
          : ((row.high - extreme.low) / extreme.low) * 100;
      if (move > best) {
        best = move;
        from = {
          date: extreme.date,
          price: direction === 'golden' ? extreme.high : extreme.low,
          source: direction === 'golden' ? 'high' : 'low',
        };
        to = {
          date: row.date,
          price: direction === 'golden' ? row.low : row.high,
          source: direction === 'golden' ? 'low' : 'high',
        };
      }
    }
    if (
      !extreme ||
      (direction === 'golden' ? row.high > extreme.high : row.low < extreme.low)
    )
      extreme = row;
  }
  const movePct = Math.max(0, best);
  const required =
    direction === 'golden' ? params.minDeclinePct : params.minAdvancePct;
  const qualified = from !== null && to !== null && movePct >= required;
  return {
    ...common,
    complete: true,
    movePct,
    from,
    to,
    qualified,
    reason: `按先${direction === 'golden' ? '高后低的最大回撤' : '低后高的最大涨幅'}计算 ${movePct.toFixed(2)}%，${qualified ? '达到' : '未达到'} ${required}% 门槛`,
  };
}

function knownPivots(
  rows: readonly TradedBar[],
  kind: 'support' | 'resistance',
  params: TriangleParams,
): Pivot[] {
  const pivots: Pivot[] = [];
  const field = kind === 'support' ? 'low' : 'high';
  for (
    let index = params.pivotRadius;
    index + params.pivotRadius < rows.length;
    index++
  ) {
    const row = rows[index];
    const radiusEnd = rows[index + params.pivotRadius];
    if (!row || !radiusEnd) continue;
    const price = row[field];
    const left = rows.slice(index - params.pivotRadius, index);
    const right = rows.slice(index + 1, index + params.pivotRadius + 1);
    const local =
      kind === 'support'
        ? left.every((other) => other.low > price) &&
          right.every((other) => other.low >= price)
        : left.every((other) => other.high < price) &&
          right.every((other) => other.high <= price);
    if (!local) continue;
    const turn = rows
      .slice(index + 1)
      .find((other) =>
        kind === 'support'
          ? other.high >= price * (1 + params.minTurnPct / 100)
          : other.low <= price * (1 - params.minTurnPct / 100),
      );
    if (!turn) continue;
    pivots.push({
      index,
      occurredAt: row.date,
      knownAt: turn.date > radiusEnd.date ? turn.date : radiusEnd.date,
      price,
    });
  }
  return pivots;
}

function hasEffectiveBreach(
  rows: readonly TradedBar[],
  kind: 'support' | 'resistance',
  level: number,
  params: TriangleParams,
): boolean {
  let run = 0;
  for (const row of rows) {
    run = (kind === 'support' ? row.close < level : row.close > level)
      ? run + 1
      : 0;
    if (run >= params.confirmSessions) return true;
  }
  return false;
}

function freezeZone(
  rows: readonly TradedBar[],
  kind: 'support' | 'resistance',
  at: TradedBar,
  params: TriangleParams,
): { zone: PriceZone | null; reason: string } {
  if (rows.length < params.zoneLookbackSessions)
    return {
      zone: null,
      reason: `区域证据需完整 ${params.zoneLookbackSessions} 日，首个交叉前仅有 ${rows.length} 日`,
    };
  const window = rows.slice(-params.zoneLookbackSessions);
  const pivots = knownPivots(window, kind, params);
  const zones: PriceZone[] = [];
  let staleCount = 0;
  for (const seed of pivots) {
    const lower = seed.price * (1 - params.zoneTolerancePct / 100);
    const upper = seed.price * (1 + params.zoneTolerancePct / 100);
    if (kind === 'support' ? upper >= at.close : lower <= at.close) continue;
    const lossLevel =
      kind === 'support'
        ? lower * (1 - params.breakoutBufferPct / 100)
        : upper * (1 + params.breakoutBufferPct / 100);
    let touches: Pivot[] = [];
    for (const pivot of pivots) {
      if (pivot.price < lower || pivot.price > upper) continue;
      const last = touches.at(-1);
      if (last) {
        if (
          hasEffectiveBreach(
            window.slice(last.index + 1, pivot.index),
            kind,
            lossLevel,
            params,
          )
        )
          touches = [];
        else {
          if (pivot.index - last.index < params.touchSeparationSessions)
            continue;
          const between = window.slice(last.index + 1, pivot.index);
          const turn =
            kind === 'support'
              ? between.some(
                  (row) =>
                    row.high >=
                    Math.max(last.price, pivot.price) *
                      (1 + params.minTurnPct / 100),
                )
              : between.some(
                  (row) =>
                    row.low <=
                    Math.min(last.price, pivot.price) *
                      (1 - params.minTurnPct / 100),
                );
          if (!turn) continue;
        }
      }
      touches.push(pivot);
    }
    const last = touches.at(-1);
    if (touches.length < params.minTouches || !last) continue;
    if (
      hasEffectiveBreach(window.slice(last.index + 1), kind, lossLevel, params)
    ) {
      staleCount++;
      continue;
    }
    zones.push({
      kind,
      center: seed.price,
      lower,
      upper,
      frozenAt: at.date,
      knownAt: touches.reduce(
        (latest, touch) => (touch.knownAt > latest ? touch.knownAt : latest),
        '',
      ),
      touches: touches.map(({ occurredAt, knownAt, price }) => ({
        occurredAt,
        knownAt,
        price,
      })),
    });
  }
  zones.sort(
    (a, b) =>
      (kind === 'support' ? b.center - a.center : a.center - b.center) ||
      b.touches.length - a.touches.length ||
      b.knownAt.localeCompare(a.knownAt),
  );
  const zone = zones[0] ?? null;
  return {
    zone: zone ? freeze(zone) : null,
    reason: zone
      ? `首个交叉前冻结，${zone.touches.length} 个已确认且相隔至少 ${params.touchSeparationSessions} 日的触点；固定中心，不链式扩宽`
      : `${staleCount ? '已剔除有效破位的旧区域；' : ''}没有首个交叉前已知、位于价格${kind === 'support' ? '下方' : '上方'}且符合触点与转折要求的${kind === 'support' ? '支撑' : '压力'}区域`,
  };
}

function advanceCandidate(
  candidate: Candidate | null,
  direction: TriangleDirection,
  crosses: readonly MaCross[],
  rows: readonly TradedBar[],
  priorPoints: readonly MaPoint[],
  index: number,
  params: TriangleParams,
): Candidate | null {
  const crossDirection = direction === 'golden' ? 'up' : 'down';
  if (
    candidate &&
    (index - candidate.startedIndex >= params.formationSessions ||
      crosses.some(
        (cross) =>
          cross.direction !== crossDirection &&
          candidate?.crosses.has(cross.pair),
      ))
  )
    candidate = null;
  const matching = crosses.filter(
    (cross) => cross.direction === crossDirection,
  );
  if (!matching.length) return candidate;
  const at = rows.at(-1);
  if (!at) return candidate;
  if (!candidate) {
    const prior = rows.slice(0, -1);
    const pretrend = pretrendEvidence(prior, direction, params);
    const support = freezeZone(prior, 'support', at, params);
    const resistance = freezeZone(prior, 'resistance', at, params);
    candidate = {
      direction,
      startedAt: at.date,
      startedIndex: index,
      crosses: new Map(),
      pretrend: freeze(pretrend),
      reasons: [
        ...contextReasons(priorPoints, direction, params),
        ...(pretrend.qualified ? [] : [pretrend.reason]),
      ],
      support: support.zone,
      resistance: resistance.zone,
      zoneReasons: { support: support.reason, resistance: resistance.reason },
    };
  }
  for (const cross of matching)
    if (!candidate.crosses.has(cross.pair))
      candidate.crosses.set(cross.pair, cross);
  return candidate;
}

function completeFormation(
  candidate: Candidate,
  bar: TradedBar,
  index: number,
  segment: readonly TradedBar[],
  params: TriangleParams,
): TriangleFormation {
  const reasons = [...candidate.reasons];
  const riskKind = candidate.direction === 'golden' ? 'support' : 'resistance';
  const zone =
    candidate.direction === 'golden' ? candidate.support : candidate.resistance;
  const anchor = candidate.pretrend.to;
  const during = segment.slice(candidate.startedIndex, index + 1);
  const factor =
    1 +
    ((candidate.direction === 'golden' ? -1 : 1) * params.breakoutBufferPct) /
      100;
  if (
    zone &&
    hasEffectiveBreach(
      during,
      riskKind,
      (candidate.direction === 'golden' ? zone.lower : zone.upper) * factor,
      params,
    )
  )
    reasons.push('首个交叉至三角形成期间，冻结区域已连续有效破位');
  if (
    anchor &&
    hasEffectiveBreach(during, riskKind, anchor.price * factor, params)
  )
    reasons.push('首个交叉至三角形成期间，前置趋势价格锚点已连续有效破位');
  const qualified = reasons.length === 0;
  const id = `${candidate.direction}:${candidate.startedAt}:${bar.date}`;
  return {
    id,
    direction: candidate.direction,
    startedAt: candidate.startedAt,
    formedAt: bar.date,
    price: bar.close,
    crosses: [...candidate.crosses.values()].sort(
      (a, b) => a.date.localeCompare(b.date) || a.pair.localeCompare(b.pair),
    ),
    qualified,
    reasons,
    pretrend: candidate.pretrend,
    support: candidate.support,
    resistance: candidate.resistance,
    zoneReasons: candidate.zoneReasons,
    thresholds: {
      breakout:
        candidate.direction === 'golden' && candidate.resistance
          ? candidate.resistance.upper * (1 + params.breakoutBufferPct / 100)
          : null,
      retestRecovery:
        candidate.direction === 'golden' && candidate.support
          ? candidate.support.upper
          : null,
      zoneInvalidation: zone
        ? (candidate.direction === 'golden' ? zone.lower : zone.upper) * factor
        : null,
      anchorInvalidation: anchor ? anchor.price * factor : null,
    },
    events: [
      {
        id: `${id}:formed:${bar.date}`,
        kind: 'formed',
        occurredAt: bar.date,
        knownAt: bar.date,
        price: bar.close,
        reason: qualified
          ? candidate.direction === 'golden'
            ? '三次上穿完成且形成严格多头排列；两条后续路径独立观察'
            : '三次下穿完成且形成严格空头排列；仅作风险观察'
          : `三次交叉完成，背景不合格：${reasons.join('；')}`,
      },
    ],
    state: qualified
      ? candidate.direction === 'golden'
        ? {
            kind: 'active',
            ageSessions: 0,
            breakout: { kind: 'waiting' },
            retest: { kind: 'waiting' },
          }
        : { kind: 'observing', ageSessions: 0 }
      : { kind: 'rejected', reason: reasons.join('；') },
  };
}

function isLive(state: TriangleState): boolean {
  return state.kind === 'active' || state.kind === 'observing';
}

function terminate(
  formation: TriangleFormation,
  kind: 'invalidated' | 'expired' | 'coverage-ended',
  occurredAt: string,
  knownAt: string,
  price: number | null,
  reason: string,
): TriangleFormation {
  return {
    ...formation,
    state: { kind, date: knownAt, reason },
    events: [
      ...formation.events,
      {
        id: `${formation.id}:${kind}:${occurredAt}`,
        kind,
        occurredAt,
        knownAt,
        price,
        reason,
      },
    ],
  };
}

function nextLoss(
  run: LossRun | null,
  lost: boolean,
  date: string,
): LossRun | null {
  return lost
    ? { startedAt: run?.startedAt ?? date, sessions: (run?.sessions ?? 0) + 1 }
    : null;
}

function advanceLifecycle(
  formation: TriangleFormation,
  life: Lifecycle,
  bar: TradedBar,
  previous: MaPoint | undefined,
  index: number,
  crosses: readonly MaCross[],
  params: TriangleParams,
): TriangleFormation {
  const ageSessions = index - life.formedIndex;
  const golden = formation.direction === 'golden';
  const reverse = crosses.find(
    (cross) => cross.direction === (golden ? 'down' : 'up'),
  );
  if (reverse)
    return terminate(
      formation,
      'invalidated',
      bar.date,
      bar.date,
      bar.close,
      `${reverse.pair} 发生反向交叉`,
    );
  const supportThreshold = formation.thresholds.zoneInvalidation;
  const anchorThreshold = formation.thresholds.anchorInvalidation;
  const loses = (threshold: number | null): boolean =>
    threshold !== null &&
    (golden ? bar.close < threshold : bar.close > threshold);
  life.supportLoss = nextLoss(
    life.supportLoss,
    loses(supportThreshold),
    bar.date,
  );
  life.anchorLoss = nextLoss(life.anchorLoss, loses(anchorThreshold), bar.date);
  if (life.supportLoss && life.supportLoss.sessions >= params.confirmSessions)
    return terminate(
      formation,
      'invalidated',
      life.supportLoss.startedAt,
      bar.date,
      bar.close,
      `收盘连续 ${params.confirmSessions} 日有效${golden ? '跌破冻结支撑' : '涨破冻结压力'}（含 ${params.breakoutBufferPct}% 缓冲）`,
    );
  if (life.anchorLoss && life.anchorLoss.sessions >= params.confirmSessions)
    return terminate(
      formation,
      'invalidated',
      life.anchorLoss.startedAt,
      bar.date,
      bar.close,
      `收盘连续 ${params.confirmSessions} 日有效${golden ? '跌破前置低点' : '涨破前置高点'}锚（含 ${params.breakoutBufferPct}% 缓冲）`,
    );
  if (ageSessions >= params.expirySessions)
    return terminate(
      formation,
      'expired',
      bar.date,
      bar.date,
      bar.close,
      `形成后已满 ${params.expirySessions} 个交易日，停止有效性观察`,
    );
  if (formation.state.kind === 'observing')
    return { ...formation, state: { kind: 'observing', ageSessions } };
  if (formation.state.kind !== 'active') return formation;

  const events = [...formation.events];
  let breakout = formation.state.breakout;
  let retest = formation.state.retest;
  const ambiguous =
    (supportThreshold !== null && bar.low < supportThreshold) ||
    (anchorThreshold !== null && bar.low < anchorThreshold);
  if (ambiguous || life.supportLoss || life.anchorLoss) {
    if (breakout.kind === 'testing') breakout = { kind: 'waiting' };
    if (retest.kind === 'testing') retest = { kind: 'waiting' };
    return {
      ...formation,
      events,
      state: { kind: 'active', ageSessions, breakout, retest },
    };
  }
  const resistance = formation.resistance;
  if (
    resistance &&
    formation.thresholds.breakout !== null &&
    breakout.kind !== 'confirmed'
  ) {
    const threshold = formation.thresholds.breakout;
    const above = bar.close > threshold;
    if (breakout.kind === 'testing')
      breakout = above
        ? advancePath(
            formation,
            breakout,
            bar,
            'resistance-breakout',
            events,
            params,
          )
        : { kind: 'waiting' };
    else if (
      above &&
      previous?.bar.kind === 'bar' &&
      previous.bar.close <= threshold
    ) {
      events.push(
        event(
          formation,
          'resistance-breakout-observed',
          bar.date,
          bar.date,
          bar.close,
          '形成后收盘首次从阈值下方穿越冻结压力与缓冲，开始连续确认',
        ),
      );
      breakout = beginPath(
        formation,
        bar,
        'resistance-breakout',
        events,
        params,
      );
    }
  }
  const support = formation.support;
  if (support && retest.kind !== 'confirmed') {
    if (retest.kind === 'testing')
      retest =
        bar.close >= support.upper
          ? advancePath(
              formation,
              retest,
              bar,
              'support-retest',
              events,
              params,
            )
          : { kind: 'waiting' };
    else if (bar.low <= support.upper && bar.high >= support.lower) {
      const recovered = bar.close >= support.upper;
      events.push(
        event(
          formation,
          'support-touch',
          bar.date,
          bar.date,
          bar.close,
          recovered
            ? '形成后价格区间实际触及冻结支撑且收盘回到区域上沿，独立开始守住确认'
            : '形成后价格区间触及冻结支撑，收盘尚未回到区域上沿，守住条件未触发',
        ),
      );
      if (recovered)
        retest = beginPath(formation, bar, 'support-retest', events, params);
    }
  }
  return {
    ...formation,
    events,
    state: { kind: 'active', ageSessions, breakout, retest },
  };
}

function event(
  formation: TriangleFormation,
  kind: TriangleEvent['kind'],
  occurredAt: string,
  knownAt: string,
  price: number,
  reason: string,
): TriangleEvent {
  return {
    id: `${formation.id}:${kind}:${occurredAt}`,
    kind,
    occurredAt,
    knownAt,
    price,
    reason,
  };
}

function beginPath(
  formation: TriangleFormation,
  bar: TradedBar,
  kind: 'resistance-breakout' | 'support-retest',
  events: TriangleEvent[],
  params: TriangleParams,
): PathStatus {
  return advancePath(
    formation,
    {
      kind: 'testing',
      startedAt: bar.date,
      observedSessions: 0,
      requiredSessions: params.confirmSessions,
    },
    bar,
    kind,
    events,
    params,
  );
}

function advancePath(
  formation: TriangleFormation,
  state: Extract<PathStatus, { kind: 'testing' }>,
  bar: TradedBar,
  kind: 'resistance-breakout' | 'support-retest',
  events: TriangleEvent[],
  params: TriangleParams,
): PathStatus {
  const observedSessions = state.observedSessions + 1;
  if (observedSessions < params.confirmSessions)
    return { ...state, observedSessions };
  const reason = `${kind === 'resistance-breakout' ? '突破冻结压力' : '触及支撑后守住区域上沿'}已连续收盘确认 ${params.confirmSessions} 日`;
  events.push(
    event(formation, kind, state.startedAt, bar.date, bar.close, reason),
  );
  return {
    kind: 'confirmed',
    occurredAt: state.startedAt,
    knownAt: bar.date,
    price: bar.close,
  };
}
