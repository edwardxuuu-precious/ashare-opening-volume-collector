export type GoldenUpdateStatus={schema:'stock.golden.update.v1';state:'RUNNING'|'COMPLETE'|'PARTIAL'|'FAILED';target_date:string;data_as_of:string|null;data_generated_at:string|null;dataset_id:string|null;total:20;processed:number;ready:number;started_at:string;updated_at:string;finished_at:string|null;lease_expires_at:string;error:string|null;missing:{symbol:string;name:string;reason:string}[];schedule:{timezone:string;first:string;retries:string[];next_run_at:string|null};run_url:string|null};
const closed=[['01-01','01-03'],['02-15','02-23'],['04-04','04-06'],['05-01','05-05'],['06-19','06-21'],['09-25','09-27'],['10-01','10-07']];
export function expectedGoldenDay(now=new Date()):string|null{
 const local=new Date(now.getTime()+8*3600000);if(local.getUTCFullYear()!==2026)return null;
 if(local.getUTCHours()*60+local.getUTCMinutes()<15*60+45)local.setUTCDate(local.getUTCDate()-1);
 for(let i=0;i<20;i++){const day=local.toISOString().slice(0,10),md=day.slice(5),week=local.getUTCDay();if(week!==0&&week!==6&&!closed.some(([a,b])=>a<=md&&md<=b))return day;local.setUTCDate(local.getUTCDate()-1);}return null;
}
export function parseGoldenStatus(input:unknown):GoldenUpdateStatus{
 const x=input as GoldenUpdateStatus;
 if(!x||x.schema!=='stock.golden.update.v1'||!['RUNNING','COMPLETE','PARTIAL','FAILED'].includes(x.state)||x.total!==20||!Number.isInteger(x.processed)||!Number.isInteger(x.ready)||x.ready<0||x.ready>x.processed||x.processed>20||!/^\d{4}-\d{2}-\d{2}$/.test(x.target_date)||!Number.isFinite(Date.parse(x.updated_at))||!Array.isArray(x.missing))throw Error('更新状态格式不完整');
 return x;
}
export function goldenState(status:GoldenUpdateStatus|null,publishedDay:string|undefined,now=new Date()){
 const expected=expectedGoldenDay(now);
 if(!expected)return {label:'日历待核对',tone:'warning'};
 if(status?.state==='RUNNING'&&status.target_date>=expected){if(Date.parse(status.lease_expires_at)<now.getTime())return {label:'更新中断，等待补跑',tone:'warning'};return {label:`正在更新 ${status.processed}/20`,tone:'running'};}
 if((!publishedDay||publishedDay<expected)&&(!status||status.target_date<expected))return {label:'等待今日更新',tone:'warning'};
 if(status?.state==='FAILED')return {label:'更新失败，等待补跑',tone:'warning'};
 if(status?.state==='PARTIAL')return {label:`部分更新 ${status.ready}/20`,tone:'warning'};
 if(status?.state==='COMPLETE'&&status.ready===20&&status.target_date>=expected)return {label:'已完成 20/20',tone:'complete'};
 return {label:status?'状态待核对':'状态读取中',tone:'warning'};
}
export function chinaTime(value:string|undefined|null){if(!value||!Number.isFinite(Date.parse(value)))return '—';return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value));}

export function goldenPresentation(status:GoldenUpdateStatus|null,latest:{as_of:string;dataset_id:string;generated_at:string;rows:{freshness:string;error:string|null}[]}|null,now=new Date()){
 const actualDay=latest?.as_of??'',statusDay=status?.data_as_of??'',date=actualDay>statusDay?actualDay:statusDay;
 const manifestTime=Date.parse(latest?.generated_at??''),statusTime=Date.parse(status?.data_generated_at??'');
 const obsolete=!!(latest&&status&&(latest.as_of>status.target_date||latest.as_of>statusDay||latest.as_of===statusDay&&latest.dataset_id!==status.dataset_id&&manifestTime>statusTime));
 const freshStatus=obsolete?null:status;
 let state=goldenState(freshStatus,date||undefined,now);
 const expected=expectedGoldenDay(now);
 if(obsolete&&latest&&expected&&latest.as_of>=expected){const ready=latest.rows.filter(r=>!r.error&&['CURRENT','CONFIRMED_NO_TRADE'].includes(r.freshness)).length;state={label:`已发布 ${ready}/20`,tone:ready===20?'complete':'warning'};}
 const newData=!!(latest&&status?.dataset_id&&latest.dataset_id!==status.dataset_id&&(statusDay>latest.as_of||statusDay===latest.as_of&&statusTime>manifestTime));
 return {date:date||undefined,state,newData,status:freshStatus,generated_at:statusDay>actualDay||statusDay===actualDay&&statusTime>manifestTime?status?.data_generated_at:latest?.generated_at};
}
