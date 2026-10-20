"""Upload tested collector code and a frozen seed; install without starting work."""
from pathlib import Path
import hashlib,json,subprocess,tarfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.auth.accounts import aws
from deploy.collector.runtime_bundle import runtime_files, archive_name
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/full-market'
BUCKET='stock-private-access-databucket-bmzud610gtex';INSTANCE='i-09b6355eec4b1c32f'

def upload(archive,files,prefix):
 with tarfile.open(archive,'w:gz') as t:
  for file,name in files:t.add(file,arcname=name)
 sha=hashlib.sha256(archive.read_bytes()).hexdigest();key=prefix+sha+'.tar.gz'
 subprocess.run(['aws','s3','cp',str(archive),'s3://'+BUCKET+'/'+key,'--profile','dev','--region','us-east-1','--only-show-errors','--sse','AES256'],check=True)
 return {'key':key,'sha256':sha,'bytes':archive.stat().st_size}
assert aws('sts','get-caller-identity')['Account']=='838775535952'
files=runtime_files(ROOT)
code=upload(OUT/'runtime.tar.gz',[(p,archive_name(p, ROOT)) for p in sorted(files)],'collector/releases/')
data=ROOT/'var/data/full-market-live'
seed=upload(OUT/'seed.tar.gz',[(p,p.name) for p in sorted(data.glob('*.json'))]+[(data/'checkpoint','checkpoint')],'collector/seeds/')
commands=['set -e','touch /etc/stock-collector.maintenance',"test -f /var/lib/stock-collector/bootstrap-complete","! systemctl is-active --quiet stock-collector.service"]
for item,name,target in [(code,'runtime','/opt/stock-collector'),(seed,'seed','/var/lib/stock-collector/data')]:
 commands += [f"/opt/stock-collector/venv/bin/python - <<'DOWNLOAD'\nimport boto3,hashlib\np='/tmp/stock-{name}.tar.gz'\nboto3.client('s3',region_name='us-east-1').download_file('{BUCKET}','{item['key']}',p)\nassert hashlib.sha256(open(p,'rb').read()).hexdigest()=='{item['sha256']}'\nDOWNLOAD",f'mkdir -p {target}',f'tar -xzf /tmp/stock-{name}.tar.gz -C {target}']
commands+=['chown -R stockcollector:stockcollector /var/lib/stock-collector','bash /opt/stock-collector/deploy/collector/install-service.sh','cd /opt/stock-collector && venv/bin/python -m py_compile scripts/collect.py server/cloud_worker.py','echo RUNTIME_AND_SEED_INSTALLED']
r=aws('ssm','send-command',{'InstanceIds':[INSTANCE],'DocumentName':'AWS-RunShellScript','Parameters':{'commands':commands,'executionTimeout':['600']}})
record={'runtime':code,'seed':seed,'commandId':r['Command']['CommandId'],'sourceHashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
(OUT/'runtime-release.json').write_text(json.dumps(record,indent=2));print({'commandId':record['commandId'],'runtimeSha256':code['sha256'],'seedBytes':seed['bytes']})
