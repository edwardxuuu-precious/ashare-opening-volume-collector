'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { SiteHeader } from '@/components/site-header';
import { StockPicker } from '@/components/stock-picker';
import { TriangleChart } from '@/components/triangle-chart';
import { startPublicationFollow } from '@/lib/publication-follow';
import {
  createTriangleDailyDetailReader,
  filterTriangleDailyRows,
  loadTriangleDailyManifest,
  loadTriangleDailyScreen,
  loadTriangleDailyStatus,
  triangleLastKnownAt,
  type TriangleDailyPublication,
  type TriangleDailyStatus,
  type TriangleDailySymbol,
} from '@/lib/triangle-publication';
import {
  analyzeTriangles,
  requiredTriangleHistory,
  type TriangleAnalysis,
  type TriangleFormation,
  type PriceZone,
} from '@/lib/triangles';

const dataBase = (process.env.NEXT_PUBLIC_DATA_BASE || '').replace(/\/$/, '');
type PublicationState =
  | { kind: 'loading' }
  | { kind: 'unavailable'; message: string }
  | { kind: 'ready'; value: TriangleDailyPublication };
type DetailState =
  | { kind: 'idle' }
  | { kind: 'error'; dataset: string; code: string; message: string }
  | {
      kind: 'ready';
      dataset: string;
      symbol: TriangleDailySymbol;
      analysis: TriangleAnalysis;
    };

function money(value: number | null | undefined, precision = 2) {
  return value === null || value === undefined
    ? '未识别'
    : value.toFixed(precision);
}
function zoneText(zone: PriceZone | null) {
  return zone ? `${money(zone.lower)}–${money(zone.upper)}` : '未识别';
}
function formationName(formation: TriangleFormation) {
  return formation.direction === 'golden' ? '金三角' : '死三角';
}
function stateText(formation: TriangleFormation) {
  const state = formation.state;
  if (state.kind === 'observing') return '风险观察中';
  if (state.kind === 'active') {
    if (
      state.breakout.kind === 'confirmed' &&
      state.retest.kind === 'confirmed'
    )
      return '突破、回踩已确认';
    if (state.breakout.kind === 'confirmed') return '突破压力已确认';
    if (state.retest.kind === 'confirmed') return '回踩支撑已确认';
    return '等待价格条件';
  }
  if (state.kind === 'invalidated') return `已失效 · ${state.date}`;
  if (state.kind === 'expired') return `已到期 · ${state.date}`;
  if (state.kind === 'coverage-ended') return `数据中断 · ${state.date}`;
  return '前期背景不满足';
}

