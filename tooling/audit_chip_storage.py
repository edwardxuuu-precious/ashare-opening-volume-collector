"""Read at most 100k current-object metadata in this project's private bucket."""
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import json
from pathlib import Path
import boto3
from botocore.config import Config
OUT=Path(__file__).resolve().parents[1]/'outputs/chips-performance-cost-20261020'
s=boto3.Session(profile_name='stock-dev',region_name='us-east-1');assert s.client('sts').get_caller_identity()['Account']=='838775535952'
c=s.client('s3',config=Config(connect_timeout=7,read_timeout=20,retries={'total_max_attempts':2}));bucket='stock-private-access-databucket-bmzud610gtex'
def scan(prefix):
    groups=defaultdict(lambda:{'bytes':0,'objects':0});days=defaultdict(lambda:{'bytes':0,'objects':0});generations=defaultdict(lambda:{'bytes':0,'objects':0});count=0;complete=False
    paginator=c.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket,Prefix=prefix,PaginationConfig={'PageSize':1000,'MaxItems':100000}):
        for x in page.get('Contents',[]):
            k=x['Key'];p=k.split('/');group='/'.join(p[:4] if k.startswith('collector/chips/') else p[:3] if k.startswith('data/chips/') else p[:2]);day=x['LastModified'].date().isoformat()
            for v in [groups[group],days[day]]:v['bytes']+=x['Size'];v['objects']+=1
            if k.startswith('collector/chips/raw/') or k.startswith('data/chips/daily/'):
                v=generations['/'.join(p[:5]) if k.startswith('collector/chips/raw/') else '/'.join(p[:4])];v['bytes']+=x['Size'];v['objects']+=1
            count+=1
        complete=not page.get('IsTruncated',False)
    return {'prefix':prefix,'complete_current_objects':complete,'count':count,'groups':dict(groups),'modified_days':dict(days),'generations':dict(generations)}
with ThreadPoolExecutor(max_workers=2) as ex:r=list(ex.map(scan,['data/','collector/']))
(OUT/'storage-breakdown.json').write_text(json.dumps(r,indent=2),encoding='utf-8')
for row in r:print(json.dumps({'prefix':row['prefix'],'count':row['count'],'complete':row['complete_current_objects'],'current_bytes':sum(x['bytes'] for x in row['groups'].values())}))
