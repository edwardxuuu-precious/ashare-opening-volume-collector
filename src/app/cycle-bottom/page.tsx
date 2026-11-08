'use client';

import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUpRight, Check, Copy, Expand, X } from 'lucide-react';
import { SiteHeader } from '@/components/site-header';
import './cycle-bottom.css';

export const dynamic = 'force-static';

const equityImage = {
  src: '/research/cycle-bottom/equity-bond-comparison.png',
  width: 1080,
  height: 322,
  title: '沪深300全收益与二级债基累计走势对照',
  alt: '用户提供的历史叠加图：黑色沪深300全收益曲线与灰紫色万得混合债券型二级指数曲线，多处靠近位置被红圈标注。原图未提供完整日期轴和归一化参数。',
};
const monthlyImage = {
  src: '/research/cycle-bottom/shanghai-monthly-channel.png',
  width: 1080,
  height: 1551,
  title: '上证指数月线与国债指数参考图',
  alt: '用户提供的上证指数月K线截图，叠加橙色长期通道与蓝色国债指数，标有6124.04及998.23。上方显示3658.03，截图未标明具体交易日。',
};
type ReferenceImage = typeof equityImage;

const history = [
  {
    year: '2012',
    question: '靠近之后，是否还会反复探底？',
    detail:
      '核对该年第一次靠近参照线的日期，以及之后是否再次创出更低点。区分首次触线与事后挑出的最终低点。',
  },
  {
    year: '2016',
    question: '触线与止跌，谁先发生？',
    detail:
      '把触线日期与实际低点逐日对齐，记录领先或滞后的交易日数，再看触线后是否仍有明显回撤。',
  },
  {
    year: '2018',
    question: '观察窗口跨年，结论还成立吗？',
    detail:
      '将核对区间延伸至次年，统一使用相同的观察窗口，避免以自然年结束作为行情已经止跌的依据。',
  },
  {
    year: '2022',
    question: '一年内多次靠近，该如何计数？',
    detail:
      '列出全年每一次靠近和跌破，把持续靠近合并为一个观察区间，避免把同一轮行情重复算成多次成功。',
  },
  {
    year: '2024',
    question: '换一个基期，还会在这里相遇吗？',
    detail:
      '固定指数与数据区间后，更换归一化起点、替换纯债参照，检验所见关系是否高度依赖画图参数。',
  },
];

function ReferenceFigure({
  image,
  className = '',
  onExpand,
}: {
  image: ReferenceImage;
  className?: string;
  onExpand: (image: ReferenceImage) => void;
}) {
  return (
    <figure className={`cb-figure ${className}`}>
      <button
        className="cb-image-button"
        onClick={() => onExpand(image)}
        aria-label={`放大查看${image.title}`}
      >
        <img
          src={image.src}
          width={image.width}
          height={image.height}
          alt={image.alt}
          loading={image === monthlyImage ? 'lazy' : 'eager'}
        />
        <span className="cb-expand">
          <Expand size={14} aria-hidden="true" /> 放大原图
        </span>
      </button>
      <figcaption>
        用户提供原图 · 水印：公众号「交易趋势分析」· 截图交易日未注明
      </figcaption>
    </figure>
  );
}

