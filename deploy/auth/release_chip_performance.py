"""Code-only update guarded against the actual deployed Lambda source."""
from copy import deepcopy
from datetime import datetime,timezone
import argparse,hashlib,io,json,urllib.request,zipfile
from pathlib import Path
import boto3
from chip_bundle import inline_handler
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/chips-performance-cost-20261020'
STACK='stock-private-access'
s=boto3.Session(profile_name='stock-dev',region_name='us-east-1')
assert s.client('sts').get_caller_identity()['Account']=='838775535952'
c=s.client('cloudformation');fn=s.client('lambda')
def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def actual_code():
    r=fn.get_function(FunctionName='stock-auth-api')
    with urllib.request.urlopen(r['Code']['Location'],timeout=25) as stream:raw=stream.read()
    return zipfile.ZipFile(io.BytesIO(raw)).read('index.py').decode('utf-8')
def template():
    t=c.get_template(StackName=STACK)['TemplateBody'];return json.loads(t) if isinstance(t,str) else t
def baseline(code):
    t=template();t['Resources']['Function']['Properties']['Code']={'ZipFile':code};return t
p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','execute','verify']);a=p.parse_args()
if a.action=='prepare':
    code=actual_code()
    expected=json.loads((ROOT/'outputs/chips-history-20261020/cloud-template-candidate.json').read_text(encoding='utf-8'))['Resources']['Function']['Properties']['Code']['ZipFile']
    if code!=expected:raise RuntimeError('Actual deployed auth code differs from the prior reviewed release')
    before=baseline(code);candidate=deepcopy(before);candidate['Resources']['Function']['Properties']['Code']={'ZipFile':inline_handler()}
    assert {k for k in before['Resources'] if before['Resources'][k]!=candidate['Resources'][k]}=={'Function'}
    raw=json.dumps(candidate,ensure_ascii=False,separators=(',',':'));assert len(raw.encode())<=51200
    c.validate_template(TemplateBody=raw)
    name='stock-chip-performance-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    r=c.create_change_set(StackName=STACK,ChangeSetName=name,ChangeSetType='UPDATE',TemplateBody=raw,Capabilities=['CAPABILITY_IAM'],Description='Code only: eight-session reads and bounded immutable-input warm cache; auth and chip model unchanged')
    save('cloud-template-before.json',before);save('cloud-template-candidate.json',candidate)
    save('backend-release.json',{'id':r['Id'],'name':name,'state':'CREATED','before_sha256':digest(before),'candidate_sha256':digest(candidate),'template_bytes':len(raw.encode())})
    print(json.dumps({'state':'CREATED','name':name,'template_bytes':len(raw.encode())}))
elif a.action=='execute':
    receipt=read('backend-release.json');before=read('cloud-template-before.json');candidate=read('cloud-template-candidate.json')
    assert digest(before)==receipt['before_sha256'] and digest(candidate)==receipt['candidate_sha256']
    assert baseline(actual_code())==before
    r=c.describe_change_set(ChangeSetName=receipt['id']);changes=[x['ResourceChange'] for x in r.get('Changes',[])]
    assert r['Status']=='CREATE_COMPLETE'
    expected={('Function','Modify')}
    if any(x['LogicalResourceId']=='Integration' for x in changes):
        assert before['Resources']['Integration']==candidate['Resources']['Integration']
        detail=next(x for x in changes if x['LogicalResourceId']=='Integration')['Details']
        assert len(detail)==1 and detail[0]['CausingEntity']=='Function.Arn' and detail[0]['ChangeSource']=='ResourceAttribute' and detail[0]['Evaluation']=='Dynamic' and detail[0]['Target']['Name']=='IntegrationUri'
        expected.add(('Integration','Modify'))
    assert {(x['LogicalResourceId'],x['Action']) for x in changes}==expected and all(x.get('Replacement')=='False' for x in changes)
    c.execute_change_set(ChangeSetName=receipt['id']);receipt.update(state='EXECUTED',changes=changes);save('backend-release.json',receipt)
    print(json.dumps({'state':'EXECUTED','resources':sorted(expected)}))
else:
    receipt=read('backend-release.json');candidate=read('cloud-template-candidate.json');code=actual_code();state=c.describe_stacks(StackName=STACK)['Stacks'][0]['StackStatus']
    config=fn.get_function_configuration(FunctionName='stock-auth-api')
    assert state=='UPDATE_COMPLETE' and code==candidate['Resources']['Function']['Properties']['Code']['ZipFile']
    receipt.update(state='VERIFIED',stack_state=state,actual_source_sha256=hashlib.sha256(code.encode()).hexdigest(),function_state=config['State'],update_status=config['LastUpdateStatus'])
    save('backend-release.json',receipt);print(json.dumps({k:receipt[k] for k in ['state','stack_state','actual_source_sha256','function_state','update_status']}))
