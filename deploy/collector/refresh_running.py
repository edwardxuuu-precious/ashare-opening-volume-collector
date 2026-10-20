"""Replace only the collector runtime, preserving live checkpoints and snapshots."""
from pathlib import Path
import hashlib,json,subprocess,tarfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.auth.accounts import aws
from deploy.collector.runtime_bundle import runtime_files, archive_name
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/full-market'
BUCKET='stock-private-access-databucket-bmzud610gtex';INSTANCE='i-09b6355eec4b1c32f'
assert aws('sts','get-caller-identity')['Account']=='838775535952'
files=runtime_files(ROOT)
archive=OUT/'runtime-current.tar.gz'
with tarfile.open(archive,'w:gz') as t:
 for p in sorted(files):t.add(p,arcname=archive_name(p, ROOT))
sha=hashlib.sha256(archive.read_bytes()).hexdigest();key='collector/releases/'+sha+'.tar.gz'
subprocess.run(['aws','s3','cp',str(archive),'s3://'+BUCKET+'/'+key,'--profile','dev','--region','us-east-1','--only-show-errors','--sse','AES256'],check=True)
commands=['set -e',f"/opt/stock-collector/venv/bin/python - <<'DOWNLOAD'\nimport boto3,hashlib\np='/tmp/stock-runtime-current.tar.gz'\nboto3.client('s3',region_name='us-east-1').download_file('{BUCKET}','{key}',p)\nassert hashlib.sha256(open(p,'rb').read()).hexdigest()=='{sha}'\nDOWNLOAD",'touch /etc/stock-collector.maintenance','systemctl stop stock-collector.service','tar -xzf /tmp/stock-runtime-current.tar.gz -C /opt/stock-collector','bash /opt/stock-collector/deploy/collector/install-service.sh','cd /opt/stock-collector && venv/bin/python -m py_compile scripts/collect.py server/cloud_worker.py','rm /etc/stock-collector.maintenance','systemctl start stock-collector.service','systemctl is-active stock-collector.service']
r=aws('ssm','send-command',{'InstanceIds':[INSTANCE],'DocumentName':'AWS-RunShellScript','Parameters':{'commands':commands,'executionTimeout':['600']}})
record={'key':key,'sha256':sha,'commandId':r['Command']['CommandId'],'preservedData':True,'sourceHashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
(OUT/'runtime-current-release.json').write_text(json.dumps(record,indent=2));print({'commandId':record['commandId'],'runtimeSha256':sha})