function ImageViewer({
  image,
  onClose,
}: {
  image: ReferenceImage;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [originalSize, setOriginalSize] = useState(false);
  useEffect(() => {
    const node = dialog.current;
    const focused = document.activeElement;
    const overflow = document.body.style.overflow;
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
      className="cb-viewer"
      aria-labelledby="cb-viewer-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="cb-viewer-bar">
        <h2 id="cb-viewer-title">{image.title}</h2>
        <button
          onClick={() => setOriginalSize((value) => !value)}
          aria-pressed={originalSize}
        >
          {originalSize ? '适应屏幕' : '原始尺寸'}
        </button>
        <button onClick={onClose} aria-label="关闭原图">
          <X size={20} aria-hidden="true" />
        </button>
      </div>
      <div
        className={`cb-viewer-scroll ${originalSize ? 'cb-original-size' : ''}`}
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

export default function CycleBottomPage() {
  const [selectedYear, setSelectedYear] = useState(history[0]);
  const [image, setImage] = useState<ReferenceImage | null>(null);
  const [shareState, setShareState] = useState<'idle' | 'copied' | 'failed'>(
    'idle',
  );
  useEffect(() => {
    const previousTitle = document.title;
    document.title = '大周期底部观察 · 量序';
    return () => {
      document.title = previousTitle;
    };
  }, []);
  useEffect(() => {
    if (shareState === 'idle') return;
    const timer = window.setTimeout(() => setShareState('idle'), 3000);
    return () => window.clearTimeout(timer);
  }, [shareState]);
  async function share() {
    try {
      await navigator.clipboard.writeText(`${location.origin}/cycle-bottom`);
      setShareState('copied');
    } catch {
      setShareState('failed');
    }
  }
  return (
    <div className="cb-site">
      <SiteHeader active="cycle-bottom" />
      <main className="cb-main">
        <div className="cb-document-bar">
          <span>
            <span className="cb-status-dot" /> 长周期研究 / 观察笔记 01
          </span>
          <button className="cb-share" onClick={share}>
            {shareState === 'copied' ? (
              <Check size={14} aria-hidden="true" />
            ) : (
              <Copy size={14} aria-hidden="true" />
            )}
            <span aria-live="polite">
              {shareState === 'copied'
                ? '链接已复制'
                : shareState === 'failed'
                  ? '请复制地址栏链接'
                  : '复制页面链接'}
            </span>
          </button>
        </div>

        <header className="cb-hero">
          <div>
            <p className="cb-eyebrow">THE LONG VIEW</p>
            <h1>
              沪深股指
              <br />
              <span>大周期底部观察</span>
            </h1>
            <p className="cb-lead">
              当股票的长期回报，回到债券参照附近。
              <br />
              用股债相对位置与月线结构，观察大周期的底部区域。
            </p>
            <a className="cb-read-link" href="#equity-bond">
              从两条曲线开始 <ArrowDown size={15} aria-hidden="true" />
            </a>
          </div>
          <aside className="cb-current" aria-label="当前研判状态">
            <span className="cb-small-label">当前研判</span>
            <div className="cb-current-state">
              <i /> 待同口径数据核对
            </div>
            <p>
              原文判断「离底部还有距离」。截图可用于理解方法，最新位置仍需用完整序列重新计算。
            </p>
            <dl>
              <div>
                <dt>距参照线</dt>
                <dd>待计算</dd>
              </div>
              <div>
                <dt>底部是否确认</dt>
                <dd>尚不能判定</dd>
              </div>
            </dl>
            <span className="cb-asof">整理于 2026.11.08 · 非实时行情</span>
          </aside>
        </header>

        <nav className="cb-section-nav" aria-label="本页目录">
          <a href="#equity-bond">
            <span>01</span> 股债相对位置
          </a>
          <a href="#monthly">
            <span>02</span> 月线长期结构
          </a>
          <a href="#framework">
            <span>03</span> 如何观察底部
          </a>
          <a href="#sources">
            <span>04</span> 口径与来源
          </a>
        </nav>

        <section
          className="cb-section"
          id="equity-bond"
          aria-labelledby="cb-equity-title"
        >
          <div className="cb-section-heading">
            <div>
              <p className="cb-eyebrow">01 / RELATIVE RETURN</p>
              <h2 id="cb-equity-title">两条曲线，相遇意味着什么？</h2>
            </div>
            <span className="cb-tag">历史图示 · 待复核假设</span>
          </div>
          <p className="cb-section-intro">
            原文提出：沪深300全收益指数与二级债基指数靠近、重合时，可能对应股票市场的长期低位。先看相对位置，再检查止跌是否真正发生。
          </p>
          <div className="cb-chart-frame">
            <div className="cb-chart-heading">
              <span>股债长期累计走势</span>
              <div className="cb-legend">
                <span>
                  <i className="cb-equity-line" /> 沪深300全收益
                </span>
                <span>
                  <i className="cb-bond-line" /> 二级债基
                </span>
                <span>
                  <i className="cb-circle" /> 原图圈注
                </span>
              </div>
            </div>
            <ReferenceFigure image={equityImage} onExpand={setImage} />
          </div>
          <div className="cb-reading-note">
            <span>读图要点</span>
            <p>
              图中标识为 <b>H00300.CSI</b> 与 <b>885007.WI</b>
              。红圈是原作者的历史标记；原图缺少完整日期轴、基期和叠加设置，尚不能逐点确认触线日期及之后的表现。
            </p>
          </div>

          <div className="cb-history-heading">
            <h3>原文提到的五个年份</h3>
            <span>选择年份，查看复核重点</span>
          </div>
          <div className="cb-history" aria-label="历史年份">
            {history.map((item) => (
              <button
                key={item.year}
                onClick={() => setSelectedYear(item)}
                aria-pressed={selectedYear.year === item.year}
                aria-controls="cb-history-detail"
              >
                <span>{item.year}</span>
                <small>原文观察节点</small>
                <ArrowUpRight size={16} aria-hidden="true" />
              </button>
            ))}
          </div>
          <div
            className="cb-history-detail"
            id="cb-history-detail"
            aria-live="polite"
          >
            <strong>
              {selectedYear.year} <span>复核问题</span>
            </strong>
            <div>
              <h4>{selectedYear.question}</h4>
              <p>{selectedYear.detail}</p>
            </div>
            <span className="cb-pending">待原始序列复核</span>
          </div>

          <details className="cb-original">
            <summary>
              查看完整原文观点 <span>保留原始表述</span>
            </summary>
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
            <p className="cb-original-context">
              用户提供文字，原始发表日期未注明。「目前」对应原文语境，不能自动作为本页整理日的市场判断。
            </p>
          </details>
        </section>

        <section
          className="cb-section cb-monthly-section"
          id="monthly"
          aria-labelledby="cb-monthly-title"
        >
          <div className="cb-section-heading">
            <div>
              <p className="cb-eyebrow">02 / MONTHLY STRUCTURE</p>
              <h2 id="cb-monthly-title">把相对位置，放回长期结构中</h2>
            </div>
            <span className="cb-tag">上证指数 · 月线视角</span>
          </div>
          <div className="cb-monthly-grid">
            <ReferenceFigure
              image={monthlyImage}
              className="cb-monthly-image"
              onExpand={setImage}
            />
            <div className="cb-monthly-copy">
              <p className="cb-monthly-deck">
                第一张图看股债回报差，
                <br />
                这张图看价格所处的长期位置。
              </p>
              <div className="cb-observation">
                <span>01</span>
                <div>
                  <h3>橙色通道：观察长期位置</h3>
                  <p>
                    原图画有一组向上的平行线。价格靠近通道下沿时，可作为进一步观察的线索；趋势线取点与延长方法需要固定后才能复现。
                  </p>
                </div>
              </div>
              <div className="cb-observation">
                <span>02</span>
                <div>
                  <h3>蓝色曲线：核对国债参照</h3>
                  <p>
                    界面标注「国债指数」。具体指数代码、基期与叠加比例未提供，不能直接把它与上证点位的交会解释为收益率相等。
                  </p>
                </div>
              </div>
              <div className="cb-observation">
                <span>03</span>
                <div>
                  <h3>月线变化：等待结构确认</h3>
                  <p>
                    可继续观察是否停止创低、是否形成更高低点、是否重新站回预先定义的结构。确认标准应在复核前固定，避免随走势改线。
                  </p>
                </div>
              </div>
              <p className="cb-image-context">
                这张图使用上证价格指数，与第一张图的沪深300全收益指数口径不同。截图中的
                3658.03、231.59
                及数字标记均保留为原图信息；右端月份标签不等于确切交易日。
              </p>
            </div>
          </div>
        </section>

        <section
          className="cb-section"
          id="framework"
          aria-labelledby="cb-framework-title"
        >
          <div className="cb-section-heading">
            <div>
              <p className="cb-eyebrow">03 / OBSERVATION FRAMEWORK</p>
              <h2 id="cb-framework-title">从「靠近」到「底部」，分三步看</h2>
            </div>
          </div>
          <div className="cb-steps">
            <article>
              <span className="cb-step-number">01</span>
              <h3>位置：进入观察区域</h3>
              <p>
                同一基期下，股票与债券类累计回报的差距是否收窄？更换基期与纯债参照后，关系是否仍然存在？
              </p>
              <span className="cb-step-footer">靠近参照 → 提高关注</span>
            </article>
            <article>
              <span className="cb-step-number">02</span>
              <h3>结构：检查止跌过程</h3>
              <p>
                月线是否继续创低？预先选定的长期结构是否被守住？股债位置与价格结构应分别核对。
              </p>
              <span className="cb-step-footer">出现变化 → 等待确认</span>
            </article>
            <article>
              <span className="cb-step-number">03</span>
              <h3>验证：保留失败样本</h3>
              <p>
                统计每次靠近后的回撤与后续表现，同时检查未触线的底部。统一阈值、窗口，再做样本外检验。
              </p>
              <span className="cb-step-footer">完整样本 → 评估可靠性</span>
            </article>
          </div>
          <div className="cb-method">
            <div>
              <p className="cb-eyebrow">先统一口径</p>
              <h3>
                比较累计回报，
                <br />
                才能谈相对位置。
              </h3>
              <p>
                全收益指数考虑现金分红再投资。二级债基允许持有股票，其回报也受到权益市场影响。
                <a href="#cb-source-csi">[1]</a>{' '}
                <a href="#cb-source-bond">[3]</a>
              </p>
            </div>
            <div className="cb-formula">
              <span>同一基期归一化 · 相对差距</span>
              <p>
                G(t) ={' '}
                <span className="cb-fraction">
                  <span>S(t) / S(t₀)</span>
                  <span>B(t) / B(t₀)</span>
                </span>{' '}
                − 1
              </p>
              <small>
                S：股票全收益指数　B：债券类回报序列
                <br />
                t₀：共同起点　t：同一观察日
              </small>
              <div>
                <b>G = 0</b>{' '}
                表示从共同起点出发，累计回报相同。它本身不能证明估值见底，也不等于股票盈利收益率或债券到期收益率相等。
              </div>
            </div>
          </div>
          <div className="cb-limit">
            <h3>什么会让这个假设失效？</h3>
            <p>
              更换基期后交点明显改变；触线后仍持续创新低；存在大量触线却未止跌的样本；关键底部未触线；替换纯债参照后关系消失。出现这些情况时，应降低对该方法的信任，重新评估适用范围。
            </p>
          </div>
        </section>

        <section
          className="cb-section cb-sources"
          id="sources"
          aria-labelledby="cb-sources-title"
        >
          <div className="cb-section-heading">
            <div>
              <p className="cb-eyebrow">04 / READING NOTES</p>
              <h2 id="cb-sources-title">口径与来源</h2>
            </div>
            <span className="cb-asof">核对日期 2026.11.08</span>
          </div>
          <div className="cb-source-list">
            <div>
              <span>原始材料</span>
              <p>
                本页文字与两张图片由用户提供。图像完整保留，原文章链接、发表日期、原始时间序列及图表参数未提供。五个年份与「国债也有同样规律」均为待检验的原文观点。
              </p>
            </div>
            <div id="cb-source-csi">
              <span>[1] 全收益口径</span>
              <p>
                <a
                  href="https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000300_Index_Methodology_cn.pdf"
                  target="_blank"
                  rel="noreferrer"
                >
                  中证指数《沪深300指数编制方案》§4.6{' '}
                  <ArrowUpRight size={12} aria-hidden="true" />
                </a>
                ：读取到的文件署期为2023年9月，说明税前现金红利再投资的计算口径。本页引用这一口径，不据此声明已审计全部后续修订。
              </p>
            </div>
            <div>
              <span>[2] 指数代码</span>
              <p>
                <a
                  href="https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000300factsheet.pdf"
                  target="_blank"
                  rel="noreferrer"
                >
                  中证指数沪深300指数单张 · 2026.10.30{' '}
                  <ArrowUpRight size={12} aria-hidden="true" />
                </a>
                ：衍生指数表列明全收益指数代码
                H00300。该资料不验证图中股债触线规律。
              </p>
            </div>
            <div id="cb-source-bond">
              <span>[3] 二级债基</span>
              <p>
                <a
                  href="https://edu.chinaamc.com/c/2026-01-21/769764.shtml"
                  target="_blank"
                  rel="noreferrer"
                >
                  华夏基金投教《债基种类多，如何选到适合那款？》· 2026.01.21{' '}
                  <ArrowUpRight size={12} aria-hidden="true" />
                </a>
                ：用于核对二级债基可持有股票的类别特征；未用于验证 Wind 885007
                的具体编制、历史样本或回测结果。
              </p>
            </div>
          </div>
          <p className="cb-next-check">
            <b>下一步最小验证</b>　取得 H00300、885007
            及明确代码的国债回报序列，固定共同起点与触线阈值，先核对全部触线日期及之后的最大回撤。新数据、指数编制调整或基期变化均需要重新核对。
          </p>
        </section>
        <footer className="cb-footer">
          <span>
            量序 <i>/</i> 长周期观察档案
          </span>
          <p>
            用于长期市场研究。历史图示与观察假设不构成交易信号；本页未建立胜率、目标点位或买卖建议。
          </p>
          <a
            href="#top"
            onClick={(event) => {
              event.preventDefault();
              window.scrollTo({ top: 0, behavior: 'smooth' });
            }}
          >
            回到顶部 ↑
          </a>
        </footer>
      </main>
      {image && <ImageViewer image={image} onClose={() => setImage(null)} />}
    </div>
  );
}
