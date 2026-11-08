'use client';

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type PointerEvent,
} from 'react';
import type { CycleComparison, MonthlyPoint } from '@/lib/cycle-bottom-data';

export type CycleWindow = 'all' | '10' | '5';
export type CycleChartMode = 'fit' | 'raw';

function useChartWidth() {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(960);
  useEffect(() => {
    const node = container.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(280, Math.round(entry.contentRect.width)));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return { container, width };
}

function cutoffDate(last: string, window: CycleWindow) {
  if (window === 'all') return '';
  return `${Number(last.slice(0, 4)) - Number(window)}${last.slice(4)}`;
}

function pct(value: number) {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function number(value: number) {
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function chartScale(values: number[], height: number, logarithmic: boolean) {
  const transform = (value: number) => (logarithmic ? Math.log(value) : value);
  const transformed = values.map(transform);
  const lo = Math.min(...transformed),
    hi = Math.max(...transformed);
  const span = hi - lo || 1;
  const bottom = lo - span * 0.07,
    top = hi + span * 0.09;
  return {
    y: (value: number) =>
      22 + ((top - transform(value)) / (top - bottom)) * height,
    ticks: Array.from({ length: 5 }, (_, index) => {
      const value = top - ((top - bottom) * index) / 4;
      return {
        y: 22 + (height * index) / 4,
        value: logarithmic ? Math.exp(value) : value,
      };
    }),
  };
}

function linePath<T>(
  points: T[],
  x: (index: number) => number,
  y: (value: number) => number,
  value: (point: T) => number,
) {
  return points
    .map(
      (point, index) =>
        `${index ? 'L' : 'M'}${x(index).toFixed(2)},${y(value(point)).toFixed(2)}`,
    )
    .join(' ');
}

function bandPath<T>(
  points: T[],
  x: (index: number) => number,
  y: (value: number) => number,
  lower: (point: T) => number,
  upper: (point: T) => number,
) {
  const top = linePath(points, x, y, upper);
  const bottom = points
    .map((point, index) => ({ point, index }))
    .reverse()
    .map(
      ({ point, index }) =>
        `L${x(index).toFixed(2)},${y(lower(point)).toFixed(2)}`,
    )
    .join(' ');
  return `${top} ${bottom} Z`;
}

function xTicks<T>(points: T[], width: number) {
  const count = Math.max(2, Math.min(7, Math.floor(width / 145)));
  return Array.from({ length: count }, (_, index) =>
    Math.round((index * (points.length - 1)) / (count - 1)),
  );
}

function pointerIndex(
  event: PointerEvent<SVGSVGElement>,
  width: number,
  left: number,
  plotWidth: number,
  count: number,
) {
  const rect = event.currentTarget.getBoundingClientRect();
  const position = ((event.clientX - rect.left) * width) / rect.width;
  return Math.max(
    0,
    Math.min(
      count - 1,
      Math.round(((position - left) / plotWidth) * (count - 1)),
    ),
  );
}

export function CycleLineChart({
  comparison,
  mode,
  window,
  showReference,
  logarithmic,
  focusDate,
}: {
  comparison: CycleComparison;
  mode: CycleChartMode;
  window: CycleWindow;
  showReference: boolean;
  logarithmic: boolean;
  focusDate: string | null;
}) {
  const { container, width } = useChartWidth();
  const titleId = useId();
  const points = useMemo(() => {
    const cutoff = cutoffDate(comparison.asOf, window);
    return comparison.points.filter((point) => point.date >= cutoff);
  }, [comparison, window]);
  const [active, setActive] = useState<number | null>(null);
  useEffect(() => {
    setActive(null);
  }, [comparison.id, window]);
  useEffect(() => {
    if (!focusDate) return;
    const index = points.findIndex((point) => point.date === focusDate);
    if (index >= 0) setActive(index);
  }, [focusDate, points]);
  const selected = points[active ?? points.length - 1];
  const height = width < 560 ? 270 : 315;
  const left = width < 560 ? 44 : 52,
    right = 18;
  const plotWidth = width - left - right;
  const x = (index: number) =>
    left + (index / Math.max(1, points.length - 1)) * plotWidth;
  const values = points.flatMap((point) =>
    mode === 'raw'
      ? [point.equity, point.reference]
      : [
          point.equity,
          point.lower,
          point.upper,
          ...(showReference ? [point.reference] : []),
        ],
  );
  const scale = chartScale(values, height, logarithmic);
  if (!selected) return <p className="cl-empty">所选区间没有共同日期数据。</p>;
  const selectedIndex = active ?? points.length - 1;
  const legends =
    mode === 'raw'
      ? [
          { color: '#3268b8', label: '沪深300全收益' },
          { color: '#7f8ba0', label: comparison.name },
        ]
      : [
          { color: '#3268b8', label: '沪深300全收益' },
          { color: '#2b9288', label: '历史低位拟合参照' },
          { color: '#c6e1dc', label: '历史残差带' },
          ...(showReference
            ? [{ color: '#7f8ba0', label: '未缩放参照指数' }]
            : []),
        ];
  return (
    <div className="cl-chart" ref={container}>
      <div className="cl-chart-legend">
        {legends.map((item) => (
          <span key={item.label}>
            <i style={{ background: item.color }} />
            {item.label}
          </span>
        ))}
      </div>
      <div className="cl-chart-readout" aria-live="polite">
        <strong>{selected.date}</strong>
        <span>
          沪深300 <b>{number(selected.equity)}</b>
        </span>
        <span>
          {mode === 'fit' ? '拟合参照' : '债券参照'}{' '}
          <b>{number(mode === 'fit' ? selected.fitted : selected.reference)}</b>
        </span>
        <span>
          差距{' '}
          <b>{pct(mode === 'fit' ? selected.gapPct : selected.rawGapPct)}</b>
        </span>
      </div>
      <svg
        className="cl-data-svg"
        viewBox={`0 0 ${width} ${height + 62}`}
        role="img"
        aria-labelledby={titleId}
        onPointerMove={(event) => {
          if (event.pointerType === 'mouse' || event.buttons === 1)
            setActive(
              pointerIndex(event, width, left, plotWidth, points.length),
            );
        }}
        onPointerDown={(event) =>
          setActive(pointerIndex(event, width, left, plotWidth, points.length))
        }
      >
        <title id={titleId}>
          {mode === 'fit'
            ? '真实沪深300全收益与历史低位拟合参照'
            : '同一基期归一化的真实股债序列'}
          ，{points[0].date}至{comparison.asOf}，基期{comparison.baseDate}
          等于100。
        </title>
        {scale.ticks.map((tick, index) => (
          <g key={index}>
            <line
              x1={left}
              x2={width - right}
              y1={tick.y}
              y2={tick.y}
              stroke="#e5ebf2"
              strokeDasharray="3 5"
            />
            <text
              x={left - 9}
              y={tick.y + 4}
              textAnchor="end"
              className="cl-chart-tick"
            >
              {Math.round(tick.value).toLocaleString('zh-CN')}
            </text>
          </g>
        ))}
        {mode === 'fit' && (
          <path
            d={bandPath(
              points,
              x,
              scale.y,
              (point) => point.lower,
              (point) => point.upper,
            )}
            fill="#a2cfc6"
            fillOpacity="0.24"
          />
        )}
        {(mode === 'raw' || showReference) && (
          <path
            d={linePath(points, x, scale.y, (point) => point.reference)}
            fill="none"
            stroke="#8a94a5"
            strokeWidth="1.45"
          />
        )}
        {mode === 'fit' && (
          <path
            d={linePath(points, x, scale.y, (point) => point.fitted)}
            fill="none"
            stroke="#2b9288"
            strokeWidth="1.8"
          />
        )}
        <path
          d={linePath(points, x, scale.y, (point) => point.equity)}
          fill="none"
          stroke="#3268b8"
          strokeWidth="1.65"
        />
        <line
          x1={x(selectedIndex)}
          x2={x(selectedIndex)}
          y1={22}
          y2={height + 22}
          stroke="#9babc0"
          strokeDasharray="3 4"
        />
        <circle
          cx={x(selectedIndex)}
          cy={scale.y(selected.equity)}
          r="3.4"
          fill="#3268b8"
          stroke="white"
          strokeWidth="1.4"
        />
        {xTicks(points, width).map((index) => (
          <text
            key={index}
            x={x(index)}
            y={height + 45}
            textAnchor={
              index === 0
                ? 'start'
                : index === points.length - 1
                  ? 'end'
                  : 'middle'
            }
            className="cl-chart-tick"
          >
            {points[index].date.slice(0, 7)}
          </text>
        ))}
      </svg>
      <div className="cl-chart-date-control">
        <span>选择观察日</span>
        <input
          type="range"
          aria-label="选择股债图中日期"
          min={0}
          max={points.length - 1}
          value={selectedIndex}
          onChange={(event) => setActive(Number(event.target.value))}
        />
        <output>{selected.date}</output>
      </div>
      <p className="cl-chart-caption">
        共同基期 {comparison.baseDate} = 100 · {points.length.toLocaleString()}{' '}
        个共同观察日 · 缺失日不补值。
        {mode === 'fit'
          ? '拟合带表示训练期残差范围，不是置信区间。'
          : '曲线相等仅表示从该共同基期出发的累计回报相等。'}
      </p>
    </div>
  );
}

export function CycleMonthlyChart({
  points: allPoints,
  window,
  logarithmic,
}: {
  points: MonthlyPoint[];
  window: CycleWindow;
  logarithmic: boolean;
}) {
  const { container, width } = useChartWidth();
  const titleId = useId();
  const points = useMemo(() => {
    const last = allPoints.at(-1);
    const cutoff = last ? cutoffDate(last.date, window) : '';
    return allPoints.filter((point) => point.date >= cutoff);
  }, [allPoints, window]);
  const [active, setActive] = useState<number | null>(null);
  useEffect(() => {
    setActive(null);
  }, [window]);
  const selected = points[active ?? points.length - 1];
  if (!selected) return <p className="cl-empty">月线数据尚未发布。</p>;
  const height = width < 560 ? 275 : 325;
  const left = width < 560 ? 48 : 57,
    right = 17;
  const plotWidth = width - left - right;
  const x = (index: number) =>
    left + (index / Math.max(1, points.length - 1)) * plotWidth;
  const scale = chartScale(
    points.flatMap((point) => [
      point.low,
      point.high,
      point.lower,
      point.upper,
    ]),
    height,
    logarithmic,
  );
  const candleWidth = Math.max(
    0.65,
    Math.min(7, (plotWidth / points.length) * 0.6),
  );
  const selectedIndex = active ?? points.length - 1;
  return (
    <div className="cl-chart" ref={container}>
      <div className="cl-chart-legend">
        <span>
          <i className="cl-candle-legend" />
          上证真实月K
        </span>
        <span>
          <i style={{ background: '#2b9288' }} />
          拟合下沿
        </span>
        <span>
          <i style={{ background: '#a5afbd' }} />
          中位线
        </span>
        <span>
          <i style={{ background: '#c39453' }} />
          拟合上沿
        </span>
      </div>
      <div className="cl-chart-readout cl-monthly-readout" aria-live="polite">
        <strong>
          {selected.month}
          {selected.incomplete && <em>进行中</em>}
        </strong>
        <span>
          开 <b>{number(selected.open)}</b>
        </span>
        <span>
          高 <b>{number(selected.high)}</b>
        </span>
        <span>
          低 <b>{number(selected.low)}</b>
        </span>
        <span>
          收 <b>{number(selected.close)}</b>
        </span>
        <span>
          下沿 <b>{number(selected.lower)}</b>
        </span>
      </div>
      <svg
        className="cl-data-svg"
        viewBox={`0 0 ${width} ${height + 62}`}
        role="img"
        aria-labelledby={titleId}
        onPointerMove={(event) => {
          if (event.pointerType === 'mouse' || event.buttons === 1)
            setActive(
              pointerIndex(event, width, left, plotWidth, points.length),
            );
        }}
        onPointerDown={(event) =>
          setActive(pointerIndex(event, width, left, plotWidth, points.length))
        }
      >
        <title id={titleId}>
          上证指数真实月K与历史拟合通道，{points[0].month}至
          {points.at(-1)?.month}
          。本月未结束时标明进行中。
        </title>
        {scale.ticks.map((tick, index) => (
          <g key={index}>
            <line
              x1={left}
              x2={width - right}
              y1={tick.y}
              y2={tick.y}
              stroke="#e5ebf2"
              strokeDasharray="3 5"
            />
            <text
              x={left - 9}
              y={tick.y + 4}
              textAnchor="end"
              className="cl-chart-tick"
            >
              {Math.round(tick.value).toLocaleString('zh-CN')}
            </text>
          </g>
        ))}
        <path
          d={bandPath(
            points,
            x,
            scale.y,
            (point) => point.lower,
            (point) => point.upper,
          )}
          fill="#dce6ed"
          fillOpacity="0.18"
        />
        <path
          d={linePath(points, x, scale.y, (point) => point.lower)}
          fill="none"
          stroke="#2b9288"
          strokeWidth="1.6"
        />
        <path
          d={linePath(points, x, scale.y, (point) => point.middle)}
          fill="none"
          stroke="#a5afbd"
          strokeWidth="1.2"
          strokeDasharray="5 4"
        />
        <path
          d={linePath(points, x, scale.y, (point) => point.upper)}
          fill="none"
          stroke="#c39453"
          strokeWidth="1.5"
        />
        {points.map((point, index) => {
          const color = point.close >= point.open ? '#bd625e' : '#3c9483';
          const top = Math.min(scale.y(point.open), scale.y(point.close));
          return (
            <g key={point.month}>
              <line
                x1={x(index)}
                x2={x(index)}
                y1={scale.y(point.high)}
                y2={scale.y(point.low)}
                stroke={color}
                strokeWidth={0.9}
              />
              <rect
                x={x(index) - candleWidth / 2}
                y={top}
                width={candleWidth}
                height={Math.max(
                  0.9,
                  Math.abs(scale.y(point.open) - scale.y(point.close)),
                )}
                fill={color}
                fillOpacity={point.incomplete ? 0.6 : 0.9}
              />
            </g>
          );
        })}
        <line
          x1={x(selectedIndex)}
          x2={x(selectedIndex)}
          y1={22}
          y2={height + 22}
          stroke="#9babc0"
          strokeDasharray="3 4"
        />
        {xTicks(points, width).map((index) => (
          <text
            key={index}
            x={x(index)}
            y={height + 45}
            textAnchor={
              index === 0
                ? 'start'
                : index === points.length - 1
                  ? 'end'
                  : 'middle'
            }
            className="cl-chart-tick"
          >
            {points[index].month}
          </text>
        ))}
      </svg>
      <div className="cl-chart-date-control">
        <span>选择月份</span>
        <input
          type="range"
          aria-label="选择月线图中月份"
          min={0}
          max={points.length - 1}
          value={selectedIndex}
          onChange={(event) => setActive(Number(event.target.value))}
        />
        <output>{selected.month}</output>
      </div>
      <p className="cl-chart-caption">
        月K由实际日行情聚合。通道来自固定历史样本的对数时间趋势与残差分位，训练期之后为外推观察；通道下沿不代表不会被跌破。
      </p>
    </div>
  );
}
