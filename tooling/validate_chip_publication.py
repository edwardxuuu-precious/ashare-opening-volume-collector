"""Bounded read-only acceptance of the committed market publication.

Checks every screening row and six stock frames with their private archives.
Full-market
frame response bounds are the runner's recorded per-frame checks, not a claim
that this Windows inspection downloaded every full profile.
"""
import argparse
from collections import Counter
from decimal import Decimal,localcontext
import gzip,hashlib,io,json
from pathlib import Path
import sys

import boto3
from botocore.config import Config

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from backend.chips.calendar import last_ten
from backend.chips.display import response_upper_bound
from backend.chips.runner import encoded,implementation_identity


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--expected-total',type=int,default=5577)
    args=parser.parse_args()
    if len(args.dataset)!=16 or any(c not in '0123456789abcdef' for c in args.dataset):
        parser.error('Invalid generation')
    client=boto3.Session(profile_name='stock-dev',region_name='us-east-1').client('s3',
        config=Config(connect_timeout=7,read_timeout=25,retries={'total_max_attempts':2}))
    bucket='stock-private-access-databucket-bmzud610gtex'
    def read(key):
        response=client.get_object(Bucket=bucket,Key=key)
        raw=response['Body'].read(20000001)
        if len(raw)>20000000:raise ValueError('Compressed object exceeds 20 MB inspection bound')
        content=gzip.GzipFile(fileobj=io.BytesIO(raw)).read(100000001) if raw[:2]==b'\x1f\x8b' else raw
        if len(content)>100000000:raise ValueError('Decoded object exceeds 100 MB inspection bound')
        return json.loads(content)
    manifest=read('data/chips/daily/manifest.json')
    assert manifest['dataset_id']==args.dataset and manifest['active_slot']==args.dataset
    assert manifest['execution_identity']==implementation_identity()
    assert manifest['coefficient']==2 and manifest['unchanged_threshold'] is None
    assert manifest['universe_total']==manifest['scanned_count']==manifest['model_ready_count']==args.expected_total
    assert manifest['official_sessions']==last_ten(manifest['as_of'])
    assert manifest['universe_coverage']=='OFFICIAL_CURRENT_LIST_RECONCILED'
    assert manifest['display_response_verified_count']==args.expected_total
    assert 0<manifest['max_display_response_upper_bound_bytes']<5900000
    screen=read(f'data/chips/daily/{args.dataset}/screening.json')
    assert screen['dataset_id']==args.dataset and screen['as_of']==manifest['as_of'] and screen['unchanged_threshold'] is None
    assert response_upper_bound(screen)<5900000
    rows=screen['rows'];by_code={r['security_id']:r for r in rows}
    assert len(rows)==len(by_code)==args.expected_total
    official=json.loads((root/'var/chip-repair-inputs'/f'official-current-universe-{manifest["as_of"]}.json').read_text(encoding='utf-8'))
    assert {s['code'] for s in official['stocks']}==set(by_code)
    counts=Counter(r['prefilter_status'] for r in rows)
    assert {k:counts[k] for k in ('PASS','FAIL','UNKNOWN')}==manifest['screen_counts']
    assert all(r['chip_status']=='READY' and 0<r['display_response_upper_bound_bytes']<5900000 for r in rows)
    quotes=read(f'collector/chips/limits/{manifest["as_of"]}.json')['quotes']
    assert set(quotes)==set(by_code)
    for row in rows:
        quote=quotes[row['security_id']]
        assert quote['date']==manifest['as_of'] and Decimal(quote['close'])==Decimal(row['close_cny'])
        evidence=row['limit_evidence']
        assert [r['date'] for r in evidence]==manifest['official_sessions']
        assert all(r['status'] in ('PASS','FAIL','UNKNOWN') and r['reason'] for r in evidence)
        positive=[r['date'] for r in evidence if r['status']=='PASS']
        assert positive==row['recent_limit_up_dates']
        close=Decimal(row['close_cny']);assert close>0
        expected='FAIL' if close>=10 else 'PASS' if positive else 'UNKNOWN' if any(r['status']=='UNKNOWN' for r in evidence) else 'FAIL'
        assert expected==row['prefilter_status']
    comparisons=Counter(r.get('comparison_status','TWO_SESSIONS') for r in rows)
    output=root/'var/chip-publication-validation'/args.dataset;output.mkdir(parents=True,exist_ok=True)
    samples=[]
    for code in ('000016','000573','600812','920010','920196','688256'):
        frame=read(f'data/chips/daily/{args.dataset}/{code}.json')
        assert frame['dataset_id']==args.dataset and frame['as_of']==manifest['as_of'] and frame['security_id']==code
        assert frame['chip_status']=='READY' and frame['unchanged_threshold'] is None
        assert frame['screening_row']==by_code[code]
        assert frame['model']['historical_turnover_decay_coefficient']=='2'
        assert response_upper_bound(frame)<5900000
        reuse=frame.get('price_exclusion_reuse')
        if reuse:
            assert reuse['date']==manifest['as_of'] and reuse['quote_private_key']==f'collector/chips/limits/{manifest["as_of"]}.json'
            assert Decimal(reuse['close_cny'])==Decimal(frame['snapshots'][manifest['as_of']]['close_cny'])==Decimal(by_code[code]['close_cny'])>=10
            assert by_code[code]['prefilter_status']=='FAIL' and frame.get('reused_from_generation')
        dates=sorted(frame['snapshots'])
        expected=[manifest['as_of']] if frame.get('comparison_status')=='NO_PREVIOUS_LISTED_SESSION' else [manifest['previous_date'],manifest['as_of']]
        assert dates==expected
        for day,snapshot in frame['snapshots'].items():
            with localcontext() as context:
                context.prec=80
                assert abs(sum(Decimal(p['share_percent']) for p in snapshot['profile'])-100)<Decimal('1e-35')
            assert len(snapshot['profile'])<=100000
        row=by_code[code]
        assert row['peak_prices_cny']==frame['snapshots'][manifest['as_of']]['peak_prices_cny']
        if len(dates)==2:
            before,after=(frame['snapshots'][day] for day in dates)
            assert row['previous_peak_prices_cny']==before['peak_prices_cny']
            assert [r['price_cny'] for r in row['original_peak_changes']]==before['peak_prices_cny']
            def exact(snapshot,price):
                if snapshot.get('profile_format'):return snapshot['exact_anchor_shares_percent'][price]
                return next((p['share_percent'] for p in snapshot['profile'] if p['price_cny']==price),'0')
            for change in row['original_peak_changes']:
                price=change['price_cny']
                assert change['previous_share_percent']==exact(before,price)
                assert change['current_share_percent']==exact(after,price)
                with localcontext() as context:
                    context.prec=80
                    delta=Decimal(change['current_share_percent'])-Decimal(change['previous_share_percent'])
                    assert abs(delta-Decimal(change['change_percentage_points']))<Decimal('1e-35')
        full_key=frame.get('full_precision_frame_private_key')
        if full_key:
            assert full_key.startswith('collector/chips/full-frames/')
            full=read(full_key)
            assert full['security_id']==code and full['source']['sha256']==frame['source']['sha256']
            anchors={p for s in full['snapshots'].values() for p in s['peak_prices_cny']}
            for day,snapshot in full['snapshots'].items():
                digest=hashlib.sha256(encoded(snapshot['profile'])).hexdigest()
                assert full['archived_display_profile_sha256'][day]==digest
                shown=frame['snapshots'][day]
                for field in ('peak_prices_cny','peak_bin_share_percent','model_mean_cost_cny','close_cny','turnover_percent'):
                    assert shown[field]==snapshot[field]
                if shown.get('original_profile_sha256'):
                    assert shown['original_profile_sha256']==digest
                    original={p['price_cny']:p['share_percent'] for p in snapshot['profile']}
                    legacy={p['price_cny']:p['share_percent'] for p in shown['profile']}
                    assert all(shown['exact_anchor_shares_percent'][p]==original.get(p,'0')==legacy.get(p,'0') for p in anchors)
        if code=='920196':
            assert frame['comparison_status']=='NO_PREVIOUS_LISTED_SESSION' and len(frame['history'])==1
            assert not by_code[code]['previous_peak_prices_cny'] and not by_code[code]['original_peak_changes']
        (output/(code+'.json')).write_bytes(encoded(frame))
        samples.append({'security_id':code,'history_rows':len(frame['history']),'snapshot_dates':dates,
            'display_bins':{d:len(s['profile']) for d,s in frame['snapshots'].items()},'private_full_precision_archive_checked':bool(full_key),
            'dated_high_price_reuse_proof_checked':bool(reuse)})
    assert read('data/chips/daily/manifest.json')['dataset_id']==args.dataset
    (output/'manifest.json').write_bytes(encoded(manifest));(output/'screening.json').write_bytes(encoded(screen))
    result={'dataset_id':args.dataset,'as_of':manifest['as_of'],'model_ready':len(rows),'unique_codes':len(by_code),
        'screen_counts':manifest['screen_counts'],'comparisons':dict(comparisons),
        'all_screen_rows_checked':True,'all_official_current_codes_match':True,
        'all_latest_closes_cross_checked_against_dated_quotes':True,
        'runner_per_frame_response_bounds_checked':manifest['display_response_verified_count'],
        'max_response_upper_bound_bytes':manifest['max_display_response_upper_bound_bytes'],
        'market_complete':manifest['market_complete'],'sampled_frames':samples,'cloud_writes':0,
        'coefficient':2,'unchanged_threshold':None}
    (root/'outputs/chips-coverage-20261018'/f'publication-validation-{args.dataset}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
