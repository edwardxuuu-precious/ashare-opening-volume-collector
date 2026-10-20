'use client';
import {useEffect,useMemo,useState} from 'react';
import {BrandIdentity} from '@/components/brand';
import {AccountControls} from '@/components/auth-gate';
import {StockNavigation} from '@/components/stock-navigation';
import {ChipTradingChart} from '@/components/chip-trading-chart';
import {readChip} from '@/lib/chip-read';
import {parseMarketManifest,type MarketManifest,type MarketState,type DailyCandle} from '@/lib/chip-market';
import {parseGoldenMarket,parseGoldenHistory,goldenLines,goldenReason,type GoldenMarket} from '@/lib/golden-grid';
import '../chips/chips.css';
import '../chips/workbench.css';
import './golden.css';
export const dynamic='force-static';
const labels={PASS:'有效格子',FAIL:'未形成 / 已失效',UNKNOWN:'待核实'};
export default function GoldenPage(){
  const [manifest,setManifest]=useState<MarketManifest|null>(null),[market,setMarket]=useState<GoldenMarket|null>(null);
  const [filter,setFilter]=useState<MarketState|'ALL'>('PASS'),[search,setSearch]=useState(''),[code,setCode]=useState(''),[page,setPage]=useState(0);
  const [stock,setStock]=useState<{security_id:string;history:DailyCandle[]}|null>(null),[error,setError]=useState(''),[detailError,setDetailError]=useState(''),[retry,setRetry]=useState(0);
  useEffect(()=>{const c=new AbortController();setError('');setMarket(null);setManifest(null);setStock(null);
    readChip('/data/chips/daily/manifest.json',c.signal,parseMarketManifest).then(async m=>{const value=await readChip(`/data/chips/daily/${m.dataset_id}/golden.json`,c.signal,v=>parseGoldenMarket(v,m));if(!c.signal.aborted){setManifest(m);setMarket(value);setCode(value.rows.find(r=>r.status==='PASS')?.security_id??value.rows[0]?.security_id??'');}}).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();
  },[retry]);
  useEffect(()=>{if(!manifest||!code)return;const c=new AbortController();setStock(null);setDetailError('');readChip(`/data/chips/daily/${manifest.dataset_id}/${code}.json`,c.signal,v=>parseGoldenHistory(v,code,manifest)).then(v=>{if(!c.signal.aborted)setStock(v);}).catch(e=>{if(!c.signal.aborted)setDetailError(e.message);});return()=>c.abort();},[manifest,code]);
  const matched=useMemo(()=>(market?.rows??[]).filter(r=>(filter==='ALL'||r.status===filter)&&`${r.name} ${r.security_id}`.toLowerCase().includes(search.trim().toLowerCase())),[market,filter,search]);
  const selected=market?.rows.find(r=>r.security_id===code),g=selected?.geometry,shown=matched.slice(page*100,(page+1)*100);
  const changeFilter=(value:MarketState|'ALL')=>{setFilter(value);setPage(0);const first=market?.rows.find(r=>(value==='ALL'||r.status===value)&&`${r.name} ${r.security_id}`.toLowerCase().includes(search.trim().toLowerCase()));setCode(first?.security_id??'');};
  return <div className="chip-site chip-workbench golden-workbench">
    <header className="chip-site-header cw-header"><a href="/" className="brand"><BrandIdentity/></a><StockNavigation active="golden"/><div className="cw-header-actions"><button className="chip-site-link" onClick={()=>setRetry(n=>n+1)}>刷新</button><AccountControls/></div></header>
    <main className="cw-shell"><aside className="cw-sidebar" aria-label="全市场黄金分割统计">
      <div className="cw-list-title"><h1>黄金分割统计</h1><span>{market?`${market.universe_total.toLocaleString()}只`:'全市场'}</span></div>
      <p className="cw-selection-rule">全市场独立统计 · 研究版画格<br/><b>逐层平分 50% / 25% / 12.5%</b><br/>有效结构：跌幅≥20%，后续未破低</p>
      <div className="cw-search"><input type="search" aria-label="查找股票" placeholder="搜索名称或代码" value={search} onChange={e=>{setSearch(e.target.value);setPage(0);}}/></div>
      <div className="cw-filters" aria-label="结构状态">{(['PASS','ALL','UNKNOWN','FAIL'] as const).map(k=><button key={k} aria-pressed={filter===k} onClick={()=>changeFilter(k)}>{k==='ALL'?'全部':k==='PASS'?'有效':k==='UNKNOWN'?'待核实':'未形成'} <b>{market?(k==='ALL'?market.universe_total:market.counts[k]).toLocaleString():'—'}</b></button>)}</div>
      <div className="cw-stock-list">{shown.map(row=><button className="cw-stock-row" key={row.security_id} aria-pressed={code===row.security_id} onClick={()=>setCode(row.security_id)}><span className="cw-stock-line"><strong>{row.name}</strong><span className="cw-stock-price">{row.geometry?Number(row.geometry.close).toFixed(2):'—'}</span></span><span className="cw-stock-line"><span className="cw-code">{row.security_id}</span><span className={`cw-row-state cw-row-${row.status.toLowerCase()}`}>{labels[row.status]}</span></span>{row.geometry&&<span className="golden-row-distance">距选中轴 {Number(row.geometry.distance_percent)>0?'+':''}{Number(row.geometry.distance_percent).toFixed(2)}%</span>}</button>)}{!shown.length&&<p className="cw-list-empty">{error||(!market?'正在读取全市场统计…':'当前范围没有结果。')}</p>}</div>
      <div className="cw-list-footer cw-pagination"><span>{page+1} / {Math.max(1,Math.ceil(matched.length/100))}页</span><button aria-label="上一页" disabled={page===0} onClick={()=>setPage(p=>p-1)}>‹</button><button aria-label="下一页" disabled={(page+1)*100>=matched.length} onClick={()=>setPage(p=>p+1)}>›</button></div>
    </aside><section className="cw-workspace chip-detail" aria-label="价格格子详情">
      {!market&&<div className="cw-empty"><h2>全市场价格格子</h2><p role={error?'alert':'status'}>{error||'正在读取已计算的统计结果…'}</p><button className="chip-site-link" onClick={()=>setRetry(n=>n+1)}>重新读取</button></div>}
      {selected&&<><div className="golden-title"><div><h2>{selected.name} <small>{selected.security_id}</small></h2><p>{selected.as_of} · {labels[selected.status]} · {goldenReason(selected.reason)}</p></div><a href="/chips">筹码工作台 →</a></div>
      {!matched.some(r=>r.security_id===code)&&<p className="cw-notice">当前股票不在左侧搜索结果中；点击名单可切换。</p>}
      {g&&<><div className="golden-metrics"><div><span>选中轴 · {Number(g.ratio)*100}%层</span><strong>{g.midpoint}</strong></div><div><span>收盘相对选中轴</span><strong>{g.close_side==='ABOVE'?'高于':g.close_side==='BELOW'?'低于':'等于'} <small>{Number(g.distance_percent)>0?'+':''}{Number(g.distance_percent).toFixed(2)}%</small></strong></div><div><span>当日收盘跨线</span><strong>{g.crossing==='UP'?'向上跨过':g.crossing==='DOWN'?'向下跨过':g.crossing==='NONE'?'未跨线':'暂不可比'}</strong></div></div>
      <p className="golden-anchor-note">H {g.H_date} · High {Number(g.H).toFixed(3)} <span>→</span> L {g.L_date} · Low {Number(g.L).toFixed(3)} <span>原跌幅 {Number(g.drawdown_percent).toFixed(2)}%</span></p>
      {stock?.security_id===code?<ChipTradingChart key={code} history={stock.history} current={null} previous={null} date={selected.as_of} compareDate="" appearance="workbench" gridLines={goldenLines(g)}/>:<p role={detailError?'alert':'status'}>{detailError||'正在读取日K线与成交量…'}</p>}
      <div className="golden-facts"><span>子空间：{Number(g.L).toFixed(3)} — {Number(g.upper).toFixed(3)}</span><span>盘中{g.intraday_touch?'已触及':'未触及'}选中轴</span><span>{goldenReason(g.comparison)}</span></div></>}
      {!g&&<p className="cw-notice">{goldenReason(selected.reason)}。当前没有完整的格子价格可展示。</p>}</>}
      <details className="cw-method chip-site-method" open={!selected}><summary>统计范围与画格规则</summary><p>当前沪深京在市股票独立统计，全部股票均保留状态；不叠加筹码页的低于10元、近10日涨停或成本位置条件。</p><p>沿用已采用的研究版60/5规则：在t−64至t−5的60根行情中取最高High，同价取后；再取其后同窗口最低Low，同价取后。后续破低判失效，缺失交易日行情保持待核实。研究结构最小跌幅20%。</p><p>分层保留原研究口径：跌幅≤20%取50%层，20%至50%取25%层，超过50%取12.5%层。价格线 = L + 比例 × (H−L)。原研究结构和群主每个案例的选点仍需分别核对。</p><p>盘中触线与收盘跨线分别统计；只有前后两天均为有效、选点及分层相同时才判断收盘跨线。格子变化、结构失效及缺项显示暂不可比。首次跨线不表示买入，统计不生成订单。</p><p>当前格子统一截至统计日期，水平线叠加在历史K线上用于查看当前空间。当前版本不把这些线当作更早日期当时已知的格子，也不认证原始历史known_at或收益表现。</p></details>
    </section></main><footer className="cw-global-footer"><span>全市场独立统计 · 研究版画格 · 缺失保持待核实</span><span>{market?`${market.as_of} · ${market.universe_total.toLocaleString()}只`:'读取统计结果中'}</span></footer>
  </div>;
}
