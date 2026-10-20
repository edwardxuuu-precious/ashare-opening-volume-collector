"""Fail closed if a production build could expose data outside the auth API."""
import os
from pathlib import Path

expected = 'https://api.stock.iteready.com'
for key in ('NEXT_PUBLIC_AUTH_API', 'NEXT_PUBLIC_DATA_BASE'):
    if os.environ.get(key, '').rstrip('/') != expected:
        raise SystemExit(f'{key} must be {expected} for a protected production build')
if os.environ.get('NEXT_PUBLIC_COLLECTOR_API', '').rstrip('/') not in ('', expected):
    raise SystemExit('Collector must use the authenticated API before production deployment')
if not (Path('src/components') / 'auth-gate.tsx').exists():
    raise SystemExit('Authentication gate is missing')
print('Protected production endpoints verified')
