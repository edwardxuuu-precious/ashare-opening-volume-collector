'use client';
/* oxlint-disable jsx-a11y/prefer-tag-over-role -- A dynamic SVG chart needs its own accessible image role. */

import { useEffect, useRef, useState } from 'react';
import { priceScale } from '@/lib/price-geometry';
import {
  boundaryAt,
  type VerifiedVideoStock,
  type VideoObservation,
  type SpaceLine,
} from '@/lib/video-space';

const price = (value: number) =>
  value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  });
export function VideoSpaceChart({
  stock,
  observation,
  expanded,
  overlays,
}: {
  stock: VerifiedVideoStock;
  observation: Extract<VideoObservation, { kind: 'ready' }>;
  expanded: boolean;
  overlays: readonly {
    id: string;
    label: string;
    style: 'comparison' | 'parent' | 'root' | 'fixed';
    observation: Extract<VideoObservation, { kind: 'ready' }>;
  }[];
}) {
  const ref = useRef<SVGSVGElement>(null),
    [width, setWidth] = useState(900);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const measure = () =>
      setWidth(Math.max(240, node.getBoundingClientRect().width));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const bars = stock.history
    .filter((b) => b.date <= observation.date)
    .slice(expanded ? -120 : -40);
  const guides: SpaceLine[] = expanded
    ? Array.from({ length: 13 }, (_, i) => ({
        k: i / 2,
        price: boundaryAt(observation.ruler, i / 2),
      }))
    : [...observation.spaceLines];
  if (!guides.some((g) => g.k === observation.reference.k))
    guides.push(observation.reference);
  const overlayLines = overlays.map((overlay) => ({
    ...overlay,
    lines: [
      overlay.observation.location.upper,
      overlay.observation.location.middle,
      overlay.observation.location.lower,
      overlay.observation.reference,
    ].filter((g, i, all) => all.findIndex((x) => x.k === g.k) === i),
  }));
  const height = 340,
    left = 12,
    right = width - 104,
    top = 25,
    bottom = 270;
  const scale = priceScale({
    prices: bars
      .flatMap((b) => [Number(b.low), Number(b.high)])
      .concat(
        guides.map((g) => g.price),
        overlayLines.flatMap((o) => o.lines.map((g) => g.price)),
      ),
    top,
    bottom,
  });
  if (!scale || !bars.length) return <p role="alert">价格轴不可用，待核。</p>;
  const step = (right - left) / bars.length,
    x = (i: number) => left + (i + 0.5) * step;
  const firstLine = bars.findIndex((b) => b.date >= observation.startDate);
  const startX = firstLine < 0 ? right : x(firstLine);
  const labels = guides
    .map((g) => ({ ...g, y: scale.y(g.price) }))
    .sort((a, b) => a.y - b.y);
  for (let i = 0; i < labels.length; i++)
    labels[i].y = Math.max(labels[i].y, i ? labels[i - 1].y + 19 : top + 4);
  for (let i = labels.length - 1; i >= 0; i--)
    labels[i].y = Math.min(
      labels[i].y,
      i < labels.length - 1 ? labels[i + 1].y - 19 : bottom,
    );
  return (
    <figure className="vs-chart">
      <figcaption>
        截至 {observation.date} · LOG 对数价格轴 · {bars.length} 根实际日线 ·
        红涨绿跌
      </figcaption>
      <svg
        ref={ref}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        data-price-geometry="log-price.v1"
        aria-label={`${stock.code} ${observation.date} ${observation.source.kind === 'HALF' ? '继承半格' : observation.source.kind === 'WAVE' ? '结构波段' : observation.source.kind === 'LOWER' ? '下降平台' : observation.source.kind === 'AUTO' ? '自动平台（试验）' : '固定视频空间'}与真实日K线`}
      >
        {scale.ticks(5).map((p, i) => (
          <g key={i}>
            <line
              x1={left}
              x2={right}
              y1={scale.y(p)}
              y2={scale.y(p)}
              stroke="var(--ui-border)"
            />
            <text
              x={left}
              y={scale.y(p) - 5}
              fontSize="10"
              fill="var(--ui-muted)"
            >
              {price(p)}
            </text>
          </g>
        ))}
        <rect
          x={startX}
          y={scale.y(observation.location.upper.price)}
          width={Math.max(0, right - startX)}
          height={
            scale.y(observation.location.lower.price) -
            scale.y(observation.location.upper.price)
          }
          fill="var(--ui-selected)"
          opacity=".65"
        />
        {bars.map((b, i) => {
          const color =
              Number(b.close) >= Number(b.open)
                ? 'var(--ui-rise)'
                : 'var(--ui-fall)',
            body = Math.max(1, Math.min(10, step * 0.65));
          return (
            <g key={b.date} data-candle-date={b.date}>
              <title>
                {b.date} 开{b.open} 高{b.high} 低{b.low} 收{b.close}
              </title>
              <line
                x1={x(i)}
                x2={x(i)}
                y1={scale.y(Number(b.high))}
                y2={scale.y(Number(b.low))}
                stroke={color}
              />
              <rect
                x={x(i) - body / 2}
                y={Math.min(scale.y(Number(b.open)), scale.y(Number(b.close)))}
                width={body}
                height={Math.max(
                  1,
                  Math.abs(scale.y(Number(b.open)) - scale.y(Number(b.close))),
                )}
                fill={color}
              />
              {i %
                Math.ceil(
                  bars.length / Math.max(2, Math.floor((right - left) / 70)),
                ) ===
                0 && (
                <text
                  x={x(i)}
                  y={height - 35}
                  textAnchor="middle"
                  fontSize="10"
                  fill="var(--ui-muted)"
                >
                  {b.date.slice(5)}
                </text>
              )}
            </g>
          );
        })}
        {overlayLines.flatMap((overlay) => {
          const index = bars.findIndex(
            (b) => b.date >= overlay.observation.startDate,
          );
          const start = index < 0 ? right : x(index);
          return overlay.lines.map((g) => (
            <line
              key={`${overlay.id}-${g.k}`}
              data-comparison-k={g.k}
              data-overlay-id={overlay.id}
              data-price={g.price}
              x1={start}
              x2={right}
              y1={scale.y(g.price)}
              y2={scale.y(g.price)}
              stroke={
                overlay.style === 'root'
                  ? '#a4611a'
                  : overlay.style === 'fixed'
                    ? '#3b7a78'
                    : '#a352a8'
              }
              strokeWidth={g.k === overlay.observation.reference.k ? 2 : 1}
              strokeDasharray={
                overlay.style === 'root'
                  ? '10 3 2 3'
                  : overlay.style === 'fixed'
                    ? '12 5'
                    : overlay.style === 'parent'
                      ? '2 4'
                      : '7 3'
              }
            />
          ));
        })}
        {guides.map((g) => {
          const label = labels.find((l) => l.k === g.k),
            y = scale.y(g.price),
            reference = g.k === observation.reference.k;
          return (
            <g
              key={g.k}
              data-boundary-k={g.k}
              data-price={g.price}
              data-start={observation.startDate}
            >
              <line
                x1={startX}
                x2={right}
                y1={y}
                y2={y}
                stroke={reference ? 'var(--ui-accent)' : '#7f8e9e'}
                strokeWidth={reference ? 2 : 1}
                strokeDasharray={Number.isInteger(g.k) ? undefined : '5 4'}
              />
              <line
                x1={right}
                x2={right + 6}
                y1={y}
                y2={label?.y ?? y}
                stroke="#a1aebb"
              />
              <text
                x={right + 9}
                y={(label?.y ?? y) + 4}
                fontSize="11"
                fill={reference ? 'var(--ui-accent)' : 'var(--ui-muted)'}
              >
                {observation.source.kind === 'HALF' ? 'S' : 'B'}({g.k}){' '}
                {price(g.price)}
              </text>
            </g>
          );
        })}
        {expanded &&
          observation.source.endpoints.kind === 'REAL' &&
          [
            { ...observation.source.endpoints.high, label: 'H' },
            { ...observation.source.endpoints.low, label: 'L' },
          ].map((point) => {
            const i = bars.findIndex((b) => b.date === point.date);
            if (i < 0) return null;
            const px = x(i),
              py = scale.y(Number(point.price));
            return (
              <g
                key={point.label}
                data-anchor={point.label}
                data-date={point.date}
                data-price={point.price}
              >
                <circle cx={px} cy={py} r="4" fill="var(--ui-accent)" />
                <text
                  x={Math.max(left, Math.min(right - 108, px + 6))}
                  y={Math.max(top + 12, py - 9)}
                  fill="var(--ui-accent)"
                  fontSize="11"
                >
                  {point.label} {point.date.slice(5)} {point.price}
                </text>
              </g>
            );
          })}
        <text x={left} y={height - 10} fontSize="10" fill="var(--ui-muted)">
          主尺：实线整格、虚线半格 · 蓝线为共同参考
        </text>
      </svg>
      {overlays.length > 0 && (
        <ul className="vs-overlay-legend" aria-label="图表对照尺图例">
          {overlays.map((overlay) => (
            <li key={overlay.id}>
              <span
                aria-hidden="true"
                className={`vs-overlay-swatch vs-overlay-${overlay.style}`}
              />
              {overlay.label} ·{' '}
              {overlay.style === 'root'
                ? '长短线'
                : overlay.style === 'fixed'
                  ? '长虚线'
                  : overlay.style === 'parent'
                    ? '点线'
                    : '虚线'}
            </li>
          ))}
        </ul>
      )}
      <p>
        水平线从 {observation.startDate} 开始。
        {overlays.length > 0 &&
          `${overlays.map((o) => o.label).join('、')}与主尺共用同一 LOG 价格轴。`}
        日线不推断盘中路径。
      </p>
    </figure>
  );
}
