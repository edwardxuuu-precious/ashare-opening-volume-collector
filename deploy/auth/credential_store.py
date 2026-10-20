"""Owner-only local credential registry. Never print credential contents."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile


def store_path():
    return Path.home() / '.config/stock-iteready/accounts.json'


def _write(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.accounts-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def registry(path=None):
    path = path or store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    lock = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock, 'w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        data = json.loads(path.read_text()) if path.exists() else {
            'url': 'https://stock.iteready.com', 'accounts': {}}
        legacy = list((path.parent / 'accounts').glob('*.json'))
        for source in legacy:
            entry = json.loads(source.read_text())
            name = entry['username'].lower()
            existing = data['accounts'].get(name)
            if existing is not None and existing != entry:
                raise ValueError(f'Conflicting credentials for {name}; originals preserved')
            data['accounts'][name] = entry
        # Keep historical hosting credentials together, clearly separate from users.
        old_access = path.parent / 'amplify-access.json'
        if old_access.exists():
            entry = json.loads(old_access.read_text())
            archive = data.setdefault('historical_hosting_access', {})
            if 'amplify' in archive and archive['amplify'] != entry:
                raise ValueError('Conflicting historical hosting credentials; originals preserved')
            archive['amplify'] = entry
            legacy.append(old_access)
        # Commit migration before a caller changes an account or invokes AWS.
        _write(path, data)
        if json.loads(path.read_text()) != data:
            raise RuntimeError('Credential migration readback failed; originals preserved')
        for source in legacy:
            source.unlink()
        yield data
        _write(path, data)


def load_credentials(username):
    with registry() as data:
        return dict(data['accounts'][username.lower()])


def set_password(username, password, apply):
    name = username.lower()
    with registry() as data:
        data.setdefault('pending_password_changes', {})[name] = password
        _write(store_path(), data)
        apply()
        data['accounts'][name] = {
            'url': data['url'], 'username': username, 'password': password}
        del data['pending_password_changes'][name]
