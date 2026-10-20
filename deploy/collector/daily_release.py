"""Stage, probe, then activate a reviewed daily runtime without replacing live data."""
from pathlib import Path
import argparse,hashlib,io,json,subprocess,tarfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.auth.accounts import aws
from deploy.collector.runtime_bundle import runtime_files, archive_name
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/daily-update'
BUCKET='stock-private-access-databucket-bmzud610gtex';INSTANCE='i-09b6355eec4b1c32f'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def upload(p,key):
 subprocess.run(['aws','s3','cp',str(p),'s3://'+BUCKET+'/'+key,'--profile','dev','--region','us-east-1','--only-show-errors','--sse','AES256'],check=True)
def dispatch(commands,timeout=1200):
 return aws('ssm','send-command',{'InstanceIds':[INSTANCE],'DocumentName':'AWS-RunShellScript','Parameters':{'commands':['set -e']+commands,'executionTimeout':[str(timeout)]}})['Command']['CommandId']
def load():return json.loads((OUT/'runtime-release.json').read_text())
def save(x):(OUT/'runtime-release.json').write_text(json.dumps(x,indent=2))
def stage():
 assert aws('sts','get-caller-identity')['Account']=='838775535952'
 files=runtime_files(ROOT)
 archive=OUT/'daily-runtime.tar.gz'
 with tarfile.open(archive,'w:gz') as t:
  for p in sorted(files):t.add(p,arcname=archive_name(p, ROOT))
 digest=sha(archive);key='collector/releases/'+digest+'.tar.gz';upload(archive,key)
 release='/opt/stock-collector/releases/'+digest
 command=dispatch(["/opt/stock-collector/venv/bin/python - <<'PYCODE'\nimport boto3,hashlib\np='/tmp/stock-daily-runtime.tar.gz'\nboto3.client('s3',region_name='us-east-1').download_file('"+BUCKET+"','"+key+"',p)\nassert hashlib.sha256(open(p,'rb').read()).hexdigest()=='"+digest+"'\nPYCODE",'mkdir -p '+release,'tar -xzf /tmp/stock-daily-runtime.tar.gz -C '+release,'cd '+release,'/opt/stock-collector/venv/bin/python -m py_compile scripts/daily_collector.py scripts/daily_state.py server/cloud_worker.py server/publisher_state.py'])
 save({'runtimeSha256':digest,'key':key,'releaseDirectory':release,'stageCommandId':command,'sourceHashes':{str(p.relative_to(ROOT)):sha(p) for p in files}});print('stage',command)
def probe():
 r=load();assert aws('ssm','get-command-invocation',{'CommandId':r['stageCommandId'],'InstanceId':INSTANCE})['Status']=='Success'
 probe='/var/lib/stock-collector/probes/daily-'+r['runtimeSha256'][:12];release=r['releaseDirectory']
 cmds=['touch /etc/stock-collector.maintenance', 'shutdown -c 2>/dev/null || true', 'systemctl stop stock-collector.service',
 "trap 'rm -f /etc/stock-collector.maintenance; systemctl start stock-collector.service' EXIT",
 'install -d -o stockcollector -g stockcollector '+probe,
 'cp /var/lib/stock-collector/data/universe.json /var/lib/stock-collector/data/calendar.json '+probe+'/',
 'chown stockcollector:stockcollector '+probe+'/*.json',
 'cd '+release,
 'runuser -u stockcollector -- /opt/stock-collector/venv/bin/python scripts/daily_collector.py --out '+probe+' --workers 4 --interval .75 --symbols 600519,000001,920002 --dates 2026-09-04 --cutoff 23:55 --bao-after 00:00 --publish-every 3',
 "PROBE_DIR='"+probe+"' /opt/stock-collector/venv/bin/python - <<'PYCODE'\nimport os,json,hashlib\nfrom pathlib import Path\np=Path(os.environ['PROBE_DIR'])\nr=json.loads((p/'checkpoint/600519.json').read_text())['days']['2026-09-04']\nassert r['sourceProvider']=='baostock' and r['ratio']==17.075087\nreport=json.loads((p/'run-status.json').read_text())\nassert report['targetCount']==3 and report['attemptedCount']==3\nprint(json.dumps({'probePassed':True,'stocks':3,'ratio':r['ratio'],'report':report},ensure_ascii=False))\nPYCODE"]
 cmds += [
 "PROBE_DIR='"+probe+"' /opt/stock-collector/venv/bin/python - <<'PYCODE'\nimport os,json,hashlib\nfrom pathlib import Path\np=Path(os.environ['PROBE_DIR'])\nfiles=list(p.glob('20*.json'))+list((p/'checkpoint').glob('*.json'))\n(p/'repeat-before.json').write_text(json.dumps({str(x.relative_to(p)):hashlib.sha256(x.read_bytes()).hexdigest() for x in files}))\nPYCODE",
 'runuser -u stockcollector -- /opt/stock-collector/venv/bin/python scripts/daily_collector.py --out '+probe+' --workers 4 --interval .75 --symbols 600519,000001,920002 --dates 2026-09-04 --cutoff 23:55 --bao-after 00:00 --publish-every 3',
 "PROBE_DIR='"+probe+"' /opt/stock-collector/venv/bin/python - <<'PYCODE'\nimport os,json,hashlib\nfrom pathlib import Path\np=Path(os.environ['PROBE_DIR'])\nbefore=json.loads((p/'repeat-before.json').read_text())\nassert all(hashlib.sha256((p/name).read_bytes()).hexdigest()==value for name,value in before.items())\nr=json.loads((p/'run-status.json').read_text())\nassert r['noOp'] is True and r['httpRequests']==0\nprint(json.dumps({'repeatNoOp':True,'httpRequests':0,'unchangedFiles':len(before)}))\nPYCODE"]
 cmds.append('trap - EXIT')
 r['probeDirectory']=probe;r['probeCommandId']=dispatch(cmds);save(r);print('probe',r['probeCommandId'])
