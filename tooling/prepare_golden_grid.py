"""Bounded read-only export of the current published universe's grid statistics.

No public quotes, strategy engine, S3 writes or market-performance search.
Immutable frames are cached locally by dataset. Max 4 readers, 10 minutes,
one request per missing frame, bounded object sizes; no automatic retry.
"""
import argparse, concurrent.futures, gzip, hashlib, io, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.chips.golden import document, grid_row


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dataset', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if len(args.dataset) != 16 or any(c not in '0123456789abcdef' for c in args.dataset):
        p.error('Expected exact published dataset identity')
    import boto3
    from botocore.config import Config
    client = boto3.Session(profile_name='stock-dev', region_name='us-east-1').client('s3',
        config=Config(connect_timeout=7, read_timeout=20, retries={'total_max_attempts': 1}, max_pool_connections=4))
    bucket = 'stock-private-access-databucket-bmzud610gtex'
    started = time.monotonic()
    cache = ROOT / 'var' / 'golden-grid-inputs' / args.dataset
    cache.mkdir(parents=True, exist_ok=True)
    def read(key, limit=6000000):
        if time.monotonic() - started > 600:
            raise RuntimeError('READ_BUDGET_EXHAUSTED')
        response = client.get_object(Bucket=bucket, Key=key)
        try:
            raw = response['Body'].read(limit + 1)
        finally:
            response['Body'].close()
        if len(raw) > limit:
            raise RuntimeError('OBJECT_SIZE_BOUND_EXCEEDED')
        if raw[:2] == b'\x1f\x8b':
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(12000001)
            if len(raw) > 12000000:
                raise RuntimeError('EXPANDED_OBJECT_SIZE_BOUND_EXCEEDED')
        return json.loads(raw)
    manifest = read('data/chips/daily/manifest.json')
    if manifest['dataset_id'] != args.dataset:
        raise RuntimeError('PUBLISHED_DATASET_CHANGED')
    screen = read(f'data/chips/daily/{args.dataset}/screening.json')
    rows = screen['rows']
    if not 1 <= len(rows) <= 15000 or len(rows) != manifest['universe_total']:
        raise RuntimeError('UNIVERSE_BOUND_MISMATCH')
    if screen['dataset_id'] != args.dataset or screen['as_of'] != manifest['as_of']:
        raise RuntimeError('MIXED_SOURCE_BATCH')
    (cache / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    (cache / 'screening.json').write_text(json.dumps(screen, ensure_ascii=False), encoding='utf-8')
    def compute(row):
        code = row['security_id']
        if len(code) != 6 or not code.isdigit():
            raise ValueError('INVALID_SECURITY')
        path = cache / f'{code}.json'
        if path.exists():
            frame = json.loads(path.read_text(encoding='utf-8'))
        else:
            frame = read(f'data/chips/daily/{args.dataset}/{code}.json')
            # Cache only candles and identity; do not retain large chip profiles.
            frame = {key: frame[key] for key in ('schema', 'dataset_id', 'as_of', 'security_id', 'name', 'history')}
            path.write_text(json.dumps(frame, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        if frame['dataset_id'] != args.dataset or frame['security_id'] != code or frame['as_of'] != manifest['as_of']:
            raise RuntimeError('MIXED_STOCK_FRAME')
        result = grid_row(frame)
        result['source_history_sha256'] = hashlib.sha256(json.dumps(frame['history'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return result
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending = {}
        source = iter(rows)
        def fill():
            for row in source:
                pending[pool.submit(compute, row)] = row['security_id']
                if len(pending) >= 4:
                    break
        fill()
        while pending:
            completed, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in completed:
                del pending[future]
                results.append(future.result())
            if len(results) % 250 < 4:
                print(json.dumps({'processed': len(results), 'total': len(rows)}), flush=True)
            fill()
    value = document(manifest, results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    args.output.write_bytes(raw)
    print(json.dumps({'file': str(args.output), 'dataset_id': args.dataset, 'as_of': value['as_of'],
        'total': len(results), 'counts': value['counts'], 'sha256': hashlib.sha256(raw).hexdigest(),
        'elapsed_seconds': round(time.monotonic() - started, 2), 'published': False}))


if __name__ == '__main__':
    main()
