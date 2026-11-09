'use client';

import { Fragment, useEffect, useState } from 'react';
import { SiteHeader } from '@/components/site-header';
import { SiteLink } from '@/components/site-router';
import { WorkbenchPageBar } from '@/components/workbench';
import { VideoSpaceChart } from '@/components/video-space-chart';
import {
  FROZEN_DATASET,
  VIDEO_RULE_VERSION,
  loadVideoSpace,
  reconcileVideoRefresh,
  observeVideoDay,
  observeAutomaticVideoDay,
  observeVideoLayers,
  boundaryAt,
  videoScenarios,
  videoStocks,
  type LineObservation,
  type PriceRelation,
  type ReferenceSelection,
  type VerifiedVideoStock,
  type VideoCode,
  type VideoObservation,
  type LayerSelection,
  type LayerResult,
} from '@/lib/video-space';
import type {
  RulerSelectionState,
  SelectionEvent,
} from '@/lib/video-space-selection';
import type {
  WaveCandidate,
  WavePivot,
  WaveRejection,
} from '@/lib/video-space-auto';
import '../../chips/chips.css';
import './video-space.css';

export const dynamic = 'force-static';
type LoadState =
  | { kind: 'loading'; previous: VerifiedVideoStock | null }
  | { kind: 'ready'; stock: VerifiedVideoStock }
  | { kind: 'error'; previous: VerifiedVideoStock | null; message: string };
function saved(state: LoadState | undefined): VerifiedVideoStock | null {
  return state?.kind === 'ready' ? state.stock : (state?.previous ?? null);
}
const money = (n: number | string) =>
  Number(n).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  });
