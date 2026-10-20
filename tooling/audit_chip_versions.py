"""Bounded version metadata only; no object reads or deletions."""
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import json
from pathlib import Path
import boto3
from botocore.config import Config
out=Path(__file__).resolve().parents[1]/'outputs/chips-performance-cost-20261020'
s=boto3.Session(profile_name='stock-dev',region_name='us-east-1');assert s.client('sts').get_caller_identity()['Account']=='838775535952'
c=s.client('s3',config=Config(connect_timeout=7,read_timeout=25,retries={'total_max_attempts':2}))
def scan(prefix):
    groups=defaultdict(lambda:{'current_bytes':0,'noncurrent_bytes':0,'current_objects':0,'noncurrent_objects':0});complete=False;pages=0
    for page in c.get_paginator('list_object_versions').paginate(Bucket='stock-private-access-databucket-bmzud610gtex',Prefix=prefix,PaginationConfig={'PageSize':1000,'MaxItems':150000}):
        pages+=1
        for v in page.get('Versions',[]):
            k=v['Key'];group='/'.join(k.split('/')[:3]) if k.startswith('collector/chips/') else 'data/chips' if k.startswith('data/chips/') else prefix+'other'
            kind='current' if v['IsLatest'] else 'noncurrent';r=groups[group];r[kind+'_bytes']+=v['Size'];r[kind+'_objects']+=1
        complete=not page.get('IsTruncated',False)
    return {'prefix':prefix,'complete':complete,'pages':pages,'groups':dict(groups)}
with ThreadPoolExecutor(max_workers=2) as ex:r=list(ex.map(scan,['data/','collector/']))
(out/'version-breakdown.json').write_text(json.dumps(r,indent=2),encoding='utf-8');print(json.dumps(r))
