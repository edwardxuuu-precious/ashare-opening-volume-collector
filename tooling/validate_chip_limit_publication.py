"""Read-only before/after and genuine source checks for historical-limit repair."""
import argparse,gzip,hashlib,json,sys
from collections import Counter
from decimal import Decimal as D
from pathlib import Path
import boto3
from botocore.config import Config
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.chips.runner import implementation_identity,encoded
from backend.chips.limit_reference import prepared,closing_pool,corroborate_quantity
from backend.chips.screen import prefilter

def candle_lines(frame):
    return [','.join([h['date'],h['open'],h['close'],h['high'],h['low'],str(D(h['volume_shares'])/100),
        h['amount_cny'],'','','',h['turnover_percent']]) for h in frame['history']]

def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True);p.add_argument('--require-complete',action='store_true');a=p.parse_args()
    s3=boto3.Session(profile_name='stock-dev',region_name='us-east-1').client('s3',config=Config(connect_timeout=7,read_timeout=25,retries={'total_max_attempts':2}))
    def read(key):
        r=s3.get_object(Bucket='stock-private-access-databucket-bmzud610gtex',Key=key);raw=r['Body'].read(20000001)
        assert len(raw)<=20000000
        decoded=gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw
        assert len(decoded)<40000000
        return json.loads(decoded)
    manifest=read('data/chips/daily/manifest.json');assert manifest['dataset_id']==a.dataset
    assert manifest['execution_identity']==implementation_identity()
    screen=read(f'data/chips/daily/{a.dataset}/screening.json');rows={r['security_id']:r for r in screen['rows']}
    baseline=json.loads((ROOT/'var/chip-limit-evidence/cloud-baseline/state.json').read_text(encoding='utf-8'))
    old=baseline['rows'];assert set(rows)==set(old) and len(rows)==5577 and manifest['as_of']==baseline['as_of']
    prior_screen=read(f'data/chips/daily/{baseline["dataset_id"]}/screening.json')
    assert {r['security_id']:r for r in prior_screen['rows']}==old,'Baseline must equal the actual immutable prior publication'
    assert manifest['coefficient']==2 and manifest['unchanged_threshold'] is None
    assert all(r['chip_status']=='READY' for r in rows.values())
    assert all(D(r['close_cny'])==D(old[c]['close_cny']) for c,r in rows.items())
    for code,row in rows.items():
        for key in ('peak_prices_cny','previous_peak_prices_cny','original_peak_changes'):
            assert row[key]==old[code][key],'Historical evidence repair changed chip metrics: '+code
    old_pass={c for c,r in old.items() if r['prefilter_status']=='PASS'}
    new_pass={c for c,r in rows.items() if r['prefilter_status']=='PASS'}
    assert old_pass<=new_pass,'A previously verified positive was lost'
    transitions=Counter(old[c]['prefilter_status']+'>'+r['prefilter_status'] for c,r in rows.items())
    unknown={c:r['limit_evidence'] for c,r in rows.items() if r['prefilter_status']=='UNKNOWN'}
    low={c for c,r in rows.items() if D(r['close_cny'])<10}
    for c in low:
        r=rows[c];assert [e['date'] for e in r['limit_evidence']]==manifest['official_sessions']
        if r['prefilter_status']=='FAIL':assert all(e['status']=='FAIL' for e in r['limit_evidence'])
        if r['prefilter_status']=='PASS':assert any(e['status']=='PASS' for e in r['limit_evidence'])
    cp={d:closing_pool(read(f'collector/chips/closing-pools/{d}.json'),d) for d in manifest['official_sessions']}
    application=json.loads((ROOT/'outputs/chips-coverage-20261018/limit-evidence-application.json').read_text())
    batch=read(f'collector/chips/references/{manifest["as_of"]}.json')
    assert hashlib.sha256(encoded(batch)).hexdigest()==application['batch_sha256']
    assert low<=set(batch['securities'])
    output=ROOT/'var/chip-publication-validation'/a.dataset;output.mkdir(parents=True,exist_ok=True)
    stocks={s['code']:s for s in baseline['stocks']};samples=[]
    for code in sorted(new_pass-old_pass):
        frame=read(f'data/chips/daily/{a.dataset}/{code}.json')
        assert frame['dataset_id']==a.dataset and frame['screening_row']==rows[code]
        assert frame['security_id']==code and frame['as_of']==manifest['as_of'] and frame['execution_identity']==manifest['execution_identity']
        original=prepared(batch['securities'][code],code,manifest['official_sessions'])
        # Reconstruct only real source bars from the published candle input.
        # The reference supplies the percentage/change, never the prior raw close.
        lines=candle_lines(frame)
        reference=original
        if frame['historical_limit_reference'].get('corroborating_source'):
            secondary=prepared(read(f'collector/chips/reference-corroboration/{manifest["as_of"]}/{code}.json'),code,manifest['official_sessions'])
            reference=corroborate_quantity(original,secondary,lines)
        assert frame['historical_limit_reference']==reference,'Published reference must equal reconstruction from hashed raw inputs'
        stock={**stocks[code],'reference_close_verified':False,
            'confirmed_no_trade_sessions':[d for d,r in reference['rows'].items() if r['tradestatus']=='0']}
        check=prefilter(stock,lines,manifest['official_sessions'],{},reference_evidence=reference,closing_pools=cp)
        assert check['prefilter_status']=='PASS'
        assert set(check['recent_limit_up_dates'])<=set(rows[code]['recent_limit_up_dates'])
        assert any('INCLUSIVE_CLOSING_LIMIT_POOL_MATCHED' in e['reason'] for e in rows[code]['limit_evidence'] if e['status']=='PASS')
        (output/(code+'.json')).write_bytes(encoded(frame))
        samples.append({'security_id':code,'name':frame['name'],'recent_limit_up_dates':rows[code]['recent_limit_up_dates'],
            'original_reference_hash':original['source_sha256'],'published_candles_rechecked':True})
    repaired_negatives=[]
    if a.require_complete:
        repair=json.loads((ROOT/'outputs/chips-coverage-20261018/limit-corroboration-application-fd54ea488652047d.json').read_text())
        assert repair['applied'] and repair['batch_sha256']==application['batch_sha256']
        for code,receipt in repair['corroboration_checks'].items():
            frame=read(f'data/chips/daily/{a.dataset}/{code}.json')
            assert frame['dataset_id']==a.dataset and frame['security_id']==code and frame['as_of']==manifest['as_of']
            assert frame['chip_status']=='READY' and frame['execution_identity']==manifest['execution_identity']
            assert frame['screening_row']==rows[code] and rows[code]['prefilter_status']=='FAIL'
            primary=prepared(batch['securities'][code],code,manifest['official_sessions'])
            secondary=prepared(read(f'collector/chips/reference-corroboration/{manifest["as_of"]}/{code}.json'),code,manifest['official_sessions'])
            assert secondary['source_sha256']==receipt['source_sha256']
            reference=corroborate_quantity(primary,secondary,candle_lines(frame))
            assert frame['historical_limit_reference']==reference
            check=prefilter({**stocks[code],'reference_close_verified':False},candle_lines(frame),manifest['official_sessions'],{},reference_evidence=reference)
            assert check['prefilter_status']=='FAIL' and all(e['status']=='FAIL' for e in check['limit_evidence'])
            (output/(code+'.json')).write_bytes(encoded(frame))
            repaired_negatives.append({'security_id':code,'source_sha256':secondary['source_sha256'],
                'all_ten_sessions_rechecked_from_published_candles':True})
    assert read('data/chips/daily/manifest.json')['dataset_id']==a.dataset
    if a.require_complete:assert not unknown and manifest['market_complete']
    result={'validation_status':'PASS','require_complete':a.require_complete,'market_complete':manifest['market_complete'],
        'dataset_id':a.dataset,'as_of':manifest['as_of'],'universe':len(rows),'all_low_price_count':len(low),
        'baseline_dataset_id':baseline['dataset_id'],'baseline_screen_sha256':hashlib.sha256(encoded(prior_screen)).hexdigest(),
        'screen_counts':manifest['screen_counts'],'before_after_transitions':dict(transitions),
        'all_previously_verified_positives_preserved':True,'newly_verified_positives':samples,
        'repaired_negative_corroborations':repaired_negatives,
        'all_5577_chip_peak_and_original_peak_change_metrics_unchanged':True,
        'remaining_unknown':unknown,'coefficient':2,'unchanged_threshold':None,'cloud_writes':0}
    (ROOT/'outputs/chips-coverage-20261018'/f'limit-publication-validation-{a.dataset}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='remaining_unknown'},ensure_ascii=False))
    print(json.dumps({'remaining_unknown_count':len(unknown),'remaining_unknown_codes':list(unknown)}))

if __name__=='__main__':main()
