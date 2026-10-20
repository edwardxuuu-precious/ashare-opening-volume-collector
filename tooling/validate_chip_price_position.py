"""Read-only acceptance of an exact new batch against its prior sealed batch.

Reads two screening tables and at most 300 prior candidates' stock frames, with
four readers. Does not write AWS, replay the model, or request public histories.
"""
import argparse,concurrent.futures,csv,gzip,hashlib,json,sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
import boto3
from botocore.config import Config

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.chips.runner import implementation_identity

def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True);p.add_argument('--previous',required=True)
    a=p.parse_args()
    assert all(len(x)==16 and all(c in '0123456789abcdef' for c in x) for x in (a.dataset,a.previous))
    client=boto3.Session(profile_name='stock-dev',region_name='us-east-1').client('s3',
        config=Config(connect_timeout=7,read_timeout=25,retries={'total_max_attempts':2},max_pool_connections=4))
    def read(key):
        raw=client.get_object(Bucket='stock-private-access-databucket-bmzud610gtex',Key=key)['Body'].read(6000001)
        assert len(raw)<=6000000
        value=gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw
        assert len(value)<=20000000
        return json.loads(value)
    manifest=read('data/chips/daily/manifest.json');assert manifest['dataset_id']==a.dataset
    assert manifest['execution_identity']==implementation_identity()
    assert manifest['coefficient']==2 and manifest['history_window_bars']==210 and manifest['unchanged_threshold'] is None
    assert manifest['price_position_rule']=='CLOSE_GT_ANY_PEAK_OR_MEAN_COST_V1'
    screen=read(f'data/chips/daily/{a.dataset}/screening.json');old=read(f'data/chips/daily/{a.previous}/screening.json')
    assert screen['as_of']==old['as_of']==manifest['as_of']
    rows=screen['rows'];before={r['security_id']:r for r in old['rows']}
    assert len(rows)==len(before)==manifest['universe_total'] and {r['security_id'] for r in rows}==set(before)
    counts=Counter();branches=Counter();candidates=[]
    for row in rows:
        prior=before[row['security_id']]
        for key in ('close_cny','prefilter_status','recent_limit_up_dates','peak_prices_cny','previous_peak_prices_cny','original_peak_changes'):
            assert row[key]==prior[key],(row['security_id'],key,'prior evidence or chip values changed')
        close=Decimal(row['close_cny']);mean=Decimal(row['model_mean_cost_cny'])
        assert close.is_finite() and mean.is_finite() and close>0 and mean>0
        peak_above=any(close>Decimal(x) for x in row['peak_prices_cny']);mean_above=close>mean
        assert row['above_peak_status']==('PASS' if peak_above else 'FAIL')
        assert row['above_mean_cost_status']==('PASS' if mean_above else 'FAIL')
        assert row['price_position_status']==('PASS' if peak_above or mean_above else 'FAIL')
        expected='PASS' if prior['prefilter_status']=='PASS' and (peak_above or mean_above) else 'FAIL'
        assert row['screen_status']==expected;counts[expected]+=1
        if prior['prefilter_status']=='PASS':candidates.append(row)
        if expected=='PASS':branches['both' if peak_above and mean_above else 'peak_only' if peak_above else 'mean_only']+=1
    assert {k:counts[k] for k in ('PASS','FAIL','UNKNOWN')}==manifest['final_screen_counts']
    assert 1<=len(candidates)<=300
    directory=ROOT/'var/chip-publication-validation'/a.dataset;directory.mkdir(parents=True,exist_ok=True)
    for name,value in [('manifest.json',manifest),('screening.json',screen)]:
        (directory/name).write_text(json.dumps(value,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    def check(row):
        stock=read(f'data/chips/daily/{a.dataset}/{row["security_id"]}.json')
        assert stock['security_id']==row['security_id'] and stock['dataset_id']==a.dataset
        assert stock['screening_row']==row and stock['unchanged_threshold'] is None
        snapshot=stock['snapshots'][manifest['as_of']]
        assert snapshot['peak_prices_cny']==row['peak_prices_cny']
        assert Decimal(snapshot['close_cny'])==Decimal(row['close_cny'])
        assert Decimal(snapshot['model_mean_cost_cny'])==Decimal(row['model_mean_cost_cny'])
        assert stock['model']['version']==manifest['model_version']
        prior_path=ROOT/'var/chip-publication-validation'/a.previous/(row['security_id']+'.json')
        prior=json.loads(prior_path.read_text(encoding='utf-8'))
        assert prior['dataset_id']==a.previous and prior['as_of']==manifest['as_of']
        for field in ('snapshots','history','model'):
            assert stock[field]==prior[field],(row['security_id'],field,'sealed calculation changed')
        (directory/(row['security_id']+'.json')).write_text(json.dumps(stock,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
        return row['security_id']
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:checked=list(pool.map(check,candidates))
    out=ROOT/'outputs/chips-workbench-20261020'
    with (out/'screened-stocks.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['日期','代码','名称','收盘','主峰','平均成本','高于主峰','高于平均成本','十日收盘涨停日'])
        for r in rows:
            if r['screen_status']=='PASS':w.writerow([manifest['as_of'],r['security_id'],r['name'],r['close_cny'],' / '.join(r['peak_prices_cny']),r['model_mean_cost_cny'],r['above_peak_status'],r['above_mean_cost_status'],' / '.join(r['recent_limit_up_dates'])])
    result={'dataset_id':a.dataset,'previous_dataset_id':a.previous,'as_of':manifest['as_of'],'whole_market_rows_checked':len(rows),
        'base_price_evidence_and_peak_changes_unchanged':True,'original_candidates':len(candidates),'candidate_stock_frames_checked':len(checked),
        'candidate_sealed_snapshots_history_and_model_identical_to_previous':True,
        'final_screen_counts':manifest['final_screen_counts'],'matches_by_branch':dict(branches),'model_source_sha256':hashlib.sha256((ROOT/'backend/chips/eastmoney_model.py').read_bytes()).hexdigest(),
        'coefficient':2,'history_window_bars':210,'unchanged_threshold':None,'market_complete':manifest['market_complete']}
    (out/'price-position-validation.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))

if __name__=='__main__':main()
