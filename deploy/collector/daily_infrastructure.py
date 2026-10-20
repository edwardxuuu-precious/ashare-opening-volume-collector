"""Review and apply only scheduled-stop and bounded-version-retention changes."""
from pathlib import Path
import argparse,copy,json,hashlib
from datetime import datetime,timezone,timedelta
from deploy.auth.accounts import aws
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/daily-update'
BUCKET='stock-private-access-databucket-bmzud610gtex'

def make_template(current, kind):
    candidate=copy.deepcopy(current)
    if kind=='collector':
        source=json.loads((ROOT/'deploy/collector/template.json').read_text())
        for name in ('DailyStopRole','DailyStopSchedule'):
            candidate['Resources'][name]=source['Resources'][name]
    else:
        source=json.loads((ROOT/'deploy/auth/template.json').read_text())
        candidate['Resources']['DataBucket']['Properties']['LifecycleConfiguration']=source['Resources']['DataBucket']['Properties']['LifecycleConfiguration']
    return candidate

def prepare():
    assert aws('sts','get-caller-identity')['Account']=='838775535952'
    OUT.mkdir(exist_ok=True)
    inventory={'versions':0,'bytes':0,'noncurrentVersions':0,'noncurrentBytes':0,'oldCurrentImports':0,'oldCurrentImportBytes':0}
    cursor={};cutoff=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    while True:
        page=aws('s3api','list-object-versions',{'Bucket':BUCKET,'MaxKeys':1000,**cursor})
        for obj in page.get('Versions',[]):
            key=obj['Key'];covered=key.startswith('data/') or key.startswith('collector/checkpoint.tar.gz') or key.startswith('collector/imports/')
            if not covered:continue
            inventory['versions']+=1;inventory['bytes']+=obj['Size']
            if not obj['IsLatest']:
                inventory['noncurrentVersions']+=1;inventory['noncurrentBytes']+=obj['Size']
            elif key.startswith('collector/imports/') and obj['LastModified']<cutoff:
                inventory['oldCurrentImports']+=1;inventory['oldCurrentImportBytes']+=obj['Size']
        if not page.get('IsTruncated'):break
        cursor={'KeyMarker':page['NextKeyMarker'],'VersionIdMarker':page['NextVersionIdMarker']}
    records=[]
    for kind,stack in [('collector','stock-private-collector'),('auth','stock-private-access')]:
        state=aws('cloudformation','describe-stacks',{'StackName':stack})['Stacks'][0]
        assert state['StackStatus'] in ('CREATE_COMPLETE','UPDATE_COMPLETE')
        current=aws('cloudformation','get-template',{'StackName':stack})['TemplateBody']
        if isinstance(current,str):current=json.loads(current)
        candidate=make_template(current,kind)
        (OUT/(kind+'-template-before.json')).write_text(json.dumps(current,indent=2))
        body=json.dumps(candidate)
        (OUT/(kind+'-template-candidate.json')).write_text(json.dumps(candidate,indent=2))
        name='daily-cost-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
        args={'StackName':stack,'ChangeSetName':name,'ChangeSetType':'UPDATE','TemplateBody':body,'Capabilities':['CAPABILITY_IAM'],'Parameters':[{'ParameterKey':p['ParameterKey'],'UsePreviousValue':True} for p in state.get('Parameters',[])]}
        result=aws('cloudformation','create-change-set',args)
        records.append({'kind':kind,'stack':stack,'changeSetId':result['Id'],'sha256':hashlib.sha256(body.encode()).hexdigest()})
    result={'lifecycleInventory':inventory,'changes':records,'status':'prepared'}
    (OUT/'infrastructure.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))

def review():
    result=json.loads((OUT/'infrastructure.json').read_text())
    for record in result['changes']:
        data=aws('cloudformation','describe-change-set',{'ChangeSetName':record['changeSetId'],'StackName':record['stack']})
        assert data['Status']=='CREATE_COMPLETE',data.get('StatusReason',data['Status'])
        changes=[c['ResourceChange'] for c in data['Changes']]
        expected={'DailyStopRole','DailyStopSchedule'} if record['kind']=='collector' else {'DataBucket','DataPolicy','Role','Function','Integration'}
        assert {c['LogicalResourceId'] for c in changes}<=expected
        if record['kind']=='auth':
            assert any(c['LogicalResourceId']=='DataBucket' for c in changes)
            for c in changes:
                if c['LogicalResourceId']!='DataBucket':
                    assert all(d['Evaluation']=='Dynamic' and d['ChangeSource']=='ResourceAttribute' for d in c.get('Details',[]))
            before=json.loads((OUT/'auth-template-before.json').read_text())
            candidate=json.loads((OUT/'auth-template-candidate.json').read_text())
            for name,value in before['Resources'].items():
                if name!='DataBucket':assert candidate['Resources'][name]==value
            record['lambdaCodeSha256Before']=aws('lambda','get-function-configuration',{'FunctionName':'stock-auth-api'})['CodeSha256']
        assert all(c.get('Replacement','False')=='False' for c in changes)
        assert all(c['Action']==('Add' if record['kind']=='collector' else 'Modify') for c in changes)
        record['review']=[{k:c.get(k) for k in ['LogicalResourceId','Action','Replacement']} for c in changes]
    result['status']='reviewed';(OUT/'infrastructure.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))

def apply(kind):
    result=json.loads((OUT/'infrastructure.json').read_text());assert result['status']=='reviewed'
    record=next(r for r in result['changes'] if r['kind']==kind)
    aws('cloudformation','execute-change-set',{'StackName':record['stack'],'ChangeSetName':record['changeSetId']})
    print('Executing reviewed change set:',kind)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','review','apply']);p.add_argument('--kind',choices=['collector','auth']);a=p.parse_args()
    if a.action=='prepare':prepare()
    elif a.action=='review':review()
    else:apply(a.kind)
