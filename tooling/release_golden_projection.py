"""Add only the reviewed protected projection reads to the deployed auth code."""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.request
import zipfile

import boto3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.auth.chip_bundle import inline_handler

OUT = ROOT / 'outputs/golden-projection-release-20261102'
STACK = 'stock-private-access'
FUNCTION = 'stock-auth-api'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def read(name):
    return json.loads((OUT / name).read_bytes())


def deployed_code(client):
    function = client.get_function(FunctionName=FUNCTION)
    with urllib.request.urlopen(function['Code']['Location'], timeout=25) as response:
        raw = response.read()
    return zipfile.ZipFile(io.BytesIO(raw)).read('index.py').decode('utf-8')


def template(client):
    value = client.get_template(StackName=STACK)['TemplateBody']
    return json.loads(value) if isinstance(value, str) else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'execute', 'verify'))
    args = parser.parse_args()
    session = boto3.Session(profile_name='stock-dev', region_name='us-east-1')
    if session.client('sts').get_caller_identity()['Account'] != '838775535952':
        raise RuntimeError('WRONG_AWS_ACCOUNT')
    cloud, client = session.client('cloudformation'), session.client('lambda')
    if args.action == 'prepare':
        actual = deployed_code(client)
        marker = '_bundle_sys.path.insert(0,_chip_library)\n'
        if marker not in actual:
            raise RuntimeError('DEPLOYED_BUNDLE_FORMAT_UNKNOWN')
        bootstrap, deployed_handler = actual.split(marker, 1)
        handler = (ROOT / 'deploy/auth/handler.py').read_text(encoding='utf-8')
        start = handler.index("        if method == 'GET' and re.fullmatch(r'/data/golden-projection/daily/")
        end = handler.index('        # Daily visual-review data', start)
        if deployed_handler != handler[:start] + handler[end:]:
            raise RuntimeError('DEPLOYED_AUTH_ADVANCED_OR_HAS_UNMERGED_CHANGES')
        candidate_code = bootstrap + marker + handler
        before = template(cloud)
        candidate = deepcopy(before)
        candidate['Resources']['Function']['Properties']['Code'] = {'ZipFile': candidate_code}
        if {key for key in before['Resources'] if before['Resources'][key] != candidate['Resources'][key]} != {'Function'}:
            raise RuntimeError('UNRELATED_RESOURCE_CHANGE')
        raw = json.dumps(candidate, ensure_ascii=False, separators=(',', ':'))
        name = 'stock-golden-projection-' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
        if len(raw.encode()) > 1024 * 1024:
            raise RuntimeError('TEMPLATE_SIZE_BOUND')
        template_args = {'TemplateBody': raw}
        artifact_key = None
        if len(raw.encode()) > 51200:
            bucket = 'stock-private-access-databucket-bmzud610gtex'
            artifact_key = 'deploy/golden-projection/' + name + '.json'
            session.client('s3').put_object(Bucket=bucket, Key=artifact_key, Body=raw.encode(),
                                          ContentType='application/json', ServerSideEncryption='AES256',
                                          CacheControl='no-store, private', IfNoneMatch='*')
            template_args = {'TemplateURL': 'https://' + bucket + '.s3.us-east-1.amazonaws.com/' + artifact_key}
        cloud.validate_template(**template_args)
        result = cloud.create_change_set(StackName=STACK, ChangeSetName=name, ChangeSetType='UPDATE',
                                        **template_args, Capabilities=['CAPABILITY_IAM'],
                                        Description='Code only: exact authenticated daily projection read paths; preserve current login sessions and all other routes')
        save('auth-template-before.json', before)
        save('auth-template-candidate.json', candidate)
        receipt = {'id': result['Id'], 'name': name, 'state': 'CREATED', 'before_sha256': digest(before),
                   'candidate_sha256': digest(candidate), 'before_code_sha256': hashlib.sha256(actual.encode()).hexdigest(),
                   'candidate_code_sha256': hashlib.sha256(candidate_code.encode()).hexdigest(), 'template_bytes': len(raw.encode()),
                   'private_template_artifact_key': artifact_key, 'deployed_bootstrap_preserved': True}
        save('auth-release.json', receipt)
        print(json.dumps({'state': 'CREATED', 'name': name, 'template_bytes': len(raw.encode())}))
    elif args.action == 'execute':
        receipt, before, candidate = read('auth-release.json'), read('auth-template-before.json'), read('auth-template-candidate.json')
        if digest(before) != receipt['before_sha256'] or digest(candidate) != receipt['candidate_sha256']:
            raise RuntimeError('REVIEWED_TEMPLATE_CHANGED')
        if (template(cloud) != before or hashlib.sha256(deployed_code(client).encode()).hexdigest() != receipt['before_code_sha256']):
            raise RuntimeError('DEPLOYED_BASELINE_ADVANCED')
        result = cloud.describe_change_set(ChangeSetName=receipt['id'])
        changes = [item['ResourceChange'] for item in result.get('Changes', [])]
        if result['Status'] != 'CREATE_COMPLETE':
            raise RuntimeError('CHANGE_SET_NOT_READY')
        allowed = {('Function', 'Modify')}
        integration = [item for item in changes if item['LogicalResourceId'] == 'Integration']
        if integration:
            if before['Resources']['Integration'] != candidate['Resources']['Integration']:
                raise RuntimeError('INTEGRATION_CHANGED')
            details = integration[0]['Details']
            if (len(details) != 1 or details[0].get('CausingEntity') != 'Function.Arn'
                    or details[0].get('ChangeSource') != 'ResourceAttribute'
                    or details[0].get('Evaluation') != 'Dynamic'
                    or details[0].get('Target', {}).get('Name') != 'IntegrationUri'):
                raise RuntimeError('UNEXPECTED_INTEGRATION_CHANGE')
            allowed.add(('Integration', 'Modify'))
        if {(item['LogicalResourceId'], item['Action']) for item in changes} != allowed or any(item.get('Replacement') != 'False' for item in changes):
            raise RuntimeError('CHANGE_SET_EXCEEDS_CODE_ONLY_SCOPE')
        cloud.execute_change_set(ChangeSetName=receipt['id'])
        receipt.update(state='EXECUTED', changes=changes)
        save('auth-release.json', receipt)
        print(json.dumps({'state': 'EXECUTED', 'resources': sorted(allowed)}))
    else:
        receipt, candidate = read('auth-release.json'), read('auth-template-candidate.json')
        state = cloud.describe_stacks(StackName=STACK)['Stacks'][0]['StackStatus']
        actual = deployed_code(client)
        configuration = client.get_function_configuration(FunctionName=FUNCTION)
        if state != 'UPDATE_COMPLETE' or actual != candidate['Resources']['Function']['Properties']['Code']['ZipFile']:
            raise RuntimeError('AUTH_RELEASE_NOT_VERIFIED')
        receipt.update(state='VERIFIED', stack_state=state, actual_code_sha256=hashlib.sha256(actual.encode()).hexdigest(),
                       function_state=configuration['State'], update_status=configuration['LastUpdateStatus'])
        save('auth-release.json', receipt)
        print(json.dumps({key: receipt[key] for key in ('state', 'stack_state', 'actual_code_sha256', 'function_state', 'update_status')}))


if __name__ == '__main__':
    main()