const side = { ABOVE: '线上', ON: '精确落线', BELOW: '线下' };
function Relation({ value }: { value: PriceRelation }) {
  return (
    <span data-side={value.side} data-in-band={value.inBand}>
      {side[value.side]}
      <small>
        {value.distancePercent >= 0 ? '+' : ''}
        {value.distancePercent.toFixed(3)}% ·{' '}
        {value.inBand ? '贴线（±0.5%带内）' : '带外'}
      </small>
    </span>
  );
}
function LineTable({
  lines,
  referenceK,
  half = false,
}: {
  half?: boolean;
  lines: readonly LineObservation[];
  referenceK: number;
}) {
  return (
    <div className="vs-table-wrap">
      <table>
        <thead>
          <tr>
            <th>同一边界／价格</th>
            <th>开盘严格侧别</th>
            <th>收盘严格侧别</th>
            <th>日范围含线</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.k} data-reference={l.k === referenceK}>
              <th>
                {half ? 'S' : 'B'}({l.k}) · {money(l.price)}
                {half && <small>父尺 B({l.k / 2})</small>}
                {l.k === referenceK && <small>本日共同参考</small>}
              </th>
              <td>
                <Relation value={l.open} />
              </td>
              <td>
                <Relation value={l.close} />
              </td>
              <td>{l.rangeIncludes ? '包含' : '不包含'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
const wavePointText = (p: WavePivot) =>
  `${p.date} ${p.field} ${p.price}（确认 ${p.confirmationDate}）`;
function waveStopText(code: string) {
  switch (code) {
    case 'OLDER_LOW_NOT_STRICTLY_LOWER':
      return '较早谷底不比当前谷底低';
    case 'NO_INTERVENING_STRICT_HIGH':
      return '两谷之间没有已确认高点';
    case 'INTERVENING_PEAK_NOT_STRICTLY_LOWER':
      return '中间最高峰不比右侧对应峰低';
    case 'NO_EARLIER_CONFIRMED_LOW':
      return '没有更早的已确认低点';
    default:
      return '上升结构关系不满足';
  }
}
const waveRejectionText: Record<WaveRejection['code'], string> = {
  no_prior_confirmed_high_at_or_above_H: '缺少较早的等高或更高峰作为边界',
  no_confirmed_low_strictly_between_boundary_and_H: '两峰之间没有已确认谷底',
  selected_low_not_below_H: '区间最低谷仍不低于当前高点',
  NO_PRIOR_CONFIRMED_LOW: '高点之前没有已确认谷底',
  RESULTING_LOW_NOT_BELOW_HIGH: '结构起点的谷底不低于当前高点',
};
function WaveRejectionEvidence({ item }: { item: WaveRejection }) {
  return (
    <details>
      <summary>
        {item.method} · H {item.H.date} high {item.H.price} ·{' '}
        {waveRejectionText[item.code]}
      </summary>
      <p>已确认高点：{wavePointText(item.H)}。</p>
      {item.method === 'A' && 'reason' in item && (
        <p>
          边界高点：{wavePointText(item.reason.boundaryH)}；区间内已确认谷底：
          {item.reason.consideredL.length
            ? item.reason.consideredL.map(wavePointText).join('；')
            : '无'}
          。
        </p>
      )}
      {item.code === 'selected_low_not_below_H' && (
        <p>区间最低谷：{wavePointText(item.selectedL)}。</p>
      )}
      {item.code === 'RESULTING_LOW_NOT_BELOW_HIGH' && (
        <p>
          结构起点：{wavePointText(item.L)}；向左扩展{' '}
          {item.reason.acceptedExtensions} 次。停止追溯：
          {item.reason.stop.code === 'RELATION_FAILED'
            ? item.reason.stop.stopCodes.map(waveStopText).join('；')
            : waveStopText(item.reason.stop.code)}
          。
        </p>
      )}
      <p>本项只说明该方法所需结构不足，不代表没有其他参考尺或观察空间。</p>
    </details>
  );
}
function WaveEvidence({ wave }: { wave: WaveCandidate }) {
  const event = (e: WaveCandidate['firstHighBreak']) =>
    e
      ? `${e.date} ${e.field} ${e.price}，次日可用 ${e.usableFrom ?? '待核'}`
      : '尚未观察到';
  return (
    <section aria-label="结构波段形成依据">
      <h4>结构波段形成依据</h4>
      <p>
        H 确认 {wave.confirmationDate} · 启用 {wave.activationDate}
        ；日高首次突破：{event(wave.firstHighBreak)}；收盘首次突破：
        {event(wave.firstCloseBreak)}。
      </p>
      <details>
        <summary>
          为什么选择此波段（{wave.reasons.map((r) => r.method).join('、')}）
        </summary>
        {wave.reasons.map((reason) =>
          reason.method === 'A' ? (
            <div key="A">
              <p>
                A 较早等高或更高边界：{wavePointText(reason.boundaryH)}
                。区间候选低点：
                {reason.consideredL.map(wavePointText).join('；')}
                。取最低，同价取最早；理由冻结 {reason.frozenOn}。
              </p>
            </div>
          ) : (
            <div key="B">
              <p>
                B 初始谷：{wavePointText(reason.initialTrough)}；此前低点{' '}
                {reason.priorLowCount} 个，向左扩展 {reason.acceptedExtensions}{' '}
                次，最终谷 {wavePointText(reason.finalTrough)}，回溯比较用峰{' '}
                {wavePointText(reason.finalRightwardPeak)}。
              </p>
              <ol>
                {reason.attempts.map((a) => (
                  <li key={`${a.olderLow.date}:${a.currentTrough.date}`}>
                    较早谷 {wavePointText(a.olderLow)}；当前谷{' '}
                    {wavePointText(a.currentTrough)}
                    ；右峰 {wavePointText(a.rightwardPeak)}；中间峰{' '}
                    {a.interveningHighs.length
                      ? a.interveningHighs.map(wavePointText).join('；')
                      : '无'}
                    ；最高峰（同价取最近）
                    {a.selectedHighestInterveningHigh
                      ? wavePointText(a.selectedHighestInterveningHigh)
                      : '无'}
                    ；低谷严格更低{' '}
                    {a.checks.olderLowStrictlyLower ? '是' : '否'}，中间峰存在{' '}
                    {a.checks.hasInterveningStrictHigh ? '是' : '否'}
                    ，峰严格更低{' '}
                    {a.checks.interveningPeakStrictlyLower ? '是' : '否'}；
                    {a.action === 'EXTEND'
                      ? '继续'
                      : `停止：${a.stopCodes.map(waveStopText).join('；')}`}
                    。
                  </li>
                ))}
              </ol>
              <p>
                停止原因：
                {reason.stop.code === 'RELATION_FAILED'
                  ? `${reason.stop.stopCodes.map(waveStopText).join('；')}，更早还有 ${reason.stop.unvisitedEarlierLowCount} 个低点未继续追溯`
                  : waveStopText(reason.stop.code)}
                ；理由冻结 {reason.endpointFrozenBy}。
              </p>
            </div>
          ),
        )}
      </details>
      <p>结构候选为工程假设，不是作者公式；不自动选主尺，突破不代表站稳。</p>
    </section>
  );
}
function Observation({
  stock,
  observation,
  expanded,
  onExpand,
  overlays,
  parent,
  rootContext,
}: {
  parent: VideoObservation | null;
  rootContext: VideoObservation | null;
  stock: VerifiedVideoStock;
  observation: Extract<VideoObservation, { kind: 'ready' }>;
  expanded: boolean;
  onExpand: () => void;
  overlays: readonly {
    id: string;
    label: string;
    style: 'comparison' | 'parent' | 'root' | 'fixed' | 'retained';
    observation: Extract<VideoObservation, { kind: 'ready' }>;
  }[];
}) {
  const o = observation,
    lines = o.spaceLines.some((l) => l.k === o.reference.k)
      ? o.spaceLines
      : [o.reference, ...o.spaceLines];
  return (
    <>
      <div className="vs-quote">
        <h2>
          {o.date} <small>已保存日线 · 非实时</small>
        </h2>
        <dl>
          {[
            ['开盘 O', o.bar.open],
            ['最高 H', o.bar.high],
            ['最低 L', o.bar.low],
            ['收盘 C', o.bar.close],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{money(value)}</dd>
            </div>
          ))}
        </dl>
      </div>
      <section
        className="vs-reference"
        aria-label="共同参考线"
        data-source-kind={o.source.kind}
        data-reference-k={o.reference.k}
        data-reference-price={o.reference.price}
        data-date={o.date}
      >
        <h3>
          开盘与收盘共用 {o.source.kind === 'HALF' ? 'S' : 'B'}({o.reference.k})
          · {money(o.reference.price)}
        </h3>
        <p>
          {o.referenceSelection.kind === 'AUTO_PREVIOUS_CLOSE'
            ? `自动参考仅使用前收 ${o.previous?.date} · ${o.previous?.close}，按LOG距离选最近整格／半格，同距取较高价。`
            : `手动固定 k=${o.reference.k}，切换日期仍使用同一条线。`}
        </p>
        <div className="vs-relations">
          <div>
            开盘 {money(o.bar.open)}
            <Relation value={o.reference.open} />
          </div>
          <div>
            收盘 {money(o.bar.close)}
            <Relation value={o.reference.close} />
          </div>
        </div>
        <p>
          严格线上下与贴线分开呈现。贴线带内不据此判为站稳；范围含线不证明盘中顺序。
        </p>
      </section>
      <section className="vs-space" aria-label="收盘所在空间">
        <h3>
          {o.location.onBoundary ? '收盘精确落在整格边界' : '收盘所在整格空间'}
        </h3>
        <dl>
          {[
            { label: '上沿', item: o.location.upper },
            { label: '半格', item: o.location.middle },
            { label: '下沿', item: o.location.lower },
          ].map(({ label, item }) => (
            <div key={label}>
              <dt>
                {label} {o.source.kind === 'HALF' ? 'S' : 'B'}({item.k})
              </dt>
              <dd>{money(item.price)}</dd>
            </div>
          ))}
        </dl>
        {o.location.adjacent && (
          <p>
            边界两侧均列示。相邻空间 B({o.location.adjacent.upper.k}){' '}
            {money(o.location.adjacent.upper.price)} ／ 半格{' '}
            {money(o.location.adjacent.middle.price)} ／ B(
            {o.location.adjacent.lower.k}){' '}
            {money(o.location.adjacent.lower.price)}。
          </p>
        )}
      </section>
      <div className="vs-chart-control">
        <span>相关空间 · 同一原尺 · LOG</span>
        <button onClick={onExpand} aria-pressed={expanded}>
          {expanded ? '聚焦相关空间' : '展开120根与0至6标尺'}
        </button>
      </div>
      <VideoSpaceChart
        stock={stock}
        observation={o}
        expanded={expanded}
        overlays={overlays}
      />
      <h3>同线开收核对</h3>
      <LineTable
        lines={lines}
        referenceK={o.reference.k}
        half={o.source.kind === 'HALF'}
      />
      <section className="vs-ruler">
        <h3>
          {o.source.kind === 'AUTO'
            ? '自动平台原尺'
            : o.source.kind === 'LOWER'
              ? '下降平台'
              : o.source.kind === 'WAVE'
                ? '结构波段候选'
                : o.source.kind === 'HALF'
                  ? '继承半格'
                  : '固定原尺与候选依据'}
        </h3>
        {o.source.endpoints.kind === 'REAL' ? (
          <dl>
            <div>
              <dt>H · {o.source.endpoints.high.date} · high</dt>
              <dd>{o.source.endpoints.high.price}</dd>
            </div>
            <div>
              <dt>L · {o.source.endpoints.low.date} · low</dt>
              <dd>{o.source.endpoints.low.price}</dd>
            </div>
          </dl>
        ) : (
          <p>
            继承父尺 {o.source.endpoints.parentId} 的 B(0){' '}
            {money(o.source.endpoints.high)} 与 B(0.5){' '}
            {money(o.source.endpoints.low)}。无新增 OHLC 端点。
          </p>
        )}
        {o.source.kind === 'FIXED' && <p>{o.source.scenario.reason}</p>}
        {o.source.kind === 'WAVE' && <WaveEvidence wave={o.source.wave} />}
        {o.source.kind === 'HALF' &&
          parent?.kind === 'ready' &&
          parent.source.kind === 'WAVE' && (
            <WaveEvidence wave={parent.source.wave} />
          )}
        {o.source.kind === 'LOWER' && (
          <>
            <p>
              前驱 {o.source.platform.predecessorId} · 跌破{' '}
              {o.source.platform.triggerDate} · 确认{' '}
              {o.source.platform.confirmationDate} · 启用{' '}
              {o.source.platform.activationDate} ·{' '}
              {o.source.platform.relationToRoot.kind === 'CONTAINED_CELL'
                ? `原尺第 ${o.source.platform.relationToRoot.cell} 格内嵌`
                : '跨格独立平台'}{' '}
              ·{' '}
              {o.source.platform.rootLifetime === 'active' ? '当前根' : '归档'}
            </p>
            <p>
              {o.source.platform.fourConfirmedPoints
                .map(
                  (p) =>
                    `${p.kind} ${p.date} ${money(p.price)}（确认 ${p.confirmed}）`,
                )
                .join('；')}
            </p>
          </>
        )}
        {o.source.kind === 'HALF' &&
          parent?.kind === 'ready' &&
          parent.source.endpoints.kind === 'REAL' && (
            <div aria-label="所选父尺真实端点">
              <h4>所选父尺真实端点</h4>
              <p>
                H · {parent.source.endpoints.high.date} ·{' '}
                {parent.source.endpoints.high.field} ·{' '}
                {money(parent.source.endpoints.high.price)}；L ·{' '}
                {parent.source.endpoints.low.date} ·{' '}
                {parent.source.endpoints.low.field} ·{' '}
                {money(parent.source.endpoints.low.price)}
              </p>
              <p>
                {parent.source.kind === 'AUTO'
                  ? `确认 ${parent.source.version.confirmationDate} · 启用 ${parent.startDate}`
                  : parent.source.kind === 'LOWER'
                    ? `确认 ${parent.source.platform.confirmationDate} · 启用 ${parent.startDate}`
                    : parent.source.kind === 'WAVE'
                      ? `确认 ${parent.source.wave.confirmationDate} · 启用 ${parent.startDate}`
                      : `启用 ${parent.startDate} · 原片锁线时间未知`}
              </p>
            </div>
          )}
        {o.source.kind === 'HALF' &&
          rootContext?.kind === 'ready' &&
          rootContext.source.kind === 'AUTO' &&
          parent?.kind === 'ready' &&
          (parent.source.kind !== 'AUTO' ||
            parent.source.version.id !== rootContext.source.version.id) && (
            <p>
              {parent.source.kind === 'FIXED' || parent.source.kind === 'WAVE'
                ? '当前 V1 对照尺'
                : '所属 V1 原尺'}
              ：H · {rootContext.source.endpoints.high.date} ·{' '}
              {rootContext.source.endpoints.high.field} ·{' '}
              {money(rootContext.source.endpoints.high.price)}；L ·{' '}
              {rootContext.source.endpoints.low.date} ·{' '}
              {rootContext.source.endpoints.low.field} ·{' '}
              {money(rootContext.source.endpoints.low.price)}；确认{' '}
              {rootContext.source.version.confirmationDate} · 启用{' '}
              {rootContext.startDate}。
            </p>
          )}
        {o.source.kind === 'HALF' && (
          <p>
            启用 {o.source.activationDate}。S(j)=父尺 B(j/2)，当前参考 S(
            {o.reference.k}) = 父尺 B({o.reference.k / 2}) ·{' '}
            {money(o.reference.price)}
            。精确一半和启用时间为工程约定。切换尺度增加父尺四分之一格参考候选，因此自动参考实际价格可能变化；手动
            k 属于当前尺度。
          </p>
        )}
        {o.source.kind === 'AUTO' && (
          <details>
            <summary>平台确认与版本记录</summary>
            <p>同一确认日取索引最早的四点组合。</p>
            <p>
              历史版本：
              {o.source.versions
                .map(
                  (v) =>
                    `锁定 ${v.confirmationDate}，启用 ${v.activationDate ?? '待核'}，退役 ${v.retiredAt ?? '尚无'}`,
                )
                .join('；')}
            </p>
            <p>
              锁定 {o.source.version.confirmationDate} · 启用{' '}
              {o.source.version.activationDate} · 竞争候选{' '}
              {o.source.version.candidateCount} · 首次跌破{' '}
              {o.source.version.downBreak ?? '尚无'}
            </p>
            <p>
              {o.source.version.points
                .map(
                  (p) =>
                    `${p.kind} ${p.date} ${money(p.price)}（确认 ${p.confirmed}）`,
                )
                .join('；')}
            </p>
          </details>
        )}
        <p>
          {o.source.kind === 'FIXED'
            ? '候选为视频波段人工固定，原作者当时锁线时间未知。历史日为描述性回看。'
            : '当前快照历史模拟，自动平台和精确半格是工程补充，原作者公式未知，推广尚未开放。'}
        </p>
      </section>
    </>
  );
}
function Comparison({
  stock,
  date,
  selection,
  fixed,
  automatic,
  scenarioId,
  layer,
}: {
  layer: LayerSelection | null;
  stock: VerifiedVideoStock;
  date: string;
  selection: ReferenceSelection;
  fixed: VideoObservation | null;
  automatic: VideoObservation | null;
  scenarioId: string;
}) {
  const recent = stock.history
    .filter((b) => b.date <= date)
    .slice(-5)
    .reverse();
  const selected = layer
    ? observeVideoLayers(stock, date, selection, layer)
    : null;
  const isRule = layer !== null && 'kind' in layer;
  const identity = (value: Extract<VideoObservation, { kind: 'ready' }>) =>
    value.source.kind === 'FIXED'
      ? value.source.scenario.id
      : value.source.kind === 'AUTO'
        ? value.source.version.id
        : value.source.kind === 'LOWER'
          ? value.source.platform.id
          : value.source.kind === 'WAVE'
            ? value.source.wave.id
            : `half:${value.source.endpoints.parentId}`;
  const historyLayer =
    layer && !('kind' in layer) && selected?.parent.kind === 'ready'
      ? { ...layer, parentId: identity(selected.parent) }
      : layer;
  if (fixed?.kind !== 'ready' || automatic?.kind !== 'ready') {
    const entries: { label: string; value: VideoObservation | null }[] = [
      { label: '固定', value: fixed },
      { label: '自动（V1）', value: automatic },
      ...(isRule
        ? [{ label: '规则观察尺', value: selected?.observation ?? null }]
        : []),
    ];
    const seen = new Set(
      entries.flatMap(({ value }) =>
        value?.kind === 'ready' ? [identity(value)] : [],
      ),
    );
    for (const [label, value] of [
      ['所选父尺', selected?.parent],
      ['继承半格', isRule ? undefined : selected?.observation],
    ] as const) {
      if (value?.kind === 'ready' && !seen.has(identity(value))) {
        entries.push({
          label: value.source.kind === 'HALF' ? label : '所选父尺',
          value,
        });
        seen.add(identity(value));
      }
    }
    const cell = (o: VideoObservation | null | undefined) =>
      o?.kind === 'ready' ? (
        <>
          {o.source.kind === 'HALF' ? 'S' : 'B'}({o.reference.k}){' '}
          {money(o.reference.price)}
          {o.source.kind === 'HALF' && <> · 父 B({o.reference.k / 2})</>}
          <br />开 <Relation value={o.reference.open} /> 收{' '}
          <Relation value={o.reference.close} />
        </>
      ) : (
        '待核'
      );
    return (
      <section className="vs-ruler">
        <h3>固定与自动对照</h3>
        <p>
          固定：{fixed?.kind === 'pending' ? fixed.reason : '可用'}；自动：
          {automatic?.kind === 'pending' ? automatic.reason : '可用'}。
        </p>
        <div className="vs-table-wrap vs-endpoint-comparison">
          <table>
            <thead>
              <tr>
                <th>尺</th>
                <th>端点 / 来源</th>
                <th>参考 / 开收</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(({ label, value }) => (
                <tr key={label}>
                  <th>{label}</th>
                  <td>
                    {value?.kind !== 'ready'
                      ? '待核'
                      : value.source.endpoints.kind === 'REAL'
                        ? `${value.source.endpoints.high.date} high ${value.source.endpoints.high.price} / ${value.source.endpoints.low.date} low ${value.source.endpoints.low.price}`
                        : '继承父边界'}
                  </td>
                  <td>{cell(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h4>最近五个实际交易日</h4>
        <div className="vs-table-wrap vs-endpoint-comparison">
          <table>
            <thead>
              <tr>
                <th>日期</th>
                {entries.map(({ label }) => (
                  <th key={label}>{label}参考 / 开收</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {recent.map((b) => {
                const day = historyLayer
                  ? observeVideoLayers(stock, b.date, selection, historyLayer)
                  : null;
                return (
                  <tr key={b.date}>
                    <th>{b.date}</th>
                    {entries.map(({ label }) => (
                      <td key={label}>
                        {cell(
                          label === '固定'
                            ? observeVideoDay(
                                stock,
                                scenarioId,
                                b.date,
                                selection,
                              )
                            : label === '自动（V1）'
                              ? observeAutomaticVideoDay(
                                  stock,
                                  b.date,
                                  selection,
                                )
                              : label === '所选父尺'
                                ? day?.parent
                                : day?.observation,
                        )}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    );
  }
  const a = automatic,
    f = fixed;
  const extra: {
    label: string;
    value: Extract<VideoObservation, { kind: 'ready' }>;
  }[] = [];
  const seen = new Set([identity(f), identity(a)]);
  for (const [label, value] of [
    ['所选父尺', selected?.parent],
    ['继承半格', isRule ? undefined : selected?.observation],
  ] as const) {
    if (value?.kind === 'ready' && !seen.has(identity(value))) {
      extra.push({
        label: value.source.kind === 'HALF' ? label : '所选父尺',
        value,
      });
      seen.add(identity(value));
    }
  }
  const matching = videoScenarios.some(
    (s) =>
      s.code === stock.code &&
      a.source.endpoints.kind === 'REAL' &&
      s.high.date === a.source.endpoints.high.date &&
      s.low.date === a.source.endpoints.low.date,
  );
  const ks = [
    ...new Set([
      f.location.upper.k,
      f.location.middle.k,
      f.location.lower.k,
      f.reference.k,
      a.location.upper.k,
      a.location.middle.k,
      a.location.lower.k,
      a.reference.k,
    ]),
  ].sort((x, y) => x - y);
  const line = (
    label: string,
    value: Extract<VideoObservation, { kind: 'ready' }>,
  ) => (
    <tr>
      <th>{label}</th>
      <td>
        {value.source.endpoints.kind === 'REAL' ? (
          <>
            {value.source.endpoints.high.date} high{' '}
            {money(value.source.endpoints.high.price)}
            <br />
            {value.source.endpoints.low.date} low{' '}
            {money(value.source.endpoints.low.price)}
          </>
        ) : (
          '继承父边界'
        )}
      </td>
      <td>{Math.log(value.ruler.H / value.ruler.L).toFixed(4)}</td>
      <td>
        {value.source.kind === 'HALF' ? 'S' : 'B'}({value.reference.k}){' '}
        {money(value.reference.price)}
        {value.source.kind === 'HALF' && <> · 父 B({value.reference.k / 2})</>}
      </td>
      <td>
        <Relation value={value.reference.open} />
      </td>
      <td>
        <Relation value={value.reference.close} />
      </td>
    </tr>
  );
  return (
    <section className="vs-ruler">
      <h3>固定与自动对照</h3>
      <p>
        {matching ? '自动端点等于一组固定候选。' : '端点不同，波段待核。'} H 差{' '}
        {((a.ruler.H / f.ruler.H - 1) * 100).toFixed(2)}%，L 差{' '}
        {((a.ruler.L / f.ruler.L - 1) * 100).toFixed(2)}%；LOG 高度比{' '}
        {(
          Math.log(a.ruler.H / a.ruler.L) / Math.log(f.ruler.H / f.ruler.L)
        ).toFixed(3)}
        。
      </p>
      <p className="vs-comparison-hint">表格可横向滚动查看完整日期与价格。</p>
      <div className="vs-table-wrap vs-endpoint-comparison">
        <table>
          <thead>
            <tr>
              <th>尺</th>
              <th>H / L 实际点位</th>
              <th>LOG 高度</th>
              <th>各自参考线</th>
              <th>开盘</th>
              <th>收盘</th>
            </tr>
          </thead>
          <tbody>
            {line('固定', f)}
            {line('自动（V1）', a)}
            {extra.map(({ label, value }) => (
              <Fragment key={identity(value)}>{line(label, value)}</Fragment>
            ))}
            {isRule &&
              (selected?.observation.kind === 'ready' ? (
                line('规则观察尺', selected.observation)
              ) : (
                <tr>
                  <th>规则观察尺</th>
                  <td colSpan={5}>
                    待核：
                    {selected?.observation.kind === 'pending'
                      ? selected.observation.reason
                      : '缺少已核验历史'}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      <h4>同 k 价格对照</h4>
      <div className="vs-table-wrap">
        <table>
          <thead>
            <tr>
              <th>k</th>
              <th>固定</th>
              <th>自动</th>
              <th>自动相对固定</th>
            </tr>
          </thead>
          <tbody>
            {ks.map((k) => (
              <tr key={k}>
                <th>{k}</th>
                <td>{money(boundaryAt(f.ruler, k))}</td>
                <td>{money(boundaryAt(a.ruler, k))}</td>
                <td>
                  {(
                    (boundaryAt(a.ruler, k) / boundaryAt(f.ruler, k) - 1) *
                    100
                  ).toFixed(2)}
                  %
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details>
        <summary>展开 0 至 6 同 k 全表</summary>
        <div className="vs-table-wrap">
          <table>
            <thead>
              <tr>
                <th>k</th>
                <th>固定</th>
                <th>自动</th>
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: 13 }, (_, i) => i / 2).map((k) => (
                <tr key={k}>
                  <th>{k}</th>
                  <td>{money(boundaryAt(f.ruler, k))}</td>
                  <td>{money(boundaryAt(a.ruler, k))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <h4>最近五个实际交易日</h4>
      <div className="vs-table-wrap">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>固定参考 / 开收</th>
              <th>自动（V1）参考 / 开收</th>
              {extra.map(({ label, value }) => (
                <th key={identity(value)}>{label}参考 / 开收</th>
              ))}
              {isRule && <th>规则观察尺参考 / 开收</th>}
            </tr>
          </thead>
          <tbody>
            {recent.map((b) => {
              const x = observeVideoDay(stock, scenarioId, b.date, selection),
                y = observeAutomaticVideoDay(stock, b.date, selection);
              const layerDay = historyLayer
                ? observeVideoLayers(stock, b.date, selection, historyLayer)
                : null;
              const cell = (o: VideoObservation) =>
                o.kind === 'pending' ? (
                  '待核'
                ) : (
                  <span>
                    {o.source.kind === 'HALF' ? 'S' : 'B'}({o.reference.k}){' '}
                    {money(o.reference.price)}
                    {o.source.kind === 'HALF' && (
                      <> · 父 B({o.reference.k / 2})</>
                    )}
                    <br />开 <Relation value={o.reference.open} /> 收{' '}
                    <Relation value={o.reference.close} />
                  </span>
                );
              return (
                <tr key={b.date}>
                  <th>{b.date}</th>
                  <td>{cell(x)}</td>
                  <td>{cell(y)}</td>
                  {extra.map(({ label, value }) => (
                    <td key={identity(value)}>
                      {cell(
                        (label === '所选父尺'
                          ? layerDay?.parent
                          : layerDay?.observation) ?? {
                          kind: 'pending',
                          reason: '待核',
                        },
                      )}
                    </td>
                  ))}
                  {isRule && (
                    <td>
                      {cell(
                        layerDay?.observation ?? {
                          kind: 'pending',
                          reason: '待核',
                        },
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p>
        当前快照历史模拟，不代表历史实时运行。原作者自动公式未知；旧平台可能长期保留，推广尚未开放。
      </p>
    </section>
  );
}
function selectionEventText(event: SelectionEvent): string {
  switch (event.kind) {
    case 'MAIN':
      return event.reason === 'OUTWARD_EXPANSION'
        ? '共同波段向外包络原主尺，扩大参考；旧格保留。'
        : event.reason === 'COMMON_ENVELOPE'
          ? '以最近共同波段包络已确认背景，作为独立参考。'
          : '没有共同包络波段，使用真实背景平台。';
    case 'BACKGROUND':
      return '背景平台改变，主尺身份相同，继续持有。';
    case 'SCALE':
      return event.to === 'HALF'
        ? `${event.triggerDate} 完成收盘低于 L，自动看继承半格。`
        : `${event.triggerDate} 已连续两日收回 L，自动回父尺。`;
    case 'RETIRED':
      return `${event.knownAtClose} 根平台完成退役，整链停止新选择，旧格保留。`;
  }
}
function RuleEvidence({ layers }: { layers: LayerResult }) {
  const state: RulerSelectionState | null = layers.selection;
  if (!state)
    return (
      <section className="vs-ruler" aria-label="规则选尺依据">
        <h3>规则选尺待核</h3>
        <p>缺少已核验自动历史，固定大格仍独立可用。</p>
      </section>
    );
  const label = (id: string) => {
    const item = state.kept.find((v) => v.ruler.id === id);
    return item
      ? `H ${item.ruler.H.date} ${money(item.ruler.H.price)} / L ${item.ruler.L.date} ${money(item.ruler.L.price)}`
      : '已确认背景';
  };
  return (
    <section
      className="vs-ruler vs-rule-selection"
      aria-label="规则选尺依据"
      data-main-id={state.kind === 'ready' ? state.current.ruler.id : ''}
      data-scale={state.kind === 'ready' ? state.current.scale : ''}
      data-selected-on={state.kind === 'ready' ? state.current.selectedOn : ''}
      data-scale-since={state.kind === 'ready' ? state.current.scaleSince : ''}
    >
      <h3>规则选尺 · 保留原大格</h3>
      <p>
        以已确认平台作背景，A/B共同波段作包络参考；背景不变时维持主尺，只允许共同波段向外扩展。包络是价格参照，不表示时间父子关系。默认仍为固定候选，三股工程回放尚未推广。
      </p>
      {state.kind === 'pending' ? (
        <p>{state.reason}</p>
      ) : (
        <>
          <dl>
            <div>
              <dt>当前主尺</dt>
              <dd>{label(state.current.ruler.id)}</dd>
            </div>
            <div>
              <dt>实际自动选入</dt>
              <dd>{state.current.selectedOn}</dd>
            </div>
            <div>
              <dt>当前观察尺度</dt>
              <dd>
                {state.current.scale === 'HALF' ? '继承半格' : '父尺'} · 自{' '}
                {state.current.scaleSince}
              </dd>
            </div>
          </dl>
          <p>
            主尺确认 {state.current.ruler.confirmationDate}，可用{' '}
            {state.current.ruler.activationDate}。背景 H{' '}
            {money(state.current.background.H)} / L{' '}
            {money(state.current.background.L)}。
          </p>
          <p>
            细尺首次可用：{state.current.fine.firstHalfDate ?? '尚未发生'}
            ；最近尺度依据完成日：{state.current.fine.triggerDate ?? '尚无'}
            。选入新主尺时只重放该尺自身已知历史，实际选入时间不回填。当前连续收回
            L：{state.current.fine.recoveryCount} 日。
          </p>
          <p>
            自动细尺：完成收盘低于 L 后次实际日进入；连续两完成收盘不低于 L
            后次实际日回父尺。精确二分与两日恢复是工程规则；已启用半格仍可在分层观察手动查看。
          </p>
          {state.disagreements.length > 0 && (
            <details>
              <summary>
                保留的不同谷底候选（{state.disagreements.length}组）
              </summary>
              {state.disagreements.map((group) => (
                <p key={group.highDate}>
                  H {group.highDate}：
                  {group.candidates
                    .map(
                      (v) =>
                        `L ${v.L.date} ${money(v.L.price)}（${v.reasons.map((r) => r.method).join('、')}）`,
                    )
                    .join('；')}
                  。本组不进入共同自动池，保留主尺及备选。
                </p>
              ))}
            </details>
          )}
        </>
      )}
      <details>
        <summary>主尺与尺度逐日变化记录（{state.events.length}项）</summary>
        <p>
          只列实际变化日；已形成主尺的持有期内，没有变化记录的交易日维持原身份及尺度。根退役后自动主尺待核，旧端点和原大格留存。
        </p>
        <div className="vs-table-wrap">
          <table>
            <thead>
              <tr>
                <th>实际生效日</th>
                <th>选择或切换</th>
                <th>主尺</th>
              </tr>
            </thead>
            <tbody>
              {state.events.map((event, i) => (
                <tr key={`${event.date}:${event.kind}:${i}`}>
                  <th>{event.date}</th>
                  <td>{selectionEventText(event)}</td>
                  <td>
                    {label(event.kind === 'MAIN' ? event.toId : event.mainId)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
export default function VideoSpacePage() {
  const [code, setCode] = useState<VideoCode>('300502'),
    [scenarioId, setScenarioId] = useState('XY_SUPP_PRIMARY'),
    [selectedDate, setDate] = useState('');
  const [loads, setLoads] = useState<Partial<Record<VideoCode, LoadState>>>({
      '300502': { kind: 'loading', previous: null },
    }),
    [revision, setRevision] = useState(0),
    [mode, setMode] = useState<ReferenceSelection['kind']>(
      'AUTO_PREVIOUS_CLOSE',
    ),
    [fixedK, setFixedK] = useState('1'),
    [expanded, setExpanded] = useState(false),
    [rulerMode, setRulerMode] = useState<'fixed' | 'auto' | 'layers' | 'rule'>(
      'fixed',
    ),
    [parentId, setParentId] = useState('CURRENT_ROOT'),
    [scale, setScale] = useState<'PARENT' | 'HALF'>('PARENT');
  useEffect(() => {
    const controller = new AbortController();
    void loadVideoSpace(code, { signal: controller.signal })
      .then((stock) => {
        if (!controller.signal.aborted)
          setLoads((old) => ({
            ...old,
            [code]: reconcileVideoRefresh(saved(old[code]), stock),
          }));
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setLoads((old) => ({
            ...old,
            [code]: {
              kind: 'error',
              previous: saved(old[code]),
              message:
                error instanceof Error ? error.message : '行情读取失败，待核。',
            },
          }));
      });
    return () => controller.abort();
  }, [code, revision]);
  const state = loads[code],
    stock = saved(state),
    date = selectedDate || stock?.asOf || '';
  const selection: ReferenceSelection =
    mode === 'AUTO_PREVIOUS_CLOSE'
      ? { kind: mode }
      : { kind: mode, k: fixedK.trim() ? Number(fixedK) : NaN };
  const fixedObservation = stock
    ? observeVideoDay(stock, scenarioId, date, selection)
    : null;
  const autoObservation = stock
    ? observeAutomaticVideoDay(stock, date, selection)
    : null;
  const layerChoice: LayerSelection =
    rulerMode === 'rule' ? { kind: 'RULE' } : { parentId, scale };
  const layers = stock
    ? observeVideoLayers(stock, date, selection, layerChoice)
    : null;
  const layered = rulerMode === 'layers' || rulerMode === 'rule';
  const viewStock =
    stock && rulerMode === 'rule' && stock.autoHistory?.kind === 'ready'
      ? { ...stock, history: stock.autoHistory.history }
      : stock;
  const replayDates = viewStock?.history ?? [];
  const replayIndex = replayDates.findIndex((b) => b.date === date);
  const observation =
    rulerMode === 'fixed'
      ? fixedObservation
      : rulerMode === 'auto'
        ? autoObservation
        : layers?.observation;
  const other = rulerMode === 'fixed' ? autoObservation : fixedObservation;
  const overlays: {
    id: string;
    label: string;
    style: 'comparison' | 'parent' | 'root' | 'fixed' | 'retained';
    observation: Extract<VideoObservation, { kind: 'ready' }>;
  }[] = [];
  if (layered) {
    if (layers?.parent.kind === 'ready' && layers.parent !== observation)
      overlays.push({
        id: 'parent',
        style: 'parent',
        label: '所选父尺',
        observation: layers.parent,
      });
    if (
      layers?.rootContext.kind === 'ready' &&
      layers.rootContext.source.kind === 'AUTO' &&
      layers.parent.kind === 'ready' &&
      (layers.parent.source.kind !== 'AUTO' ||
        layers.parent.source.version.id !==
          layers.rootContext.source.version.id)
    )
      overlays.push({
        id: 'root',
        style: 'root',
        label:
          layers.parent.source.kind === 'FIXED' ||
          layers.parent.source.kind === 'WAVE'
            ? '当前 V1 对照尺'
            : '所属 V1 原尺',
        observation: layers.rootContext,
      });
    if (
      fixedObservation?.kind === 'ready' &&
      fixedObservation.source.kind === 'FIXED' &&
      !(
        layers?.parent.kind === 'ready' &&
        layers.parent.source.kind === 'FIXED' &&
        layers.parent.source.scenario.id === fixedObservation.source.scenario.id
      )
    )
      overlays.push({
        id: 'fixed',
        style: 'fixed',
        label: '固定视频对照尺',
        observation: fixedObservation,
      });
    if (rulerMode === 'rule')
      for (const item of layers?.retained ?? []) {
        if (item.observation.kind === 'ready')
          overlays.push({
            id: `retained:${item.id}`,
            label: item.label,
            style: 'retained',
            observation: item.observation,
          });
      }
  } else if (other?.kind === 'ready')
    overlays.push({
      id: 'comparison',
      style: 'comparison',
      label: rulerMode === 'fixed' ? '自动对照尺' : '固定对照尺',
      observation: other,
    });
  const scenario = videoScenarios.find((s) => s.id === scenarioId),
    name = videoStocks.find((s) => s.code === code)?.name;
  function beginLoading(next: VideoCode) {
    setLoads((old) => ({
      ...old,
      [next]: { kind: 'loading', previous: saved(old[next]) },
    }));
  }
  function changeStock(next: VideoCode) {
    if (next === code) return;
    beginLoading(next);
    setCode(next);
    setScenarioId(
      videoScenarios.find((s) => s.code === next && s.primary)?.id ?? '',
    );
    setDate('');
    setParentId('CURRENT_ROOT');
    setScale('PARENT');
    setExpanded(false);
  }
  return (
    <div className="chip-site video-space">
      <SiteHeader active="golden" />
      <WorkbenchPageBar
        title="视频空间观察"
        date={stock?.asOf}
        dateLabel="实际数据日"
        modeLabel="三股试点 · 非实时"
      >
        <SiteLink href="/golden/daily">每日画线</SiteLink>
        <SiteLink href="/golden">人工核对</SiteLink>
      </WorkbenchPageBar>
      <div className="vs-layout">
        <aside className="vs-sidebar">
          <h2>三股试点</h2>
          <p>人工波段原尺，不随新行情换点。</p>
          <nav aria-label="选择股票">
            {videoStocks.map((s) => (
              <button
                key={s.code}
                onClick={() => changeStock(s.code)}
                aria-pressed={code === s.code}
              >
                <strong>{s.name}</strong>
                <span>{s.code}</span>
              </button>
            ))}
          </nav>
          <p>共11个候选，保留端点敏感性。</p>
          <p>当前候选来源为原视频观察，尚非全市场自动选点规则。</p>
        </aside>
        <main className="vs-main">
          <div className="vs-heading">
            <div>
              <h2>
                {name} <small>{code}</small>
              </h2>
              <p>
                {rulerMode === 'rule'
                  ? '规则选尺（回放试验）'
                  : rulerMode === 'layers'
                    ? '分层观察（试验）'
                    : rulerMode === 'auto'
                      ? '自动平台空间（试验）'
                      : '固定视频空间'}{' '}
                · 不复权 · LOG
              </p>
            </div>
            <button
              onClick={() => {
                beginLoading(code);
                setRevision((n) => n + 1);
              }}
              disabled={state?.kind === 'loading'}
            >
              {state?.kind === 'loading' ? '读取中…' : '刷新已发布行情'}
            </button>
          </div>
          <div className="vs-controls">
            <label>
              原尺模式
              <select
                aria-label="原尺模式"
                value={rulerMode}
                onChange={(e) =>
                  setRulerMode(
                    e.target.value === 'rule'
                      ? 'rule'
                      : e.target.value === 'layers'
                        ? 'layers'
                        : e.target.value === 'auto'
                          ? 'auto'
                          : 'fixed',
                  )
                }
              >
                <option value="fixed">固定候选</option>
                <option value="auto">自动平台（试验）</option>
                <option value="layers">分层观察（试验）</option>
                <option value="rule">规则选尺（回放试验）</option>
              </select>
            </label>
            {rulerMode === 'layers' && (
              <>
                <label>
                  查看父尺
                  <select
                    aria-label="查看父尺"
                    value={parentId}
                    onChange={(e) => {
                      setParentId(e.target.value);
                      setScale('PARENT');
                    }}
                  >
                    <option value="CURRENT_ROOT">当前 V1 原尺</option>
                    {parentId !== 'CURRENT_ROOT' &&
                      !layers?.options.some((o) => o.id === parentId) && (
                        <option value={parentId} disabled>
                          当前所选候选在该日尚未启用
                        </option>
                      )}
                    {layers?.options
                      .filter((o) => o.label !== '当前 V1 原尺')
                      .map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.label}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  查看尺度
                  <select
                    aria-label="查看尺度"
                    value={scale}
                    onChange={(e) =>
                      setScale(e.target.value === 'HALF' ? 'HALF' : 'PARENT')
                    }
                  >
                    <option value="PARENT">父尺</option>
                    <option value="HALF">继承半格</option>
                  </select>
                </label>
              </>
            )}
            <label>
              固定对照基线
              <select
                aria-label="场景"
                value={scenarioId}
                onChange={(e) => setScenarioId(e.target.value)}
              >
                {videoScenarios
                  .filter((s) => s.code === code)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              查看交易日
              <select
                aria-label="查看交易日"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                disabled={!stock}
              >
                {!stock && <option value="">待行情</option>}
                {viewStock?.history
                  .slice()
                  .reverse()
                  .map((b) => (
                    <option key={b.date} value={b.date}>
                      {b.date}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              共同参考
              <select
                aria-label="共同参考"
                value={mode}
                onChange={(e) =>
                  setMode(
                    e.target.value === 'FIXED'
                      ? 'FIXED'
                      : 'AUTO_PREVIOUS_CLOSE',
                  )
                }
              >
                <option value="AUTO_PREVIOUS_CLOSE">
                  自动 · 前收最近整／半格
                </option>
                <option value="FIXED">手动固定 k</option>
              </select>
            </label>
            {mode === 'FIXED' && (
              <label>
                固定 k
                <input
                  aria-label="固定 k"
                  type="number"
                  step="0.5"
                  value={fixedK}
                  onChange={(e) => setFixedK(e.target.value)}
                />
              </label>
            )}
          </div>
          {rulerMode === 'rule' && (
            <div className="vs-replay-controls" aria-label="逐日回放控制">
              <button
                disabled={replayIndex <= 0}
                onClick={() => setDate(replayDates[replayIndex - 1].date)}
              >
                上一个实际交易日
              </button>
              <span>{date || '待行情'} · 仅用此前完成数据选尺</span>
              <button
                disabled={
                  replayIndex < 0 || replayIndex >= replayDates.length - 1
                }
                onClick={() => setDate(replayDates[replayIndex + 1].date)}
              >
                下一个实际交易日
              </button>
            </div>
          )}
          {rulerMode === 'rule' && layers && <RuleEvidence layers={layers} />}
          {state?.kind === 'loading' && (
            <output>
              {stock
                ? `正在刷新；暂保留已核验数据日 ${stock.asOf}。`
                : '正在读取行情并核验固定原尺…'}
            </output>
          )}
          {state?.kind === 'error' && (
            <p className="vs-error" role="alert">
              {state.message}
              {stock
                ? ` 刷新未成功，继续展示旧数据日 ${stock.asOf}。`
                : ' 本股暂停判读，可切换其他股票。'}
            </p>
          )}
          {layered && layers && (
            <section className="vs-ruler">
              <h3>分层观察</h3>
              {layers.waveCatalog?.kind === 'ready' && (
                <details>
                  <summary>
                    结构候选未形成的原因（{layers.waveCatalog.rejections.length}{' '}
                    项）
                  </summary>
                  <p>
                    每项按已确认高点和假设记录；缺少结构时不补选。展开可查看真实点位、确认时间与排除依据。
                  </p>
                  {layers.waveCatalog.rejections.map((item) => (
                    <WaveRejectionEvidence
                      key={`${item.method}:${item.H.date}:${item.code}`}
                      item={item}
                    />
                  ))}
                </details>
              )}
              {layers.waveCatalog?.kind === 'pending' && (
                <p>{layers.waveCatalog.reason}</p>
              )}

              <p>
                切换尺度会改变自动参考候选及其实际价格；手动 k
                以当前尺度计。切换日期时，固定 k 保持当前尺度坐标。
              </p>
              <p>
                {layers.family?.lowerPlatforms.some(
                  (p) => p.rootLifetime === 'active',
                )
                  ? '当前根已形成下降平台，可在父尺中选择。'
                  : '当前根尚未形成下降平台。'}{' '}
                {layers.half.kind === 'available'
                  ? `半格自 ${layers.half.activationDate} 起可查看。`
                  : layers.half.reason}
              </p>
              <p>
                原片 F1 支持原尺向下延伸，F2 支持保留大格增细。精确
                1/2、自动下降平台与启用时点为工程约定；作者完整公式未知。
              </p>
            </section>
          )}
          {observation?.kind === 'pending' && (
            <output className="vs-error">{observation.reason}</output>
          )}
          {viewStock &&
            rulerMode === 'rule' &&
            observation?.kind === 'pending' &&
            (() => {
              const archived = layers?.retained.find(
                (v) => v.observation.kind === 'ready',
              );
              const original =
                archived?.observation.kind === 'ready'
                  ? archived.observation
                  : fixedObservation;
              if (original?.kind !== 'ready') return null;
              return (
                <section aria-label="归档大格对照">
                  <h3>
                    {archived ? '归档大格对照' : '固定视频原大格对照'} ·
                    当前自动主尺待核
                  </h3>
                  <VideoSpaceChart
                    stock={viewStock}
                    observation={original}
                    expanded={expanded}
                    overlays={overlays.filter((v) =>
                      archived
                        ? v.id !== `retained:${archived.id}`
                        : v.id !== 'fixed',
                    )}
                  />
                </section>
              );
            })()}
          {stock && observation?.kind === 'ready' && (
            <Observation
              stock={viewStock ?? stock}
              observation={observation}
              expanded={expanded}
              onExpand={() => setExpanded((v) => !v)}
              overlays={overlays}
              parent={layered ? (layers?.parent ?? null) : null}
              rootContext={layered ? (layers?.rootContext ?? null) : null}
            />
          )}
          {stock && (
            <Comparison
              stock={viewStock ?? stock}
              date={date}
              selection={selection}
              fixed={fixedObservation}
              automatic={autoObservation}
              scenarioId={scenarioId}
              layer={layered ? layerChoice : null}
            />
          )}
          <details className="vs-rules">
            <summary>选点、公式与数据口径</summary>
            <p>{scenario?.reason}</p>
            <p>
              B(k)=H×(L/H)^k，B(0)=H、B(1)=L；半格为k+0.5的几何中点，仅作辅助细分。负k可用。整格编号为实现编号，未声称原作者全部黄金比例已确认。
            </p>
            <p>
              默认共同参考只由前一根收盘选择。所选日开高低收及未来日线不会参与自动选线。±0.5%是工程观察带，不是作者参数或交易信号。
            </p>
            <p>
              冻结原尺来自固定批次 {FROZEN_DATASET}
              ，逐股核验来源与120根冻结历史。最新行情直接读取已发布的同批日线原帧，逐重叠日核验OHLC；口径冲突保留待核，不自动换尺。
            </p>
            <p>
              自动 B 是工程候选，并非原作者公式。从{' '}
              {stock?.autoHistory?.kind === 'ready'
                ? stock.autoHistory.historyStartDate
                : '2024-10-17'}{' '}
              冷启动，之前的平台状态未知。固定种子与最新行情逐重叠日核验 OHLC
              后衔接；本会话继续核对已接受的全部历史，新浏览器会话无法核对上次会话追加历史的修订，因此并非永久时点快照。
            </p>
            <p>
              自动平台使用前后各两根确认的严格转折，排除同日双转折；40
              根内四点高低交替、跨度至少 10 根，上下影线分别按 0.5%
              容差重叠，区间内收盘占比至少 80%，LOG 高度在 log(1.05) 至 log(1.6)
              之间。同日多候选取最早点位组合，确认后下一实际交易日启用。跌破 L
              仅记录，不换点；连续两日收盘高于 H 的 1.01
              倍才退役，之后只用退役后的点寻找新平台。旧平台没有期限，可能长期保留，是已知缺口。
            </p>
            <p>
              实际数据日 {stock?.asOf ?? '待核'}；最新批次{' '}
              {stock?.dataset ?? '待核'}；来源 THS_UNADJUSTED /
              00_UNADJUSTED；规则 {VIDEO_RULE_VERSION}
              。未把客户端日期当行情日期。
            </p>
            <p>
              本页只描述已保存日线与固定、平台及结构候选的关系，不提供盘中顺序、预测或买卖指令。线上可用不代表选点普适性或策略有效性已验证。
            </p>
          </details>
        </main>
      </div>
    </div>
  );
}
