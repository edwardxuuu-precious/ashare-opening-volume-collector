'use client';

import { useEffect, useRef, useState } from 'react';
import { RetryMessage } from '@/components/read-state';
import { readChip } from '@/lib/chip-read';
import {
  parseConcentrationCandles,
  type ConcentrationCandles,
  type ConcentrationCandleTarget,
} from '@/lib/concentration-candles';
import type {
  ConcentrationRow,
  ConcentrationSource,
} from '@/lib/concentration-screen';
import { TrendChart } from '../trend/trend-chart';

type State =
  | { kind: 'loading'; key: string }
  | { kind: 'error'; key: string; message: string }
  | { kind: 'empty'; key: string }
  | { kind: 'ready'; key: string; stock: ConcentrationCandles };

export function ConcentrationCandles({
  source,
  security,
  period,
  onPeriodChange,
}: {
  source: Pick<ConcentrationSource, 'dataset_id' | 'as_of'>;
  security: Pick<ConcentrationRow, 'security_id' | 'name'>;
  period: number;
  onPeriodChange: (period: number) => void;
}) {
  const { dataset_id, as_of } = source,
    { security_id, name } = security;
  const key = JSON.stringify([dataset_id, as_of, security_id, name]);
  const cache = useRef(new Map<string, ConcentrationCandles>());
  const [state, setState] = useState<State>({ kind: 'loading', key });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    const target: ConcentrationCandleTarget = {
      dataset_id,
      as_of,
      security_id,
      name,
    };
    const accept = (stock: ConcentrationCandles) =>
      setState(
        stock.history.length
          ? { kind: 'ready', key, stock }
          : { kind: 'empty', key },
      );
    const cached = cache.current.get(key);
    if (cached) {
      accept(cached);
      return () => controller.abort();
    }
    setState({ kind: 'loading', key });
    void readChip(
      `/data/chips/daily/${dataset_id}/${security_id}.json`,
      controller.signal,
      (raw) => parseConcentrationCandles(raw, target),
    )
      .then((stock) => {
        if (disposed || controller.signal.aborted) return;
        cache.current.set(key, stock);
        if (cache.current.size > 12) {
          const oldest = cache.current.keys().next();
          if (!oldest.done) cache.current.delete(oldest.value);
        }
        accept(stock);
      })
      .catch((error: unknown) => {
        if (!disposed && !controller.signal.aborted)
          setState({
            kind: 'error',
            key,
            message:
              error instanceof Error ? error.message : '日K线暂时无法读取。',
          });
      });
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [key, dataset_id, as_of, security_id, name, retry]);
  const current: State = state.key === key ? state : { kind: 'loading', key };
  const lastDate =
    current.kind === 'ready' ? current.stock.history.at(-1)?.date : undefined;
  return (
    <section
      className="conc-candles"
      aria-labelledby="concentration-candles-heading"
    >
      <h3 id="concentration-candles-heading">{name}日K与成交量</h3>
      {current.kind === 'ready' ? (
        <>
          {lastDate && lastDate < as_of && (
            <p className="conc-note">
              最后保存的行情为 {lastDate}，筛选数据截至 {as_of}。
            </p>
          )}
          <TrendChart
            stock={current.stock}
            date={as_of}
            screen={null}
            periodValue={period}
            onPeriodChange={onPeriodChange}
          />
        </>
      ) : (
        <div
          className="conc-candle-state"
          aria-live="polite"
          aria-busy={current.kind === 'loading'}
        >
          <div className="conc-candle-reservation" aria-hidden="true">
            <div className="wb-loading-controls" />
            <div className="wb-loading-readout" />
            <div className="wb-chart-placeholder" />
          </div>
          <div className="conc-candle-message">
          {current.kind === 'loading' ? (
            <p>正在读取{name}日K线…</p>
          ) : current.kind === 'empty' ? (
            <p>本批次没有已保存的{name}日K线。</p>
          ) : (
            <RetryMessage
              message={current.message}
              onRetry={() => setRetry((value) => value + 1)}
            />
          )}
          </div>
        </div>
      )}
    </section>
  );
}
