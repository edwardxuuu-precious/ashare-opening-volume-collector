"""Windows GCM-backed GitHub calls. Credentials never leave memory or stdout."""
import json,os,subprocess,sys,urllib.request
BASE='https://api.github.com/repos/edwardxuuu-precious/ashare-opening-volume-collector'
def request(path,method='GET',payload=None,raw_response=False):
    r=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\nusername=edwardxuuu-precious\n\n',
        capture_output=True,text=True,env={**os.environ,'GCM_INTERACTIVE':'never'},check=True)
    values=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
    token=values['password']
    req=urllib.request.Request(BASE+path,method=method,data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'Stock-Windows-deploy','Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=35) as response:
        raw=response.read()
        return raw if raw_response else json.loads(raw) if raw else {'status':response.status}
if __name__=='__main__':
    value=request(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else 'GET',json.loads(sys.argv[3]) if len(sys.argv)>3 else None)
    if isinstance(value,dict) and 'workflow_runs' in value:
        value={**{k:v for k,v in value.items() if k!='workflow_runs'},'workflow_runs':[{k:r.get(k) for k in ('id','status','conclusion','html_url','head_sha','created_at')} for r in value['workflow_runs']]}
    print(json.dumps(value,ensure_ascii=False))
