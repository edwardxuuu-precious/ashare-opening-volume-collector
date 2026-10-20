"""Verify full-universe progress using existing real collection checkpoints; no HTTP."""
from pathlib import Path
import json
from collect import make_snapshot,publish,write_json
source=Path('var/data/initial-batch');out=Path('var/data/batch-verification')
universe=json.loads((source/'universe.json').read_text())['rows'][:3]
records={item['code']:json.loads((source/'checkpoint'/f"{item['code']}.json").read_text()) for item in universe}
dates=sorted(next(iter(records.values()))['days'])[-5:]
partial,scope,attempted=make_snapshot(universe,{universe[0]['code']:records[universe[0]['code']]},dates,True)
publish(partial,dates,out/'partial',scope,len(universe),attempted)
a=json.loads((out/'partial'/'manifest.json').read_text())
assert a['scope']=='partial' and a['attemptedCount']==1 and a['pendingCount']==2
assert all(day['total']==3 and day['valid']==1 and day['missing']==2 and day['status']=='partial' for day in a['dates'])
full,scope,attempted=make_snapshot(universe,records,dates,True)
publish(full,dates,out/'complete',scope,len(universe),attempted)
b=json.loads((out/'complete'/'manifest.json').read_text())
assert b['scope']=='full' and b['attemptedCount']==3 and b['pendingCount']==0
summary={'status':'passed','source':'actual initial-batch checkpoints','codes':[item['code'] for item in universe],'dates':dates,'partial':{'scope':a['scope'],'attempted':a['attemptedCount'],'pending':a['pendingCount'],'dates':len(a['dates'])},'complete':{'scope':b['scope'],'attempted':b['attemptedCount'],'pending':b['pendingCount'],'dates':len(b['dates'])}}
write_json(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False))
