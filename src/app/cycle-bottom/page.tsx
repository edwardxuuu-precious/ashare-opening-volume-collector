'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpRight, ChevronDown, Expand, RefreshCw, X } from 'lucide-react';
import { SiteHeader } from '@/components/site-header';
import {
  CycleLineChart,
  CycleMonthlyChart,
  type CycleChartMode,
  type CycleWindow,
} from '@/components/cycle-bottom-charts';
import {
  loadCycleBundle,
  type CycleBundle,
  type CycleComparison,
  type CycleEvent,
  type ForwardOutcome,
} from '@/lib/cycle-bottom-data';
import './cycle-bottom.css';

export const dynamic = 'force-static';

type DataState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | {
      kind: 'ready';
      bundle: CycleBundle;
      refreshing: boolean;
      refreshError: string | null;
      readAt: string;
    };

function useCycleData() {
  const [state, setState] = useState<DataState>({ kind: 'loading' });
  const request = useRef<AbortController | null>(null);
  const alive = useRef(false);
  const refresh = useCallback(async () => {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setState((current) =>
      current.kind === 'ready'
        ? { ...current, refreshing: true, refreshError: null }
        : { kind: 'loading' },
    );
    try {
      const bundle = await loadCycleBundle(controller.signal);
      if (alive.current && !controller.signal.aborted)
        setState({
          kind: 'ready',
          bundle,
          refreshing: false,
          refreshError: null,
          readAt: new Date().toISOString(),
        });
    } catch (error) {
      if (controller.signal.aborted || !alive.current) return;
      const message =
        error instanceof Error ? error.message : '周期数据暂时无法读取。';
      setState((current) =>
        current.kind === 'ready'
          ? { ...current, refreshing: false, refreshError: message }
          : { kind: 'error', message },
      );
    } finally {
      if (request.current === controller) request.current = null;
    }
  }, []);
  useEffect(() => {
    alive.current = true;
    void refresh();
    const ifVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    const timer = window.setInterval(ifVisible, 60_000);
    window.addEventListener('focus', ifVisible);
    document.addEventListener('visibilitychange', ifVisible);
    return () => {
      alive.current = false;
      request.current?.abort();
      request.current = null;
      window.clearInterval(timer);
      window.removeEventListener('focus', ifVisible);
      document.removeEventListener('visibilitychange', ifVisible);
    };
  }, [refresh]);
  return { state, refresh };
}

function pct(value: number | null, unit = '%') {
  return value === null
    ? '—'
    : `${value > 0 ? '+' : ''}${value.toFixed(2)}${unit}`;
}
function number(value: number) {
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}
function time(value: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return '尚无记录';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(value));
}
function tone(value: number | null) {
  return value === null ? '' : value > 0 ? 'cl-up' : value < 0 ? 'cl-down' : '';
}
function OutcomeCells({
  outcomes,
  sessions,
}: {
  outcomes: ForwardOutcome[];
  sessions: number;
}) {
  const item = outcomes.find((outcome) => outcome.sessions === sessions);
  if (!item?.complete)
    return (
      <>
        <td className="cl-unmatured">未满窗口</td>
        <td className="cl-unmatured">未满窗口</td>
      </>
    );
  return (
    <>
      <td className={tone(item.returnPct)}>{pct(item.returnPct)}</td>
      <td>{pct(item.downsidePct)}</td>
    </>
  );
}

