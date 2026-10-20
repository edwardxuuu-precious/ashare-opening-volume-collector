"""Read current deployment and published data; no credentials written to evidence."""
import json
from collections import Counter
from pathlib import Path
from deploy.auth.accounts import aws
from deploy.reconciliation_release import s3read
OUT=Path('outputs/full-market')
r=json.loads((OUT/'baostock-production-release.json').read_text())
command=aws('ssm','get-command-invocation',{'CommandId':r['commandId'],'InstanceId':'i-09b6355eec4b1c32f'})
assert command['Status']=='Success',command['Status']
status=json.loads(s3read('collection-status.json')[0]);manifest=json.loads(s3read('manifest.json')[0]);latest=max(manifest['dates'],key=lambda x:x['date'])
day=json.loads(s3read(latest['file'])[0]);row=next(r for r in day['rows'] if r['code']=='600519')
assert len(day['rows'])==5556
assert row['sourceProvider']=='baostock' and row['status']=='ok'
assert row['ratio']==17.075087 and row['first15Volume']==775476 and row['dailyVolume']==4541564
result=dict(website='https://stock.iteready.com',jobId='8',runtimeCommandStatus=command['Status'],runtimeCommandId=r['commandId'],date=latest['date'],tradingDates=len(manifest['dates']),totalStocks=5556,collection={k:v for k,v in status.items() if k not in ('dates','serviceInvocationId')},latestCounts=dict(Counter(r['status'] for r in day['rows'])),maotai={k:row[k] for k in ('code','sourceProvider','status','ratio','first15Volume','dailyVolume','minuteDayVolume')},allMarketComplete=bool(status.get('historyTraversalCompleted')),limitations=['Historical source mismatches remain unverified when no matching provider pair exists','BaoStock fallback does not support Beijing exchange'])
(OUT/'deployment-status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False))