def activate():
 r=load();assert aws('ssm','get-command-invocation',{'CommandId':r['probeCommandId'],'InstanceId':INSTANCE})['Status']=='Success'
 assert all(sha(ROOT/p)==h for p,h in r['sourceHashes'].items()),'Runtime sources changed after probe'
 batch=OUT/'daily-supplement.tar.gz';paths=sorted((ROOT/'var/data/full-market-supplement/checkpoint').glob('*.json'))
 with tarfile.open(batch,'w:gz') as target:
  for p in paths:
   raw=p.read_bytes();json.loads(raw);info=tarfile.TarInfo('checkpoint/'+p.name);info.size=len(raw);target.addfile(info,io.BytesIO(raw))
 digest=sha(batch);key='collector/imports/'+digest+'.tar.gz';upload(batch,key)
 cmds=["/opt/stock-collector/venv/bin/python - <<'PYCODE'\nimport boto3,hashlib\np='/tmp/stock-daily-supplement.tar.gz'\nboto3.client('s3',region_name='us-east-1').download_file('"+BUCKET+"','"+key+"',p)\nassert hashlib.sha256(open(p,'rb').read()).hexdigest()=='"+digest+"'\nPYCODE",'touch /etc/stock-collector.maintenance','shutdown -c 2>/dev/null || true','systemctl stop stock-collector.service',
 'tar -xzf /tmp/stock-daily-runtime.tar.gz -C /opt/stock-collector',
 'bash /opt/stock-collector/deploy/collector/install-service.sh','cd /opt/stock-collector',
 '/opt/stock-collector/venv/bin/python server/import_checkpoint_batch.py --root /var/lib/stock-collector --archive /tmp/stock-daily-supplement.tar.gz --expected-sha256 '+digest,
 'runuser -u stockcollector -- /opt/stock-collector/venv/bin/python server/publish_cached.py --root /var/lib/stock-collector --baostock-fallback --reconcile-latest-first',
 "runuser -u stockcollector -- /opt/stock-collector/venv/bin/python - <<'PYCODE'\nfrom pathlib import Path\nimport boto3,json\nfrom server.cloud_worker import Publisher\np=Publisher(boto3.client('s3',region_name='us-east-1'),'"+BUCKET+"',Path('/var/lib/stock-collector/data'))\ncount=p.publish(all_dates=True)\nassert count>0\np.save_checkpoint()\nprint(json.dumps({'publishedDates':count,**p.latest_published,'metrics':p.metrics}))\nPYCODE",'rm /etc/stock-collector.maintenance','systemctl start stock-collector.service','systemctl is-active stock-collector.service']
 r['activationCommandId']=dispatch(cmds,1800);r['supplementSha256']=digest;r['supplementMembers']=len(paths);save(r);print('activate',r['activationCommandId'])
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['stage','probe','activate']);a=p.parse_args();globals()[a.action]()
