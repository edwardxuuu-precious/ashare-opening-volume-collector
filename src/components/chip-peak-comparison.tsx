import { anchoredShare, chipNumber, type ChipSnapshot } from '@/lib/chips';
import { chipChange } from '@/lib/chip-display';
export function ChipPeakComparison({current,previous,date,previousDate,workbench=false}:{current:ChipSnapshot;previous:ChipSnapshot|null;date:string;previousDate:string;workbench?:boolean}){
  const difference=previous?.peak_prices_cny.length===1&&current.peak_prices_cny.length===1?chipNumber(current.peak_prices_cny[0])-chipNumber(previous.peak_prices_cny[0]):null;
  if(workbench){
    const anchors=previous?.peak_prices_cny.map(price=>({price,previous:anchoredShare(previous,price),current:anchoredShare(current,price)}))??[],one=anchors.length===1?anchors[0]:null;
    return <section className="cw-comparison" aria-label="峰价和原峰筹码占比变化">
      <div className="cw-compare-grid">
        <div className="cw-metric"><p>主峰价格</p><div className="cw-metric-value">{previous&&<>{previous.peak_prices_cny.join(' / ')} <span>→</span> </>}{current.peak_prices_cny.join(' / ')}<small>元</small></div><div className="cw-metric-note">{difference!==null?`较前一交易日 ${difference>0?'+':''}${difference.toFixed(2)}元`:previous?'存在多个主峰，逐峰数据见下方':'当前仅有当日筹码，无前日对比'}</div></div>
        <div className="cw-metric"><p>{previous?'原峰同价区间占比':'当前峰档占比'}</p><div className="cw-metric-value">{one?<>{one.current.toFixed(4)}<small>%</small></>:previous?`${anchors.length}个原峰区间`:<>{chipNumber(current.peak_bin_share_percent).toFixed(4)}<small>%</small></>}</div><div className="cw-metric-note">{one?`原峰 ${one.price}元 · 前日 ${one.previous.toFixed(4)}%`:previous?'保留每个原峰的独立比较':'未推算前一交易日占比'}</div></div>
        <div className="cw-metric"><p>{previous?'占比日变化':'估算平均成本'}</p><div className="cw-metric-value">{one?<>{chipChange(one.current-one.previous)}<small>百分点</small></>:previous?'见逐峰对照':<>{chipNumber(current.model_mean_cost_cny).toFixed(4)}<small>元</small></>}</div><div className="cw-metric-note">{one?`固定追踪 ${one.price}元 原峰区间`:previous?'多峰不合并为单一变化值':'由当前筹码分布估算'}</div></div>
      </div>
      <p className="cw-comparison-note">{difference!==null?difference===0?'主峰价格持平。':difference>0?'主峰价格上移。':'主峰价格下移。':''}同价区间含新分配筹码，不代表原持有人留存率；尚未判断“没动”。</p>
      <details className="cw-exact-comparison" open={anchors.length>1}><summary>逐峰占比与计算窗口</summary>
        {previous&&<div className="chip-site-table-scroll chip-peak-table-wrap"><table className="chip-peak-table"><caption>固定{previousDate}的原峰价格区间，比较{date}同区间筹码占比</caption><thead><tr><th>原峰价</th><th>原日占比</th><th>当前同区间占比</th><th>变化（百分点）</th></tr></thead><tbody>{anchors.map(anchor=><tr key={anchor.price}><td>{anchor.price}元</td><td>{anchor.previous.toFixed(4)}%</td><td>{anchor.current.toFixed(4)}%</td><td>{chipChange(anchor.current-anchor.previous)}</td></tr>)}</tbody></table></div>}
        <p>所选日 {date}，{previous?`对比 ${previousDate}`:'前日对比不适用'}。峰档占比 {chipNumber(current.peak_bin_share_percent).toFixed(4)}%，估算平均成本 {chipNumber(current.model_mean_cost_cny).toFixed(4)}元。</p>
        {current.price_grid_kind==='EASTMONEY_150_DYNAMIC_NODES'&&<p>150动态节点 · 系数2 · 回放窗口 {current.window_source_start_date} 至 {date}，{current.loaded_window_rows}根有效行情（上限210根），前日使用同窗前缀回放。</p>}
      </details>
    </section>;
  }
  return <>{previous&&<p className="chip-peak-transition">主峰：{previousDate} <strong>{previous.peak_prices_cny.join(' / ')}元</strong> → {date} <strong>{current.peak_prices_cny.join(' / ')}元</strong>{difference!==null&&<span>（{difference>0?'+':''}{difference.toFixed(2)}元）</span>}</p>}<div className="chip-site-readout"><span>所选日主峰 <strong>{current.peak_prices_cny.join(' / ')}元</strong></span><span>峰档占比 <strong>{chipNumber(current.peak_bin_share_percent).toFixed(4)}%</strong></span><span>估算平均成本 <strong>{chipNumber(current.model_mean_cost_cny).toFixed(4)}元</strong></span></div>
    {current.price_grid_kind==='EASTMONEY_150_DYNAMIC_NODES'&&<p className="chip-site-note">150动态节点 · 系数2 · 回放窗口{current.window_source_start_date?` ${current.window_source_start_date} 至 ${date}`:''}，最近{current.loaded_window_rows}根有效日线（上限210根）；前一交易日使用同一窗口的前缀回放。</p>}
    {previous&&<div className="chip-site-table-scroll chip-peak-table-wrap"><table className="chip-peak-table"><caption>固定{previousDate}的原峰价格区间，比较{date}同区间筹码占比</caption><thead><tr><th>原峰价</th><th>原日占比</th><th>当前同区间占比</th><th>变化（百分点）</th></tr></thead><tbody>{previous.peak_prices_cny.map(p=>{const a=anchoredShare(previous,p),b=anchoredShare(current,p);return <tr key={p}><td>{p}元</td><td>{a.toFixed(4)}%</td><td>{b.toFixed(4)}%</td><td>{chipChange(b-a)}</td></tr>;})}</tbody></table></div>}
    <p className="chip-site-note">同价位占比包含新分配筹码，不代表原持有人留存率。“没动”的阈值尚未设置，待截图校准。</p></>;
}
