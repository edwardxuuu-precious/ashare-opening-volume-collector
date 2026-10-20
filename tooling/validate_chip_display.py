"""Read three real staged examples and verify bounded display transport locally."""
import argparse,base64,gzip,hashlib,io,json
from decimal import Decimal,localcontext
from pathlib import Path
import sys
import boto3
from botocore.config import Config

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from backend.chips.display import display_frame
from backend.chips.runner import encoded

parser=argparse.ArgumentParser();parser.add_argument('--dataset',required=True);args=parser.parse_args()
if len(args.dataset)!=16 or any(c not in '0123456789abcdef' for c in args.dataset):parser.error('Invalid generation')
client=boto3.Session(profile_name='stock-dev',region_name='us-east-1').client('s3',
    config=Config(connect_timeout=7,read_timeout=25,retries={'total_max_attempts':2}))
directory=root/'var/chip-display-validation'/args.dataset;directory.mkdir(parents=True,exist_ok=True)
results=[]
for code in ('000156','688111','688256'):
    response=client.get_object(Bucket='stock-private-access-databucket-bmzud610gtex',Key=f'data/chips/daily/{args.dataset}/{code}.json')
    raw=response['Body'].read(20000001)
    if len(raw)>20000000:raise ValueError('Compressed example exceeds 20 MB bound')
    payload=gzip.GzipFile(fileobj=io.BytesIO(raw)).read(100000001) if raw[:2]==b'\x1f\x8b' else raw
    if len(payload)>100000000:raise ValueError('Decoded example exceeds 100 MB bound')
    original=json.loads(payload)
    if original['dataset_id']!=args.dataset or original['security_id']!=code or original['chip_status']!='READY':raise ValueError('Wrong staged example')
    shown=display_frame(original)
    anchors={p for s in original['snapshots'].values() for p in s['peak_prices_cny']}
    with localcontext() as context:
        context.prec=80
        for day,before in original['snapshots'].items():
            after=shown['snapshots'][day]
            for field in ('peak_prices_cny','peak_bin_share_percent','model_mean_cost_cny','close_cny','turnover_percent'):
                if before[field]!=after[field]:raise ValueError('Exact reported metric changed')
            old_total=sum(Decimal(p['share_percent']) for p in before['profile'])
            new_total=sum(Decimal(p['share_percent']) for p in after['profile'])
            if abs(old_total-new_total)>Decimal('1e-40'):raise ValueError('Display grouping lost mass')
            if after.get('profile_format'):
                exact={p['price_cny']:p['share_percent'] for p in before['profile']}
                if any(after['exact_anchor_shares_percent'][p]!=exact.get(p,'0') for p in anchors):raise ValueError('Exact anchored share changed')
                legacy={p['price_cny']:p['share_percent'] for p in after['profile']}
                if any(legacy.get(p,'0')!=exact.get(p,'0') for p in anchors):raise ValueError('Older browser exact lookup changed')
    if original['screening_row']!=shown['screening_row']:raise ValueError('Screening row changed')
    content=encoded(shown)
    # A conservative upper bound for the existing authenticated gzip response envelope.
    envelope_bound=len(base64.b64encode(gzip.compress(json.dumps(shown,ensure_ascii=False).encode(),compresslevel=6)))+20000
    if envelope_bound>=5900000:raise ValueError('Displayed example still exceeds API envelope')
    (directory/(code+'.json')).write_bytes(content)
    results.append({'security_id':code,'original_json_bytes':len(payload),'display_json_bytes':len(content),
        'api_envelope_upper_bound':envelope_bound,'display_sha256':hashlib.sha256(content).hexdigest(),
        'original_bins':{d:len(s['profile']) for d,s in original['snapshots'].items()},
        'display_bins':{d:len(s['profile']) for d,s in shown['snapshots'].items()},
        'exact_metrics_and_anchors_unchanged':True,'legacy_exact_anchor_lookup_unchanged':True,
        'screening_unchanged':True,'profile_mass_preserved':True})
receipt={'dataset':args.dataset,'scope':'THREE_REAL_STAGED_EXAMPLES_NOT_PUBLISHED_MARKET_ACCEPTANCE','cloud_writes':0,'results':results}
(root/'outputs/chips-coverage-20261018/real-display-transport.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
print(json.dumps(receipt))
