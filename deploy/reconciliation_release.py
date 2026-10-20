"""Publish a fixed protected artifact and verified private snapshot to existing Stock resources."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/reconciliation'
APP, BRANCH = 'd1nb0daasi2snk', 'production'
BUCKET = 'stock-private-access-databucket-bmzud610gtex'


def aws(service, operation, **payload):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as f:
        json.dump(payload, f); f.flush()
        run = subprocess.run(['aws', service, operation, '--profile', 'dev', '--region', 'us-east-1',
            '--cli-input-json', 'file://' + f.name, '--no-cli-pager', '--output', 'json'], capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError(service + '/' + operation + ' failed; inspect AWS status without exposing request URLs')
    return json.loads(run.stdout or '{}')


def digest(data): return hashlib.sha256(data).hexdigest()


def s3read(name):
    with tempfile.NamedTemporaryFile() as f:
        run = subprocess.run(['aws', 's3api', 'get-object', '--profile', 'dev', '--region', 'us-east-1',
            '--bucket', BUCKET, '--key', 'data/' + name, f.name, '--no-cli-pager', '--output', 'json'], capture_output=True, text=True)
        if run.returncode: raise RuntimeError('Private data read failed: ' + name)
        return Path(f.name).read_bytes(), json.loads(run.stdout)


def write_result(result):
    (OUT / 'release.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))


def publish(expected_job, source_files=None):
    assert aws('sts', 'get-caller-identity')['Account'] == '838775535952'
    current = aws('amplify', 'get-branch', appId=APP, branchName=BRANCH)['branch']
    if str(current.get('activeJobId', '')).lstrip('0') != expected_job: raise RuntimeError('Production changed since audited job; review before release')
    source = ROOT / 'dist/client'
    assert (source / 'index.html').is_file() and not (source / 'data').exists()
    files = sorted(p for p in source.rglob('*') if p.is_file())
    archive = OUT / 'stock-reconciliation.zip'
    hashes = {str(p.relative_to(source)): digest(p.read_bytes()) for p in files}
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in files: z.write(p, p.relative_to(source))
    previous = json.loads((OUT/'release.json').read_text()) if (OUT/'release.json').exists() else {}
    if previous.get('jobId'):
        raise RuntimeError('A deployment is already recorded; inspect or resume that job before creating another')
    recorded_sources = source_files if source_files is not None else [
        'src/app/page.tsx', 'src/lib/stock.ts', 'src/lib/chart-data.ts',
        'backend/scripts/collect.py', 'backend/scripts/reconciliation.py',
        'src/components/auth-gate.tsx', 'src/lib/auth-fetch.ts',
    ]
    if any(Path(p).is_absolute() or '..' in Path(p).parts for p in recorded_sources):
        raise RuntimeError('Source record paths must remain within the protected release directory')
    result = {**previous, **dict(appId=APP, branch=BRANCH, previousJob=expected_job, archiveSha256=digest(archive.read_bytes()),
                  staticHashes=hashes, sourceCommit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  workingTree=True, sourceHashes={p:digest((ROOT/p).read_bytes()) for p in recorded_sources})}
    write_result(result)
    job = aws('amplify', 'create-deployment', appId=APP, branchName=BRANCH)
    result.update(jobId=job['jobId'], status='CREATED'); write_result(result)
    # Presigned upload URL stays in process memory and is never written to evidence.
    request = urllib.request.Request(job['zipUploadUrl'], data=archive.read_bytes(), method='PUT', headers={'Content-Type':'application/zip'})
    with urllib.request.urlopen(request, timeout=90) as response: assert response.status == 200
    result.update(status='UPLOADED'); write_result(result)
    aws('amplify', 'start-deployment', appId=APP, branchName=BRANCH, jobId=job['jobId'])
    result.update(jobId=job['jobId'], status='STARTED'); write_result(result)
    print(json.dumps({'jobId':job['jobId'],'status':'STARTED'}), flush=True)


def publish_data():
    result = json.loads((OUT/'release.json').read_text())
    job = aws('amplify','get-job',appId=APP,branchName=BRANCH,jobId=result['jobId'])['job']['summary']
    if job['status'] != 'SUCCEED': raise RuntimeError('Static deployment is not successful yet')
    before, candidate = OUT/'production-before', OUT/'production-candidate'
    names = sorted(p.name for p in before.glob('*.json'))
    if set(names) != {p.name for p in candidate.glob('*.json')}: raise RuntimeError('Unexpected date/catalog scope change')
    checked = {}
    def verify_before(name):
        raw, meta = s3read(name)
        if raw != (before/name).read_bytes(): raise RuntimeError('Concurrent private data change: ' + name)
        return name, meta
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, meta in pool.map(verify_before,names): checked[name] = meta
    staged = {}
    for name in names:
        payload=json.loads((candidate/name).read_text())
        if name[:2]=='20':
            assert payload['date']+'.json'==name
            for row in payload['rows']:
                if row.get('ratio') is not None:
                    assert row['status']=='ok' and row.get('dayVolumeDifference',0)==0 and row['minuteDayVolume']==row['dailyVolume']
                    assert abs(row['ratio']-row['first15Volume']/row['dailyVolume']*100)<1e-5
                if row['status']=='unverified': assert row['ratio'] is None
        staged[name] = digest((candidate/name).read_bytes())
    result.update(status='SUCCEED', privateDataBefore={n:{'versionId':m.get('VersionId'),'etag':m['ETag']} for n,m in checked.items()}, dataHashes=staged)
    write_result(result)
    changed=[n for n in names if (candidate/n).read_bytes() != (before/n).read_bytes()]
    ordered=[n for n in changed if n!='manifest.json']+(['manifest.json'] if 'manifest.json' in changed else [])
    result['uploadedData']=[]
    for name in ordered:
        # Conditional writes reject an object changed after the read-only preflight.
        args=['aws','s3api','put-object','--profile','dev','--region','us-east-1','--bucket',BUCKET,
              '--key','data/'+name,'--body',str(candidate/name),'--if-match',checked[name]['ETag'],
              '--content-type','application/json; charset=utf-8','--cache-control','no-store','--output','json','--no-cli-pager']
        r=subprocess.run(args,capture_output=True,text=True)
        if r.returncode: raise RuntimeError('Conditional data write failed: '+name)
        result['uploadedData'].append(name);write_result(result)
        if len(result['uploadedData'])%10==0: print('Uploaded private data',len(result['uploadedData']), '/',len(ordered),flush=True)
    def verify_after(name):
        raw,_=s3read(name)
        if digest(raw)!=staged[name]: raise RuntimeError('Private readback mismatch: '+name)
    with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(verify_after,names))
    result.update(privateDataReadback='passed',privateDataFiles=len(names));write_result(result)
    print(json.dumps({'jobId':result['jobId'],'status':'SUCCEED','privateDataReadback':'passed','files':len(names)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['static','data']);p.add_argument('--expected-job',default='2');a=p.parse_args()
    if a.action=='static': publish(a.expected_job)
    else: publish_data()
