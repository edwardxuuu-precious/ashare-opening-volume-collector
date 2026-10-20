"""Bounded read-only account cost, API latency, and saved-history benchmark."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
import json,time
from pathlib import Path
import sys
import boto3
from botocore.config import Config
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.chips.history import load_history
OUT=ROOT/'outputs/chips-performance-cost-20261020'
OUT.mkdir(parents=True,exist_ok=True)
session=boto3.Session(profile_name='stock-dev',region_name='us-east-1')
cfg=Config(connect_timeout=7,read_timeout=30,retries={'total_max_attempts':2})
assert session.client('sts',config=cfg).get_caller_identity()['Account']=='838775535952'
now=datetime.now(timezone.utc)
def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def costs():
    try:
        r=session.client('ce',config=cfg).get_cost_and_usage(TimePeriod={'Start':'2026-10-01','End':'2026-10-21'},Granularity='DAILY',Metrics=['UnblendedCost'],GroupBy=[{'Type':'DIMENSION','Key':'SERVICE'},{'Type':'DIMENSION','Key':'RECORD_TYPE'}])
        r.pop('ResponseMetadata',None);save('cost-explorer.json',r)
        totals={}
        for d in r['ResultsByTime']:
            for g in d['Groups']:
                key=' / '.join(g['Keys']);totals[key]=totals.get(key,0)+float(g['Metrics']['UnblendedCost']['Amount'])
        return {'costs':totals}
    except Exception as e:
        v={'error':str(e)};save('cost-explorer.json',v);return v
def metrics():
    cw=session.client('cloudwatch',config=cfg)
    queries=[]
    def add(id,namespace,name,dimensions,stat='Sum',period=86400):
        queries.append({'Id':id,'MetricStat':{'Metric':{'Namespace':namespace,'MetricName':name,'Dimensions':dimensions},'Period':period,'Stat':stat}})
    dim=[{'Name':'FunctionName','Value':'stock-auth-api'}]
    for name,stat in [('Invocations','Sum'),('Duration','Average'),('Duration','p95'),('Errors','Sum'),('Throttles','Sum')]:add('lambda_'+name.lower()+stat.lower().replace('sum',''), 'AWS/Lambda',name,dim,stat)
    bucket='stock-private-access-databucket-bmzud610gtex'
    for name,kind in [('BucketSizeBytes','StandardStorage'),('NumberOfObjects','AllStorageTypes')]:add('s3_'+name.lower(),'AWS/S3',name,[{'Name':'BucketName','Value':bucket},{'Name':'StorageType','Value':kind}],'Average')
    r=cw.get_metric_data(MetricDataQueries=queries,StartTime=now-timedelta(days=7),EndTime=now,ScanBy='TimestampAscending');r.pop('ResponseMetadata',None);save('metrics.json',r)
    fn=session.client('lambda',config=cfg).get_function_configuration(FunctionName='stock-auth-api')
    jobs=session.client('amplify',config=cfg).list_jobs(appId='d1nb0daasi2snk',branchName='production',maxResults=1)['jobSummaries']
    v={'memory_mb':fn['MemorySize'],'timeout':fn['Timeout'],'region':'us-east-1','last_modified':fn['LastModified'],'latest_site_job':jobs};save('infrastructure.json',v)
    return {**v,'metrics':[{'id':q['Id'],'values':q['Values']} for q in r['MetricDataResults']]}
def logs():
    cl=session.client('logs',config=cfg)
    groups=['/aws/apigateway/stock-auth-api-access','/aws/lambda/stock-auth-api']
    statements=["fields path, responseLatencyMs, status | filter path like /\\/history\\// | stats count(*) as requests, avg(responseLatencyMs) as avg_ms, pct(responseLatencyMs,50) as p50_ms, pct(responseLatencyMs,95) as p95_ms by bin(1d), status", "filter @type = 'REPORT' | stats count(*) as requests, avg(@duration) as avg_ms, pct(@duration,95) as p95_ms, avg(@billedDuration) as billed_ms, max(@maxMemoryUsed) as memory_bytes by bin(1d)"]
    result=[]
    for group,q in zip(groups,statements):
        try:
            id=cl.start_query(logGroupName=group,startTime=int((now-timedelta(days=3)).timestamp()),endTime=int(now.timestamp()),queryString=q,limit=20)['queryId']
            for _ in range(12):
                r=cl.get_query_results(queryId=id)
                if r['status'] in ('Complete','Failed','Cancelled','Timeout'):break
                time.sleep(1)
            result.append({'group':group,'query':q,**{k:r[k] for k in ['status','results','statistics'] if k in r}})
        except Exception as e:result.append({'group':group,'error':str(e)})
    save('latency-logs.json',result);return {'logs':result}
def benchmark():
    s3=session.client('s3',config=cfg);bucket='stock-private-access-databucket-bmzud610gtex'
    dataset=json.loads(s3.get_object(Bucket=bucket,Key='data/chips/daily/manifest.json')['Body'].read())['dataset_id']
    class Timed:
        def __init__(self):self.reads=[]
        def get_object(self,**kw):
            t=time.perf_counter();r=s3.get_object(**kw);raw=r['Body'].read();self.reads.append({'key':kw['Key'],'ms':round((time.perf_counter()-t)*1000,1),'bytes':len(raw)})
            import io
            r['Body']=io.BytesIO(raw);return r
    rows=[]
    for day in ['2026-10-12','2026-09-30','2026-08-14','2024-09-20']:
        client=Timed();t=time.perf_counter();r=load_history(client,bucket,dataset,'000573',day)
        rows.append({'date':day,'total_ms':round((time.perf_counter()-t)*1000,1),'reads':client.reads,'compute_decode_ms':round((time.perf_counter()-t)*1000-sum(x['ms'] for x in client.reads),1),'peak':r['snapshots'][day]['peak_prices_cny']})
    save('baseline-benchmark.json',{'dataset':dataset,'rows':rows});return {'benchmark':rows,'dataset':dataset}
with ThreadPoolExecutor(max_workers=4) as ex:
    for result in ex.map(lambda f:f(),[costs,metrics,logs,benchmark]):print(json.dumps(result,default=str,ensure_ascii=False))
