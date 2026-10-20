import type { StockRow } from '../lib/stock';
import {
  CircleCheck,
  CircleHelp,
  CirclePause,
  GitMerge,
  TriangleAlert,
  X,
} from 'lucide-react';

const EVIDENCE_HOSTS = new Set([
  'www.sse.com.cn',
  'sse.com.cn',
  'static.sse.com.cn',
  'star.sse.com.cn',
  'static.cninfo.com.cn',
  'disc.static.szse.cn',
  'epaper.cs.com.cn',
]);

function safeEvidenceUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && EVIDENCE_HOSTS.has(url.hostname)
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

export function TradingStatus({
  row,
  idPrefix = '',
  hideNormal = false,
}: {
  row: StockRow;
  idPrefix?: string;
  hideNormal?: boolean;
}) {
  if (hideNormal && row.status === 'ok') return null;
  const special = row.status === 'suspended' ? row.specialStatus : undefined;
  if (special) {
    const evidenceUrl = safeEvidenceUrl(special.announcementUrl);
    const isDelisting = special.type === 'pending_delisting';
    const isMerger =
      special.type === 'merger' || special.type === 'merger_pending_delisting';
    const tone = isDelisting ? 'delisting' : isMerger ? 'merger' : 'suspended';
    const StatusIcon = isDelisting
      ? TriangleAlert
      : isMerger
        ? GitMerge
        : CirclePause;
    const popoverId = `${idPrefix}trading-status-${row.code}-${row.date || 'current'}`;
    return (
      <>
        <button
          type="button"
          className={`trading-status-trigger trading-status-${tone}`}
          popoverTarget={popoverId}
          popoverTargetAction="toggle"
          aria-label={`${special.label}，点击查看具体说明`}
          title={`${special.label} · 点击查看具体说明`}
          aria-haspopup="dialog"
        >
          <span
            className={`status-icon status-icon-${tone}`}
            aria-hidden="true"
          >
            <StatusIcon size={18} strokeWidth={2.1} />
          </span>
        </button>
        <dialog
          id={popoverId}
          popover="auto"
          aria-label={special.label}
          className="trading-status-popover"
        >
          <div className="trading-status-popover-heading">
            <span
              className={`status-icon status-icon-${tone}`}
              aria-hidden="true"
            >
              <StatusIcon size={18} strokeWidth={2.1} />
            </span>
            <div>
              <strong>{special.label}</strong>
              <span>{special.startedAt}起</span>
            </div>
            <button
              type="button"
              className="trading-status-popover-close"
              popoverTarget={popoverId}
              popoverTargetAction="hide"
              aria-label="关闭状态说明"
              title="关闭"
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>
          <p>{special.description}</p>
          <div className="trading-status-evidence">
            <span title={special.announcementTitle}>{special.source}</span>
            {evidenceUrl && (
              <a
                href={evidenceUrl}
                target="_blank"
                rel="noreferrer noopener"
                title={special.announcementTitle || '查看停牌依据'}
              >
                查看依据
              </a>
            )}
          </div>
        </dialog>
      </>
    );
  }

  if (row.status === 'suspended') {
    return (
      <span
        className="status-icon status-icon-suspended"
        title="确认无交易 · 当日停牌或全天成交量为 0"
      >
        <CirclePause
          size={18}
          strokeWidth={2.1}
          aria-label="确认无交易，当日停牌或全天成交量为 0"
        />
      </span>
    );
  }
  if (row.status !== 'ok') {
    const popoverId = `${idPrefix}missing-status-${row.code}-${row.date || 'current'}`;
    return (
      <>
        <button
          type="button"
          className="trading-status-trigger"
          popoverTarget={popoverId}
          popoverTargetAction="toggle"
          aria-label="数据待补，点击查看缺失原因"
          title="数据待补 · 点击查看缺失原因"
          aria-haspopup="dialog"
        >
          <span className="status-icon status-icon-missing" aria-hidden="true">
            <CircleHelp size={18} strokeWidth={2.1} />
          </span>
        </button>
        <dialog
          id={popoverId}
          popover="auto"
          aria-label="缺失数据说明"
          className="trading-status-popover"
        >
          <div className="trading-status-popover-heading">
            <span
              className="status-icon status-icon-missing"
              aria-hidden="true"
            >
              <CircleHelp size={18} strokeWidth={2.1} />
            </span>
            <div>
              <strong>开盘数据待补</strong>
              <span>尚无足够依据判断无交易</span>
            </div>
            <button
              type="button"
              className="trading-status-popover-close"
              popoverTarget={popoverId}
              popoverTargetAction="hide"
              aria-label="关闭缺失说明"
              title="关闭"
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>
          <p>
            {row.reason ||
              (row.status === 'missing'
                ? '目标日期的原始数据尚不完整，开盘占比保持为空。'
                : '记录状态尚未确认，请查看原始数据与来源。')}
          </p>
        </dialog>
      </>
    );
  }
  return (
    <span className="status-icon status-icon-ok" title="正常交易">
      <CircleCheck size={18} strokeWidth={2.1} aria-label="正常交易" />
    </span>
  );
}
