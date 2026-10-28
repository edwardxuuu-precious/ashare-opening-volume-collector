"""Publish the protected Windows build using this host's stock-dev AWS SSO."""
import argparse,hashlib,json,time,urllib.request,zipfile
from pathlib import Path
from aws_stock import aws,verify_account

def package(root,output):
    client=root/'dist/client'
    for relative in ('index.html','chips.html','chips/calibration.html','golden.html','trend.html','trend/research.html'):
        if not (client/relative).is_file():raise RuntimeError('Missing protected route: '+relative)
    if (client/'data').exists():raise RuntimeError('Public data directory is forbidden')
    files=sorted(p for p in client.rglob('*') if p.is_file())
    if any(p.suffix in ('.pem','.key') or p.name.startswith('.env') for p in files):raise RuntimeError('Unexpected sensitive build artifact')
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for path in files:z.write(path,path.relative_to(client).as_posix())
    return {'artifact':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'bytes':output.stat().st_size,'files':len(files)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--expected-job');p.add_argument('--dry-run',action='store_true')
    p.add_argument('--artifact',type=Path);p.add_argument('--expected-sha256');a=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    if a.artifact:
        artifact=a.artifact.resolve();digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
        if not a.expected_sha256 or digest!=a.expected_sha256:raise RuntimeError('Reviewed artifact SHA-256 is required and must match')
        with zipfile.ZipFile(artifact) as z:
            names=z.namelist()
            if not all(name in names for name in ('index.html','chips.html','chips/calibration.html','golden.html','trend.html','trend/research.html')):raise RuntimeError('Reviewed artifact is missing a protected route')
            if any(name.startswith(('/', 'data/')) or '..' in Path(name).parts or Path(name).name.startswith('.env') or Path(name).suffix in ('.pem','.key') for name in names):raise RuntimeError('Unexpected public or sensitive artifact path')
        value={'artifact':str(artifact),'sha256':digest,'bytes':artifact.stat().st_size,'files':len(names),'reused_reviewed_artifact':True}
    else:
        artifact=root/'outputs/chips-daily-20261018/stock-daily-site-windows.zip'
        value=package(root,artifact)
    if a.dry_run:print(json.dumps({**value,'published':False}));return
    if not a.expected_job:p.error('expected-job is required to prevent overwriting another release')
    verify_account()
    base=['--app-id','d1nb0daasi2snk','--branch-name','production']
    latest=aws(['amplify','list-jobs',*base,'--max-results','1'])['jobSummaries'][0]
    if latest['jobId']!=a.expected_job or latest['status']!='SUCCEED':raise RuntimeError('Amplify advanced or current release is unhealthy; inspect before publishing')
    deployment=aws(['amplify','create-deployment',*base]);job=deployment['jobId']
    with urllib.request.urlopen(urllib.request.Request(deployment['zipUploadUrl'],data=artifact.read_bytes(),method='PUT',headers={'Content-Type':'application/zip'}),timeout=60) as r:
        if r.status!=200:raise RuntimeError('Artifact upload failed')
    aws(['amplify','start-deployment',*base,'--job-id',job])
    receipt=root/'outputs/chips-daily-20261018/windows-site-release.jsonl'
    receipt.parent.mkdir(parents=True,exist_ok=True)
    with receipt.open('a',encoding='utf-8') as out:out.write(json.dumps({**value,'job_id':job,'state':'STARTED','host':'Windows'})+'\n')
    for _ in range(60):
        status=aws(['amplify','get-job',*base,'--job-id',job])['job']['summary']['status']
        if status in ('SUCCEED','FAILED','CANCELLED'):break
        time.sleep(5)
    with receipt.open('a',encoding='utf-8') as out:out.write(json.dumps({'job_id':job,'state':status})+'\n')
    print(json.dumps({'job_id':job,'state':status,'artifact_sha256':value['sha256']}))
    if status!='SUCCEED':raise SystemExit(1)
if __name__=='__main__':main()
