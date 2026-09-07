"""Content-addressed publisher receipts. Persist only successful S3 acknowledgements."""
import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'), sort_keys=True).encode()


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def semantic_digest(value):
    # Observation timestamps inside rows remain meaningful. Only envelope generation is ignored.
    return digest_bytes(canonical({key: item for key, item in value.items() if key != 'generatedAt'}))


class PublisherState:
    def __init__(self, path, bucket):
        self.path, self.bucket = Path(path), bucket
        self.receipts = {}
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text())
                if isinstance(value, dict) and value.get('version') == 1 and value.get('bucket') == bucket:
                    receipts = value.get('receipts', {})
                    if isinstance(receipts, dict):
                        self.receipts = {key: item for key, item in receipts.items()
                                         if isinstance(key, str) and isinstance(item, dict)
                                         and isinstance(item.get('sha256'), str)}
            except (ValueError, OSError):
                pass  # A corrupt optimization receipt must force verification/uploads, never suppress them.

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_bytes(canonical({'version': 1, 'bucket': self.bucket, 'receipts': self.receipts}))
        temp.replace(self.path)

    def record(self, key, sha256, semantic=None, generated_at=None):
        value = {'sha256': sha256}
        if semantic is not None:
            value['semantic'] = semantic
        if generated_at is not None:
            value['generatedAt'] = generated_at
        self.receipts[key] = value
        self.save()

    def forget(self, key):
        if key in self.receipts:
            del self.receipts[key]
            self.save()


def checkpoint_members(out):
    out = Path(out)
    result = []
    for name in ('checkpoint', 'universe.json', 'calendar.json', 'run-status.json', 'daily-state.json', 'daily-attempts.jsonl'):
        path = out / name
        if path.is_symlink():
            raise ValueError('Checkpoint symlinks are not supported')
        if path.is_file():
            result.append(path)
        elif path.is_dir():
            for item in sorted(path.rglob('*')):
                if item.is_symlink():
                    raise ValueError('Checkpoint symlinks are not supported')
                if item.is_file():
                    result.append(item)
    return result


def checkpoint_digest(out):
    out = Path(out)
    parts = [(str(path.relative_to(out)), file_digest(path)) for path in checkpoint_members(out)]
    return digest_bytes(canonical(parts)), parts
