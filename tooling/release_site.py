"""Package a protected build or publish an explicitly reviewed site ZIP."""
import argparse
import hashlib
import io
import json
import os
import platform
import re
import stat
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from aws_stock import aws, verify_account

ROOT = Path(__file__).resolve().parents[1]
AMPLIFY_BRANCH = ['--app-id', 'd1nb0daasi2snk', '--branch-name', 'production']
TERMINAL_STATUSES = {'SUCCEED', 'FAILED', 'CANCELLED'}


def required_html(root):
    bridge = Path(__file__).with_name('site_routes.mjs')
    try:
        result = subprocess.run(
            ['node', '--experimental-strip-types', str(bridge), str(root)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        routes = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise RuntimeError('Cannot read sitePaths; Node.js >=22.13.0 is required') from None
    if (not isinstance(routes, list) or not routes
            or any(not isinstance(route, str)
                   or not re.fullmatch(r'/(?:[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)?', route)
                   for route in routes)
            or len(set(routes)) != len(routes)):
        raise RuntimeError('sitePaths must contain unique canonical route paths')
    required = tuple('index.html' if route == '/' else route[1:] + '.html' for route in routes)
    if len(set(required)) != len(required):
        raise RuntimeError('sitePaths maps routes to duplicate HTML files')
    return required


def validate_site_files(entries, required):
    names = set()
    files = set()
    private_parts = {'.aws', '.git', '.ssh', '.npmrc', '.pypirc', 'data', 'private',
                     'credentials', 'credentials.json', 'secrets', 'secrets.json'}
    for name, is_file in entries:
        relative = name.removesuffix('/') if not is_file else name
        parts = relative.split('/')
        if (not relative or '\\' in name or ':' in name
                or any(part in ('', '.', '..') for part in parts)
                or any(ord(character) < 32 or ord(character) == 127 for character in name)):
            raise RuntimeError('Unsafe artifact path: ' + repr(name))
        key = relative.casefold()
        if key in names:
            raise RuntimeError('Duplicate artifact path: ' + relative)
        names.add(key)
        if any(part.casefold() in private_parts or part.casefold().startswith('.env')
               or part.casefold().endswith(('.env', '.pem', '.key', '.p12', '.pfx'))
               for part in parts):
            raise RuntimeError('Public data or sensitive artifact path: ' + relative)
        if is_file:
            files.add(relative)
    missing = sorted(set(required) - files)
    if missing:
        raise RuntimeError('Missing protected route: ' + ', '.join(missing))
    return len(files)


def package(root, output):
    root, output = Path(root).resolve(), Path(output).absolute()
    client = root / 'dist/client'
    if output.resolve().is_relative_to(client.resolve()):
        raise RuntimeError('Artifact output must be outside dist/client')
    if output.suffix.lower() != '.zip' or output.exists() or output.is_symlink():
        raise RuntimeError('Artifact output must be a new .zip file')
    if not client.is_dir() or client.is_symlink():
        raise RuntimeError('Protected static build is missing or is a symlink')
    paths = sorted(client.rglob('*'))
    if any(path.is_symlink() or not (path.is_file() or path.is_dir()) for path in paths):
        raise RuntimeError('Symlinks and special files are forbidden in the build')
    files = [path for path in paths if path.is_file()]
    validate_site_files(((path.relative_to(client).as_posix(), path.is_file())
                         for path in paths), required_html(root))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(client).as_posix())
    raw = output.read_bytes()
    return {'artifact': str(output), 'sha256': hashlib.sha256(raw).hexdigest(),
            'bytes': len(raw), 'files': len(files)}


def reviewed_artifact(root, artifact, expected_sha256):
    artifact = Path(artifact).resolve()
    raw = artifact.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if not expected_sha256 or digest != expected_sha256.lower():
        raise RuntimeError('Reviewed artifact SHA-256 is required and must match')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if any(stat.S_IFMT(entry.external_attr >> 16)
               not in (0, stat.S_IFREG, stat.S_IFDIR) for entry in entries):
            raise RuntimeError('Symlinks and special files are forbidden in the artifact')
        count = validate_site_files(((entry.orig_filename, not entry.is_dir())
                                     for entry in entries), required_html(root))
        if archive.testzip() is not None:
            raise RuntimeError('Reviewed artifact contains a corrupt file')
    return ({'artifact': str(artifact), 'sha256': digest, 'bytes': len(raw),
             'files': count, 'reused_reviewed_artifact': True}, raw)


def write_receipt(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish(value, raw, expected_job, *, profile, region):
    artifact = Path(value['artifact'])
    receipt = artifact.with_name(artifact.stem + '-deployment.json')
    if receipt.exists() or receipt.is_symlink():
        raise RuntimeError('Deployment receipt already exists; inspect its job before another release: '
                           + str(receipt))
    verify_account(profile=profile, region=region)
    jobs = aws(['amplify', 'list-jobs', *AMPLIFY_BRANCH, '--max-results', '1'],
               profile=profile, region=region).get('jobSummaries', [])
    if not jobs or jobs[0].get('jobId') != expected_job or jobs[0].get('status') != 'SUCCEED':
        raise RuntimeError('Amplify advanced or current release is unhealthy; inspect before publishing')
    record = {**value, 'expected_job': expected_job, 'profile': profile, 'region': region,
              'host': platform.node(), 'platform': platform.system(), 'state': 'CREATE_REQUESTED'}
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        deployment = aws(['amplify', 'create-deployment', *AMPLIFY_BRANCH],
                         profile=profile, region=region)
        record['job_id'] = deployment['jobId']
        upload_url = deployment['zipUploadUrl']
    except Exception:
        record['state'] = 'CREATE_UNKNOWN'
        write_receipt(receipt, record)
        raise RuntimeError('Deployment creation is uncertain; inspect AWS and ' + str(receipt)) from None
    record['state'] = 'UPLOAD_REQUESTED'
    write_receipt(receipt, record)
    try:
        request = urllib.request.Request(upload_url, data=raw, method='PUT',
                                         headers={'Content-Type': 'application/zip'})
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 200:
                raise RuntimeError('Artifact upload failed')
    except Exception:
        record['state'] = 'UPLOAD_UNKNOWN'
        write_receipt(receipt, record)
        raise RuntimeError('Artifact upload is uncertain; inspect job and ' + str(receipt)) from None
    record['state'] = 'START_REQUESTED'
    write_receipt(receipt, record)
    try:
        aws(['amplify', 'start-deployment', *AMPLIFY_BRANCH, '--job-id', record['job_id']],
            profile=profile, region=region)
    except Exception:
        record['state'] = 'START_UNKNOWN'
        write_receipt(receipt, record)
        raise RuntimeError('Deployment start is uncertain; inspect job and ' + str(receipt)) from None
    record['state'] = 'STARTED'
    write_receipt(receipt, record)
    for attempt in range(60):
        try:
            status = aws(['amplify', 'get-job', *AMPLIFY_BRANCH, '--job-id', record['job_id']],
                         profile=profile, region=region)['job']['summary']['status']
        except Exception:
            record['state'] = 'POLL_UNKNOWN'
            write_receipt(receipt, record)
            raise RuntimeError('Cannot read deployment status; inspect job and ' + str(receipt)) from None
        record.update(state=status, job_status=status)
        write_receipt(receipt, record)
        if status in TERMINAL_STATUSES:
            break
        if attempt < 59:
            time.sleep(5)
    print(json.dumps({'job_id': record['job_id'], 'state': status,
                      'artifact_sha256': value['sha256'], 'receipt': str(receipt)}))
    if status != 'SUCCEED':
        raise SystemExit(1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--output', type=Path, help='New ZIP path, only with --dry-run')
    source.add_argument('--artifact', type=Path, help='Previously reviewed ZIP to validate or publish')
    parser.add_argument('--expected-sha256', help='SHA-256 of the reviewed ZIP')
    parser.add_argument('--expected-job', help='Current successful Amplify job ID')
    parser.add_argument('--dry-run', action='store_true', help='Validate or package without AWS calls')
    parser.add_argument('--profile', default='stock-dev')
    parser.add_argument('--region', default='us-east-1')
    args = parser.parse_args(argv)
    if args.output and not args.dry_run:
        parser.error('--output requires --dry-run; publishing requires a reviewed --artifact')
    if args.artifact and (not args.expected_sha256
                          or not re.fullmatch(r'[0-9a-fA-F]{64}', args.expected_sha256)):
        parser.error('--artifact requires --expected-sha256 with a 64-digit SHA-256')
    if args.expected_sha256 and not args.artifact:
        parser.error('--expected-sha256 requires --artifact')
    if not args.dry_run and not args.expected_job:
        parser.error('--expected-job is required to prevent overwriting another release')
    if not args.profile.strip() or not args.region.strip():
        parser.error('--profile and --region must not be empty')
    if args.artifact:
        value, raw = reviewed_artifact(ROOT, args.artifact, args.expected_sha256)
    else:
        value = package(ROOT, args.output)
    if args.dry_run:
        print(json.dumps({**value, 'published': False}))
        return
    publish(value, raw, args.expected_job, profile=args.profile, region=args.region)


if __name__ == '__main__':
    main()
