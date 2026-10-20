"""Read-only cold replays of the release's actual repaired and special cases."""
import argparse,gzip,hashlib,io,json,time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import boto3
from botocore.config import Config
from backend.chips.eastmoney_model import Bar,MODEL
from backend.chips.runner import implementation_identity,session_snapshots


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dataset',required=True)
    parser.add_argument('--max-minutes',type=int,default=90);args=parser.parse_args()
    if len(args.dataset)!=16 or any(c not in '0123456789abcdef' for c in args.dataset):parser.error('Invalid dataset')
    if not 2<=args.max_minutes<=100:parser.error('Runtime bound must be 2..100 minutes')
    root=Path(__file__).resolve().parents[1];base=root/'outputs/chips-eastmoney-release-20261019'
    repaired=set(json.loads((base/'roundoff-staged-missing-replays.json').read_text(encoding='utf8'))['rows'])
    extra={'601218','601985','000573','600812','000725','000001','688256','920010',
           '000016','002388','002717','002731','301314','301468','600293','603517','603759','603838','688496'}
    wanted=repaired|extra;expected=implementation_identity();output=base/args.dataset;output.mkdir(exist_ok=True)
    s3=boto3.Session(profile_name='stock-dev',region_name='us-east-1').client('s3',
        config=Config(max_pool_connections=5,connect_timeout=7,read_timeout=25,retries={'total_max_attempts':2}))
    def read(key):
        raw=s3.get_object(Bucket='stock-private-access-databucket-bmzud610gtex',Key=key)['Body'].read(20000001)
        if len(raw)>20000000:raise ValueError('Bounded read exceeded')
        decoded=gzip.GzipFile(fileobj=io.BytesIO(raw)).read(80000001) if raw[:2]==b'\x1f\x8b' else raw
        if len(decoded)>80000000:raise ValueError('Bounded decoded body exceeded')
        return json.loads(decoded),hashlib.sha256(raw).hexdigest()
    def check(code):
        f,body_hash=read(f'data/chips/daily/{args.dataset}/{code}.json')
        assert f['dataset_id']==args.dataset and f['security_id']==code and f['as_of']=='2026-10-19'
        assert f['execution_identity']==expected and f['model']==MODEL and f['unchanged_threshold'] is None
        if f['chip_status']!='READY':return code,{'status':'MISSING','reasons':f['reasons'],'stored_body_sha256':body_hash}
        bars=[Bar(h['date'],*(Decimal(h[k]) for k in ('open','high','low','close','volume_shares','amount_cny','turnover_percent'))) for h in f['history']]
        no_trade={d for snap in f['snapshots'].values() for d in snap.get('no_trade_confirmation_dates',[])}
        replay,_=session_snapshots(bars,sorted(f['snapshots']),security_id=code,no_trade=no_trade)
        for day,snap in f['snapshots'].items():
            for field in ('raw_node_masses','profile','peak_prices_cny','peak_unrounded_prices_cny','peak_node_indices','roundoff_repairs'):
                assert snap[field]==replay[day][field],(code,day,field)
            assert len(snap['profile'])==150 and all(Decimal(x)>=0 for x in snap['raw_node_masses'])
            assert abs(sum(float(x['share_percent']) for x in snap['profile'])-100)<1e-10
        if code in ('601218','601985'):
            assert f['screening_row']['prefilter_status']=='FAIL'
            witness=f['historical_limit_reference']['corroborating_source']
            assert witness['archived_request_as_of']=='2026-10-16'
            assert '2026-10-19' not in witness['current_overlap_sessions']
        return code,{'status':'PASS','stored_body_sha256':body_hash,'exact_raw_share_peak_and_repair_replay':True,
                    'roundoff_repair_count':sum(len(x['roundoff_repairs']) for x in f['snapshots'].values()),
                    'prefilter':f['screening_row']['prefilter_status']}
    checked={};deadline=time.monotonic()+args.max_minutes*60;bound=False
    with ThreadPoolExecutor(max_workers=4) as pool:
        while time.monotonic()<deadline:
            state,_=read('collector/chips/state.json')
            if state['dataset_id']!=args.dataset:break
            assert state['execution_identity']==expected
            pending=sorted((set(state['rows'])&wanted)-set(checked))
            for code,result in pool.map(check,pending):checked[code]=result
            manifest,_=read('data/chips/daily/manifest.json')
            bound=manifest.get('dataset_id')==args.dataset and manifest.get('execution_identity')==expected
            result={'dataset_id':args.dataset,'scope':'Actual formerly missing, reference-conflict, zero-quote and representative frames; cold replay checks cache migration, not an independent market-holder estimate.',
                'expected_frames':len(wanted),'checked_frames':len(checked),'published_manifest_bound':bound,
                'all_checked_pass':all(v['status']=='PASS' for v in checked.values()),'cloud_writes':0,'frames':checked}
            (output/'repaired-frames-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
            if pending:print(json.dumps({k:v for k,v in result.items() if k not in ('frames','scope')},ensure_ascii=False),flush=True)
            if len(checked)==len(wanted) and bound:
                assert result['all_checked_pass'];print(json.dumps({'repaired_frames_validation':'PASS','frames':len(checked),'published_manifest_bound':True}),flush=True);return
            time.sleep(25)
    raise RuntimeError('Validation window ended or batch changed before full publication binding')


if __name__=='__main__':main()
