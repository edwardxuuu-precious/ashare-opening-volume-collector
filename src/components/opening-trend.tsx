'use client';
import { useMemo } from 'react';
import { Line, LineChart, CartesianGrid, XAxis, YAxis } from 'recharts';
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart';
import { buildTrend } from '@/lib/chart-data';
import type { StockRow } from '@/lib/stock';
import { InfoHint } from '@/components/info-hint';
const palette = ['#3268b8', '#27928b', '#c18a39', '#b5697b', '#8375b1'];
export function OpeningTrend({
  rows,
  dates,
  symbols,
}: {
  rows: StockRow[];
  dates: string[];
  symbols: string[] | null;
}) {
  const { points, series, individual, axisTop } = useMemo(
    () => buildTrend(rows, dates, symbols),
    [rows, dates, symbols],
  );
  const config = Object.fromEntries(
    series.map((s, i) => [
      s.key,
      { label: s.name, color: palette[i % palette.length] },
    ]),
  );
  const hasValues = points.some((p) => series.some((s) => p[s.key] !== null));
  return (
    <section className="trend-panel" aria-label="逐日开盘占比趋势">
      <div className="trend-heading">
        <div>
          <h3>{individual ? '开盘占比' : '每日占比中位数'}</h3>
          <InfoHint label="趋势口径">
            {individual
              ? '按所选交易日绘制。缺失记录留空，不补零、不跨空值连线。'
              : '各交易日分别计算不加权中位数，样本数量见图中提示。缺失记录留空。'}
          </InfoHint>
        </div>
        <span>%</span>
      </div>
      {hasValues ? (
        <ChartContainer className="trend-chart" config={config}>
          <LineChart
            data={points}
            margin={{ top: 12, right: 16, bottom: 0, left: 0 }}
            accessibilityLayer
          >
            <CartesianGrid vertical={false} strokeDasharray="3 5" />
            <XAxis
              dataKey="date"
              tickFormatter={(value) =>
                String(value).slice(5).replace('-', '/')
              }
              axisLine={false}
              tickLine={false}
              minTickGap={32}
              tickMargin={12}
              fontSize={12}
            />
            <YAxis
              domain={[0, axisTop]}
              ticks={[0, axisTop / 4, axisTop / 2, (axisTop * 3) / 4, axisTop]}
              axisLine={false}
              tickLine={false}
              width={40}
              fontSize={12}
              tickFormatter={(value) => `${value}`}
            />
            <ChartTooltip
              content={
                <ChartTooltipContent
                  className="trend-tooltip"
                  labelFormatter={(_, payload) => {
                    const row = payload?.[0]?.payload as
                      | { date?: string; validCount?: number }
                      | undefined;
                    return (
                      <span>
                        {row?.date}
                        {!individual && ` · ${row?.validCount ?? 0} 条可计算`}
                      </span>
                    );
                  }}
                  formatter={(value, name) => (
                    <div className="trend-tooltip-value">
                      <span style={{ color: config[String(name)]?.color }}>
                        {config[String(name)]?.label ?? name}
                      </span>
                      <strong>{Number(value).toFixed(2)}%</strong>
                    </div>
                  )}
                />
              }
            />
            {series.map((s, i) => (
              <Line
                key={s.key}
                name={s.key}
                type="linear"
                dataKey={s.key}
                stroke={palette[i % palette.length]}
                strokeWidth={2.5}
                dot={
                  points.length <= 8
                    ? { r: 3, strokeWidth: 2, fill: '#fff' }
                    : false
                }
                activeDot={{ r: 5, strokeWidth: 3, stroke: '#fff' }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ChartContainer>
      ) : (
        <p className="trend-empty">暂无可计算占比</p>
      )}
      <div className="trend-bottom">
        <div className="trend-legend">
          {series.map((s, i) => (
            <span key={s.key}>
              <i style={{ background: palette[i % palette.length] }} />
              {s.name}
              {s.code && <small>{s.code}</small>}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}
