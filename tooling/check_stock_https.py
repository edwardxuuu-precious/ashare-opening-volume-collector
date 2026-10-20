"""Read-only comparison of production routes and the reviewed Windows build."""
import argparse
import hashlib
import json
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--artifact', type=Path,
                    default=root / 'outputs/chips-daily-20261018/stock-daily-site-windows.zip')
parser.add_argument('--output', type=Path,
                    default=root / 'outputs/windows-aws-20261018/https-check.json')
args = parser.parse_args()
artifact = args.artifact.resolve()
routes = {'/': 'index.html', '/chips': 'chips.html',
          '/chips/calibration': 'chips/calibration.html'}
with zipfile.ZipFile(artifact) as archive:
    expected = {route: hashlib.sha256(archive.read(name)).hexdigest()
                for route, name in routes.items()}
rows = []
for origin in ('https://stock.iteready.com',
               'https://production.d1nb0daasi2snk.amplifyapp.com'):
    for route in routes:
        request = urllib.request.Request(origin + route,
                                         headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(5000000)
            rows.append({'origin': origin, 'route': route,
                         'status': response.status, 'final_url': response.url,
                         'bytes': len(raw),
                         'html_sha256': hashlib.sha256(raw).hexdigest()})
result = {
    'verified_at': datetime.now(timezone.utc).isoformat(),
    'reviewed_artifact_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
    'reviewed_html_sha256': expected,
    'responses': rows,
    'custom_and_branch_identical': all(
        len({row['html_sha256'] for row in rows if row['route'] == route}) == 1
        for route in routes),
    'matches_reviewed_build': all(
        row['status'] == 200 and row['html_sha256'] == expected[row['route']]
        for row in rows),
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result))
if not result['custom_and_branch_identical'] or not result['matches_reviewed_build']:
    raise SystemExit(1)
