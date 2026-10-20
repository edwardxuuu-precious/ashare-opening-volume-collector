"""Bounded anonymous API verification; no credentials are read."""
import json,urllib.request,urllib.error
results=[]
for path in ('/data/chips/daily/status.json','/data/chips/manifest.json'):
    try:
        with urllib.request.urlopen('https://api.stock.iteready.com'+path,timeout=15) as r:status=r.status
    except urllib.error.HTTPError as e:status=e.code
    results.append({'path':path,'status':status})
print(json.dumps(results))
if not all(r['status']==401 for r in results):raise SystemExit(1)
