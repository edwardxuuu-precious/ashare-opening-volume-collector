"""Recheck saved aggregates without claiming that historical data was downloaded again."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo
try:
    from .collect import METHOD
except ImportError:
    from collect import METHOD


def upgrade_row(original):
    row = deepcopy(original)
    row.setdefault('sourceProvider', 'sina')
    if row.get('status') == 'ok':
        minute, daily = row.get('minuteDayVolume'), row.get('dailyVolume')
        reconciled = (isinstance(minute, (int, float)) and isinstance(daily, (int, float))
                      and daily > 0 and minute == daily
                      and row.get('quality') != 'source_difference'
                      and row.get('dayVolumeDifference', 0) == 0)
        if not reconciled:
            row.update(status='unverified', referenceRatio=row.get('referenceRatio', row.get('ratio')),
                       ratio=None, verificationState='unverified',
                       reason='保存数据的分钟合计与日线尚未核对一致，待重新获取及备用核验')
        else:
            row.setdefault('verificationState', 'matched')
            row['quality'] = 'matched'
    if row.get('status') == 'unverified':
        if row.get('ratio') is not None:
            row.setdefault('referenceRatio', row['ratio'])
        row['ratio'] = None
    # This audit is not a substitute for a fresh collector checkpoint.
    row.setdefault('verificationAudit', 'saved_aggregate_recheck_v2')
    return row


def upgrade_snapshot(source, destination):
    source, destination = Path(source), Path(destination)
    manifest = json.loads((source / 'manifest.json').read_text())
    now = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    counts = Counter()
    staged = []
    for entry in manifest['dates']:
        filename = entry['file']
        if filename != entry['date'] + '.json' or Path(filename).name != filename:
            raise ValueError('Invalid snapshot filename')
        data = json.loads((source / filename).read_text())
        if data['date'] != entry['date']:
            raise ValueError('Snapshot date mismatch')
        old = data['rows']
        data['rows'] = [upgrade_row(row) for row in old]
        counts['newlyQuarantined'] += sum(a['status'] == 'ok' and b['status'] == 'unverified'
                                         for a, b in zip(old, data['rows']))
        for row in data['rows']:
            counts[row['status']] += 1
        data['verificationAuditedAt'] = now
        data['methodology'] = METHOD
        for key, status in [('valid', 'ok'), ('missing', 'missing'), ('unverified', 'unverified'), ('suspended', 'suspended')]:
            entry[key] = sum(row['status'] == status for row in data['rows'])
        entry['status'] = 'complete' if data['scope'] == 'full' and entry['valid'] + entry['suspended'] == len(data['rows']) else 'partial'
        staged.append((filename, data))
    manifest.update(verificationAuditedAt=now, generatedAt=now, methodology=METHOD)
    destination.mkdir(parents=True, exist_ok=True)
    for filename, data in staged:
        (destination / filename).write_text(json.dumps(data, ensure_ascii=False, allow_nan=False))
    if (source / 'stocks.json').exists() and source.resolve() != destination.resolve():
        (destination / 'stocks.json').write_bytes((source / 'stocks.json').read_bytes())
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False))
    return {'dates': len(staged), 'rows': sum(len(d['rows']) for _, d in staged), **counts}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    p.add_argument('--destination', required=True)
    args = p.parse_args()
    print(json.dumps(upgrade_snapshot(args.source, args.destination), ensure_ascii=False))
