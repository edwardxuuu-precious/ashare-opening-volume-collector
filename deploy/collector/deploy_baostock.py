"""Deploy tested BaoStock fallback and merge validated local checkpoints in maintenance."""
from pathlib import Path
import hashlib,json,subprocess,tarfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.auth.accounts import aws
from deploy.collector.runtime_bundle import runtime_files, archive_name
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/full-market'
BUCKET='stock-private-access-databucket-bmzud610gtex';INSTANCE='i-09b6355eec4b1c32f'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def upload(p,key):
 subprocess.run(['aws','s3','cp',str(p),'s3://'+BUCKET+'/'+key,'--profile','dev','--region','us-east-1','--only-show-errors','--sse','AES256'],check=True)
def main():
 assert aws('sts','get-caller-identity')['Account']=='838775535952'
 assert aws('amplify','get-job',{'appId':'d1nb0daasi2snk','branchName':'production','jobId':'8'})['job']['summary']['status']=='SUCCEED'
 files=runtime_files(ROOT)
 archive=OUT/'runtime-baostock.tar.gz'
 with tarfile.open(archive,'w:gz') as t:
  for p in sorted(files):t.add(p,arcname=archive_name(p, ROOT))
 sha=digest(archive);key='collector/releases/'+sha+'.tar.gz';upload(archive,key)
 checkpoints={p.name:p for p in (ROOT/'var/data/full-market-supplement/checkpoint').glob('*.json')}
 checkpoints['600519.json']=ROOT/'var/data/full-market-repair/checkpoint/600519.json'
 checkpoints['600577.json']=ROOT/'var/data/supplement-recovery-probe/checkpoint/600577.json'
 batch=OUT/'supplement-baostock.tar.gz'
 with tarfile.open(batch,'w:gz') as t:
  for name,p in sorted(checkpoints.items()):t.add(p,arcname='checkpoint/'+name)
 batch_sha=digest(batch);batch_key='collector/imports/'+batch_sha+'.tar.gz';upload(batch,batch_key)
 download="import boto3,hashlib\ns=boto3.client('s3',region_name='us-east-1')\n"
 for k,h,p in [(key,sha,'/tmp/stock-runtime-baostock.tar.gz'),(batch_key,batch_sha,'/tmp/stock-import-baostock.tar.gz')]:
  download+=f"s.download_file('{BUCKET}','{k}','{p}')\nassert hashlib.sha256(open('{p}','rb').read()).hexdigest()=='{h}'\n"
 publish="import boto3,json\nfrom pathlib import Path\nfrom server.cloud_worker import Publisher\np=Publisher(boto3.client('s3',region_name='us-east-1'),'"+BUCKET+"',Path('/var/lib/stock-collector/data'))\nn=p.publish(all_dates=True)\nassert n==126\np.save_checkpoint()\nprint(json.dumps({'publishedDates':n,**p.latest_published}))\n"
 commands=['set -e',"/opt/stock-collector/venv/bin/python - <<'DOWNLOAD'\n"+download+'DOWNLOAD',
 'touch /etc/stock-collector.maintenance','systemctl stop stock-collector.service',
 'tar -xzf /tmp/stock-runtime-baostock.tar.gz -C /opt/stock-collector',
 'bash /opt/stock-collector/deploy/collector/install-service.sh',
 'cd /opt/stock-collector',
 'venv/bin/python -c "import baostock; from scripts.baostock_source import BaoStockSession"',
 f'venv/bin/python server/import_checkpoint_batch.py --archive /tmp/stock-import-baostock.tar.gz --root /var/lib/stock-collector --expected-sha256 {batch_sha}',
 'runuser -u stockcollector -- venv/bin/python server/publish_cached.py --root /var/lib/stock-collector --baostock-fallback --reconcile-latest-first',
 "runuser -u stockcollector -- venv/bin/python - <<'PUBLISH'\n"+publish+'PUBLISH',
 'rm /etc/stock-collector.maintenance','systemctl start stock-collector.service','systemctl is-active stock-collector.service']
 r=aws('ssm','send-command',{'InstanceIds':[INSTANCE],'DocumentName':'AWS-RunShellScript','Parameters':{'commands':commands,'executionTimeout':['1800']}})
 record={'commandId':r['Command']['CommandId'],'runtimeSha256':sha,'batchSha256':batch_sha,'batchMembers':len(checkpoints),'sourceHashes':{str(p.relative_to(ROOT)):digest(p) for p in files}}
 (OUT/'baostock-production-release.json').write_text(json.dumps(record,indent=2));print(json.dumps({k:v for k,v in record.items() if k!='sourceHashes'}),flush=True)
if __name__=='__main__':main()
