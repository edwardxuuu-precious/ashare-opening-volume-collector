"""Remove market files from the built static site, preserving local source data."""
from pathlib import Path
import shutil

root = Path('dist/client')
if not (root / 'index.html').is_file():
    raise SystemExit('Static build missing')
public_data = root / 'data'
if public_data.exists():
    shutil.rmtree(public_data)
if any(p.is_file() for p in root.glob('**/data/*.json')):
    raise SystemExit('Public data unexpectedly remains in build')
print('Static artifact contains no public market-data directory')
