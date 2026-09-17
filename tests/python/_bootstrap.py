"""Make repository and backend imports available during direct unittest discovery."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT / 'backend', ROOT / 'backend/scripts', ROOT / 'backend/server'):
    sys.path.insert(0, str(directory))
