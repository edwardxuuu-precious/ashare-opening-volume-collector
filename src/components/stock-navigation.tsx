import './stock-navigation.css';
export function StockNavigation({active}:{active:'opening'|'chips'|'golden'}) {
  return <nav className="stock-feature-nav" aria-label="主要功能"><a href="/" aria-current={active==='opening'?'page':undefined}>开盘量查询</a><a href="/chips" aria-current={active==='chips'?'page':undefined}>每日筹码筛选</a><a href="/golden" aria-current={active==='golden'?'page':undefined}>黄金分割统计</a></nav>;
}
