'use client';
import {useEffect,useLayoutEffect,useState,useRef,useCallback,useId} from 'react';
import {authFetch} from '@/lib/auth-fetch';
import {readChip} from '@/lib/chip-read';
import {pointLabel,type ManualDraft} from '@/lib/golden-drawing';
import {reviewLabels,type Annotations,type Drawing,type Example,type ReviewRound} from '@/lib/golden-workbench';
export {blankDraft,type ManualDraft} from '@/lib/golden-drawing';
const api=(process.env.NEXT_PUBLIC_DATA_BASE||'').replace(/\/$/,'');
const time=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
export function GoldenReviewTools({example,user,draft,setDraft,resetDraft,setDrawing,setShowManual,autoMode,axisId,initialRound,onViewRound}:{example:Example;user:string;draft:ManualDraft;setDraft:(d:ManualDraft)=>void;resetDraft:(d:ManualDraft)=>void;setDrawing:(v:boolean)=>void;setShowManual:(v:boolean)=>void;autoMode:string;axisId:string;initialRound?:ReviewRound|null;onViewRound:(r:ReviewRound)=>void}){
 const [doc,setDoc]=useState<Annotations|null>(null),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[expanded,setExpanded]=useState('');
 const lock=useRef(false),dirty=useRef(false);
 const [tab,setTab]=useState<'edit'|'rounds'|'drawings'>('edit'),[roundLimit,setRoundLimit]=useState(20);
 const panelId=useId();
 const liveDraft=useRef(draft);useLayoutEffect(()=>{liveDraft.current=draft;},[draft]);
 const reviewKey=`golden-review-draft-v1:${user}:${example.dataset_id}:${example.symbol}`;
 const [restored]=useState<{review:string;note:string}|null>(()=>{try{return JSON.parse(localStorage.getItem(reviewKey)||'null');}catch{return null;}});
 const [review,setReview]=useState(initialRound?.status??restored?.review??'UNCERTAIN'),[note,setNote]=useState(initialRound?.note??restored?.note??'');
 useEffect(()=>{if(user)try{localStorage.setItem(reviewKey,JSON.stringify({review,note}));}catch{}},[reviewKey,review,note,user]);
 const path=`/data/golden-workbench/annotations/${example.symbol}.json`;
 const load=useCallback((hydrate=false)=>{const c=new AbortController();readChip(path,c.signal,v=>v as Annotations).then(v=>{if(c.signal.aborted)return;setDoc(v);setError('');const own=v.reviews.find(r=>r.owner===user&&r.dataset_id===example.dataset_id);if(hydrate&&!initialRound&&!restored&&!dirty.current&&own){setReview(own.status);setNote(own.note);}}).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();},[path,user,example.dataset_id,initialRound,restored]);
 useEffect(()=>load(true),[load]);
 async function save(withDrawing:boolean){
  if(!doc||lock.current)return;lock.current=true;setBusy(true);setError('');setMessage('');
  const id=draft.id||crypto.randomUUID();
  if(withDrawing&&!draft.id)setDraft({...draft,id});
  const payload=withDrawing?{kind:'calibration',drawing:{...draft,id,as_of:example.as_of,dataset_id:example.dataset_id},review:{status:review,note,axis_id:axisId,auto_mode:autoMode}}:{kind:'review',as_of:example.as_of,dataset_id:example.dataset_id,status:review,note,axis_id:axisId,auto_mode:autoMode};
  try{
   const response=await authFetch(api+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,revision:doc.revision})});
   const value=await response.json() as Annotations&{error?:string};if(!response.ok)throw Error(value.error||'保存失败');
   setDoc(value);
   const sent={...draft,id},unchanged=JSON.stringify({...liveDraft.current,id})===JSON.stringify(sent)&&(!liveDraft.current.id||liveDraft.current.id===id);
   if(withDrawing&&unchanged){resetDraft(sent);setDrawing(false);}
   setMessage(withDrawing?(unchanged?'本轮画线与批注已保存，自动结果和人工点位已留存。':'上一轮快照已保存；保存期间的新调整仍保留在当前草稿。'):'本轮批注已保存，未关联人工画线。');
  }catch(e){setError((e as Error).message);}finally{lock.current=false;setBusy(false);}
 }
 function open(d:Drawing,copy=false){resetDraft({id:copy?'':d.id,title:copy?d.title+'（副本）':d.title,mode:d.mode,points:d.points,note:d.note});setTab('edit');setShowManual(true);setDrawing(false);setMessage('已打开人工画线，可直接拖动图上锚点。');}
 const valid=draft.points.length===2&&draft.points.every(p=>Number(p.price)>0&&p.date<=example.as_of)&&Number(draft.points[0]?.price)>Number(draft.points[1]?.price)&&!!draft.title.trim();
 const rounds=(doc?.reviews??[]).flatMap(r=>[...(r.history??[]),r].map((v,i)=>({...v,number:i+1}))).sort((a,b)=>b.updated_at.localeCompare(a.updated_at)||(Number(b.round_id?.split(':').at(-1))||0)-(Number(a.round_id?.split(':').at(-1))||0));
 return <div className="gw-tools">
  <div className="gw-inspector-tabs" role="tablist" aria-label="人工校准记录">{([['edit','批注'],['rounds',`轮次 ${rounds.length}`],['drawings',`画线 ${doc?.drawings.length??'—'}`]] as const).map(([id,label],i)=><button key={id} role="tab" id={`${panelId}-${id}-tab`} aria-controls={`${panelId}-${id}`} aria-selected={tab===id} tabIndex={tab===id?0:-1} onClick={()=>setTab(id)} onKeyDown={e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const tabs=['edit','rounds','drawings'] as const;const next=e.key==='Home'?0:e.key==='End'?2:(i+(e.key==='ArrowRight'?1:2))%3;setTab(tabs[next]);document.getElementById(`${panelId}-${tabs[next]}-tab`)?.focus();}}>{label}</button>)}</div>
  {(error||message)&&<div className={error?'gw-warning':'gw-success'} role={error?'alert':'status'}>{error||message}{error&&<button className="gw-link" onClick={()=>load(false)}>刷新云端记录，保留草稿</button>}</div>}
  <section className="gw-calibration-editor" role="tabpanel" id={`${panelId}-edit`} aria-labelledby={`${panelId}-edit-tab`} hidden={tab!=='edit'}><div className="gw-panel-title"><h3>本轮校准</h3><span>{example.as_of}</span></div>
   <div className="gw-draft-points">{[0,1].map(i=>{const p=draft.points[i];return <div key={i}><span>{i===0?'高点 H':draft.mode==='HL'?'低点 L':'中轴 M'}</span><b>{p?.price??'待选点'}</b><small>{p?`${p.date.slice(5)} · ${pointLabel(p.field)}`:'在图上拖拽选取'}</small></div>;})}</div>
   <label className="gw-field">对比结论<select aria-label="自动画法核对结论" value={review} onChange={e=>{dirty.current=true;setReview(e.target.value);}}>{Object.entries(reviewLabels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
   <label className="gw-field">本轮批注<textarea rows={4} aria-label="本轮校准批注" placeholder="自动线与人工线哪里不同？\n下一轮要调整什么？" value={note} maxLength={2000} onChange={e=>{dirty.current=true;setNote(e.target.value);}}/></label>
   <button className="gw-primary gw-save" aria-label="保存本轮画线与批注" disabled={busy||!doc||!user||!valid} onClick={()=>save(true)}>{busy?'保存中…':'保存本轮'}</button>
   <button className="gw-link gw-comment-only" disabled={busy||!doc||!user} onClick={()=>save(false)}>仅保存批注</button>
   <small className="gw-muted gw-save-help">保存画线、批注与本轮自动结果，共享可见。</small>
   <details className="gw-exact-points"><summary>画线详情与精确点位</summary>
   <label className="gw-field">画线名称<input value={draft.title} maxLength={80} onChange={e=>setDraft({...draft,title:e.target.value})}/></label>
   <label className="gw-field">选点依据<textarea rows={2} value={draft.note} maxLength={2000} onChange={e=>setDraft({...draft,note:e.target.value})} placeholder="为什么选这根K线、这个价格？"/></label>
   {draft.points.map((p,i)=><div className="gw-point-editor" key={i}><b>{i===0?'H':draft.mode==='HL'?'L':'M'}</b><input aria-label={`人工点${i+1}日期`} type="date" value={p.date} max={example.as_of} onChange={e=>setDraft({...draft,points:draft.points.map((v,j)=>i===j?{...v,date:e.target.value,field:'custom'}:v)})}/><input aria-label={`人工点${i+1}价格`} type="number" step="0.0001" min="0.00000001" value={p.price} onChange={e=>setDraft({...draft,points:draft.points.map((v,j)=>i===j?{...v,field:'custom',price:e.target.value}:v)})}/><small>{pointLabel(p.field)}</small></div>)}</details>
   {draft.points.some(p=>!example.history.some(r=>r.date===p.date))&&<p className="gw-warning">有人工点在当前行情范围外，保存的日期与价格仍保留。</p>}
  </section>
  <section role="tabpanel" id={`${panelId}-rounds`} aria-labelledby={`${panelId}-rounds-tab`} hidden={tab!=='rounds'}><div className="gw-panel-title"><h3>多轮校准</h3><span>{rounds.length}轮</span></div>{rounds.slice(0,roundLimit).map((r,i)=><article className="gw-round" key={r.round_id??`${r.owner}:${r.dataset_id}:${r.updated_at}:${i}`}><div className="gw-round-heading"><b>第{r.number}轮 · {reviewLabels[r.status]}</b><span>{r.as_of}</span></div><small>{r.owner} · {time(r.updated_at)} · 北京时间</small><p>{r.note||'未填写批注'}</p>{r.drawing_snapshot?<><div className="gw-round-points"><span>人工 H <b>{r.drawing_snapshot.points[0].price}</b></span><span>{r.drawing_snapshot.mode==='HL'?'L':'M'} <b>{r.drawing_snapshot.points[1].price}</b></span></div><p className="gw-muted">自动{r.auto_context?.auto_mode??r.auto_mode??'—'} · H {r.auto_context?.H?.price??'待核'} / {r.auto_context?.auto_mode==='HL'?'L '+(r.auto_context?.L?.price??'待核'):'M '+(r.auto_context?.M?.price??'未选')} · 批次 {r.dataset_id.slice(0,8)}</p><button className="gw-link" onClick={()=>onViewRound(r)}>回看本轮并继续校准 ↗</button></>:<small className="gw-muted">仅批注，未关联人工画线</small>}</article>)}{rounds.length>roundLimit&&<button className="gw-button" onClick={()=>setRoundLimit(n=>n+20)}>查看更早轮次</button>}{doc&&!rounds.length&&<p className="gw-muted gw-panel-empty">保存第一轮后，在这里回看和继续调整。</p>}</section>
  <section role="tabpanel" id={`${panelId}-drawings`} aria-labelledby={`${panelId}-drawings-tab`} hidden={tab!=='drawings'}><div className="gw-panel-title"><h3>已保存人工画线</h3><span>{doc?.drawings.length??'—'}份</span></div>{doc?.drawings.slice().reverse().map(d=><div className="gw-saved" key={d.id}><b>{d.title}</b><span>{d.owner} · {time(d.updated_at)}</span><small>{d.mode} · H {d.points[0].price} / {d.mode==='HL'?'L':'M'} {d.points[1].price}</small><p>{d.note}</p><div><button className="gw-link" onClick={()=>open(d,d.owner!==user)}>{d.owner===user?'打开 / 修改':'查看并复制'}</button><button className="gw-link" onClick={()=>setExpanded(expanded===d.id?'':d.id)}>历史版本 {d.history?.length??0}</button></div>{expanded===d.id&&<div>{d.history?.map((h,i)=><button className="gw-version" key={i} onClick={()=>open({...h,history:[]} as Drawing,true)}>{time(h.updated_at)} · {h.points.map(p=>p.price).join(' / ')} → 复制</button>)}</div>}</div>)}{doc&&!doc.drawings.length&&<p className="gw-muted gw-panel-empty">尚无已保存人工画线。</p>}</section>
 </div>;
}
