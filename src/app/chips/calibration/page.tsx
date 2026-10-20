'use client';
import { useEffect, useState } from 'react';
import { ChipTradingChart } from '@/components/chip-trading-chart';
import { StockNavigation } from '@/components/stock-navigation';
import type { DailyCandle } from '@/lib/chip-market';
import '@/components/stock-navigation.css';
import { BrandIdentity } from '@/components/brand';
import { AccountControls } from '@/components/auth-gate';
import { authRead } from '@/lib/auth-fetch';
import { anchoredShare, chipNumber, parseChipManifest, parseChipSample, parseChipScreen,
  type ChipManifest, type ChipSample, type ChipScreenRow } from '@/lib/chips';
import '../chips.css';

export const dynamic = 'force-static';
const api = (process.env.NEXT_PUBLIC_DATA_BASE || '').replace(/\/$/, '');
const fmt = (v: unknown, n = 4) => chipNumber(v).toFixed(n);
const status = { PASS: '满足', FAIL: '不满足', UNKNOWN: '待判定' };
async function read<T>(path: string, signal: AbortSignal, parse: (value: unknown) => T) {
  return authRead(api + path, { signal, cache: 'no-store' }, async response => {
    if (!response.ok) throw Error(response.status === 401 ? '登录已失效，请重新登录。' : `筹码数据暂时无法读取（HTTP ${response.status}）。`);
    return parse(await response.json());
  });
}

