"""Read-only, budgeted guard for trusted collector-completion chip catch-up."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta, timezone
import gzip, io, json, math, os, re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.chips.calendar import sessions
from backend.chips.stability import RULE_ID, THRESHOLDS, POLICY_METADATA

REPOSITORY = 'edwardxuuu-precious/ashare-opening-volume-collector'
WORKFLOW = 'Collect opening volume into private storage'
CN = timezone(timedelta(hours=8))
MANIFEST = 'data/chips/daily/manifest.json'
LEASE = 'collector/chips/lease.json'
COLLECTOR_STATUS = 'data/collection-status.json'
REPAIR_PREFIX = 'collector/chips/followup-repair/'
MAX_METADATA_BYTES = 1000000

def timestamp(value, label):
    if not isinstance(value, str): raise ValueError('INVALID_'+label)
    try: result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError: raise ValueError('INVALID_'+label) from None
    if result.tzinfo is None or result.utcoffset() is None: raise ValueError('INVALID_'+label)
    return result

def model_missing_count(manifest):
    """Only unavailable model inputs qualify; terminal comparison UNKNOWN does not."""
    total = manifest['universe_total']; ready = manifest.get('model_ready_count')
    if 'model_ready_count' in manifest and (type(ready) is not int or not 0 <= ready <= total):
        raise ValueError('INVALID_PUBLISHED_MODEL_READY_COUNT')
    if 'input_repair' in manifest:
        repair = manifest['input_repair']
        if not isinstance(repair, dict) or repair.get('schema') != 'stock.chips.input-repair.v1':
            raise ValueError('INVALID_INPUT_REPAIR_SCHEMA')
        missing = repair.get('model_missing_count')
        if type(missing) is not int or not 0 <= missing <= total or ready is not None and missing != total-ready:
            raise ValueError('INVALID_INPUT_REPAIR_COUNT')
        return missing
    # A legacy manifest without an explicit readiness count cannot establish a
    # repairable input gap. It is never inferred from UNKNOWN or coverage flags.
    return total-ready if ready is not None else 0

def repair_key(day):
    return REPAIR_PREFIX+day+'.json'

def valid_repair_marker(marker, day, now):
    if (not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)
        or not isinstance(marker, dict) or marker.get('schema') != 'stock.chips.followup-repair.v1'
        or marker.get('as_of') != day or marker.get('attempts') != 1 or type(marker.get('attempts')) is not int
        or not isinstance(marker.get('source_dataset_id'), str) or not re.fullmatch('[0-9a-f]{16}', marker['source_dataset_id'])
        or type(marker.get('model_missing_count')) is not int or not 1 <= marker['model_missing_count'] <= 15000):
        raise ValueError('INVALID_REPAIR_MARKER')
    generated = timestamp(marker.get('source_generated_at'), 'REPAIR_SOURCE_TIME')
    progress = timestamp(marker.get('collector_progress_at'), 'REPAIR_PROGRESS_TIME')
    claimed = timestamp(marker.get('claimed_at'), 'REPAIR_CLAIM_TIME')
    if not generated < progress <= claimed <= now or any(t.astimezone(CN).date().isoformat() != day for t in (generated, progress, claimed)):
        raise ValueError('INVALID_REPAIR_MARKER_TIME')

def collector_progress(status, day, generated, now):
    if status is None: return None, 'COLLECTOR_PROGRESS_REQUIRED'
    if not isinstance(status, dict): raise ValueError('INVALID_COLLECTOR_STATUS')
    target = status.get('targetDate')
    if not isinstance(target, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', target):
        raise ValueError('INVALID_COLLECTOR_TARGET_DATE')
    try: date.fromisoformat(target)
    except ValueError: raise ValueError('INVALID_COLLECTOR_TARGET_DATE') from None
    if target != day: return None, 'COLLECTOR_TARGET_NOT_TODAY'
    for name in ('dataComplete', 'priceDataComplete'):
        if name in status and type(status[name]) is not bool: raise ValueError('INVALID_COLLECTOR_COMPLETION')
    if status.get('dataComplete') is not True and status.get('priceDataComplete') is not True:
        return None, 'COLLECTOR_NOT_COMPLETE'
    times = []
    for name in ('publishedAt', 'fullyPublishedAt', 'firstPassCompletedAt'):
        if status.get(name) is None: continue
        stamp = timestamp(status[name], 'COLLECTOR_PROGRESS_TIME')
        if stamp > now or stamp.astimezone(CN).date().isoformat() != day:
            raise ValueError('INVALID_COLLECTOR_PROGRESS_TIME')
        times.append(stamp)
    progress = max(times, default=None)
    if progress is None: return None, 'COLLECTOR_PROGRESS_REQUIRED'
    if progress <= generated: return None, 'COLLECTOR_NO_NEW_PROGRESS'
    return progress.isoformat(), 'TODAY_MODEL_INPUT_REPAIR_DUE'

def trusted_event(event):
    run = event.get('workflow_run', {})
    if (event.get('action') != 'completed' or run.get('status') != 'completed'
        or run.get('name') != WORKFLOW or run.get('head_branch') != 'main'
        or (run.get('head_repository') or {}).get('full_name') != REPOSITORY):
        raise ValueError('UNTRUSTED_COLLECTOR_COMPLETION')

def time_gate(now):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('AWARE_CLOCK_REQUIRED')
    local = now.astimezone(CN); day = local.date().isoformat()
    if not sessions(day, day):
        return {'action': 'SKIP', 'reason': 'NON_TRADING_DAY', 'as_of': day}
    if (local.hour, local.minute) < (15, 30):
        return {'action': 'SKIP', 'reason': 'BEFORE_CLOSE_HANDOFF', 'as_of': day}
    ready = local.replace(hour=15, minute=40, second=0, microsecond=0)
    wait = max(0, math.ceil((ready-local).total_seconds()))
    if wait > 600: raise ValueError('WAIT_BOUND_EXCEEDED')
    return {'action': 'WAIT' if wait else 'READY', 'reason': 'WAIT_FOR_DAILY_WINDOW' if wait else 'DAILY_WINDOW',
            'as_of': day, 'wait_seconds': wait}

def manifest_day(manifest):
    if manifest is None: return None
    if not isinstance(manifest, dict) or manifest.get('schema') != 'stock.chips.market.manifest.v1':
        raise ValueError('INVALID_PUBLISHED_MANIFEST')
    day = manifest.get('as_of')
    if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) or date.fromisoformat(day).isoformat() != day:
        raise ValueError('INVALID_PUBLISHED_DATE')
    total, scanned = manifest.get('universe_total'), manifest.get('scanned_count')
    if type(total) is not int or not 1 <= total <= 15000 or type(scanned) is not int or scanned != total:
        raise ValueError('INVALID_PUBLISHED_COVERAGE')
    dataset = manifest.get('dataset_id')
    if not isinstance(dataset, str) or not re.fullmatch('[0-9a-f]{16}', dataset) or manifest.get('active_slot') != dataset:
        raise ValueError('INVALID_PUBLISHED_GENERATION')
    return day

def adopted(manifest):
    return (manifest.get('chip_unchanged_rule') == RULE_ID and manifest.get('chip_stability_thresholds') == THRESHOLDS
        and manifest.get('chip_stability_policy') == POLICY_METADATA
        and manifest.get('price_filter_rule') == 'NO_ABSOLUTE_PRICE_FILTER_V1'
        and manifest.get('price_position_rule') == 'CLOSE_GT_MEAN_COST_V2')

def decide(now, manifest, lease, collector_status=None, repair_marker=None):
    gate = time_gate(now)
    if gate['action'] != 'READY': return gate
    published = manifest_day(manifest)
    if published and published > gate['as_of']: raise ValueError('FUTURE_PUBLICATION')
    if lease is not None:
        expires = lease.get('expires') if isinstance(lease, dict) else None
        if type(expires) not in (int, float) or not math.isfinite(expires) or expires < 0:
            raise ValueError('INVALID_CHIP_LEASE')
        if expires > now.timestamp(): return {**gate, 'action': 'SKIP', 'reason': 'CHIP_WRITER_ACTIVE'}
    if published == gate['as_of'] and adopted(manifest):
        missing = model_missing_count(manifest)
        if not missing:
            # Precision/basis UNKNOWN and unverified catalog coverage alone do
            # not request another data acquisition or calculation.
            return {**gate, 'action': 'SKIP', 'reason': 'TODAY_ALREADY_PUBLISHED'}
        if repair_marker is not None:
            valid_repair_marker(repair_marker, gate['as_of'], now)
            return {**gate, 'action': 'SKIP', 'reason': 'DAILY_MODEL_INPUT_REPAIR_ALREADY_ATTEMPTED'}
        generated = timestamp(manifest.get('generated_at'), 'PUBLISHED_GENERATION_TIME')
        if generated > now or generated.astimezone(CN).date().isoformat() != gate['as_of']:
            raise ValueError('INVALID_PUBLISHED_GENERATION_TIME')
        progress, reason = collector_progress(collector_status, gate['as_of'], generated, now)
        if progress is None: return {**gate, 'action': 'SKIP', 'reason': reason}
        return {**gate, 'action': 'RUN', 'reason': reason, 'repair_key': repair_key(gate['as_of']),
            'source_dataset_id': manifest['dataset_id'], 'source_generated_at': manifest['generated_at'],
            'collector_progress_at': progress, 'model_missing_count': missing}
    return {**gate, 'action': 'RUN', 'reason': 'TODAY_NOT_PUBLISHED'}

def run_guard(fetch, clock=lambda: datetime.now(timezone.utc), sleep=time.sleep, claim=None):
    started = clock(); gate = time_gate(started)
    if gate['action'] == 'SKIP': return {**gate, 'should_run': 'false', 'max_minutes': '0'}
    waited = gate['wait_seconds']
    if waited: sleep(waited)
    now = clock()
    if now < started or time_gate(now)['action'] != 'READY' or time_gate(now)['as_of'] != gate['as_of']:
        raise ValueError('WAIT_DID_NOT_REACH_SAME_DAY_WINDOW')
    # State is freshly fetched AFTER the bounded wait.
    manifest, lease = fetch(MANIFEST), fetch(LEASE)
    now = clock(); result = decide(now, manifest, lease)
    if result['reason'] == 'COLLECTOR_PROGRESS_REQUIRED':
        collector, marker = fetch(COLLECTOR_STATUS), fetch(repair_key(gate['as_of']))
        now = clock(); result = decide(now, manifest, lease, collector, marker)
    if result['as_of'] != gate['as_of']: raise ValueError('DAY_CHANGED_DURING_GUARD')
    # Guard+wait+compute share <=290 minutes after setup, inside the unchanged
    # 310-minute job. Five minutes are reserved for commit/cleanup.
    minutes = min(285, math.floor(((started+timedelta(minutes=290))-now).total_seconds()/60)-5)
    if result['action'] == 'RUN' and minutes < 2: raise ValueError('FOLLOWUP_BUDGET_EXHAUSTED')
    if result.get('repair_key') and result['action'] == 'RUN':
        # Recheck the writer after reading collector progress, before consuming
        # the one daily attempt. A competing guard is resolved by marker CAS.
        lease = fetch(LEASE); now = clock()
        result = decide(now, manifest, lease, collector, marker)
        if now < started or result['as_of'] != gate['as_of']: raise ValueError('DAY_OR_CLOCK_CHANGED_DURING_GUARD')
        minutes = min(285, math.floor(((started+timedelta(minutes=290))-now).total_seconds()/60)-5)
        if result['action'] == 'RUN':
            if minutes < 2: raise ValueError('FOLLOWUP_BUDGET_EXHAUSTED')
            if claim is None: raise ValueError('REPAIR_CLAIM_REQUIRED')
            attempt = {'schema': 'stock.chips.followup-repair.v1', 'as_of': result['as_of'], 'attempts': 1,
                'source_dataset_id': result['source_dataset_id'], 'source_generated_at': result['source_generated_at'],
                'collector_progress_at': result['collector_progress_at'], 'model_missing_count': result['model_missing_count'],
                'claimed_at': now.isoformat()}
            if not claim(result['repair_key'], attempt):
                result = {**result, 'action': 'SKIP', 'reason': 'DAILY_MODEL_INPUT_REPAIR_CLAIM_CONFLICT'}
            else:
                now = clock()
                if now < started or time_gate(now)['as_of'] != gate['as_of'] or time_gate(now)['action'] != 'READY':
                    raise ValueError('DAY_OR_CLOCK_CHANGED_DURING_CLAIM')
                minutes = min(285, math.floor(((started+timedelta(minutes=290))-now).total_seconds()/60)-5)
                if minutes < 2: raise ValueError('FOLLOWUP_BUDGET_EXHAUSTED_AFTER_CLAIM')
    return {**result, 'should_run': 'true' if result['action'] == 'RUN' else 'false',
            'max_minutes': str(minutes if result['action'] == 'RUN' else 0), 'wait_seconds': waited}

def read_metadata(client, bucket, key):
    try: response = client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('NoSuchKey', '404'): return None
        raise
    with response['Body'] as stream: raw = stream.read(MAX_METADATA_BYTES+1)
    if len(raw) > MAX_METADATA_BYTES: raise ValueError('METADATA_READ_BOUND')
    if raw[:2] == b'\x1f\x8b': raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(MAX_METADATA_BYTES+1)
    if len(raw) > MAX_METADATA_BYTES: raise ValueError('METADATA_DECODE_BOUND')
    return json.loads(raw)

def claim_repair(client, bucket, key, marker):
    """One irreversible daily attempt claim; never delete or reset this marker."""
    if not isinstance(marker, dict) or not isinstance(marker.get('as_of'), str) or key != repair_key(marker['as_of']):
        raise ValueError('INVALID_REPAIR_MARKER_KEY')
    valid_repair_marker(marker, marker['as_of'], timestamp(marker.get('claimed_at'), 'REPAIR_CLAIM_TIME'))
    try:
        client.put_object(Bucket=bucket, Key=key, Body=json.dumps(marker,separators=(',',':')).encode(),
            ContentType='application/json', ServerSideEncryption='AES256', CacheControl='no-store', IfNoneMatch='*')
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('PreconditionFailed','ConditionalRequestConflict','412','409'):
            return False
        raise
    return True

def verify_publication(manifest, as_of):
    if manifest_day(manifest) != as_of or not adopted(manifest):
        raise ValueError('CURRENT_DAILY_PUBLICATION_NOT_COMMITTED')
    return {'as_of': as_of, 'dataset_id': manifest['dataset_id'], 'publication_verified': True}

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--verify-as-of'); args = parser.parse_args()
    if os.environ.get('GITHUB_EVENT_NAME') != 'workflow_run': raise ValueError('FOLLOWUP_EVENT_REQUIRED')
    with Path(os.environ['GITHUB_EVENT_PATH']).open('rb') as stream: raw = stream.read(500001)
    if len(raw) > 500000: raise ValueError('EVENT_READ_BOUND')
    trusted_event(json.loads(raw))
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', config=Config(connect_timeout=7, read_timeout=25, retries={'total_max_attempts': 2}))
    fetch = lambda key: read_metadata(client, os.environ['STOCK_BUCKET'], key)
    if args.verify_as_of: result = verify_publication(fetch(MANIFEST), args.verify_as_of)
    else:
        result = run_guard(fetch, claim=lambda key,marker: claim_repair(client, os.environ['STOCK_BUCKET'], key, marker))
        with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
            for key in ('should_run', 'as_of', 'max_minutes', 'reason'): output.write(key+'='+result[key]+'\n')
    print(json.dumps(result, separators=(',', ':')))

if __name__ == '__main__': main()
