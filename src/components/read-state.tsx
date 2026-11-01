import './workbench-controls.css';

export function RetryMessage({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="stock-read-state" role="alert"><p>{message}</p><button type="button" onClick={onRetry}>重新读取</button></div>;
}

export function StockListNote({ empty = false, outside = false }: { empty?: boolean; outside?: boolean }) {
  return <>{empty && <output className="stock-list-note">当前范围没有结果，请调整分类或搜索。</output>}{outside && <output className="stock-list-note">当前选择不在本次列表中，详情仍保留。</output>}</>;
}
