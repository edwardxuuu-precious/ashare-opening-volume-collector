#!/usr/bin/env python3
"""Import verified supplemental checkpoints only; never publish or control services."""
from __future__ import annotations
import argparse, fcntl, hashlib, json, math, os, re, tarfile, tempfile
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
try:
    from .cloud_worker import validate_day
except ImportError:
    from cloud_worker import validate_day

NAME = re.compile(r'checkpoint/([0-9]{6})\.json')
ISO_DAY = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
MAX_MEMBER = 8 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_RECORDS = 10000
REQUIRED_VERSION = 2


def strict_json(raw):
    def invalid(_): raise ValueError('Non-finite JSON value')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, parse_constant=invalid, object_pairs_hook=unique)


def valid_date(value):
    if not isinstance(value, str) or not ISO_DAY.fullmatch(value): raise ValueError('Invalid date')
    date.fromisoformat(value)
    return value


def finite_nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0 and int(value) == value


def record_disposition(record, code, universe, requested):
    if not isinstance(record, dict) or record.get('code') != code:
        raise ValueError('Checkpoint code differs from filename')
    if code not in universe: return 'unknown'
    if record.get('fetchStatus') in ('error', 'pending'): return 'error_or_pending'
    if record.get('fetchStatus') != 'ok': raise ValueError('Invalid fetchStatus')
    valid_date(record.get('fetchedOn'))
    days = record.get('days')
    if not isinstance(days, dict): raise ValueError('Checkpoint days missing')
    if not set(requested) <= set(days): return 'incomplete_dates'
    has_observation = False
    for day, row in days.items():
        valid_date(day)
        if not isinstance(row, dict) or row.get('code') != code:
            raise ValueError('Stock-day identity mismatch')
        if row.get('verificationVersion') != REQUIRED_VERSION:
            raise ValueError('Unsupported verification version')
        if row.get('status') not in ('ok', 'unverified', 'missing', 'suspended'):
            raise ValueError('Invalid stock-day status')
        validate_day({'date': day, 'rows': [row]}, day)
        first, daily = row.get('first15Volume'), row.get('dailyVolume')
        if first is not None and not finite_nonnegative(first): raise ValueError('Invalid first15 volume')
        if daily is not None and not finite_nonnegative(daily): raise ValueError('Invalid daily volume')
        if row['status'] == 'ok':
            if type(row.get('ratio')) not in (int, float) or not finite_nonnegative(first) or not finite_nonnegative(daily) or daily <= 0:
                raise ValueError('Verified row has no valid volumes/ratio')
            if row.get('calculationPolicy') != 'download_only' and row.get('minuteDayVolume') != daily:
                raise ValueError('Verified row minute total does not match daily')
        elif row.get('ratio') is not None:
            raise ValueError('Unverified row exposes ratio')
        reference = row.get('referenceRatio')
        if reference is not None:
            if not finite_nonnegative(first) or not finite_nonnegative(daily) or daily <= 0:
                raise ValueError('Reference ratio lacks volumes')
            if type(reference) not in (int, float) or not math.isfinite(reference) or not 0 <= reference <= 100 or abs(reference - first / daily * 100) > 1e-5:
                raise ValueError('Invalid reference ratio')
        minute_total = row.get('minuteDayVolume')
        if day in requested and row['status'] in ('ok', 'unverified') and finite_nonnegative(first) and finite_nonnegative(daily) and daily > 0 and first <= daily and (row.get('calculationPolicy') == 'download_only' or (finite_nonnegative(minute_total) and first <= minute_total)):
            has_observation = True
    return 'eligible' if has_observation else 'missing_only'


def atomic_json(path, value, *, no_replace=False, owner=None):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.import-', suffix='.tmp', delete=False) as handle:
            tmp = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            if owner is not None and (os.geteuid(), os.getegid()) != owner:
                os.fchown(handle.fileno(), *owner)
            handle.flush(); os.fsync(handle.fileno())
        if no_replace:
            # Hard-link insertion is atomic and fails when *any* destination already exists.
            os.link(tmp, path)
        else:
            os.replace(tmp, path); tmp = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if tmp is not None: tmp.unlink(missing_ok=True)


