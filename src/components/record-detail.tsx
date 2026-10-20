'use client';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/components/ui/dialog';
import { TradingStatus } from '@/components/trading-status';
import { hasDailyQuote } from '@/components/daily-quote';
import {
  formatPct,
  formatPrice,
  pctTone,
  providerLabel,
  type StockRow,
} from '@/lib/stock';

export function RecordDetail({
  row,
  onClose,
}: {
  row: StockRow | null;
  onClose: () => void;
}) {
  const volume = (value: number | null) =>
    value === null ? '—' : value.toLocaleString('zh-CN');
  const price = (value: number | null | undefined) =>
    row && hasDailyQuote(row) ? formatPrice(value) : '—';
  return (
    <Dialog open={Boolean(row)} onOpenChange={(open) => !open && onClose()}>
      {row && (
        <DialogContent className="record-dialog">
          <div className="record-heading">
            <DialogTitle>{row.name}</DialogTitle>
            <DialogDescription>
              {row.code} · {row.date}
            </DialogDescription>
          </div>
          <div className="record-ratio">
            <span>开盘占比</span>
            <strong>
              {row.ratio === null ? '—' : `${row.ratio.toFixed(2)}%`}
            </strong>
          </div>
          <dl className="record-metrics">
            <div>
              <dt>开盘量 / 股</dt>
              <dd>{volume(row.first15Volume)}</dd>
            </div>
            <div>
              <dt>全天量 / 股</dt>
              <dd>{volume(row.dailyVolume)}</dd>
            </div>
            <div>
              <dt>开盘 / 元</dt>
              <dd>{price(row.open)}</dd>
            </div>
            <div>
              <dt>收盘 / 元</dt>
              <dd>{price(row.close)}</dd>
            </div>
            <div>
              <dt>最高 / 元</dt>
              <dd>{price(row.high)}</dd>
            </div>
            <div>
              <dt>最低 / 元</dt>
              <dd>{price(row.low)}</dd>
            </div>
            <div>
              <dt>涨跌幅</dt>
              <dd className={`quote-value ${pctTone(row.pctChange)}`}>
                {formatPct(row.pctChange)}
              </dd>
            </div>
            <div>
              <dt>振幅</dt>
              <dd>{formatPct(row.amplitude).replace(/^\+/, '')}</dd>
            </div>
          </dl>
          <div className="record-status">
            <TradingStatus row={row} idPrefix="detail-" />
            <span>
              {row.specialStatus?.label ||
                (row.status === 'suspended'
                  ? '确认无成交'
                  : row.status === 'missing'
                    ? '数据待补'
                    : '正常交易')}
            </span>
          </div>
          {row.status === 'missing' && (
            <p className="record-note">
              {row.reason || '原始数据不完整，占比留空。'}
            </p>
          )}
          <p className="record-source">
            成交量：{providerLabel(row.sourceProvider)} · 日行情：
            {providerLabel(row.priceSourceProvider)}
          </p>
        </DialogContent>
      )}
    </Dialog>
  );
}
