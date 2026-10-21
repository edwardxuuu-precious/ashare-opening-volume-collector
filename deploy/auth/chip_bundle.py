"""Bundle the exact shared model into inline CloudFormation Lambda code."""
import base64
import hashlib
from io import BytesIO
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[2]
MODULES=('backend/__init__.py','backend/chips/__init__.py','backend/chips/model.py',
         'backend/chips/ohlc4_input.py','backend/chips/eastmoney_model.py',
         'backend/chips/calendar.py','backend/chips/history.py',
         'backend/golden/__init__.py','backend/golden/watchlist.py','backend/golden/annotations.py')


def inline_handler():
    buffer=BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in MODULES:
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            path=ROOT/name
            source=path.read_text(encoding='utf-8').replace('\r\n','\n') if path.exists() else '' if name.endswith('/__init__.py') else None
            if source is None:raise FileNotFoundError(path)
            archive.writestr(info,source)
    raw=buffer.getvalue();digest=hashlib.sha256(raw).hexdigest()
    bootstrap=("# Build-time embedded shared model; no network code loading.\n"
        "import base64 as _bundle64, pathlib as _bundle_path, sys as _bundle_sys\n"
        f"_chip_library='/tmp/stock-chip-library-{digest}.zip'\n"
        f"_bundle_path.Path(_chip_library).write_bytes(_bundle64.b64decode({base64.b64encode(raw).decode()!r}))\n"
        "_bundle_sys.path.insert(0,_chip_library)\n")
    return bootstrap+(ROOT/'deploy/auth/handler.py').read_text(encoding='utf-8')