def import_batch(archive, root, expected_sha, *, maintenance_path=Path('/etc/stock-collector.maintenance')):
    archive, root, maintenance_path = Path(archive), Path(root), Path(maintenance_path)
    if not re.fullmatch('[0-9a-fA-F]{64}', expected_sha): raise ValueError('Expected SHA256 is required')
    expected_sha = expected_sha.lower()
    if not maintenance_path.is_file() or maintenance_path.is_symlink():
        raise RuntimeError('Root maintenance marker is required')
    if not root.is_dir() or root.is_symlink() or not (root / 'data').is_dir() or (root / 'data').is_symlink():
        raise ValueError('Explicit existing collector root/data is required')
    checkpoint = root / 'data/checkpoint'
    if checkpoint.is_symlink(): raise ValueError('Checkpoint directory cannot be a symlink')
    data_stat = (root / 'data').stat()
    owner = (data_stat.st_uid, data_stat.st_gid)
    with ExitStack() as stack:
        for lock_path in (root / '.worker.lock', root / 'data/.collector.lock'):
            if lock_path.is_symlink(): raise ValueError('Lock cannot be a symlink')
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            lock = stack.enter_context(os.fdopen(fd, 'a'))
            if os.geteuid() == 0: os.fchown(lock.fileno(), *owner)
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise RuntimeError('Collector or worker is active; stop it before importing')
        state = strict_json((root / 'worker-state.json').read_bytes())
        requested = state.get('dates')
        if not isinstance(requested, list) or not requested or len(requested) > 186 or len(requested) != len(set(requested)):
            raise ValueError('Frozen requested dates must exist in worker state')
        requested = sorted(valid_date(d) for d in requested)
        catalog = strict_json((root / 'data/universe.json').read_bytes())
        rows = catalog.get('rows', [])
        universe = {r['code'] for r in rows if isinstance(r, dict) and isinstance(r.get('code'), str)}
        if not universe or len(universe) != len(rows) or catalog.get('total') != len(rows) or any(not re.fullmatch('[0-9]{6}', c) for c in universe):
            raise ValueError('Invalid local universe catalog')
        # Hold the same source descriptor for hashing and tar validation (no path-swap race).
        source = stack.enter_context(archive.open('rb'))
        digest = hashlib.file_digest(source, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else None
        if digest is None:
            h = hashlib.sha256()
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk: break
                h.update(chunk)
            digest = h.hexdigest()
        if digest != expected_sha: raise ValueError('Archive SHA256 mismatch')
        source.seek(0)
        seen = set(); eligible = []; skipped = {}; total_bytes = 0
        with tarfile.open(fileobj=source, mode='r:*') as bundle:
            for member in bundle:
                match = NAME.fullmatch(member.name)
                if not match or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.issparse():
                    raise ValueError('Archive permits only regular checkpoint/NNNNNN.json files')
                if member.name in seen: raise ValueError('Duplicate archive member')
                seen.add(member.name); total_bytes += member.size
                if len(seen) > MAX_RECORDS or member.size <= 0 or member.size > MAX_MEMBER or total_bytes > MAX_TOTAL:
                    raise ValueError('Archive exceeds bounded import size')
                stream = bundle.extractfile(member)
                if stream is None: raise ValueError('Missing archive member body')
                raw = stream.read(MAX_MEMBER + 1)
                if len(raw) != member.size: raise ValueError('Truncated archive member')
                code = match.group(1); record = strict_json(raw)
                disposition = record_disposition(record, code, universe, requested)
                if os.path.lexists(checkpoint / (code + '.json')): disposition = 'existing'
                if disposition != 'eligible':
                    skipped.setdefault(disposition, []).append(code)
                    continue
                # Keep only member identities/hashes, not thousands of expanded JSON records.
                eligible.append((code, hashlib.sha256(raw).hexdigest()))
        if not seen: raise ValueError('Empty archive')
        # Nothing was imported until every archive member passed validation.
        imported_at = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
        report = dict(status='validated', archiveSha256=digest, importedAt=imported_at, dates=requested,
                      totalMembers=len(seen), plannedCodes=[code for code, _ in eligible], importedCodes=[],
                      skippedCodes=skipped, skippedCounts={key:len(value) for key,value in skipped.items()})
        reports = root / 'imports'
        if reports.is_symlink(): raise ValueError('Import report directory cannot be a symlink')
        report_path = reports / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + digest[:12] + '.json')
        atomic_json(report_path, report)
        checkpoint.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0:
            os.chown(checkpoint, *owner)
        try:
            pending = dict(eligible)
            source.seek(0)
            with tarfile.open(fileobj=source, mode='r:*') as bundle:
                for member in bundle:
                    match = NAME.fullmatch(member.name)
                    if not match or match.group(1) not in pending:
                        continue
                    code = match.group(1)
                    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.issparse() or member.size <= 0 or member.size > MAX_MEMBER:
                        raise ValueError('Archive changed after validation')
                    raw = bundle.extractfile(member).read(MAX_MEMBER + 1)
                    if hashlib.sha256(raw).hexdigest() != pending.pop(code):
                        raise ValueError('Archive member changed after validation')
                    record = strict_json(raw)
                    payload = dict(record, importedAt=imported_at, importedArchiveSha256=digest, importSource='supplement_batch')
                    try: atomic_json(checkpoint / (code + '.json'), payload, no_replace=True, owner=owner)
                    except FileExistsError:
                        report['skippedCodes'].setdefault('existing', []).append(code)
                        continue
                    report['importedCodes'].append(code)
                    if len(report['importedCodes']) % 50 == 0: atomic_json(report_path, report)
            if pending: raise ValueError('Archive lost validated members')
            report['status'] = 'completed'
        except Exception as exc:
            report.update(status='partial', error=type(exc).__name__)
            raise
        finally:
            report['importedCount'] = len(report['importedCodes'])
            report['skippedCounts'] = {key:len(value) for key,value in report['skippedCodes'].items()}
            atomic_json(report_path, report)
        return dict(report, reportPath=str(report_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True); parser.add_argument('--root', required=True)
    parser.add_argument('--expected-sha256', required=True)
    args = parser.parse_args()
    report = import_batch(args.archive, args.root, args.expected_sha256)
    print(json.dumps({key: report[key] for key in ('status','archiveSha256','totalMembers','importedCount','skippedCounts','reportPath')}, ensure_ascii=False))

if __name__ == '__main__': main()
