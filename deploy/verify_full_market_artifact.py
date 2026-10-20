"""Read back static artifact and anonymous access boundaries; never uses account credentials."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'outputs/full-market'
release=json.loads((out/'release.json').read_text())
from reconciliation_release import aws, APP, BRANCH
job=aws('amplify','get-job',appId=APP,branchName=BRANCH,jobId=release['jobId'])['job']['summary']
assert job['status']=='SUCCEED'
release['status']=job['status']
(out/'release.json').write_text(json.dumps(release,ensure_ascii=False,indent=2))
base='https://stock.iteready.com/'

def get(url):
    try:r=urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache'}),timeout=30)
    except urllib.error.HTTPError as e:r=e
    with r:return r.status,r.read()

def verify(item):
    name,expected=item
    status,raw=get(base+name)
    if status!=200 or hashlib.sha256(raw).hexdigest()!=expected:raise RuntimeError('Static readback failed: '+name)
    return {'path':name,'sha256':expected,'passed':True}

with ThreadPoolExecutor(max_workers=4) as p: results=list(p.map(verify,release['staticHashes'].items()))
boundaries=[]
for name,url,expected in [
 ('public website contains no raw data',base+'data/manifest.json',404),
 ('private data requires login','https://api.stock.iteready.com/data/manifest.json',401),
 ('S3 denies anonymous access','https://stock-private-access-databucket-bmzud610gtex.s3.us-east-1.amazonaws.com/data/manifest.json',403)]:
 status,_=get(url);assert status==expected,(name,status)
 boundaries.append({'name':name,'status':status,'passed':True})
(out/'artifact-verification.json').write_text(json.dumps({'staticFiles':results,'boundaries':boundaries},indent=2))
print(json.dumps({'staticFilesVerified':len(results),'boundaries':boundaries}))