export function TriangleDaily({ onResearch }: { onResearch: () => void }) {
  const [publicationState, setPublicationState] = useState<PublicationState>({
    kind: 'loading',
  });
  const [status, setStatus] = useState<TriangleDailyStatus | null>(null);
  const [statusError, setStatusError] = useState('');
  const [refreshError, setRefreshError] = useState('');
  const [retry, setRetry] = useState(0);
  const [direction, setDirection] = useState<'all' | 'golden' | 'death'>('all');
  const [recent, setRecent] = useState(5);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [selectedCode, setSelectedCode] = useState('');
  const [formationId, setFormationId] = useState('');
  const [detail, setDetail] = useState<DetailState>({ kind: 'idle' });
  const detailSection = useRef<HTMLElement>(null);
  const publication =
    publicationState.kind === 'ready' ? publicationState.value : null;

  useEffect(() => {
    const follow = startPublicationFollow({
      readStatus: (signal) => loadTriangleDailyStatus({ dataBase, signal }),
      readManifest: (signal) => loadTriangleDailyManifest({ dataBase, signal }),
      readPayload: (manifest, signal) =>
        loadTriangleDailyScreen(manifest, { dataBase, signal }),
      onStatus: setStatus,
      onPublication: (manifest, screen) => {
        setPublicationState({ kind: 'ready', value: { manifest, screen } });
        setPage(1);
      },
      onStatusError: (error) => setStatusError(error?.message ?? ''),
      onPublicationError: (error) => {
        setRefreshError(error?.message ?? '');
        if (error)
          setPublicationState((previous) =>
            previous.kind === 'ready'
              ? previous
              : { kind: 'unavailable', message: error.message },
          );
      },
      isRunning: (value) => value.state === 'RUNNING',
      publishedDataset: (value) =>
        value.state === 'COMMITTED' ? value.dataset_id : null,
    });
    return () => follow.stop();
  }, [retry]);

  const readDetail = useMemo(
    () => (publication ? createTriangleDailyDetailReader(publication) : null),
    [publication],
  );
  useEffect(() => {
    if (!publication || !readDetail || !selectedCode) return;
    const controller = new AbortController();
    readDetail(selectedCode, { dataBase, signal: controller.signal })
      .then((symbol) => {
        if (controller.signal.aborted) return;
        const analysis = analyzeTriangles({
          bars: symbol.bars,
          params: publication.manifest.params,
          asOf: publication.manifest.as_of,
        });
        setDetail({
          kind: 'ready',
          dataset: publication.manifest.dataset_id,
          symbol,
          analysis,
        });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setDetail({
            kind: 'error',
            dataset: publication.manifest.dataset_id,
            code: selectedCode,
            message:
              error instanceof Error ? error.message : '个股历史读取失败。',
          });
      });
    return () => controller.abort();
  }, [publication, readDetail, selectedCode]);

  const stocks = useMemo(
    () =>
      publication
        ? [
            ...publication.screen.rows.map((row) => row.security),
            ...publication.manifest.history_only_securities,
          ]
        : [],
    [publication],
  );
  const filtered = useMemo(
    () =>
      publication
        ? filterTriangleDailyRows(publication.screen, direction, recent).filter(
            ({ row }) =>
              `${row.security.code} ${row.security.name}`
                .toLowerCase()
                .includes(search.trim().toLowerCase()),
          )
        : [],
    [publication, direction, recent, search],
  );
  const counts = useMemo(
    () =>
      publication
        ? {
            golden: filterTriangleDailyRows(
              publication.screen,
              'golden',
              recent,
            ).length,
            death: filterTriangleDailyRows(publication.screen, 'death', recent)
              .length,
            unknown: publication.screen.rows.filter(
              (row) => row.availability.kind === 'insufficient',
            ).length,
          }
        : null,
    [publication, recent],
  );
  const pageCount = Math.max(1, Math.ceil(filtered.length / 50));
  const shownPage = Math.min(page, pageCount);
  const visibleDetail =
    detail.kind === 'ready' &&
    detail.dataset === publication?.manifest.dataset_id &&
    detail.symbol.security.code === selectedCode
      ? detail
      : null;
  const detailError =
    detail.kind === 'error' &&
    detail.code === selectedCode &&
    detail.dataset === publication?.manifest.dataset_id
      ? detail
      : null;
  const detailPending =
    !!selectedCode && !!publication && !visibleDetail && !detailError;
  const historicFormations =
    visibleDetail?.analysis.formations.filter(
      (formation) => formation.qualified,
    ) ?? [];
  const selectedFormation =
    historicFormations.find((formation) => formation.id === formationId) ??
    historicFormations.at(-1) ??
    null;
  const pending =
    status?.state === 'RUNNING' || status?.state === 'SOURCE_PENDING';

  function chooseStock(code: string, formation = '') {
    setSelectedCode(code);
    setFormationId(formation);
    detailSection.current?.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    });
  }

  return (
    <div className="triangle-site" data-triangle-view="daily">
      <SiteHeader active="triangles" />
      <main className="triangle-main">
        <div className="triangle-title">
          <div>
            <p>每日自动更新</p>
            <h1>趋势三角与关键点位</h1>
          </div>
          <button type="button" onClick={onResearch}>
            自定义参数研究
          </button>
        </div>
        <p className="triangle-scope">
          收盘行情发布后，后台自动计算全市场默认结果。打开即可查看，日常无需手动筛选。日线
          MA5 / MA10 / MA20 · 不复权价格 · 规则观察候选。
        </p>
        {publicationState.kind === 'loading' && (
          <output className="triangle-empty">正在读取每日已计算结果…</output>
        )}
        {publicationState.kind === 'unavailable' && (
          <div role="alert" className="triangle-error">
            每日结果暂时无法读取。{publicationState.message}
            <button onClick={() => setRetry((value) => value + 1)}>
              重新读取每日结果
            </button>
          </div>
        )}
        {publication && counts && (
          <>
            <div
              className="triangle-daily-publication"
              aria-label="每日结果发布状态"
            >
              <strong>行情截至 {publication.manifest.as_of}</strong>
              <span>
                全市场 {publication.manifest.universe_total.toLocaleString()} 只
                · 默认回看 {publication.manifest.sessions.length} 个交易日
              </span>
              <span>
                结果发布{' '}
                {new Date(publication.manifest.generated_at).toLocaleString(
                  'zh-CN',
                  { timeZone: 'Asia/Shanghai', hour12: false },
                )}
              </span>
            </div>
            {pending && (
              <p className="triangle-notice">
                {status.message} 当前展示 {publication.manifest.as_of}{' '}
                的完整结果，发布完成后自动更新。
              </p>
            )}
            {status?.state === 'FAILED' && (
              <output className="triangle-notice">
                本次后台更新未完成。{status.message} 保留{' '}
                {publication.manifest.as_of} 的已发布结果。
              </output>
            )}
            {(refreshError || statusError) && (
              <output className="triangle-notice">
                更新检查暂时失败，当前仍是 {publication.manifest.as_of}{' '}
                的完整结果。{refreshError || statusError}
                <button
                  className="triangle-text-button"
                  onClick={() => setRetry((value) => value + 1)}
                >
                  重试更新检查
                </button>
              </output>
            )}
            <section
              className="triangle-daily-history-entry"
              aria-label="个股全部历史"
            >
              <div>
                <h2>个股全部历史</h2>
                <p>
                  输入名称或代码，直接查看系统已保存日线中的历次金三角、死三角。
                </p>
              </div>
              <StockPicker
                stocks={stocks}
                value={selectedCode ? [selectedCode] : []}
                multiple={false}
                onChange={(codes) => chooseStock(codes[0] ?? '')}
              />
              <p className="triangle-form-note">
                可查询历史 {publication.manifest.detail_sessions[0]} 至{' '}
                {publication.manifest.as_of}，共{' '}
                {publication.manifest.detail_sessions.length}{' '}
                个交易日。历史不足和行情缺口保留为未知，不补造形态。
              </p>
            </section>
            <section className="triangle-results" aria-label="每日三角筛选结果">
              <div className="triangle-section-heading">
                <div>
                  <h2>每日筛选结果</h2>
                  <p>
                    最近事件按实际可知日期统计；每只股票、每个方向保留最新匹配记录。历史已失效形态见个股全部历史。
                  </p>
                </div>
              </div>
              <div className="triangle-daily-counts" aria-label="每日三角数量">
                <button
                  onClick={() => {
                    setDirection('golden');
                    setPage(1);
                  }}
                  aria-pressed={direction === 'golden'}
                >
                  <span className="triangle-tag golden">金三角</span>
                  <strong data-triangle-count="golden">{counts.golden}</strong>
                </button>
                <button
                  onClick={() => {
                    setDirection('death');
                    setPage(1);
                  }}
                  aria-pressed={direction === 'death'}
                >
                  <span className="triangle-tag death">死三角</span>
                  <strong data-triangle-count="death">{counts.death}</strong>
                </button>
                <span>
                  最新连续行情不足 <strong>{counts.unknown}</strong> 只
                </span>
              </div>
              <div className="triangle-daily-filters">
                <label className="triangle-field">
                  形态方向
                  <select
                    aria-label="形态方向"
                    value={direction}
                    onChange={(event) => {
                      const next = event.target.value;
                      if (
                        next === 'all' ||
                        next === 'golden' ||
                        next === 'death'
                      )
                        setDirection(next);
                      setPage(1);
                    }}
                  >
                    <option value="all">金三角与死三角</option>
                    <option value="golden">金三角</option>
                    <option value="death">死三角</option>
                  </select>
                </label>
                <label className="triangle-field">
                  最近事件窗口
                  <select
                    aria-label="最近事件窗口"
                    value={recent}
                    onChange={(event) => {
                      setRecent(Number(event.target.value));
                      setPage(1);
                    }}
                  >
                    <option value="5">最近 5 个交易日</option>
                    <option value="10">最近 10 个交易日</option>
                    <option value="0">全部仍有效形态</option>
                  </select>
                </label>
                <label className="triangle-field">
                  查找列表
                  <input
                    aria-label="查找每日筛选股票"
                    value={search}
                    placeholder="名称 / 代码"
                    onChange={(event) => {
                      setSearch(event.target.value);
                      setPage(1);
                    }}
                  />
                </label>
              </div>
              <div className="triangle-table-scroll">
                <table>
                  <caption>
                    匹配 {filtered.length} 只 ·
                    支撑压力为形态形成时冻结的历史参考区间，单位元。
                  </caption>
                  <thead>
                    <tr>
                      <th>股票</th>
                      <th>形态与状态</th>
                      <th>形成日</th>
                      <th>最近事件</th>
                      <th>收盘</th>
                      <th>支撑区间</th>
                      <th>压力区间</th>
                      <th>查看</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered
                      .slice((shownPage - 1) * 50, shownPage * 50)
                      .map(({ row, formation }) => (
                        <tr
                          key={`${row.security.code}-${formation.id}`}
                          data-selected={selectedCode === row.security.code}
                        >
                          <td>
                            {row.security.name}
                            <small>{row.security.code}</small>
                          </td>
                          <td>
                            <span
                              className={`triangle-tag ${formation.direction}`}
                            >
                              {formationName(formation)}
                            </span>
                            <small>{stateText(formation)}</small>
                          </td>
                          <td>{formation.formedAt}</td>
                          <td>{triangleLastKnownAt(formation)}</td>
                          <td>{money(row.latestClose)}</td>
                          <td>{zoneText(formation.support)}</td>
                          <td>{zoneText(formation.resistance)}</td>
                          <td>
                            <button
                              onClick={() =>
                                chooseStock(row.security.code, formation.id)
                              }
                            >
                              查看历史标记
                            </button>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              {!filtered.length && (
                <p className="triangle-empty">
                  当前已发布结果在此条件下没有匹配记录。
                </p>
              )}
              {pageCount > 1 && (
                <div className="triangle-pagination">
                  <button
                    disabled={shownPage === 1}
                    onClick={() => setPage(shownPage - 1)}
                  >
                    上一页
                  </button>
                  <span>
                    {shownPage} / {pageCount}
                  </span>
                  <button
                    disabled={shownPage === pageCount}
                    onClick={() => setPage(shownPage + 1)}
                  >
                    下一页
                  </button>
                </div>
              )}
            </section>
            <section
              ref={detailSection}
              id="triangle-daily-detail"
              className="triangle-detail"
              aria-label="个股三角全部历史与关键点位"
            >
              {!selectedCode && (
                <p className="triangle-empty">
                  选择一只股票，即可查看全部已保存日线和历史三角标记。
                </p>
              )}
              {detailPending && (
                <output className="triangle-empty">
                  正在读取 {selectedCode} 的全部已保存日线…
                </output>
              )}
              {detailError && (
                <p role="alert" className="triangle-error">
                  {detailError.message}
                  <button
                    onClick={() => {
                      setDetail({ kind: 'idle' });
                      setRetry((value) => value + 1);
                    }}
                  >
                    重新读取
                  </button>
                </p>
              )}
              {visibleDetail && (
                <>
                  <div className="triangle-section-heading">
                    <div>
                      <h2>
                        {visibleDetail.symbol.security.name}{' '}
                        <span>{selectedCode}</span>
                      </h2>
                      <p>
                        日线全部历史 {visibleDetail.symbol.bars[0]?.date} 至{' '}
                        {publication.manifest.as_of} ·{' '}
                        {visibleDetail.symbol.bars.length} 个交易日
                      </p>
                    </div>
                    <span>
                      金三角{' '}
                      {
                        historicFormations.filter(
                          (formation) => formation.direction === 'golden',
                        ).length
                      }{' '}
                      次 · 死三角{' '}
                      {
                        historicFormations.filter(
                          (formation) => formation.direction === 'death',
                        ).length
                      }{' '}
                      次
                    </span>
                  </div>
                  <p className="triangle-form-note">
                    图中标注全部符合前期背景条件的历史形态，包括后续失效或到期的形态。默认规则需至少{' '}
                    {requiredTriangleHistory(publication.manifest.params)}{' '}
                    个连续有效交易日，历史开头及缺口后的不足区间不能确认形态。不复权价格尚未核对公司行为影响。
                  </p>
                  {visibleDetail.analysis.availability.kind ===
                    'insufficient' && (
                    <p className="triangle-notice">
                      {visibleDetail.analysis.availability.reason}
                      。最新连续有效行情{' '}
                      {visibleDetail.analysis.availability.consecutiveBars} 日。
                    </p>
                  )}
                  <div className="triangle-detail-controls">
                    <label className="triangle-field">
                      选择历史形态
                      <select
                        aria-label="选择历史形态"
                        value={selectedFormation?.id ?? ''}
                        onChange={(event) => setFormationId(event.target.value)}
                      >
                        {!historicFormations.length && (
                          <option value="">当前保存历史没有可确认的三角</option>
                        )}
                        {[...historicFormations].reverse().map((formation) => (
                          <option key={formation.id} value={formation.id}>
                            {formation.formedAt} · {formationName(formation)} ·{' '}
                            {stateText(formation)}
                          </option>
                        ))}
                      </select>
                    </label>
                    {selectedFormation && (
                      <p className="triangle-state">
                        {stateText(selectedFormation)}
                      </p>
                    )}
                  </div>
                  <TriangleChart
                    analysis={visibleDetail.analysis}
                    formation={selectedFormation}
                    name={visibleDetail.symbol.security.name}
                    onFormationSelect={(formation) =>
                      setFormationId(formation.id)
                    }
                  />
                  {selectedFormation && (
                    <section
                      className="triangle-daily-evidence"
                      aria-label="选中历史三角依据"
                    >
                      <h3>
                        {selectedFormation.formedAt}{' '}
                        {formationName(selectedFormation)}的关键点位
                      </h3>
                      <p>
                        三次交叉从 {selectedFormation.startedAt} 开始，至{' '}
                        {selectedFormation.formedAt} 完成。前期
                        {selectedFormation.direction === 'golden'
                          ? '回撤'
                          : '涨幅'}{' '}
                        {selectedFormation.pretrend.movePct?.toFixed(2) ??
                          '未知'}
                        %。{stateText(selectedFormation)}。
                      </p>
                      <div className="triangle-table-scroll">
                        <table>
                          <thead>
                            <tr>
                              <th>均线交叉</th>
                              <th>日期</th>
                              <th>均线观察值</th>
                            </tr>
                          </thead>
                          <tbody>
                            {selectedFormation.crosses.map((cross) => (
                              <tr key={cross.id}>
                                <td>
                                  {cross.pair === 'fast-middle'
                                    ? 'MA5 / MA10'
                                    : cross.pair === 'fast-slow'
                                      ? 'MA5 / MA20'
                                      : 'MA10 / MA20'}{' '}
                                  {cross.direction === 'up' ? '上穿' : '下穿'}
                                </td>
                                <td>{cross.date}</td>
                                <td>{money(cross.value)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <div className="triangle-daily-levels">
                        <p>
                          历史支撑区间{' '}
                          <strong>{zoneText(selectedFormation.support)}</strong>
                        </p>
                        <p>
                          历史压力区间{' '}
                          <strong>
                            {zoneText(selectedFormation.resistance)}
                          </strong>
                        </p>
                        <p>
                          突破确认参考阈值{' '}
                          <strong>
                            {money(selectedFormation.thresholds.breakout, 4)}
                          </strong>
                        </p>
                        <p>
                          支撑收复参考阈值{' '}
                          <strong>
                            {money(
                              selectedFormation.thresholds.retestRecovery,
                              4,
                            )}
                          </strong>
                        </p>
                        <p>
                          区域失效参考阈值{' '}
                          <strong>
                            {money(
                              selectedFormation.thresholds.zoneInvalidation,
                              4,
                            )}
                          </strong>
                        </p>
                        <p>
                          趋势锚点失效参考阈值{' '}
                          <strong>
                            {money(
                              selectedFormation.thresholds.anchorInvalidation,
                              4,
                            )}
                          </strong>
                        </p>
                      </div>
                      <div className="triangle-table-scroll">
                        <table>
                          <caption>
                            后续事件区分发生日与可知日，不将后来的确认提前标记。
                          </caption>
                          <thead>
                            <tr>
                              <th>事件</th>
                              <th>发生日</th>
                              <th>可知日</th>
                              <th>价格</th>
                            </tr>
                          </thead>
                          <tbody>
                            {selectedFormation.events.map((event) => (
                              <tr key={event.id}>
                                <td>{event.reason}</td>
                                <td>{event.occurredAt}</td>
                                <td>{event.knownAt}</td>
                                <td>{money(event.price)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </section>
                  )}
                </>
              )}
            </section>
            <p className="triangle-form-note">
              默认筛选和关键点位用于规则观察，尚未评估未来收益、胜率与交易成本。历史拒绝形态、全部状态和自定义参数可在“自定义参数研究”查看。
            </p>
          </>
        )}
      </main>
    </div>
  );
}
