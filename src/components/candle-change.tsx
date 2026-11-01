import './candle-change.css';
import { priceChangeClass, priceChangeText } from '@/lib/chart-readout';

export function CandleChange({ percent }: { percent: number | null }) {
  return <span className="candle-change" title="相对上一根已发布的不复权K线收盘价，不替代交易所涨跌幅参考价">涨跌幅 <strong className={priceChangeClass(percent)}>{priceChangeText(percent)}</strong></span>;
}
