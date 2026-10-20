"""Read published run observations after shutdown; never starts collection or changes AWS."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import argparse,json,subprocess,tempfile
from deploy.auth.accounts import aws
BUCKET='stock-private-access-databucket-bmzud610gtex'

def read_json(key):
    with tempfile.TemporaryDirectory() as d:
        p=Path(d)/'object.json'
        subprocess.run(['aws','s3','cp','s3://'+BUCKET+'/'+key,str(p),'--profile','dev','--region','us-east-1','--only-show-errors'],check=True,capture_output=True)
        return json.loads(p.read_text())

def assess(observations, trading_dates, collection):
    eligible=sorted((x for x in observations if x.get('baselineEligible')),key=lambda x:x['endedAt'])
    baselines=[x for x in eligible if x.get('benchmarkPassed')]
    if not collection.get('historyTraversalCompleted'):
        return {'phase':'waiting_backfill','complete':False,'collectionStatus':collection.get('status'),'processedStocks':collection.get('completedStocks')}
    if not baselines:
        return {'phase':'waiting_benchmark','complete':False,'failedBenchmarkRuns':len(eligible),'collectionStatus':collection.get('status')}
    baseline=baselines[0]
    wanted=sorted(d for d in set(trading_dates) if d>baseline['runDate'])[:3]
    days=[]
    for day in wanted:
        runs=[x for x in observations if x.get('phase')=='daily' and x.get('runDate')==day and not x.get('noOp')]
        candidates=[x for x in runs if x.get('benchmarkPassed')]
        selected=max(candidates or runs,key=lambda x:x['endedAt']) if runs else None
        days.append({'date':day,'observed':selected is not None,'passed':bool(selected and selected.get('benchmarkPassed')),
                     'elapsedSeconds':selected.get('elapsedSeconds') if selected else None,
                     'observedBootSeconds':selected.get('observedBootSeconds') if selected else None,
                     'pendingCount':selected.get('pendingCount') if selected else None,
                     'estimatedComputeAndIpv4Usd':selected.get('estimatedComputeAndIpv4Usd') if selected else None})
    complete=len(days)==3 and all(x['observed'] for x in days)
    passed=complete and all(x['passed'] and x.get('observedBootSeconds') is not None and x['observedBootSeconds']<=5*3600 for x in days)
    return {'phase':'completed' if complete else 'observing','complete':complete,'fiveHourTargetVerified':passed,
            'benchmark':{k:baseline.get(k) for k in ['runDate','elapsedSeconds','todayTargetCount','todayAttemptedCount','publishedBefore22','estimatedComputeAndIpv4Usd']},
            'days':days,'note':'Compute/IP estimate only; storage, serving and tax are separate. Missing days are not successful observations.'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',default='outputs/daily-update/monitor-status.json');args=parser.parse_args()
    observations=[];cursor={}
    while True:
        page=aws('s3api','list-objects-v2',{'Bucket':BUCKET,'Prefix':'collector/observations/','MaxKeys':1000,**cursor})
        for obj in page.get('Contents',[]):
            if obj['Key'].endswith('.json'):observations.append(read_json(obj['Key']))
        if not page.get('IsTruncated'):break
        cursor={'ContinuationToken':page['NextContinuationToken']}
    collection=read_json('data/collection-status.json');manifest=read_json('data/manifest.json')
    trading=manifest.get('tradingDates') or [x['date'] for x in manifest['dates']]
    result=assess(observations,trading,collection)
    result['checkedAt']=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
