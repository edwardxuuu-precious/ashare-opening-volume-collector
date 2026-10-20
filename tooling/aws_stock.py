"""Windows deployment client; uses stock-dev SSO without any Mac dependency."""
import json,os,subprocess,shutil
EXPECTED_ACCOUNT='838775535952'
def aws(args):
    binary=shutil.which('aws') or r'C:\Program Files\Amazon\AWSCLIV2\aws.exe'
    r=subprocess.run([binary,'--profile','stock-dev','--region','us-east-1','--output','json','--no-cli-pager','--cli-connect-timeout','7','--cli-read-timeout','25',*args],
        capture_output=True,text=True,env={**os.environ,'AWS_MAX_ATTEMPTS':'1','AWS_EC2_METADATA_DISABLED':'true'},timeout=45)
    if r.returncode:raise RuntimeError(r.stderr[:600])
    return json.loads(r.stdout) if r.stdout.strip() else {}
def verify_account():
    v=aws(['sts','get-caller-identity'])
    if v.get('Account')!=EXPECTED_ACCOUNT:raise RuntimeError('Unexpected AWS account; release stopped')
    return v
