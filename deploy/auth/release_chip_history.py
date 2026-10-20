"""Bounded Windows deployment of the authenticated, read-only history feature."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import boto3
from botocore.config import Config
from chip_bundle import inline_handler

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/chips-history-20261020'
STACK='stock-private-access'


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def clients():
    session=boto3.Session(profile_name='stock-dev',region_name='us-east-1')
    if session.client('sts').get_caller_identity()['Account']!='838775535952':raise RuntimeError('Unexpected deployment account')
    return session.client('cloudformation',config=Config(connect_timeout=7,read_timeout=25,retries={'total_max_attempts':2}))


def template(client):
    value=client.get_template(StackName=STACK)['TemplateBody']
    return json.loads(value) if isinstance(value,str) else value


def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def prepare(client):
    before=template(client)
    handler=(ROOT/'deploy/auth/handler.py').read_text(encoding='utf-8')
    start=handler.index('        historical = re.fullmatch(')
    end=handler.index("        if method == 'GET' and is_chip_data_path(path):",start)
    if before['Resources']['Function']['Properties']['Code']['ZipFile']!=handler[:start]+handler[end:]:
        raise RuntimeError('Live auth code changed; inspect the exact baseline before release')
    candidate=deepcopy(before)
    candidate['Resources']['Function']['Properties']['Code']={'ZipFile':inline_handler()}
    statement={'Effect':'Allow','Action':['s3:GetObject'],'Resource':{'Fn::Sub':'${DataBucket.Arn}/collector/chips/raw/*'}}
    candidate['Resources']['Role']['Properties']['Policies'][0]['PolicyDocument']['Statement'].append(statement)
    changed={k for k in before['Resources'] if before['Resources'][k]!=candidate['Resources'][k]}
    if changed!={'Role','Function'}:raise RuntimeError('Unexpected infrastructure scope')
    raw=json.dumps(candidate,ensure_ascii=False,separators=(',',':'))
    if len(raw.encode())>51200:raise RuntimeError('Inline template size bound exceeded')
    client.validate_template(TemplateBody=raw)
    name='stock-chip-history-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    result=client.create_change_set(StackName=STACK,ChangeSetName=name,ChangeSetType='UPDATE',
        TemplateBody=raw,Capabilities=['CAPABILITY_IAM'],Description='Authenticated chip history; exact same model, saved market quote reads only')
    save('cloud-template-before.json',before);save('cloud-template-candidate.json',candidate)
    receipt={'id':result['Id'],'name':name,'before_sha256':digest(before),'candidate_sha256':digest(candidate),
        'allowed_resources':['Role','Function'],'state':'CREATED','template_bytes':len(raw.encode()),
        'read_scope_added':'collector/chips/raw/*; s3:GetObject only; no ListBucket, write or new public route'}
    save('backend-release.json',receipt)
    print(json.dumps({k:receipt[k] for k in ['name','state','allowed_resources','template_bytes']}))


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','execute','status']);a=p.parse_args();client=clients()
    if a.action=='prepare':return prepare(client)
    receipt=json.loads((OUT/'backend-release.json').read_text(encoding='utf-8'))
    if a.action=='status':
        change=client.describe_change_set(ChangeSetName=receipt['id'])
        stack=client.describe_stacks(StackName=STACK)['Stacks'][0]
        print(json.dumps({'change_set':change['Status'],'stack':stack['StackStatus'],
            'changes':[{k:r['ResourceChange'].get(k) for k in ['LogicalResourceId','Action','Replacement']} for r in change.get('Changes',[])]}))
        return
    before=json.loads((OUT/'cloud-template-before.json').read_text(encoding='utf-8'))
    candidate=json.loads((OUT/'cloud-template-candidate.json').read_text(encoding='utf-8'))
    if digest(before)!=receipt['before_sha256'] or digest(candidate)!=receipt['candidate_sha256'] or template(client)!=before:
        raise RuntimeError('Reviewed deployment inputs or live baseline changed')
    change=client.describe_change_set(ChangeSetName=receipt['id'])
    changes=[r['ResourceChange'] for r in change.get('Changes',[])]
    actual={(r['LogicalResourceId'],r['Action']) for r in changes}
    expected={('Role','Modify'),('Function','Modify')}
    # CloudFormation also reports the existing integration's dynamic ARN
    # dependency. Its template and function name must remain byte-for-byte equal.
    if ('Integration','Modify') in actual:
        if before['Resources']['Integration']!=candidate['Resources']['Integration'] or before['Resources']['Function']['Properties']['FunctionName']!=candidate['Resources']['Function']['Properties']['FunctionName']:
            raise RuntimeError('Integration or function identity changed')
        detail=next(r for r in changes if r['LogicalResourceId']=='Integration')['Details']
        if len(detail)!=1 or detail[0].get('CausingEntity')!='Function.Arn' or detail[0].get('ChangeSource')!='ResourceAttribute' or detail[0].get('Evaluation')!='Dynamic' or detail[0].get('Target',{}).get('Name')!='IntegrationUri':
            raise RuntimeError('Unexpected integration dependency change')
        expected.add(('Integration','Modify'))
    if change['Status']!='CREATE_COMPLETE' or actual!=expected or any(r.get('Replacement')!='False' for r in changes):
        raise RuntimeError('Change set is not ready or has an unexpected replacement')
    client.execute_change_set(ChangeSetName=receipt['id'])
    receipt.update(state='EXECUTED',changes=changes,integration_template_unchanged=True);save('backend-release.json',receipt)
    print(json.dumps({'state':'EXECUTED','resources':['Role','Function'],'integration_arn_dependency_only':True}))


if __name__=='__main__':main()
