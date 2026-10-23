"""Create an explicit minimal public source bundle, never copying workspace data or Git history."""
from pathlib import Path
import hashlib
import json
import re
import shutil

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [
    'backend/scripts/' + name + '.py' for name in (
        'collect', 'retention', 'reconciliation', 'sina_unadjusted', 'universe_cache',
        'baostock_source', 'daily_state', 'daily_collector', 'actions_source_probe', 'download_only',
        'backfill_quotes', 'sina_daily', 'sina_spot', 'refresh', 'no_trade_evidence',
        'exchange_status')
] + ['backend/server/' + name + '.py' for name in (
    'cloud_worker', 'publisher_state', 'actions_runner', 'import_checkpoint_batch', 'refresh_worker', 'failure_status')] + [
    'backend/chips/' + name + '.py' for name in ('__init__','calendar','model','eastmoney_model','ohlc4_input','current_quote','probe','runner','screen','source','baostock_history','ths_history','listing','display','limit_reference','golden')]
SOURCES += ['backend/golden/' + name + '.py' for name in ('__init__','anchors','automatic','watchlist','daily','annotations','sources','update')]
SECRET_PATTERNS = [r'AKIA[0-9A-Z]{16}', r'ASIA[0-9A-Z]{16}',
                   r'gh[pousr]_[A-Za-z0-9]{30,}', r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']

def build(destination, probe_only=False):
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Use an empty destination; existing files are never overwritten')
    mapped = {name: ROOT / name for name in SOURCES if not probe_only or name.startswith('backend/scripts/')}
    mapped.update({'.github/workflows/' + p.name: p for p in (ROOT/'deploy/actions/workflows').glob('*.yml')
                   if not probe_only or p.name == 'source-probe.yml'})
    contents = {}
    for name, source in mapped.items():
        if source.is_symlink() or not source.is_file():
            raise ValueError('Missing or symlinked source: ' + name)
        raw = source.read_bytes()
        text = raw.decode()
        if any(re.search(pattern, text) for pattern in SECRET_PATTERNS):
            raise ValueError('Credential-like content in ' + name)
        contents[name] = raw
    contents['requirements.txt'] = (ROOT/'requirements.txt').read_bytes() + b'boto3>=1.40,<2\n'
    contents['.gitignore'] = b'.venv/\n__pycache__/\n*.pyc\n.env*\nvar/\noutputs/\n'
    contents['README.md'] = b'''# A-share opening volume collector

Minimal collection runtime for an opening-volume dashboard. New collection uses
only Sina through AKShare. The current trading day uses one bounded whole-market
snapshot after 15:30; historical gaps use Sina daily bars. Results keep their
original source and calculate opening volume divided by daily volume without
reconciliation. Only the recent six months are restored for active collection;
the bounded manual OHLC backfill may additionally restore its frozen historical
range. Older saved dates otherwise remain queryable without repeated downloads.

The source probe runs without AWS permissions and prints aggregate diagnostics.
The production workflow writes to private S3 through a repository-scoped OIDC
role. No accounts, credentials, private market snapshots, or website are included.

Scheduled collection is enabled by default after the previous writer is retired.
Before maintenance, disable the four independent AWS Schedulers and set
DISABLE_DAILY_UPDATE to true to stop GitHub cron. Explicit manual dispatch still
works; confirm no active/queued collector and wait for its writer lease to drain
before a review or alternate writer is started. Restore the exact prior settings.
Configure STOCK_BUCKET and AWS_ROLE_ARN separately.
Use only a public repository and a standard GitHub-hosted runner for free compute.
GitHub execution and scheduling limits still apply. This is not a real-time feed
or a guarantee that all source records are available by a fixed clock time.
'''
    destination.mkdir(parents=True, exist_ok=True)
    for name, raw in contents.items():
        path = destination/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(contents.items())}

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--probe-only', action='store_true')
    args = parser.parse_args()
    hashes = build(args.out, args.probe_only)
    Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(args.manifest).write_text(json.dumps({'files': hashes, 'fileCount':len(hashes), 'containsPrivateData':False}, indent=2))
    print(json.dumps({'fileCount':len(hashes),'containsPrivateData':False}))
