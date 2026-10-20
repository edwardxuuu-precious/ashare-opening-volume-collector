import { filterRows, type DayData, type Filters, type Manifest } from './stock';
type Tool = {
  name: string;
  title: string;
  description: string;
  inputSchema: object;
  annotations: { readOnlyHint: boolean; untrustedContentHint: boolean };
  execute: (input: unknown) => unknown;
};
type Context = {
  registerTool: (
    tool: Tool,
    options: { signal: AbortSignal },
  ) => void | Promise<void>;
};
export function registerStockTools(
  doc: Document,
  manifest: Manifest | null,
  day: DayData | null,
  filters: Filters,
  apply: (filters: Filters) => void,
) {
  const context = (doc as Document & { modelContext?: Context }).modelContext;
  if (!context?.registerTool) return;
  const lifetime = new AbortController();
  const tool: Tool = {
    name: 'filter_opening_volume',
    title: '筛选开盘量占比',
    description:
      '在当前已载入单日或日期区间筛选股票并更新网页，返回前20条结果及数据覆盖；不会触发行情采集。',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', maxLength: 80 },
        stockScope: { type: 'string', enum: ['single', 'multiple', 'all'] },
        symbols: {
          type: 'array',
          items: { type: 'string', pattern: '^[0-9]{6}$' },
          uniqueItems: true,
        },
        market: { type: 'string', enum: ['all', 'SH', 'SZ', 'BJ'] },
        min: { type: 'number', minimum: 0, maximum: 100 },
        order: {
          type: 'string',
          enum: ['asc', 'desc', 'date_desc', 'date_asc'],
        },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, untrustedContentHint: true },
    execute(input) {
      if (!day || !manifest) throw Error('当前交易日数据尚未载入');
      if (!input || typeof input !== 'object' || Array.isArray(input))
        throw Error('参数必须是对象');
      const p = input as Record<string, unknown>;
      if (
        Object.keys(p).some(
          (k) =>
            ![
              'query',
              'market',
              'min',
              'order',
              'stockScope',
              'symbols',
            ].includes(k),
        )
      )
        throw Error('含不支持的参数');
      if (
        p.query !== undefined &&
        (typeof p.query !== 'string' || p.query.length > 80)
      )
        throw Error('搜索内容不正确');
      if (
        p.market !== undefined &&
        (typeof p.market !== 'string' ||
          !['all', 'SH', 'SZ', 'BJ'].includes(p.market))
      )
        throw Error('市场不正确');
      if (
        p.min !== undefined &&
        (typeof p.min !== 'number' ||
          !Number.isFinite(p.min) ||
          p.min < 0 ||
          p.min > 100)
      )
        throw Error('最低占比应在0到100之间');
      if (
        p.order !== undefined &&
        (typeof p.order !== 'string' ||
          !['asc', 'desc', 'date_desc', 'date_asc'].includes(p.order))
      )
        throw Error('排序不正确');
      if (
        p.stockScope !== undefined &&
        (typeof p.stockScope !== 'string' ||
          !['single', 'multiple', 'all'].includes(p.stockScope))
      )
        throw Error('股票范围不正确');
      if (
        p.symbols !== undefined &&
        (!Array.isArray(p.symbols) ||
          p.symbols.some((s) => typeof s !== 'string' || !/^\d{6}$/.test(s)))
      )
        throw Error('股票代码必须为6位字符串');
      const next = { ...filters, ...p } as Filters;
      next.symbols =
        next.stockScope === 'all' ? [] : [...new Set(next.symbols)];
      if (next.stockScope === 'single' && next.symbols.length !== 1)
        throw Error('单只股票模式必须选择1只股票');
      const result = filterRows(day.rows, next);
      apply(next);
      return {
        date: day.date,
        dates: day.dates,
        scope: day.scope,
        total: day.total,
        matched: result.length,
        rows: result.slice(0, 20),
        source: day.source,
      };
    },
  };
  try {
    void Promise.resolve(
      context.registerTool(tool, { signal: lifetime.signal }),
    ).catch(() => {});
  } catch {
    /* Optional API unsupported: normal controls remain available. */
  }
  return () => lifetime.abort();
}
