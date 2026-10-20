"""Read-only live checks using Windows SSO/GCM. No SSH or credential export."""
import gzip,hashlib,json,tempfile,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from aws_stock import aws,verify_account
from github_stock import request

root=Path(__file__).resolve().parents[1]
identity=verify_account()
app=aws(['amplify','get-app','--app-id','d1nb0daasi2snk'])['app']
branch=aws(['amplify','get-branch','--app-id','d1nb0daasi2snk','--branch-name','production'])['branch']
latest=aws(['amplify','list-jobs','--app-id','d1nb0daasi2snk','--branch-name','production','--max-results','1'])['jobSummaries'][0]
domains=aws(['amplify','list-domain-associations','--app-id','d1nb0daasi2snk'])['domainAssociations']
bound=[]
for association in domains:
    for sub in association.get('subDomains',[]):
        setting=sub['subDomainSetting'];prefix=setting.get('prefix') or ''
        hostname=(prefix+'.' if prefix else '')+association['domainName']
        if hostname=='stock.iteready.com':
            bound.append({'hostname':hostname,'branch':setting['branchName'],'verified':sub['verified'],'status':association['domainStatus']})
if not any(d['branch']=='production' and d['status']=='AVAILABLE' for d in bound):raise RuntimeError('Website domain/branch is not configured as available')
# Keep AWS's raw subdomain flag, which is false on this existing AVAILABLE
# association. Establish the functional binding independently through valid
# HTTPS and matching content from the app's production hostname.
https=[]
for origin in ('https://stock.iteready.com','https://'+branch['branchName']+'.'+app['defaultDomain']):
    with urllib.request.urlopen(urllib.request.Request(origin+'/chips',headers={'User-Agent':'Mozilla/5.0'}),timeout=25) as response:
        raw=response.read(5000001)
        if response.status!=200 or len(raw)>5000000:raise RuntimeError('Website HTTPS response invalid')
        https.append({'origin':origin,'status':response.status,'html_sha256':hashlib.sha256(raw).hexdigest()})
if https[0]['html_sha256']!=https[1]['html_sha256']:raise RuntimeError('Custom domain differs from the configured production branch')
function=aws(['lambda','get-function-configuration','--function-name','stock-auth-api'])
stack=aws(['cloudformation','describe-stacks','--stack-name','stock-private-access'])['Stacks'][0]
bucket='stock-private-access-databucket-bmzud610gtex'
aws(['s3api','head-bucket','--bucket',bucket])
with tempfile.TemporaryDirectory(prefix='stock-windows-check-') as folder:
    path=Path(folder)/'status.json'
    meta=aws(['s3api','get-object','--bucket',bucket,'--key','data/chips/daily/status.json',str(path)])
    raw=path.read_bytes()
    if len(raw)>100000:raise RuntimeError('Unexpected daily status size')
    status=json.loads(gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw)
repo=request('')
if not repo.get('permissions',{}).get('push'):raise RuntimeError('Windows GitHub repository push permission is unavailable')
receipt={'verified_at':datetime.now(timezone.utc).isoformat(),'host':'Windows','ssh_used':False,
    'aws_profile':'stock-dev','account':identity['Account'],'principal_arn':identity['Arn'],
    'app_id':app['appId'],'branch':branch['branchName'],'domain':bound,'domain_https_evidence':https,
    'domain_binding_check':'AVAILABLE_CONFIGURATION_AND_MATCHING_HTTPS_CONTENT; RAW_VERIFIED_FLAG_PRESERVED',
    'latest_amplify_job':{k:latest[k] for k in ('jobId','status')},
    'auth_function':{k:function.get(k) for k in ('FunctionName','State','LastUpdateStatus')},
    'private_stack_status':stack['StackStatus'],'private_bucket_readable':True,
    'private_daily_status':{k:status.get(k) for k in ('as_of','state','dataset_id','universe_total','scanned_count','model_ready_count','updated_at','unchanged_threshold')},
    'github_repository':repo['full_name'],'github_push_permission':True,
    'actual_windows_publish':'PENDING_AMPLIFY_RELEASE'}
output=root/'outputs/windows-aws-20261018';output.mkdir(parents=True,exist_ok=True)
(output/'access-check.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:receipt[k] for k in ('host','ssh_used','account','app_id','branch','latest_amplify_job','private_bucket_readable','github_push_permission','actual_windows_publish')}))