function WindowButtons({
  value,
  onChange,
  label,
}: {
  value: CycleWindow;
  onChange: (value: CycleWindow) => void;
  label: string;
}) {
  const items: { value: CycleWindow; label: string }[] = [
    { value: 'all', label: '全部' },
    { value: '10', label: '近10年' },
    { value: '5', label: '近5年' },
  ];
  return (
    <div className="cl-window-buttons" role="group" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.value}
          aria-pressed={value === item.value}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function EventTable({ events }: { events: CycleEvent[] }) {
  if (!events.length)
    return (
      <div className="cl-empty cl-events-empty">
        所选口径与期间没有满足条件的收盘进入事件。没有触线，也属于本次数据结果。
      </div>
    );
  return (
    <div className="cl-table-scroll">
      <table className="cl-table cl-events-table">
        <thead>
          <tr>
            <th rowSpan={2}>进入日期</th>
            <th rowSpan={2}>区间</th>
            <th rowSpan={2}>当日差距</th>
            <th colSpan={2}>之后126个交易日</th>
            <th colSpan={2}>之后252个交易日</th>
          </tr>
          <tr>
            <th>期末收益</th>
            <th>最深下探</th>
            <th>期末收益</th>
            <th>最深下探</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.date}>
              <td>{event.date}</td>
              <td>
                <span
                  className={`cl-phase ${event.phase === 'holdout' ? 'cl-phase-holdout' : ''}`}
                >
                  {event.phase === 'holdout' ? '训练外' : '样本内回看'}
                </span>
              </td>
              <td>{pct(event.gapPct)}</td>
              <OutcomeCells outcomes={event.outcomes} sessions={126} />
              <OutcomeCells outcomes={event.outcomes} sessions={252} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const originalImages = [
  {
    src: '/research/cycle-bottom/equity-bond-comparison.png',
    width: 1080,
    height: 322,
    title: '原文股债对照图',
    alt: '用户提供的沪深300全收益指数与二级债基历史叠加图，含原作者红圈标记。',
  },
  {
    src: '/research/cycle-bottom/shanghai-monthly-channel.png',
    width: 1080,
    height: 1551,
    title: '原文上证月线图',
    alt: '用户提供的上证指数月线截图，含原作者画出的长期通道和国债指数参考线。',
  },
];
type OriginalImage = (typeof originalImages)[number];

function OriginalViewer({
  image,
  onClose,
}: {
  image: OriginalImage;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [originalSize, setOriginalSize] = useState(false);
  useEffect(() => {
    const node = dialog.current,
      focused = document.activeElement,
      overflow = document.body.style.overflow;
    node?.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      node?.close();
      document.body.style.overflow = overflow;
      if (focused instanceof HTMLElement) focused.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="cl-viewer"
      aria-labelledby="cl-viewer-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="cl-viewer-bar">
        <h2 id="cl-viewer-title">{image.title}</h2>
        <button
          onClick={() => setOriginalSize((current) => !current)}
          aria-pressed={originalSize}
        >
          {originalSize ? '适应屏幕' : '原始尺寸'}
        </button>
        <button onClick={onClose} aria-label="关闭原图">
          <X size={19} aria-hidden="true" />
        </button>
      </div>
      <div
        className={`cl-viewer-scroll ${originalSize ? 'cl-original-size' : ''}`}
      >
        <img
          src={image.src}
          width={image.width}
          height={image.height}
          alt={image.alt}
        />
      </div>
    </dialog>
  );
}

function OriginalReference() {
  const [image, setImage] = useState<OriginalImage | null>(null);
  return (
    <>
      <details className="cl-original-reference">
        <summary>
          <span>原文与截图参考</span>
          <span>
            查看原始假设 <ChevronDown size={15} aria-hidden="true" />
          </span>
        </summary>
        <div className="cl-original-content">
          <blockquote>
            <p>
              如果把“沪深300全收益指数”和“二级债基指数”放在一起，两线重合的时候就会止跌。
            </p>
            <p>2012、2016、2018、2022、2024年都是如此。</p>
            <p>
              基本上相当于股票市场的收益率达到了债券收益率水平的时候，就是估值见底了。
            </p>
            <p>如果拿国债指数基本也能得到同样的规律。</p>
            <p>目前看离底部还有距离。</p>
          </blockquote>
          <p className="cl-muted-note">
            这是用户提供的原始观点，原始发表日期未注明。「目前」保留为原文语境。下方截图用于对照思路，当前数值、拟合与观察来自本页实际数据。
          </p>
          <p className="cl-muted-note">
            原图 Wind 885007.WI
            的历史仅作本地复核，未作为本页自动更新来源。当前公开参照与原图指数不同，不能将其读数作为原
            Wind 指数的实时行情。
          </p>
          <div className="cl-original-images">
            {originalImages.map((item) => (
              <figure key={item.src}>
                <button
                  onClick={() => setImage(item)}
                  aria-label={`放大${item.title}`}
                >
                  <img
                    src={item.src}
                    width={item.width}
                    height={item.height}
                    alt={item.alt}
                    loading="lazy"
                  />
                  <span>
                    <Expand size={13} aria-hidden="true" />
                    查看原图
                  </span>
                </button>
                <figcaption>{item.title} · 水印完整保留</figcaption>
              </figure>
            ))}
          </div>
        </div>
      </details>
      {image && <OriginalViewer image={image} onClose={() => setImage(null)} />}
    </>
  );
}

export default function CycleBottomPage() {
  const { state, refresh } = useCycleData();
  const [referenceId, setReferenceId] = useState<CycleComparison['id'] | null>(
    null,
  );
  const [mode, setMode] = useState<CycleChartMode>('fit');
  const [window, setWindow] = useState<CycleWindow>('all');
  const [monthlyWindow, setMonthlyWindow] = useState<CycleWindow>('all');
  const [showReference, setShowReference] = useState(false);
  const [logarithmic, setLogarithmic] = useState(false);
  const [monthlyLogarithmic, setMonthlyLogarithmic] = useState(true);
  const [year, setYear] = useState(2024);
  const [focusDate, setFocusDate] = useState<string | null>(null);
  const [eventKind, setEventKind] = useState<'fit' | 'raw'>('fit');
  const [eventPhase, setEventPhase] = useState<'holdout' | 'all'>('holdout');
  useEffect(() => {
    const title = document.title;
    document.title = '大周期底部观察 · 量序';
    return () => {
      document.title = title;
    };
  }, []);
  const bundle = state.kind === 'ready' ? state.bundle : null;
  const snapshot = bundle?.snapshot;
  const comparison =
    snapshot?.comparisons.find((item) => item.id === referenceId) ??
    snapshot?.comparisons.find((item) => item.id === 'nonpure') ??
    snapshot?.comparisons[0];
  const currentCase =
    comparison?.cases.find((item) => item.year === year) ??
    comparison?.cases[0];
  const selectedEvents = comparison
    ? (eventKind === 'fit'
        ? comparison.events
        : comparison.rawTouchEvents
      ).filter((item) => eventPhase === 'all' || item.phase === 'holdout')
    : [];
  const refreshing =
    state.kind === 'loading' || (state.kind === 'ready' && state.refreshing);
  const currentMonth = snapshot?.monthly.points.at(-1);
  const latestSourceDate = snapshot?.sources.reduce(
    (latest, source) => (source.lastDate > latest ? source.lastDate : latest),
    '',
  );
  const status = bundle?.status;
  return (
    <div className="cl-site">
      <SiteHeader active="cycle-bottom" />
      <main className="cl-main">
        <header className="cl-page-header">
          <div>
            <p className="cl-eyebrow">沪深股指 / 长周期数据</p>
            <h1>大周期底部观察</h1>
            <p className="cl-page-subtitle">
              用真实股债序列拟合历史低位，持续核对当前相对位置与月线结构。
            </p>
          </div>
          <div className="cl-update-controls">
            <div className="cl-update-label">
              <i
                className={status?.state === 'ready' ? 'cl-status-ready' : ''}
              />
              <span>
                {state.kind === 'loading'
                  ? '正在读取数据'
                  : state.kind === 'error'
                    ? '数据读取失败'
                    : status?.state === 'ready'
                      ? comparison?.id === 'wind'
                        ? 'Wind 指数观察已更新'
                        : '公开参照观察已更新'
                      : status?.state === 'partial'
                        ? '部分来源待更新'
                        : status?.state === 'updating'
                          ? '后台正在更新'
                          : '后台更新未完成'}
              </span>
            </div>
            <span className="cl-last-check">
              后台核对 {time(status?.checkedAt ?? null)} · 北京时间
            </span>
            <button
              className="cl-refresh"
              onClick={() => {
                void refresh();
              }}
              disabled={refreshing}
            >
              <RefreshCw
                size={14}
                className={refreshing ? 'cl-spinning' : ''}
                aria-hidden="true"
              />
              {refreshing ? '读取中…' : '刷新数据'}
            </button>
          </div>
        </header>

        {state.kind === 'loading' && (
          <section className="cl-initial-state" aria-live="polite">
            <RefreshCw size={21} className="cl-spinning" aria-hidden="true" />
            <h2>正在读取真实历史序列与最新观察</h2>
            <p>核对版本、来源日期和拟合结果后显示。</p>
          </section>
        )}
        {state.kind === 'error' && (
          <section className="cl-initial-state cl-initial-error" role="alert">
            <h2>真实数据暂未载入</h2>
            <p>{state.message}</p>
            <button
              className="cl-text-button"
              onClick={() => {
                void refresh();
              }}
            >
              重新读取
            </button>
          </section>
        )}
        {state.kind === 'ready' && state.refreshError && (
          <div className="cl-alert" role="alert">
            <strong>本次刷新失败，保留上次有效数据。</strong>
            <span>
              {state.refreshError} 上次成功读取：{time(state.readAt)}。
            </span>
          </div>
        )}
        {status &&
          (status.state === 'failed' || status.state === 'partial') && (
            <div className="cl-alert" role="status">
              <strong>
                {status.state === 'failed'
                  ? '后台本次更新未完成'
                  : '部分数据来源尚未更新'}
              </strong>
              <span>
                {status.message} 最后成功：{time(status.lastSuccessAt)}
                。当前图表保留已发布版本。
              </span>
            </div>
          )}

        {snapshot && comparison && (
          <>
            <section className="cl-metrics" aria-label="当前周期数据">
              <div>
                <span>共同数据截至</span>
                <strong>{comparison.asOf}</strong>
                <small>最新来源 {latestSourceDate}</small>
              </div>
              <div>
                <span>距历史拟合参照</span>
                <strong className="cl-distance">
                  {pct(comparison.latest.gapPct)}
                </strong>
                <small>
                  {comparison.latest.gapPct < 0 ? '低于' : '高于'}拟合线 ·
                  不等于底部确认
                </small>
              </div>
              <div>
                <span>近20共同日差距变化</span>
                <strong>{pct(comparison.latest.gapChange20Pp, ' pp')}</strong>
                <small>
                  {comparison.latest.gapChange20Pp === null
                    ? '观察日不足'
                    : comparison.latest.gapChange20Pp < 0
                      ? '相对位置下移'
                      : comparison.latest.gapChange20Pp > 0
                        ? '相对位置上移'
                        : '相对位置持平'}
                </small>
              </div>
              <div>
                <span>上证距月线拟合下沿</span>
                <strong>{pct(snapshot.monthly.latestGapPct)}</strong>
                <small>月线截至 {snapshot.monthly.asOf}</small>
              </div>
            </section>

            <section
              className="cl-panel cl-comparison-panel"
              aria-labelledby="cl-comparison-title"
            >
              <div className="cl-panel-heading">
                <div>
                  <p className="cl-eyebrow">01 / 股债相对位置</p>
                  <h2 id="cl-comparison-title">沪深300全收益 × 债券参照</h2>
                </div>
                <span className="cl-source-badge">
                  H00300 · {comparison.code}
                </span>
              </div>
              <div
                className="cl-reference-options"
                role="group"
                aria-label="债券参照选择"
              >
                {snapshot.comparisons.map((item) => (
                  <button
                    key={item.id}
                    aria-pressed={comparison.id === item.id}
                    onClick={() => {
                      setReferenceId(item.id);
                      setFocusDate(null);
                    }}
                  >
                    {item.name}
                    <small>{item.code}</small>
                  </button>
                ))}
              </div>
              {comparison.id === 'nonpure' && (
                <div className="cl-reference-note" role="note">
                  <strong>
                    公开参照 · 中证非纯债债券型基金指数 930897
                  </strong>
                  <p>
                    它与原图的 Wind 二级债基指数 885007.WI
                    不同，触线日期和差距不保证一致；当前展示的是公开参照，不是原
                    Wind 实时行情。
                  </p>
                  <details className="cl-fit-details">
                    <summary>
                      公开参照的选样与历史口径{' '}
                      <ChevronDown size={14} aria-hidden="true" />
                    </summary>
                    <div>
                      <p>
                        该指数选样覆盖可投资股票或可转债的债券型基金，范围与原
                        Wind 二级债基指数不同。指数于2016-08-31发布，之前的历史为回溯数据。
                      </p>
                      <p>
                        本页持续更新这条公开序列，用于检查股债相对位置与原文假说。它的拟合、历史触线和后续表现只对应930897这一参照口径。
                      </p>
                    </div>
                  </details>
                </div>
              )}
              <div className="cl-chart-toolbar">
                <div
                  className="cl-mode-buttons"
                  role="group"
                  aria-label="股债比较口径"
                >
                  <button
                    aria-pressed={mode === 'fit'}
                    onClick={() => setMode('fit')}
                  >
                    历史低位拟合
                  </button>
                  <button
                    aria-pressed={mode === 'raw'}
                    onClick={() => setMode('raw')}
                  >
                    同基期实际股债
                  </button>
                </div>
                <div className="cl-toolbar-options">
                  <WindowButtons
                    value={window}
                    onChange={setWindow}
                    label="股债图显示区间"
                  />
                  <label>
                    <input
                      type="checkbox"
                      checked={logarithmic}
                      onChange={(event) => setLogarithmic(event.target.checked)}
                      aria-label="股债对数刻度"
                    />
                    对数刻度
                  </label>
                  {mode === 'fit' && (
                    <label>
                      <input
                        type="checkbox"
                        checked={showReference}
                        onChange={(event) =>
                          setShowReference(event.target.checked)
                        }
                      />
                      显示未缩放参照
                    </label>
                  )}
                </div>
              </div>
              <CycleLineChart
                comparison={comparison}
                mode={mode}
                window={window}
                showReference={showReference}
                logarithmic={logarithmic}
                focusDate={focusDate}
              />
              <div className="cl-quote-strip">
                <span>共同日实际点位</span>
                <b>沪深300全收益 {number(comparison.latest.equityClose)}</b>
                <span>
                  {comparison.name} {number(comparison.latest.referenceClose)}
                </span>
                <span>拟合参照 {number(comparison.latest.fittedClose)}</span>
              </div>
              {comparison.fit.holdoutBelowTrainingDays > 0 && (
                <div className="cl-fit-warning">
                  <span className="cl-warning-marker">!</span>
                  <div>
                    <strong>训练外已经突破历史训练低位</strong>
                    <p>
                      2020年后有{' '}
                      {comparison.fit.holdoutBelowTrainingDays.toLocaleString()}{' '}
                      个共同观察日低于训练期的全部相对低位。训练期最低差距{' '}
                      {pct(comparison.fit.trainingMinGapPct)}，训练外最低{' '}
                      {pct(comparison.fit.holdoutMinGapPct)}
                      。这条拟合参照曾被实际走势打破，不能把“在参照线以下”解释为已经见底。
                    </p>
                  </div>
                </div>
              )}
              <details className="cl-fit-details">
                <summary>
                  拟合方法与参数 <ChevronDown size={14} aria-hidden="true" />
                </summary>
                <div>
                  <p>
                    固定训练期 {comparison.fit.trainStart} 至{' '}
                    {comparison.fit.trainEnd}，使用{' '}
                    {comparison.fit.samples.toLocaleString()} 个共同日，拟合{' '}
                    {number(comparison.fit.quantile * 100)}%
                    分位。2020年之后的数据用于训练外历史核对，不移动训练终点来迎合当前走势。
                  </p>
                  <p className="cl-formula">
                    ln(沪深300全收益) = α + β × ln({comparison.name})
                  </p>
                  <dl>
                    <div>
                      <dt>α</dt>
                      <dd>{comparison.fit.alpha.toFixed(6)}</dd>
                    </div>
                    <div>
                      <dt>β</dt>
                      <dd>{comparison.fit.beta.toFixed(6)}</dd>
                    </div>
                    <div>
                      <dt>方法</dt>
                      <dd>{comparison.fit.method}</dd>
                    </div>
                  </dl>
                  <p>
                    本页使用今天可取得的历史数据重建。2020年后仅作训练外历史核对，未证明模型曾于2020年投入运行或拥有当时可得的数据认证。原文已列出2022、2024年，不能把这些已知年份声称为真正独立的前瞻验证，也不据此证明策略有效。
                  </p>
                  <p>
                    图中实际序列按共同基期 {comparison.baseDate} = 100
                    归一化。拟合模式中的债券参照经过上述模型映射；“同基期实际股债”保留未经拟合映射的原始比较。历史残差带用于展示训练期离散程度，不是置信区间。累计回报相同也不等于股票盈利收益率、债券到期收益率相同。
                  </p>
                </div>
              </details>
            </section>

            <section
              className="cl-observations"
              aria-labelledby="cl-observation-title"
            >
              <div className="cl-section-title">
                <h2 id="cl-observation-title">本次数据观察</h2>
                <span>
                  由当前发布版本自动计算 · {snapshot.generatedAt.slice(0, 10)}
                </span>
              </div>
              <div className="cl-observation-list">
                {snapshot.observations.map((item, index) => (
                  <article key={`${index}-${item.title}`}>
                    <span className="cl-observation-number">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <div>
                      <h3>{item.title}</h3>
                      <p>{item.detail}</p>
                    </div>
                  </article>
                ))}
              </div>
            </section>

            <section
              className="cl-panel"
              id="history"
              aria-labelledby="cl-history-title"
            >
              <div className="cl-panel-heading">
                <div>
                  <p className="cl-eyebrow">02 / 历史节点复核</p>
                  <h2 id="cl-history-title">五个年份，回到真实数值</h2>
                </div>
                <span className="cl-panel-note">
                  年度最低收盘由事后数据定位
                </span>
              </div>
              <p className="cl-section-description">
                逐年检查原文提到的位置，并显示当时差距和之后的实际走势。年度最低收盘是事后定位，只用于对照原文年份；当时不能预先识别，也不计入触线成功率。
              </p>
              <div
                className="cl-year-options"
                role="group"
                aria-label="历史观察年份"
              >
                {comparison.cases.map((item) => (
                  <button
                    key={item.year}
                    aria-pressed={currentCase?.year === item.year}
                    onClick={() => {
                      setYear(item.year);
                      setWindow('all');
                      setFocusDate(item.date);
                    }}
                  >
                    <strong>{item.year}</strong>
                    <span>{item.date}</span>
                    <small>
                      {item.phase === 'holdout'
                        ? '训练外历史核对'
                        : '训练期回看'}
                    </small>
                  </button>
                ))}
              </div>
              {currentCase && (
                <div className="cl-case-detail">
                  <div className="cl-case-overview">
                    <div>
                      <span>{currentCase.year} 年最低收盘</span>
                      <strong>{currentCase.date}</strong>
                      <small>
                        {currentCase.phase === 'holdout'
                          ? '未参与固定训练期拟合'
                          : '位于拟合训练期内'}
                      </small>
                    </div>
                    <div>
                      <span>沪深300全收益</span>
                      <strong>{number(currentCase.equityClose)}</strong>
                      <small>
                        {comparison.name} {number(currentCase.referenceClose)}
                      </small>
                    </div>
                    <div>
                      <span>距拟合参照</span>
                      <strong>{pct(currentCase.gapPct)}</strong>
                      <small>拟合点位 {number(currentCase.fittedClose)}</small>
                    </div>
                    <div>
                      <span>未拟合的同基期差距</span>
                      <strong>{pct(currentCase.rawGapPct)}</strong>
                      <small>使用共同基期 {comparison.baseDate}</small>
                    </div>
                  </div>
                  <div className="cl-table-scroll">
                    <table className="cl-table cl-case-outcomes">
                      <thead>
                        <tr>
                          <th>后续观察窗口</th>
                          <th>期末收益</th>
                          <th>期间最深下探</th>
                          <th>窗口状态</th>
                        </tr>
                      </thead>
                      <tbody>
                        {currentCase.outcomes.map((item) => (
                          <tr key={item.sessions}>
                            <td>{item.sessions} 个交易日</td>
                            <td className={tone(item.returnPct)}>
                              {item.complete ? pct(item.returnPct) : '—'}
                            </td>
                            <td>
                              {item.complete ? pct(item.downsidePct) : '—'}
                            </td>
                            <td>{item.complete ? '窗口完整' : '尚未成熟'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              <p className="cl-muted-note">
                期末收益与最深下探均相对于该观察日起点计算。最深下探是后续最低收盘相对起点的变化，包含起点，因此为零或负值；它不同于期间峰值到谷底的最大回撤。尚未成熟的窗口保留空值。
              </p>
            </section>

            <section className="cl-panel" aria-labelledby="cl-events-title">
              <div className="cl-panel-heading">
                <div>
                  <p className="cl-eyebrow">03 / 全部进入事件</p>
                  <h2 id="cl-events-title">接近之后，还有多深的下探？</h2>
                </div>
                <span className="cl-count">
                  {selectedEvents.length} 个所选事件
                </span>
              </div>
              <div className="cl-event-controls">
                <div
                  className="cl-mode-buttons"
                  role="group"
                  aria-label="历史事件口径"
                >
                  <button
                    aria-pressed={eventKind === 'fit'}
                    onClick={() => setEventKind('fit')}
                  >
                    进入拟合观察区
                  </button>
                  <button
                    aria-pressed={eventKind === 'raw'}
                    onClick={() => setEventKind('raw')}
                  >
                    实际股债靠近
                  </button>
                </div>
                <div
                  className="cl-mode-buttons cl-phase-buttons"
                  role="group"
                  aria-label="历史事件期间"
                >
                  <button
                    aria-pressed={eventPhase === 'holdout'}
                    onClick={() => setEventPhase('holdout')}
                  >
                    2020年后核对
                  </button>
                  <button
                    aria-pressed={eventPhase === 'all'}
                    onClick={() => setEventPhase('all')}
                  >
                    全部历史回看
                  </button>
                </div>
              </div>
              <p className="cl-section-description">
                {eventKind === 'fit'
                  ? `收盘差距由上方进入 ≤ ${comparison.thresholdPct}% 区域时记录。`
                  : `收盘时实际股债的同基期差距进入 ±${comparison.thresholdPct}% 区域时记录，两个方向均可进入。`}
                相邻记录至少间隔 {comparison.cooldownSessions}{' '}
                个共同观察日。只检查收盘数据，不推测日内是否触线；完整列出已记录事件，保留继续下跌的结果。
              </p>
              <EventTable events={selectedEvents} />
              <p className="cl-muted-note">
                样本内事件使用事后固定模型回看，不能作为当时可实现的信号回测。2020年后数据未参与这次参数拟合，但仍是历史检验，不能替代未来验证。表中不将尚未成熟或缺失窗口计为成功。
              </p>
            </section>

            <section
              className="cl-panel"
              id="monthly"
              aria-labelledby="cl-monthly-title"
            >
              <div className="cl-panel-heading">
                <div>
                  <p className="cl-eyebrow">04 / 真实月线结构</p>
                  <h2 id="cl-monthly-title">上证月K与长期拟合通道</h2>
                </div>
                <span className="cl-source-badge">
                  000001 · 截至 {snapshot.monthly.asOf}
                </span>
              </div>
              <div className="cl-chart-toolbar">
                <p className="cl-monthly-current">
                  最新月收盘{' '}
                  <b>{currentMonth ? number(currentMonth.close) : '—'}</b>
                  {currentMonth?.incomplete && (
                    <span className="cl-incomplete-badge">本月进行中</span>
                  )}
                </p>
                <div className="cl-toolbar-options">
                  <WindowButtons
                    value={monthlyWindow}
                    onChange={setMonthlyWindow}
                    label="月线图显示区间"
                  />
                  <label>
                    <input
                      type="checkbox"
                      checked={monthlyLogarithmic}
                      onChange={(event) =>
                        setMonthlyLogarithmic(event.target.checked)
                      }
                      aria-label="月线对数刻度"
                    />
                    对数刻度
                  </label>
                </div>
              </div>
              <CycleMonthlyChart
                points={snapshot.monthly.points}
                window={monthlyWindow}
                logarithmic={monthlyLogarithmic}
              />
              <details className="cl-fit-details">
                <summary>
                  月线通道口径 <ChevronDown size={14} aria-hidden="true" />
                </summary>
                <div>
                  <p>
                    固定训练期 {snapshot.monthly.fit.trainStart} 至{' '}
                    {snapshot.monthly.fit.trainEnd}，共{' '}
                    {snapshot.monthly.fit.samples}{' '}
                    个月度观察。使用对数价格对时间的线性趋势，训练残差的10%、50%、90%分位形成下沿、中位线、上沿。年度对数斜率参数{' '}
                    {snapshot.monthly.fit.annualLogSlope.toFixed(6)}，方法{' '}
                    {snapshot.monthly.fit.method}。
                  </p>
                  <p>
                    训练期之后保持参数不变，属于外推观察。当前月会随已公布日行情更新，未结束时标为进行中。这里的通道是可复现的数据模型，未宣称恢复原作者画线公式，也不能凭下沿确认价格不会继续下跌。
                  </p>
                </div>
              </details>
            </section>

            <section
              className="cl-sources"
              id="sources"
              aria-labelledby="cl-sources-title"
            >
              <div className="cl-section-title">
                <h2 id="cl-sources-title">数据与更新状态</h2>
                <span>本页可见时每60秒读取最新发布版本</span>
              </div>
              <div className="cl-data-schedule">
                <div>
                  <span>后台采集与观察</span>
                  <p>{status?.schedule}</p>
                </div>
                <div>
                  <span>最近后台核对</span>
                  <p>{time(status?.checkedAt ?? null)}（北京时间）</p>
                </div>
                <div>
                  <span>最后成功发布</span>
                  <p>{time(status?.lastSuccessAt ?? null)}（北京时间）</p>
                </div>
              </div>
              <div className="cl-table-scroll">
                <table className="cl-table cl-source-table">
                  <thead>
                    <tr>
                      <th>序列 / 来源</th>
                      <th>实际覆盖</th>
                      <th>有效点数</th>
                      <th>剔除 / 缺失</th>
                      <th>口径说明</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshot.sources.map((source) => (
                      <tr key={source.id}>
                        <td>
                          <strong>{source.name}</strong>
                          <small>{source.code}</small>
                          <a href={source.url} target="_blank" rel="noreferrer">
                            {source.provider}
                            <ArrowUpRight size={11} aria-hidden="true" />
                          </a>
                        </td>
                        <td>
                          {source.firstDate}
                          <br />至 {source.lastDate}
                        </td>
                        <td>{source.rows.toLocaleString()}</td>
                        <td>
                          {source.excludedRows} / {source.missingSessions}
                        </td>
                        <td>{source.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {snapshot.unavailable.length > 0 && (
                <div className="cl-unavailable">
                  {snapshot.unavailable.map((item) => (
                    <div key={item.code}>
                      <span className="cl-unavailable-dot" />
                      <div>
                        <strong>
                          {item.name} <code>{item.code}</code>
                        </strong>
                        <p>{item.reason}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              <p className="cl-muted-note">
                每条序列按实际公布日期显示，使用共同日期计算股债差距，不把不同日期的最新值拼成同日观察。刷新失败保留上一版，不补零、不把缺失当成没有风险。数据版本{' '}
                <code>{snapshot.version}</code> · 生成时间{' '}
                {time(snapshot.generatedAt)}（北京时间）。
              </p>
            </section>
          </>
        )}
        <OriginalReference />
        <footer className="cl-footer">
          <span>量序 / 长周期数据观察</span>
          <p>
            数据、拟合和历史检验分别呈现。拟合差距用于描述相对位置，不是预测还会下跌多少，也不形成底部确认或交易指令。
          </p>
        </footer>
      </main>
    </div>
  );
}
