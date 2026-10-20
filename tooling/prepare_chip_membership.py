"""Reconcile recorded official current lists; prepare a private dated input locally."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from backend.chips.listing import sh_sz
from backend.chips.runner import encoded

output=root/'outputs/chips-coverage-20261018'
as_of='2026-10-16'

class RecordedOfficialSources:
    def read_bytes(self,url,**kwargs):
        if 'query.sse.com.cn' in url:
            name='sse_star.raw' if parse_qs(urlparse(url).query)['STOCK_TYPE']==['8'] else 'sse_list.raw'
        elif 'www.szse.cn' in url:name='szse_xlsx.raw'
        else:raise ValueError('Unrecognized recorded source')
        return (output/name).read_bytes()
    def receipt(self,url,raw,**kwargs):
        return {'url':url,'sha256':hashlib.sha256(raw).hexdigest(),
            'observed_date':'2026-10-18','recorded_raw':True,**kwargs}

stocks,receipts=sh_sz(RecordedOfficialSources(),as_of)
before=json.loads((output/'state-before.json').read_text(encoding='utf-8'))
existing={s['code']:s for s in before['stocks']}
visible=(output/'bse-official-visible-list.txt').read_bytes()
pairs=visible.decode('utf-8').splitlines()[-1].split(',')
bse={code:datetime.strptime(day,'%Y%m%d').date().isoformat() for code,day in (p.split(':') for p in pairs)}
if len(bse)!=351:raise ValueError('BSE visible list count or duplicate mismatch')
for code,day in bse.items():
    if day>as_of:raise ValueError('Current official list includes a future listing')
    stocks.append({**existing[code],'listed':day})
if len(stocks)!=len(existing) or {s['code'] for s in stocks}!=set(existing):
    raise ValueError('Official code set differs from existing current catalog; inspect discrepancy')
stocks.sort(key=lambda s:s['code'])
receipts.append({'provider':'BSE_CURRENT_LIST_VISIBLE_BROWSER_TABLE',
    'url':'https://www.bse.cn/nq/listedcompany.html','sha256':hashlib.sha256(visible).hexdigest(),
    'observed_date':'2026-10-18','pages':18,'total':351,'first_17_page_count':20,'last_page_count':11,
    'listing_date_caveat':'Transferred selected-tier listings show their selected-tier date; no historical BSE membership inference.'})
bundle={'schema':'stock.chips.official-current-universe.v1','as_of':as_of,
    'observed_at':datetime.now(timezone.utc).isoformat(),'historical_membership':False,
    'stocks':stocks,'stocks_sha256':hashlib.sha256(encoded(stocks)).hexdigest(),'receipts':receipts}
target=root/'var/chip-repair-inputs/official-current-universe-2026-10-16.json'
target.parent.mkdir(parents=True,exist_ok=True)
target.write_text(json.dumps(bundle,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
print(json.dumps({'as_of':as_of,'official_unique':len(stocks),'same_code_set_as_existing':True,
    'SSE':sum(s['code'].startswith('6') for s in stocks),
    'SZSE':sum(s['code'].startswith(('0','3')) for s in stocks),'BSE':len(bse),'cloud_writes':0}))
