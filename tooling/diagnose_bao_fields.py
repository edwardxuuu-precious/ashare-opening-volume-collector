"""One bounded read for an observed conversion failure; preserve SDK strings."""
import json,sys,traceback
from pathlib import Path
from backend.chips import baostock_history as bao
code=sys.argv[1];root=Path(__file__).resolve().parents[1]/'outputs/chips-daily-20261018'
original=bao.normalize
def observe(stock,rows,as_of,start_date='1990-12-19'):
    (root/(code+'-bao-diagnostic-sdk.json')).write_text(json.dumps({'fields':bao.FIELDS.split(','),'rows':rows,'as_of':as_of,'start_date':start_date}),encoding='utf-8')
    return original(stock,rows,as_of,start_date)
bao.normalize=observe
source=bao.BaoHistory()
try:
    _,receipt,raw=source.history({'code':code,'name':'SOURCE_DIAGNOSTIC'},'2026-10-16')
    (root/(code+'-bao-diagnostic.raw.json')).write_bytes(raw)
    print(json.dumps({'code':code,'outcome':'SOURCE_OK','bytes':len(raw)}))
except Exception as e:
    traceback.print_exc(limit=6)
    raise SystemExit(1)
finally:source.close()
