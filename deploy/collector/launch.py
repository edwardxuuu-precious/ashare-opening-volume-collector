"""Create the bounded Stock-only collector from a fixed source bundle."""
import hashlib,json,subprocess,sys,tarfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'auth'))
from accounts import aws
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.collector.runtime_bundle import runtime_files, archive_name
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/full-market'
network=json.loads((OUT/'network.json').read_text())
assert aws('sts','get-caller-identity')['Account']=='838775535952'
routes=aws('ec2','describe-route-tables',{'Filters':[{'Name':'vpc-id','Values':[network['VpcId']]}]})['RouteTables']
assigned=[t for t in routes if any(a.get('SubnetId')==network['SubnetId'] for a in t['Associations'])]
if not assigned:assigned=[t for t in routes if any(a.get('Main') for a in t['Associations'])]
assert len(assigned)==1 and any(r.get('DestinationCidrBlock')=='0.0.0.0/0' and r.get('GatewayId','').startswith('igw-') and r.get('State')=='active' for r in assigned[0]['Routes'])
files=runtime_files(ROOT)
archive=OUT/'collector.tar.gz'
with tarfile.open(archive,'w:gz') as tf:
 for p in files:tf.add(p,arcname=archive_name(p, ROOT))
digest=hashlib.sha256(archive.read_bytes()).hexdigest();key='collector/releases/'+digest+'.tar.gz'
subprocess.run(['aws','s3','cp',str(archive),'s3://stock-private-access-databucket-bmzud610gtex/'+key,'--profile','dev','--region','us-east-1','--sse','AES256','--only-show-errors'],check=True)
params=[{'ParameterKey':k,'ParameterValue':str(v)} for k,v in {'VpcId':network['VpcId'],'SubnetId':network['SubnetId'],'BundleKey':key}.items()]
stack=aws('cloudformation','create-stack',{'StackName':'stock-private-collector','TemplateBody':(ROOT/'deploy/collector/template.json').read_text(),'Parameters':params,'Capabilities':['CAPABILITY_IAM'],'Tags':[{'Key':'Project','Value':'Stock'}]})
record=dict(stack,network=network,bundleKey=key,bundleSha256=digest,sourceHashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
(OUT/'cloud-deployment.json').write_text(json.dumps(record,indent=2));print(json.dumps({'stackId':stack['StackId'],'bundleSha256':digest}),flush=True)
