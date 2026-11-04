import json
import os
import shutil
import subprocess

EXPECTED_ACCOUNT = '838775535952'


def aws(args, *, profile='stock-dev', region='us-east-1'):
    binary = shutil.which('aws') or r'C:\Program Files\Amazon\AWSCLIV2\aws.exe'
    result = subprocess.run(
        [binary, '--profile', profile, '--region', region, '--output', 'json',
         '--no-cli-pager', '--cli-connect-timeout', '7', '--cli-read-timeout', '25', *args],
        capture_output=True, text=True,
        env={**os.environ, 'AWS_MAX_ATTEMPTS': '1', 'AWS_EC2_METADATA_DISABLED': 'true'},
        timeout=45,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[:600])
    return json.loads(result.stdout) if result.stdout.strip() else {}


def verify_account(*, profile='stock-dev', region='us-east-1'):
    identity = aws(['sts', 'get-caller-identity'], profile=profile, region=region)
    if identity.get('Account') != EXPECTED_ACCOUNT:
        raise RuntimeError('Unexpected AWS account; release stopped')
    return identity
