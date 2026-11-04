'use client';

import { priceScale } from '@/lib/price-geometry';
import { useEffect, useRef, useState } from 'react';
import type {
  MaPoint,
  PriceZone,
  TriangleAnalysis,
  TriangleFormation,
} from '@/lib/triangles';

const colors = {
  fast: '#b77e20',
  middle: '#8670ba',
  slow: '#3268b8',
  rise: '#b94b42',
  fall: '#27928b',
};
const gapLabels = {
  'not-traded': '当日无成交',
  'missing-row': '缺少股票日记录',
  'missing-day': '缺少日文件',
  'invalid-price': '日行情价格帧不完整',
};

export function TriangleChart({
  analysis,
  formation,
  name,
  onFormationSelect,
}: {
  analysis: TriangleAnalysis;
  formation: TriangleFormation | null;
  name: string;
  onFormationSelect?: (formation: TriangleFormation) => void;
}) {
  const [focused, setFocused] = useState<string>('');
  const viewport = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = viewport.current;
    if (element) element.scrollLeft = element.scrollWidth;
  }, [analysis]);
  const points = analysis.points;
  const current =
    points.find((point) => point.date === focused) ?? points.at(-1);
  const width = Math.max(1120, analysis.points.length * 7),
    height = 430;
  const plot = { left: 64, right: width - 84, top: 78, bottom: 336 };
  const prices = points.flatMap((point) =>
    point.bar.kind === 'bar' ? [point.bar.low, point.bar.high] : [],
  );
  for (const zone of [formation?.support, formation?.resistance]) {
    if (zone) prices.push(zone.lower, zone.upper);
  }
  if (formation)
    for (const level of Object.values(formation.thresholds))
      if (level !== null) prices.push(level);
  if (!prices.length)
    return (
      <p className="triangle-empty">
        当前范围没有完整日行情，无法绘制价格与均线。
      </p>
    );
  const scale = priceScale({prices, top: plot.top, bottom: plot.bottom});
  if (!scale) return <p className="triangle-empty">价格数据无效，无法绘制对数价格轴。</p>;
  const x = (index: number) =>
    plot.left + ((plot.right - plot.left) * (index + 0.5)) / Math.max(1, points.length);
  const y = scale.y;
  const dateIndex = new Map(points.map((point, index) => [point.date, index]));
  const xDate = (date: string) => {
    const index = dateIndex.get(date);
    return index === undefined ? null : x(index);
  };
  const candleWidth = Math.max(
    2,
    Math.min(9, ((plot.right - plot.left) / Math.max(1, points.length)) * 0.6),
  );
  const tickCount = Math.min(7, points.length);
  const dateTicks = new Set(
    Array.from({ length: tickCount }, (_, index) =>
      Math.round((index * (points.length - 1)) / Math.max(1, tickCount - 1)),
    ),
  );
  function line(key: 'fast' | 'middle' | 'slow') {
    let path = '',
      drawing = false;
    points.forEach((point, index) => {
      const value = point[key];
      if (value === null) drawing = false;
      else {
        path += `${drawing ? 'L' : 'M'}${x(index).toFixed(2)},${y(value).toFixed(2)} `;
        drawing = true;
      }
    });
    return path;
  }
  function zoneBand(zone: PriceZone | null | undefined) {
    if (!zone) return null;
    const start = xDate(zone.frozenAt);
    if (start === null) return null;
    const color = zone.kind === 'support' ? '#27928b' : '#b77e20';
    const label = zone.kind === 'support' ? 'S 支撑' : 'R 压力';
    return (
      <g key={zone.kind}>
        <rect
          x={start}
          y={y(zone.upper)}
          width={plot.right - start}
          height={Math.max(1, y(zone.lower) - y(zone.upper))}
          fill={color}
          opacity="0.09"
        />
        <line
          x1={start}
          x2={plot.right}
          y1={y(zone.center)}
          y2={y(zone.center)}
          stroke={color}
          strokeDasharray="5 4"
        />
        <text
          x={plot.right + 8}
          y={y(zone.center) - 5}
          fontSize="11"
          fill={color}
        >
          {label}
        </text>
        <text
          x={plot.right + 8}
          y={y(zone.center) + 10}
          fontSize="11"
          fill={color}
        >
          {zone.center.toFixed(2)}
        </text>
        {zone.touches.map((touch) => {
          const position = xDate(touch.occurredAt);
          return position === null ? null : (
            <circle
              key={touch.occurredAt}
              cx={position}
              cy={y(touch.price)}
              r="5"
              fill="white"
              stroke={color}
              strokeWidth="1.5"
            >
              <title>{`${label}依据 · 触点 ${touch.occurredAt} · 可知 ${touch.knownAt} · ${touch.price.toFixed(2)}元`}</title>
            </circle>
          );
        })}
      </g>
    );
  }
  const crosses = formation?.crosses ?? analysis.crosses;
  const eventNames: Record<string, string> = {
    'resistance-breakout-observed': '首次突破',
    'resistance-breakout': '突破确认',
    'support-touch': '触及支撑',
    'support-retest': '回踩确认',
    invalidated: '失效',
    expired: '到期',
    'coverage-ended': '数据中断',
  };
  function focusPoint(point: MaPoint) {
    setFocused(point.date);
  }
  return (
    <div className="triangle-chart-wrap">
      <div className="triangle-chart-legend" aria-label="图例">
        <span style={{ color: colors.fast }}>
          MA{analysis.params.fastPeriod}
        </span>
        <span style={{ color: colors.middle }}>
          MA{analysis.params.middlePeriod}
        </span>
        <span style={{ color: colors.slow }}>
          MA{analysis.params.slowPeriod}
        </span>
        <span>空心圆为历史价位依据</span>
        <span style={{ color: '#a36c13' }}>金三角 · 背景满足</span>
        <span style={{ color: '#7960ad' }}>死三角 · 背景满足</span>
        <span>点击三角文字查看当时依据</span>
        <span>方块为后续事件</span>
      </div>
      <label className="triangle-chart-date">
        查看K线日期
        <select
          aria-label="查看K线日期"
          value={current?.date ?? ''}
          onChange={(event) => setFocused(event.target.value)}
        >
          {points.map((point) => (
            <option key={point.date} value={point.date}>
              {point.date}
            </option>
          ))}
        </select>
      </label>
      <p className="triangle-chart-swipe">
        窄屏可横向滑动查看完整历史，默认显示最新点位。
      </p>
      <div className="triangle-chart-scroll" ref={viewport}>
        <svg
          className="triangle-chart-svg"
          viewBox={`0 0 ${width} ${height}`}
          style={{ minWidth: width }}
          aria-label={`${name}日K线、均线和关键点位`}
        >
          <title>{name}日K线与趋势三角</title>
          <desc>
            红涨绿跌。价格区间从冻结可知日开始延伸。交叉圆点使用日线均线观察值。缺失交易日留空，不连接均线。
          </desc>
          {scale.ticks(6).map((price, index) => {
            return (
              <g key={index}>
                <line
                  x1={plot.left}
                  x2={plot.right}
                  y1={y(price)}
                  y2={y(price)}
                  stroke="#edf0f5"
                />
                <text
                  x={plot.left - 10}
                  y={y(price) + 4}
                  textAnchor="end"
                  fill="#6a7788"
                  fontSize="11"
                >
                  {price.toFixed(2)}
                </text>
              </g>
            );
          })}
          {zoneBand(formation?.support)}
          {zoneBand(formation?.resistance)}
          {formation &&
            Object.entries(formation.thresholds)
              .filter(
                ([key, level]) => key !== 'retestRecovery' && level !== null,
              )
              .map(([key, level]) =>
                level === null ? null : (
                  <line
                    key={key}
                    x1={xDate(formation.formedAt) ?? plot.left}
                    x2={plot.right}
                    y1={y(level)}
                    y2={y(level)}
                    stroke={key === 'breakout' ? '#3268b8' : '#bc8385'}
                    strokeWidth="0.8"
                    strokeDasharray="2 5"
                  >
                    <title>{`${key === 'breakout' ? '突破确认参考阈值' : key === 'zoneInvalidation' ? '价区失效参考阈值' : '趋势锚点失效参考阈值'} ${level.toFixed(4)}元`}</title>
                  </line>
                ),
              )}
          {points.map((point, index) => {
            const bar = point.bar;
            if (bar.kind === 'gap')
              return (
                <g key={point.date}>
                  <rect
                    x={x(index) - candleWidth / 2}
                    y={plot.top}
                    width={candleWidth}
                    height={plot.bottom - plot.top}
                    fill="#e8ecf2"
                    opacity="0.45"
                  />
                  <title>{`${point.date} · ${gapLabels[bar.reason]}`}</title>
                </g>
              );
            const color = bar.close >= bar.open ? colors.rise : colors.fall;
            return (
              <g key={point.date}>
                <line
                  x1={x(index)}
                  x2={x(index)}
                  y1={y(bar.high)}
                  y2={y(bar.low)}
                  stroke={color}
                />
                <rect
                  x={x(index) - candleWidth / 2}
                  y={Math.min(y(bar.open), y(bar.close))}
                  width={candleWidth}
                  height={Math.max(1.2, Math.abs(y(bar.open) - y(bar.close)))}
                  fill={color}
                />
                <rect
                  x={x(index) - (plot.right - plot.left) / points.length / 2}
                  y={plot.top}
                  width={(plot.right - plot.left) / points.length}
                  height={plot.bottom - plot.top}
                  fill="transparent"
                  onMouseEnter={() => focusPoint(point)}
                >
                  <title>{`${point.date} 收盘${bar.close.toFixed(2)}元`}</title>
                </rect>
              </g>
            );
          })}
          {(
            ['fast', 'middle', 'slow'] satisfies ('fast' | 'middle' | 'slow')[]
          ).map((key) => (
            <path
              key={key}
              d={line(key)}
              fill="none"
              stroke={colors[key]}
              strokeWidth="1.6"
              pointerEvents="none"
            />
          ))}
          {crosses.map((cross) => {
            const position = xDate(cross.date);
            return position === null ? null : (
              <circle
                key={cross.id}
                cx={position}
                cy={y(cross.value)}
                r="4"
                fill={cross.direction === 'up' ? '#b77e20' : '#8670ba'}
                stroke="white"
              >
                <title>{`${cross.date} · ${cross.pair} ${cross.direction === 'up' ? '上穿' : '下穿'} · 均线观察值${cross.value.toFixed(2)}`}</title>
              </circle>
            );
          })}
          {analysis.formations
            .filter((item) => item.qualified)
            .map((item, index) => {
              const position = xDate(item.formedAt);
              if (position === null) return null;
              const vertical = y(item.price),
                direction = item.direction === 'golden' ? -1 : 1;
              const color = item.direction === 'golden' ? '#b77e20' : '#8670ba';
              const selected = formation?.id === item.id;
              const label = item.direction === 'golden' ? '金三角' : '死三角';
              const labelY =
                item.direction === 'golden'
                  ? 27 + (index % 3) * 17
                  : 359 + (index % 3) * 17;
              const labelX = Math.min(
                plot.right - 58,
                Math.max(plot.left + 58, position),
              );
              const trianglePoints = item.crosses
                .flatMap((cross) => {
                  const crossX = xDate(cross.date);
                  return crossX === null ? [] : [`${crossX},${y(cross.value)}`];
                })
                .join(' ');
              return (
                <g
                  key={item.id}
                  data-triangle-formation={item.id}
                  data-triangle-direction={item.direction}
                  data-triangle-qualified="true"
                  data-triangle-selected={selected ? 'true' : 'false'}
                  role={onFormationSelect ? 'button' : undefined}
                  tabIndex={onFormationSelect ? 0 : undefined}
                  aria-label={`${item.formedAt} ${label}`}
                  aria-pressed={onFormationSelect ? selected : undefined}
                  onClick={() => onFormationSelect?.(item)}
                  onKeyDown={(event) => {
                    if (
                      onFormationSelect &&
                      (event.key === 'Enter' || event.key === ' ')
                    ) {
                      event.preventDefault();
                      onFormationSelect(item);
                    }
                  }}
                  style={{ cursor: onFormationSelect ? 'pointer' : 'default' }}
                >
                  <polygon
                    points={trianglePoints}
                    fill={color}
                    fillOpacity={selected ? 0.3 : 0.13}
                    stroke={color}
                    strokeWidth={selected ? 2 : 1}
                  />
                  <path
                    d={`M${position},${vertical} l-7,${direction * 13} h14 Z`}
                    fill={color}
                    stroke="white"
                  />
                  <line
                    x1={position}
                    y1={vertical + direction * 14}
                    x2={labelX}
                    y2={labelY + (direction < 0 ? 4 : -12)}
                    stroke={color}
                    strokeDasharray="2 3"
                    opacity="0.6"
                  />
                  <rect
                    x={labelX - 58}
                    y={labelY - 12}
                    width="116"
                    height="16"
                    rx="3"
                    fill={selected ? color : '#fff'}
                    stroke={color}
                    strokeWidth={selected ? 1.5 : 0.7}
                  />
                  <text
                    x={labelX}
                    y={labelY}
                    textAnchor="middle"
                    fontSize="10"
                    fill={selected ? '#fff' : color}
                  >
                    {label} {item.formedAt}
                  </text>
                  <title>{`${item.formedAt} ${item.direction === 'golden' ? '金三角' : '死三角'} · ${item.price.toFixed(2)}元 · ${item.qualified ? '背景满足' : item.reasons.join('；')}`}</title>
                </g>
              );
            })}
          {[formation?.pretrend.from, formation?.pretrend.to].map(
            (anchor, index) => {
              if (!anchor) return null;
              const position = xDate(anchor.date);
              return position === null ? null : (
                <g key={`${anchor.date}-${index}`}>
                  <circle
                    cx={position}
                    cy={y(anchor.price)}
                    r="4.5"
                    fill="white"
                    stroke="#51627a"
                  />
                  <text
                    x={position + 7}
                    y={y(anchor.price) + (anchor.source === 'high' ? -8 : 16)}
                    fontSize="11"
                    fill="#51627a"
                  >
                    {anchor.source === 'high' ? '前高' : '前低'}{' '}
                    {anchor.price.toFixed(2)}
                  </text>
                  <title>{`${anchor.date} · 前期幅度依据`}</title>
                </g>
              );
            },
          )}
          {formation?.events
            .filter((event) => event.kind !== 'formed' && event.price !== null)
            .map((event, index) => {
              const position = xDate(event.knownAt);
              if (position === null || event.price === null) return null;
              const observedBar = points.find(
                (point) => point.date === event.knownAt,
              )?.bar;
              const markerPrice =
                event.kind === 'support-touch' && observedBar?.kind === 'bar'
                  ? observedBar.low
                  : event.price;
              const positive =
                event.kind === 'resistance-breakout' ||
                event.kind === 'support-retest';
              const color = positive ? '#27928b' : '#9a6770';
              return (
                <g key={event.id}>
                  <rect
                    x={position - 4}
                    y={y(markerPrice) - 4}
                    width="8"
                    height="8"
                    fill={color}
                    stroke="white"
                  />
                  <text
                    x={position + 7}
                    y={y(markerPrice) - 9 - (index % 2) * 12}
                    fill={color}
                    fontSize="10"
                  >
                    {eventNames[event.kind] ?? event.kind}
                  </text>
                  <title>{`${event.reason} · 发生 ${event.occurredAt} · 可知 ${event.knownAt} · 标记价${markerPrice.toFixed(2)}元 · 收盘${event.price.toFixed(2)}元`}</title>
                </g>
              );
            })}
          {current && xDate(current.date) !== null && (
            <line
              x1={xDate(current.date) ?? 0}
              x2={xDate(current.date) ?? 0}
              y1={plot.top}
              y2={plot.bottom}
              stroke="#a8b6c9"
              strokeDasharray="2 4"
              pointerEvents="none"
            />
          )}
          {points
            .filter((_, index) => dateTicks.has(index))
            .map((point) => (
              <text
                key={point.date}
                x={xDate(point.date) ?? 0}
                y={height - 8}
                textAnchor="middle"
                fill="#6a7788"
                fontSize="11"
              >
                {point.date.slice(5)}
              </text>
            ))}
          <text x={plot.left} y="17" fontSize="11" fill="#6a7788">
            对数价格轴 · 不复权 · 元/股
          </text>
        </svg>
      </div>
      <output className="triangle-chart-readout" aria-live="polite">
        {current && (
          <>
            <strong>{current.date}</strong>
            {current.bar.kind === 'bar' ? (
              <span>
                开 {current.bar.open.toFixed(2)} · 高{' '}
                {current.bar.high.toFixed(2)} · 低 {current.bar.low.toFixed(2)}{' '}
                · 收 {current.bar.close.toFixed(2)}
              </span>
            ) : (
              <span>{gapLabels[current.bar.reason]}</span>
            )}
            <span>
              MA{analysis.params.fastPeriod}{' '}
              {current.fast?.toFixed(2) ?? '未计算'} · MA
              {analysis.params.middlePeriod}{' '}
              {current.middle?.toFixed(2) ?? '未计算'} · MA
              {analysis.params.slowPeriod}{' '}
              {current.slow?.toFixed(2) ?? '未计算'}
            </span>
          </>
        )}
      </output>
    </div>
  );
}
