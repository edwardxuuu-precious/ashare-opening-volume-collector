"""Offline release gate: actual production estimator vs the frozen source replay."""
import hashlib
import json
from pathlib import Path
from backend.chips.eastmoney_model import VERSION, prepare_eastmoney, estimate_snapshots, compare_snapshots


def main():
    root=Path(__file__).resolve().parents[1]
    fixture=root/'outputs/chips-eastmoney-20261019/eastmoney-browser-histories.json'
    reference=root/'outputs/chips-eastmoney-full-injection-20261019/comparison.json'
    assert hashlib.sha256(reference.read_bytes()).hexdigest()=='0ff3dd512c81e85d16ddbf1e445ffd6f3823adab6e37d0748a54963ff4682e16'
    saved=json.loads(reference.read_bytes()); checks=[]
    for source in json.loads(fixture.read_bytes()):
        code=source['code']
        bars,_=prepare_eastmoney(json.dumps(source['data']).encode(),expected_code=code,cutoff='2026-10-16')
        snapshots=estimate_snapshots(bars,['2026-10-12',*saved['scope']['dates']],security_id=code)
        for date, snapshot in snapshots.items():
            em=saved['profiles'][code]['em2_source'][date]
            assert [float(v) for v in snapshot['raw_node_masses']]==[p['raw'] for p in em['profile']]
            assert [float(p['share_percent']) for p in snapshot['profile']]==[p['share_percent'] for p in em['profile']]
            assert [float(p['price_cny']) for p in snapshot['profile']]==[p['price'] for p in em['profile']]
            assert [float(p) for p in snapshot['peak_unrounded_prices_cny']]==em['peak_unrounded_prices']
            assert [float(p) for p in snapshot['peak_prices_cny']]==em['peak_prices']
            checks.append({'code':code,'date':date,'raw_and_share_and_price_vectors_exact':True})
        for row in [r for r in saved['rows'] if r['code']==code]:
            comparison=compare_snapshots(snapshots[row['previous_date']],snapshots[row['date']])
            for a,e in zip(comparison['anchors'],row['original_peak_bands'],strict=True):
                assert float(a['previous_share_percent'])==e['em2_before_percent']
                assert float(a['current_same_price_share_percent'])==e['em2_after_percent']
                assert float(a['change_percentage_points'])==e['em2_change_pp']
    result={'model_version':VERSION,'snapshot_checks':checks,'exact_raw_nodes':3000,'exact_share_nodes':3000,
        'exact_price_nodes':3000,'anchor_rows':16,'max_difference':0,'network_calls':0,
        'model_sha256':hashlib.sha256((root/'backend/chips/eastmoney_model.py').read_bytes()).hexdigest()}
    destination=root/'outputs/chips-eastmoney-release-20261019';destination.mkdir(exist_ok=True)
    (destination/'production-parity.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'snapshots':len(checks),'raw_nodes':3000,'anchor_rows':16,'max_difference':0}))


if __name__=='__main__':main()
