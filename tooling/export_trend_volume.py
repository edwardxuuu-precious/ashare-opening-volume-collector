"""Export saved, frozen research observations for the authenticated workbench."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPLAY = 'reports/I002/trend_volume_v3_replay_20261028'
PANEL = 'reports/I002/trend_volume_v2_holdout_20261027'
GUOFANG = 'reports/I002/trend_volume_case_601086_20261027/INPUT.json'
DIAGNOSIS = 'reports/I002/trend_volume_v3_remaining11_20261028/CLASSIFICATION.json'
FROZEN = {
    REPLAY + '/run-v3/RESULTS.json': '889dc514facf5d687b16f874c2967f3d4dafbfe25eb1faaa6f427ecb3938e9aa',
    REPLAY + '/PROTOCOL.json': '4a96512a8a16271f8fb90eff2762d088ee49a9a2550d2338a2a29bfc711fe21e',
    REPLAY + '/CODE_FREEZE.json': 'ed94e624e253cf0d1cbca98299f1237e87f5067e9de46a49aed569e10cadf9e6',
    REPLAY + '/RECEIPT.json': 'a2d16d35b641ba2213c94e3d4afb74fc4330eaa905e1cd761b0c91263d45e833',
    DIAGNOSIS: 'd6b8d2b0d1eb416980155e40247ac4c7b6e77caf86ad3fc78fbd4a65dcaf13ba',
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path, expected=None):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Saved source exceeds 32 MiB bound')
    raw = path.read_bytes()
    if expected and sha(raw) != expected:
        raise ValueError('Frozen source identity changed: ' + path.name)
    return json.loads(raw)


def number(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Non-finite display metric')
    return result


def gate(value, kind):
    value = value or {}
    windows = value.get('windows', {})
    result = {
        'status': value.get('status', 'UNKNOWN'),
        'metrics': {k: number(v) for k, v in value.get('metrics', {}).items()},
        'reasons': value.get('reasons', []),
    }
    if kind == 'trend':
        result['dates'] = windows.get('context', windows.get('trend', []))
    else:
        result['recent_dates'] = windows.get('recent', [])
        result['reference_dates'] = windows.get('reference', [])
    return result


def screen_detail(value):
    conditions = value.get('conditions', {})
    setup = conditions.get('setup', {})
    surge = conditions.get('surge', {})
    metrics = surge.get('metrics', {})
    trend = setup.get('trend_at_setup') or conditions.get('trend', {})
    shrink = setup.get('shrink_at_setup', {})
    candidates = []
    for candidate in setup.get('checked_candidates', []):
        t = candidate.get('trend_at_setup', {})
        s = candidate.get('shrink_at_setup', {})
        tm, sm = t.get('metrics', {}), s.get('metrics', {})
        candidates.append({
            'date': candidate['date'], 'status': candidate['status'],
            'trend_status': t.get('status', 'UNKNOWN'), 'shrink_status': s.get('status', 'UNKNOWN'),
            'pullback_pct': number(tm.get('pullback_pct')),
            'context_slope': number(tm.get('context_close_ols_slope', tm.get('close_ols_slope'))),
            'local_slope': number(tm.get('local_close_ols_slope')),
            'shrink_ratio': number(sm.get('recent_to_reference_volume')),
            'reasons': candidate.get('reasons', []),
        })
    return {
        'status': value.get('status', 'UNKNOWN'), 'rule_id': value.get('rule_id', ''),
        'setup_date': setup.get('date'), 'setup_age': setup.get('age_bars'),
        'baseline_mean': number(setup.get('baseline_mean_volume', metrics.get('setup_mean_volume'))),
        'volume_multiple': number(metrics.get('volume_to_setup_mean')),
        'close_return_pct': number(metrics.get('close_return_pct')),
        'bullish': metrics.get('close_gt_open'),
        'trend_gate': gate(trend, 'trend'), 'shrink_gate': gate(shrink, 'shrink'),
        'surge_status': surge.get('status', 'UNKNOWN'),
        'reasons': list(dict.fromkeys(reason for c in conditions.values() for reason in c.get('reasons', []))),
        'candidates': candidates,
    }


def index_row(row, diagnosis):
    is_guofang = row['code'] == '601086'
    detail = screen_detail(row.get('screen', {}))
    return {
        'code': row['code'], 'date': row['date'],
        'period': 'GUOFANG' if is_guofang else row['period'],
        'case_id': row.get('case_id'),
        'reference': 'UNLABELED' if is_guofang else row['reference'],
        'source_status': row['source_status'],
        'close_return_pct': number(row.get('close_return_pct', detail['close_return_pct'])),
        'v2': row['v2_prediction'], 'v3': row['prediction'],
        'diagnostic_group': diagnosis.get((row['code'], row['date'])),
    }


def saved_name(source, panel_root, normalized):
    if normalized.get('security_name'):
        return normalized['security_name']
    raw = load(panel_root / source['raw_ref'], source['raw_sha256'])
    symbol = ('sh' if source['code'].startswith('6') else 'sz') + source['code']
    quote = raw.get('data', {}).get(symbol, {}).get('qt', {}).get(symbol, [])
    return quote[1] if len(quote) > 1 and isinstance(quote[1], str) else source['code']


def confusion(rows, version):
    result = {k: 0 for k in ('TP', 'FP', 'FN', 'TN')}
    for row in rows:
        if row['reference'] not in ('POSITIVE', 'NEGATIVE') or row[version] == 'UNKNOWN':
            continue
        key = ('T' if (row[version] == 'PASS') == (row['reference'] == 'POSITIVE') else 'F')
        key += 'P' if row[version] == 'PASS' else 'N'
        result[key] += 1
    return result


def build(filter_root):
    sources = {name: load(filter_root / name, expected) for name, expected in FROZEN.items()}
    results = sources[REPLAY + '/run-v3/RESULTS.json']
    receipt = sources[REPLAY + '/RECEIPT.json']
    input_identities = {value['ref']: value['sha256'] for value in results['input_identities']}
    manifest = load(filter_root / PANEL / 'PANEL_MANIFEST.json', input_identities[PANEL + '/PANEL_MANIFEST.json'])
    protocol = load(filter_root / PANEL / 'PROTOCOL.json', input_identities[PANEL + '/PROTOCOL.json'])
    diagnosis = {(r['code'], r['date']): r['diagnostic_group'] for r in sources[DIAGNOSIS]['cases']}
    identities = [{'label': Path(name).name if name != DIAGNOSIS else '剩余11例复看', 'sha256': expected}
                  for name, expected in FROZEN.items()]
    for label, name in [('面板行情清单', PANEL + '/PANEL_MANIFEST.json'),
                        ('面板统计口径', PANEL + '/PROTOCOL.json'), ('国芳集团保存行情', GUOFANG)]:
        identities.append({'label': label, 'sha256': input_identities[name]})
    identities.append({'label': '导出器', 'sha256': sha(Path(__file__).read_bytes())})
    dataset = sha(encoded(identities))[:16]
    rows = [index_row(row, diagnosis) for row in results['rows']]
    gf_rows = [index_row(row, diagnosis) for row in results['guofang']['rows']]
    if len(rows) != 1280 or confusion(rows, 'v2') != {'TP': 1, 'FP': 0, 'FN': 19, 'TN': 938}:
        raise ValueError('V2 frozen aggregate mismatch')
    if confusion(rows, 'v3') != {'TP': 9, 'FP': 0, 'FN': 11, 'TN': 938}:
        raise ValueError('V3 frozen aggregate mismatch')
    stocks, details = [], {}
    all_saved = results['rows'] + results['guofang']['rows']
    for source in manifest['sources'] + [{'code': '601086', 'status': 'ACQUIRED', 'provider': 'EASTMONEY'}]:
        code = source['code']
        if code == '601086':
            normalized = load(filter_root / GUOFANG, input_identities[GUOFANG])
            name = normalized['security_name']
        elif source['status'] == 'ACQUIRED':
            normalized = load(filter_root / PANEL / source['normalized_ref'], source['normalized_sha256'])
            name = saved_name(source, filter_root / PANEL, normalized)
        else:
            normalized, name = {}, code
        known = bool(normalized)
        meta = {'code': code, 'name': name, 'source_status': 'KNOWN' if known else 'UNKNOWN',
                'provider': normalized.get('provider', source.get('provider', 'UNKNOWN')),
                'volume_unit': normalized.get('volume_unit', 'UNKNOWN')}
        stocks.append(meta)
        observations = []
        for original in all_saved:
            if original['code'] == code:
                observations.append({**index_row(original, diagnosis), 'results': {
                    'V2': screen_detail(original.get('v2_screen', {})),
                    'V3': screen_detail(original.get('screen', {})),
                }})
        details[code] = {'schema': 'stock.trend-volume.stock.v1', 'dataset_id': dataset, **meta,
                         'adjustment': 'unadjusted', 'historical_known_at_verified': False,
                         'history': [{k: b[k] if k == 'date' else number(b[k])
                                      for k in ('date', 'open', 'high', 'low', 'close', 'volume')}
                                     for b in normalized.get('bars', [])], 'observations': observations}
    summary = {
        'schema': 'stock.trend-volume.summary.v1', 'dataset_id': dataset,
        'created_at': receipt['completed_at_utc'],
        'periods': [{k: p[k] for k in ('id', 'start', 'end')} | {'label': p['start'][:7]}
                    for p in protocol['cohort']['periods']],
        'stocks': stocks, 'rows': rows + gf_rows,
        'rules': {
            'V2': ['趋势：前一日20日收盘回归斜率<0，且较20日最高价回撤≥5%；建立缩量观察日也须满足趋势。',
                   '缩量：连续3日均量≤此前不重叠10日均量的50%；在前5个交易日寻找观察日。',
                   '启动：量≥所选观察日3日均量的2倍、收盘涨幅≥4%、收盘价>开盘价。'],
            'V3': ['趋势：观察日较20日最高价回撤≥5%，20日或5日收盘回归斜率<0。',
                   '缩量：连续3日均量≤此前不重叠10日均量的70%；前5日选最近合格观察日，固定该日均量基准。',
                   '启动：量≥固定基准2倍、收盘涨幅≥4%、收盘价>开盘价；不另设信号前一日趋势门槛。'],
            'persistence': ['用户定性要求：已形成的下跌趋势，在未出现大量且大涨时持续；缩量本身不改变趋势。',
                            '待验证候选：依次确认H1、L1、H2、L2，H2<H1且L2<L1；幅度(H1−L2)/H1≥5%。',
                            '转折点左右各2根K线仅为工程候选；必须等右侧2根收盘后才可确认，禁止倒填。',
                            '大量且大涨只触发启动或反转评估，不直接确认上涨趋势；持续状态与缩量基准有效期分别定义。'],
        },
        'limitations': [
            '这是已参与修订的开发样本复算。形态参考来自同模型代理复看，尚非用户确认标签或新的独立样本验证。',
            '32只股票中24只具有保存行情，8只缺失；320个股票日保持UNKNOWN。每日名单仅为已知下限。',
            '938个反例共享涨幅4%的门槛；大涨候选中缺少已确认负例，当前误选0不能证明误选控制有效。',
            '2个形态争议样本不进入漏选与误选分母。国芳集团14日单独核对，不混入面板错误率。',
            '使用后下载的不复权历史日线，特征计算只用截至观察日的保存前缀，不能证明历史当时已入库。',
            '最新下跌持续定义尚未计算；无交易执行、收益率或收益回测，未加入黄金分割。',
        ],
        'sources': identities,
        'diagnosis': {'numeric_review': 8, 'unresolved': 3, 'mismatch_supported': 0},
        'candidate_status': 'NOT_EVALUATED',
    }
    gf_signal = [r['date'] for r in gf_rows if r['v3'] == 'PASS']
    if gf_signal != ['2026-10-27'] or [r['date'] for r in gf_rows if r['v2'] == 'PASS'] != gf_signal:
        raise ValueError('Guofang control mismatch')
    return summary, details


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--filter-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/trend-volume-20261028/private')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'outputs').resolve()):
        raise ValueError('Real research data must stay in ignored outputs, never public or source files')
    summary, details = build(args.filter_root.resolve())
    slot = output / summary['dataset_id']
    payloads = {'summary.json': summary} | {code + '.json': value for code, value in details.items()}
    slot.mkdir(parents=True, exist_ok=True)
    for name, value in payloads.items():
        path, raw = slot / name, encoded(value)
        if path.exists() and path.read_bytes() != raw:
            raise ValueError('Immutable export already exists with different contents')
        path.write_bytes(raw)
    manifest = {'schema': 'stock.trend-volume.manifest.v1', 'dataset_id': summary['dataset_id'],
                'published_at': datetime.now(timezone.utc).isoformat(),
                'summary_sha256': sha(encoded(summary))}
    (output / 'manifest.json').write_bytes(encoded(manifest))
    print(json.dumps({'dataset_id': summary['dataset_id'], 'files': len(payloads) + 1,
                      'panel_rows': 1280, 'guofang_rows': 14, 'v2': confusion(summary['rows'][:1280], 'v2'),
                      'v3': confusion(summary['rows'][:1280], 'v3'), 'new_source_requests': 0,
                      'screener_calls': 0, 'published': False, 'output': str(output)}))


if __name__ == '__main__':
    main()