export default function ChipsPage() {
  const [tab, setTab] = useState<'distribution' | 'screen'>('distribution');
  const [manifest, setManifest] = useState<ChipManifest | null>(null);
  const [sampleView, setSampleView] = useState<{ dataset: string; value: ChipSample } | null>(null);
  const [screenView, setScreenView] = useState<{ dataset: string; value: ChipScreenRow[] } | null>(null);
  const [code, setCode] = useState('');
  const [day, setDay] = useState('');
  const [previousDay, setPreviousDay] = useState('');
  const [error, setError] = useState('');
  const [screenError, setScreenError] = useState('');
  const [filter, setFilter] = useState('all');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const c = new AbortController();
    setManifest(null); setSampleView(null); setScreenView(null);
    setError(''); setScreenError('');
    read('/data/chips/manifest.json', c.signal, parseChipManifest).then(value => {
      if (c.signal.aborted) return;
      setManifest(value); setCode(value.securities[0]?.code ?? '');
      setDay(value.dates.at(-1)!); setPreviousDay(value.dates.at(-2) ?? '');
    }).catch(e => { if (!c.signal.aborted) setError(e instanceof Error ? e.message : '筹码目录无法读取。'); });
    return () => c.abort();
  }, [retry]);
  useEffect(() => {
    if (!manifest || !code) return;
    const c = new AbortController();
    setSampleView(null); setError('');
    read(`/data/chips/${manifest.dataset_id}/${code}.json`, c.signal, v => parseChipSample(v, code, manifest.dates))
      .then(v => { if (!c.signal.aborted) setSampleView({ dataset: manifest.dataset_id, value: v }); })
      .catch(e => { if (!c.signal.aborted) setError(e instanceof Error ? e.message : '筹码分布无法读取。'); });
    return () => c.abort();
  }, [manifest, code]);
  useEffect(() => {
    if (!manifest) return;
    const c = new AbortController();
    setScreenView(null); setScreenError('');
    read(`/data/chips/${manifest.dataset_id}/screening.json`, c.signal, v => parseChipScreen(v, manifest))
      .then(v => { if (!c.signal.aborted) setScreenView({ dataset: manifest.dataset_id, value: v }); })
      .catch(e => { if (!c.signal.aborted) setScreenError(e instanceof Error ? e.message : '筛选结果无法读取。'); });
    return () => c.abort();
  }, [manifest]);
  const sample = sampleView && sampleView.dataset === manifest?.dataset_id ? sampleView.value : null;
  const screen = screenView && screenView.dataset === manifest?.dataset_id ? screenView.value : null;
  const current = sample?.security_id === code ? sample.snapshots[day] : null;
  const previous = current && previousDay && previousDay < day ? sample!.snapshots[previousDay] : null;
  const shown = screen?.filter(row => filter === 'all' || row.screen_status === filter) ?? [];
  function showStock(value: string) { setCode(value); setTab('distribution'); }
  return <div className="chip-site">
    <header className="chip-site-header">
      <a href="/" className="brand"><BrandIdentity /></a>
      <StockNavigation active="chips" />
      <AccountControls />
    </header>
    <main className="chip-site-main">
      <div className="chip-site-title"><div><p><a href="/chips">← 每日筹码筛选</a></p><h1>历史筹码校准（旧模型）</h1></div><span>成交均价顶点 · 0.01元价格档 · 系数2</span></div>
      <div className="chip-site-scope" role="status">{manifest ? <>
        此页保留旧模型历史样本；210根行情、150动态节点的新模型结果请查看每日筹码筛选。样本日期 {manifest.as_of} · 覆盖 {manifest.covered_securities} 只。
        “没动”标准待截图对比后确定，当前不作筹码稳定判定。
      </> : '正在读取私有筹码目录…'}</div>
      <div className="chip-site-tabs" aria-label="筹码视图"><button type="button" aria-pressed={tab === 'distribution'} onClick={() => setTab('distribution')}>筹码分布</button><button type="button" aria-pressed={tab === 'screen'} onClick={() => setTab('screen')}>样本数据检查</button></div>
      {error && <div className="chip-site-error" role="alert">{error}<button onClick={() => setRetry(v => v + 1)}>重新读取</button></div>}
      {tab === 'distribution' && <section aria-label="个股筹码详情">
        <div className="chip-site-controls">
          <label>股票<select aria-label="筹码股票" value={code} onChange={e => setCode(e.target.value)}>{manifest?.securities.map(s => <option key={s.code} value={s.code}>{s.name} · {s.code}</option>)}</select></label>
          <label>当前日期<select aria-label="筹码日期" value={day} onChange={e => { setDay(e.target.value); if (previousDay >= e.target.value) setPreviousDay(''); }}>{manifest?.dates.map(d => <option key={d} value={d}>{d}</option>)}</select></label>
          <label>对比日期<select aria-label="筹码对比日期" value={previousDay} onChange={e => setPreviousDay(e.target.value)}><option value="">不比较</option>{manifest?.dates.filter(d => d < day).map(d => <option key={d} value={d}>{d}</option>)}</select></label>
        </div>
        {current ? <>
          <div className="chip-site-readout"><span>当前主峰 <strong>{current.peak_prices_cny.join(' / ')}元</strong></span><span>峰档占比 <strong>{fmt(current.peak_bin_share_percent)}%</strong></span><span>估算平均成本 <strong>{fmt(current.model_mean_cost_cny)}元</strong></span></div>
          <ChipTradingChart history={((sample as ChipSample & {history?:DailyCandle[]}).history??[]).filter(b=>b.date<=day)} current={current} previous={previous} date={day} compareDate={previousDay}/>
          {previous && <div className="chip-site-table-scroll"><table><caption>固定{previousDay}的原峰价位，读取{day}同一价位</caption><thead><tr><th>原峰价格</th><th>原日占比</th><th>当前同价位占比</th><th>变化（百分点）</th><th>当前主峰</th></tr></thead><tbody>{previous.peak_prices_cny.map(p => {
            const a = anchoredShare(previous,p), b = anchoredShare(current,p), d = b-a;
            return <tr key={p}><td>{p}元</td><td>{fmt(a)}%</td><td>{fmt(b)}%</td><td>{d > 0 ? '+' : ''}{fmt(d)}</td><td>{current.peak_prices_cny.join(' / ')}元</td></tr>;
          })}</tbody></table></div>}
          <div className="chip-site-table-scroll"><table><caption>同周期每日数据</caption><thead><tr><th>日期</th><th>主峰价格</th><th>主峰档占比</th><th>估算平均成本</th><th>收盘价</th><th>源换手率</th></tr></thead><tbody>{manifest!.dates.map(d => { const s = sample!.snapshots[d]; return <tr key={d}><td>{d}</td><td>{s.peak_prices_cny.join(' / ')}元</td><td>{fmt(s.peak_bin_share_percent)}%</td><td>{fmt(s.model_mean_cost_cny)}元</td><td>{fmt(s.close_cny,2)}元</td><td>{fmt(s.turnover_percent,2)}%</td></tr>; })}</tbody></table></div>
          <p className="chip-site-note">同价位占比包含新分配筹码，不是原持有人留存率。请用相同日期、三角分布、系数2及不复权设置截图，对比后再确定“没动”标准。</p>
        </> : !error && <p className="chip-site-loading" role="status">正在读取筹码分布…</p>}
      </section>}
      {tab === 'screen' && <section aria-label="筹码筛选结果">
        <div className="chip-site-filter-head"><p>收盘价低于10元 · 近10个交易日至少一次收盘涨停 · 筹码条件待定</p><label>显示<select aria-label="筛选结果状态" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">全部</option><option value="UNKNOWN">待判定</option><option value="PASS">明确入选</option><option value="FAIL">明确排除</option></select></label></div>
        {screenError && <p role="alert" className="chip-site-error">{screenError}</p>}
        {!screen && !screenError && <p role="status">正在读取筛选结果…</p>}
        {screen && <><p className="chip-site-note">明确入选 {screen.filter(r=>r.screen_status==='PASS').length} · 待判定 {screen.filter(r=>r.screen_status==='UNKNOWN').length} · 排除 {screen.filter(r=>r.screen_status==='FAIL').length}（仅当前覆盖样本）</p>
          <div className="chip-site-table-scroll"><table><thead><tr><th>股票</th><th>收盘价</th><th>10元以下</th><th>近10日收盘涨停</th><th>筹码条件</th><th>结果</th><th>详情</th></tr></thead><tbody>{shown.map(row => <tr key={row.security_id}><td>{row.name}<small>{row.security_id}</small></td><td>{fmt(row.close_cny,2)}元</td><td>{status[row.conditions.close_below_10]}</td><td>{row.conditions.closing_limit_up_in_10_sessions === 'UNKNOWN' ? '待历史涨停价及交易日历' : status[row.conditions.closing_limit_up_in_10_sessions]}</td><td>等待截图校准后定标准</td><td>{status[row.screen_status]}</td><td><button className="chip-site-link" onClick={() => showStock(row.security_id)}>查看筹码</button></td></tr>)}</tbody></table></div>
          {!shown.length && <p className="chip-site-empty">此范围暂无结果。筹码标准未定、数据缺失的样本仍保留为待判定。</p>}
        </>}
      </section>}
      <details className="chip-site-method"><summary>估算方法与数据范围</summary><p>历史换手衰减系数固定为2，按 a=min(1,2×换手率/100) 递推；当日成交分配为低价至高价的三角分布，峰位取成交额÷成交股数。采用固定0.01元价格档积分。</p><p>当前是回看历史行情的校准样本，尚未与看盘软件逐档核对。早期异常数据保留并从连续有效历史初始化；公司行为及历史可得时点尚未认证。全市场每日结果见独立导航。涨停将按当日适用涨停价判断，不把所有股票统一设成10%。</p></details>
    </main>
  </div>;
}
