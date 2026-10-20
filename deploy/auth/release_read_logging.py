"""Prepare a log-only CloudFormation change set against an exact live baseline."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from access_logging import add_access_logs, assert_log_only_change

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'outputs/read-reliability-20261003'
STACK = 'stock-private-access'


def aws(service, operation, **args):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as f:
        json.dump(args, f); f.flush()
        result = subprocess.run(['aws', service, operation, '--profile', 'dev', '--region', 'us-east-1',
            '--cli-input-json', 'file://' + f.name, '--no-cli-pager', '--output', 'json'],
            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(service + '/' + operation + ' failed')
    return json.loads(result.stdout or '{}')


def prepare(name):
    if aws('sts', 'get-caller-identity')['Account'] != '838775535952':
        raise RuntimeError('AWS account changed')
    current = aws('cloudformation', 'get-template', StackName=STACK)['TemplateBody']
    if isinstance(current, str): current = json.loads(current)
    before = json.loads((OUT/'cloud-template-before.json').read_text())
    if current != before: raise RuntimeError('Production stack template changed; review before applying')
    candidate = add_access_logs(current)
    assert_log_only_change(current, candidate)
    raw = json.dumps(candidate, ensure_ascii=False, separators=(',', ':'))
    (OUT/'cloud-template-candidate.json').write_text(json.dumps(candidate, ensure_ascii=False, indent=2))
    aws('cloudformation', 'validate-template', TemplateBody=raw)
    result = aws('cloudformation', 'create-change-set', StackName=STACK, ChangeSetName=name,
                 ChangeSetType='UPDATE', TemplateBody=raw, Capabilities=['CAPABILITY_IAM'],
                 Description='Add minimal Stock HTTP API access logs; preserve all existing resources and function code')
    receipt = {'name': name, 'id': result['Id'], 'stackId': result['StackId'],
               'candidateSHA256': hashlib.sha256(raw.encode()).hexdigest(),
               'allowedResources': ['AccessLogs', 'Stage'], 'status': 'CREATED'}
    (OUT/'logging-change-set.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))


def execute():
    receipt = json.loads((OUT/'logging-change-set.json').read_text())
    description = aws('cloudformation', 'describe-change-set', ChangeSetName=receipt['id'])
    if description['Status'] != 'CREATE_COMPLETE': raise RuntimeError('Change set is not ready')
    changes = [item['ResourceChange'] for item in description.get('Changes', [])]
    if {(item['LogicalResourceId'], item['Action']) for item in changes} != {('AccessLogs', 'Add'), ('Stage', 'Modify')}:
        raise RuntimeError('Change set contains unexpected resources')
    if any(item.get('Replacement') in ('True', 'Conditional') for item in changes):
        raise RuntimeError('Change set may replace a production resource')
    receipt['changes'] = changes
    (OUT/'logging-change-set.json').write_text(json.dumps(receipt, indent=2))
    aws('cloudformation', 'execute-change-set', ChangeSetName=receipt['id'])
    receipt['status'] = 'EXECUTED'
    (OUT/'logging-change-set.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps({'status': 'EXECUTED', 'resources': [x['LogicalResourceId'] for x in changes]}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'execute'])
    parser.add_argument('--name', default='stock-read-logs-20261003')
    args = parser.parse_args()
    if args.action == 'prepare': prepare(args.name)
    else: execute()
