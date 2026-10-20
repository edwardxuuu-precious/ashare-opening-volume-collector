"""One-time bounded local supplement; it never writes production or cloud checkpoints."""
import json,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[2]
source=root/'var/data/full-market-live';out=root/'var/data/full-market-supplement';out.mkdir(exist_ok=True)
for name in ['universe.json','calendar.json']:
 target=out/name
 if not target.exists():target.write_bytes((source/name).read_bytes())
universe=json.loads((out/'universe.json').read_text())
assert universe['total']==5556 and len(universe['rows'])==5556
symbols=[r['code'] for r in universe['rows'][-2500:]]
(root/'outputs/full-market/supplement-scope.json').write_text(json.dumps({'stocks':2500,'symbols':symbols,'purpose':'initial backfill acceleration only; no production publication'},ensure_ascii=False))
command=[sys.executable,str(Path(__file__).with_name('collect.py')),'--months','6','--symbols',','.join(symbols),'--max-stocks','2500','--resume','--resume-attempted','--efficient-sina','--baostock-fallback','--reconcile-latest-first','--publish-every','100','--out',str(out)]
with (root/'outputs/full-market/supplement.log').open('a') as log:
 result=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
print(json.dumps({'supplementExitCode':result.returncode,'requestedStocks':2500}),flush=True)
raise SystemExit(result.returncode)
