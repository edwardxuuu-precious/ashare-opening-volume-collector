"""Read-only private publication check for the new 210-bar node model.

All screening rows are checked; six stock distributions are independently
replayed from their exported source candles. This does not download all frames.
"""
import argparse
import gzip
import io
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
import boto3
from botocore.config import Config
from backend.chips.eastmoney_model import MODEL,Bar,compare_snapshots
from backend.chips.runner import implementation_identity,session_snapshots


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dataset',required=True);a=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    s3=boto3.Session(profile_name='stock-dev',region_name='us-east-1').client('s3',
        config=Config(connect_timeout=7,read_timeout=25,retries={'total_max_attempts':2}))
    def read(key):
        raw=s3.get_object(Bucket='stock-private-access-databucket-bmzud610gtex',Key=key)['Body'].read(8000001)
        assert len(raw)<=8000000
        if raw[:2]==b'\x1f\x8b':raw=gzip.GzipFile(fileobj=io.BytesIO(raw)).read(64000001)
        assert len(raw)<=64000000
        return json.loads(raw)
    m=read('data/chips/daily/manifest.json')
    assert m['dataset_id']==m['active_slot']==a.dataset
    assert m['execution_identity']==implementation_identity()
    assert m['model']==MODEL and m['model_version']==MODEL['version']
    assert m['history_window_bars']==210 and m['coefficient']==2 and m['unchanged_threshold'] is None
    screen=read(f'data/chips/daily/{a.dataset}/screening.json')
    rows=screen['rows'];by_code={r['security_id']:r for r in rows}
    assert screen['dataset_id']==a.dataset and screen['as_of']==m['as_of'] and screen['unchanged_threshold'] is None
    assert len(rows)==len(by_code)==m['universe_total']==m['scanned_count']
    assert sum(r['chip_status']=='READY' for r in rows)==m['model_ready_count']
    counts=Counter(r['prefilter_status'] for r in rows)
    assert {k:counts[k] for k in ('PASS','FAIL','UNKNOWN')}==m['screen_counts']
    for r in rows:
        assert r['prefilter_status'] in ('PASS','FAIL','UNKNOWN')
        assert all(d in m['official_sessions'] for d in r['recent_limit_up_dates'])
        if r['prefilter_status']=='PASS':assert Decimal(r['close_cny'])<10 and r['recent_limit_up_dates']
        if r.get('limit_evidence'):
            assert [e['date'] for e in r['limit_evidence']]==m['official_sessions']
            assert [e['date'] for e in r['limit_evidence'] if e['status']=='PASS']==r['recent_limit_up_dates']
        for anchor in r['original_peak_changes']:
            assert float(anchor['current_share_percent'])-float(anchor['previous_share_percent'])==float(anchor['change_percentage_points'])
    output=root/'outputs/chips-eastmoney-release-20261019'/a.dataset;output.mkdir(exist_ok=True)
    checks=[]
    for code in ('000573','600812','000725','000001','688256','920010'):
        if code not in by_code:continue
        frame=read(f'data/chips/daily/{a.dataset}/{code}.json')
        assert frame['model']==MODEL and frame['execution_identity']==m['execution_identity']
        assert frame['security_id']==code and frame['dataset_id']==a.dataset and frame['as_of']==m['as_of']
        assert frame['screening_row']==by_code[code] and frame['unchanged_threshold'] is None
        if frame['chip_status']!='READY':checks.append({'code':code,'status':'MISSING','reasons':frame['reasons']});continue
        bars=[Bar(h['date'],*(Decimal(h[k]) for k in ('open','high','low','close','volume_shares','amount_cny','turnover_percent'))) for h in frame['history']]
        targets=sorted(frame['snapshots'])
        no_trade={d for s in frame['snapshots'].values() for d in s.get('no_trade_confirmation_dates',[])}
        replay,_=session_snapshots(bars,targets,security_id=code,no_trade=no_trade)
        for day,s in frame['snapshots'].items():
            expected=replay[day]
            assert s['model_version']==MODEL['version'] and s['history_window_bars']==210
            assert 1<=s['history_rows_processed']<=s['loaded_window_rows']<=210 and len(s['profile'])==150
            for field in ('raw_node_masses','profile','peak_prices_cny','peak_unrounded_prices_cny','peak_node_indices'):
                assert s[field]==expected[field],(code,day,field)
            assert abs(sum(float(p['share_percent']) for p in s['profile'])-100)<1e-10
        if len(targets)==2:
            comparison=compare_snapshots(*(frame['snapshots'][d] for d in targets))
            for anchor,row in zip(comparison['anchors'],by_code[code]['original_peak_changes'],strict=True):
                assert anchor['previous_share_percent']==row['previous_share_percent']
                assert anchor['current_same_price_share_percent']==row['current_share_percent']
                assert anchor['change_percentage_points']==row['change_percentage_points']
                for day,field in ((targets[0],'previous_share_percent'),(targets[1],'current_same_price_share_percent')):
                    assert frame['snapshots'][day]['comparison_anchor_shares_percent'][row['price_cny']]==anchor[field]
        (output/(code+'.json')).write_text(json.dumps(frame,ensure_ascii=False),encoding='utf-8')
        checks.append({'code':code,'status':'PASS','exact_raw_and_share_and_peak_replay':True,
                       'window_rows':frame['snapshots'][m['as_of']]['loaded_window_rows']})
    assert read('data/chips/daily/manifest.json')['dataset_id']==a.dataset
    (output/'manifest.json').write_text(json.dumps(m,ensure_ascii=False),encoding='utf-8')
    (output/'screening.json').write_text(json.dumps(screen,ensure_ascii=False),encoding='utf-8')
    result={'dataset_id':a.dataset,'as_of':m['as_of'],'universe_total':len(rows),'model_ready':m['model_ready_count'],
        'screen_counts':m['screen_counts'],'market_complete':m['market_complete'],'universe_coverage':m['universe_coverage'],
        'model_version':MODEL['version'],'history_window_bars':210,'coefficient':2,'unchanged_threshold':None,
        'all_screen_rows_checked':True,'frame_replays':checks,'cloud_writes':0}
    (output/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
