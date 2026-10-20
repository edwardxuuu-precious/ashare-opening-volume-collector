"""Legacy runtime export; the unified Git repository needs no extra export."""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path

root=Path(__file__).resolve().parents[1]
repo=root.parent/'ashare-opening-volume-collector'
files={f'backend/chips/{name}':root/'backend'/'chips'/name for name in
       ('__init__.py','calendar.py','model.py','eastmoney_model.py','ohlc4_input.py','current_quote.py','screen.py','source.py','baostock_history.py','ths_history.py','listing.py','display.py','limit_reference.py','runner.py','probe.py')}
files['.github/workflows/chips.yml']=root/'deploy/actions/workflows/chips.yml'
p=argparse.ArgumentParser();p.add_argument('--refresh-export',action='store_true');a=p.parse_args()
if (root/'.git').exists():
    top=subprocess.run(['git','rev-parse','--show-toplevel'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    if Path(top).resolve()!=root:raise SystemExit('Unexpected Git root')
    subprocess.run(['git','diff','--check'],cwd=root,check=True)
    print(json.dumps({'unified_repository':True,'exported_files':0,'message':'Website and runtime are already versioned together; commit reviewed changes directly.'}))
    raise SystemExit(0)
changes=subprocess.run(['git','status','--porcelain'],cwd=repo,capture_output=True,text=True,check=True).stdout.splitlines()
if changes:
    if not a.refresh_export:raise SystemExit('Public checkout contains existing changes; preserve them before export.')
    previous=json.loads((root/'outputs/chips-daily-20261018/RELEASE_PROPOSAL.json').read_text(encoding='utf-8'))['public_files']
    for change in changes:
        name=change[3:]
        if name not in files or name not in previous or hashlib.sha256((repo/name).read_bytes()).hexdigest()!=previous[name]:
            raise SystemExit('Existing change is not the exact previous export: '+name)
transport='backend/scripts/baostock_source.py'
if (root/transport).read_text(encoding='utf-8')!=(repo/transport).read_text(encoding='utf-8'):raise SystemExit('Shared source transport differs; review required.')
hashes={}
for name,source in files.items():
    raw=source.read_bytes()
    for marker in (b'BEGIN PRIVATE KEY',b'BEGIN OPENSSH PRIVATE KEY',b'AKIA',b'aws_secret_access_key',b'ghp_'):
        if marker in raw:raise SystemExit('Unexpected secret-like content in '+name)
    target=repo/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    hashes[name]=hashlib.sha256(raw).hexdigest()
proposal=json.loads((root/'outputs/chips-daily-20261018/RELEASE_PROPOSAL.json').read_text(encoding='utf-8'))
proposal['public_files']=hashes
proposal['implementation']='Immutable recoverable SH/SZ/BJ chip batches, unadjusted history fallback, exact same-session cache replay, explicit no-trade and first-listing status, bounded daily repair passes'
(root/'outputs/chips-daily-20261018/RELEASE_PROPOSAL.json').write_text(json.dumps(proposal,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
subprocess.run(['git','diff','--check'],cwd=repo,check=True)
print(json.dumps({'exported_files':len(files),'proposal_sha256':hashlib.sha256((root/'outputs/chips-daily-20261018/RELEASE_PROPOSAL.json').read_bytes()).hexdigest()}))
