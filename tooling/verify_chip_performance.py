"""Real saved-input equivalence and bounded warm-input read measurements."""
from pathlib import Path
import sys,time,json,hashlib
import boto3
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.chips.history import load_history,load_history_batch,ImmutableHistoryStore
out=ROOT/'outputs/chips-performance-cost-20261020'
s=boto3.Session(profile_name='stock-dev',region_name='us-east-1');assert s.client('sts').get_caller_identity()['Account']=='838775535952'
client=s.client('s3');bucket='stock-private-access-databucket-bmzud610gtex';dataset='c9b4155a3e9c87e4'
class Counted:
    def __init__(self):self.calls=0
    def get_object(self,**kw):self.calls+=1;return client.get_object(**kw)
rows=[]
for code in ['000573','600812','000725','000001']:
    counter=Counted();warm=ImmutableHistoryStore(counter);t=time.perf_counter();batch=load_history_batch(warm,bucket,dataset,code,'2026-10-13');elapsed=(time.perf_counter()-t)*1000;before=counter.calls
    for day in batch['dates']:
        single=load_history(warm,bucket,dataset,code,day);assert batch['frames'][day]==single
        assert single['unchanged_threshold'] is None
    assert counter.calls==before
    t=time.perf_counter();again=load_history_batch(warm,bucket,dataset,code,'2026-10-13');warm_ms=(time.perf_counter()-t)*1000;assert again==batch
    rows.append({'code':code,'dates':batch['dates'],'frames_exact':len(batch['frames']),'cold_block_ms':round(elapsed,1),'warm_block_ms':round(warm_ms,1),'s3_reads_cold':before,'additional_s3_reads_warm':counter.calls-before})
counter=Counted();warm=ImmutableHistoryStore(counter);older=[]
for day in ['2024-09-20','2024-09-23','2024-09-24']:
    before=counter.calls;t=time.perf_counter();r=load_history(warm,bucket,dataset,'000573',day);older.append({'date':day,'ms':round((time.perf_counter()-t)*1000,1),'s3_reads':counter.calls-before,'peak':r['snapshots'][day]['peak_prices_cny']})
v={'dataset':dataset,'model_source_sha256':hashlib.sha256((ROOT/'backend/chips/eastmoney_model.py').read_bytes()).hexdigest(),'equivalence':rows,'old_dates':older,'measured_on':'Windows saved-S3-input read and replay; not browser or Lambda wall time'}
(out/'real-input-verification.json').write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(v))
