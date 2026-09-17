"""Read-only private publication health audit; stdout contains aggregate evidence only."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from scripts.refresh import complete

ZONE = ZoneInfo('Asia/Shanghai')
BUCKET = 'stock-private-access-databucket-bmzud610gtex'
REPOSITORY = 'edwardxuuu-precious/ashare-opening-volume-collector'


def at_or_before(value, day, clock):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(ZONE)
    return parsed.date().isoformat() == day and parsed.strftime('%H:%M:%S') <= clock


def target_state(status, day):
    targets = status.get('targets')
    summary = targets.get(day) if isinstance(targets, dict) else None
    if status.get('targetDate') == day:
        return dict(status, **(summary or {}))
    return summary if isinstance(summary, dict) else {}


def latest_closed_day(status, checked_at):
    dates = status.get('calendarDates')
    if not isinstance(dates, list) or not dates:
        return None
    today = checked_at.astimezone(ZONE).date().isoformat()
    clock = checked_at.astimezone(ZONE).strftime('%H:%M')
    closed = [day for day in dates if isinstance(day, str) and (
        day < today or (day == today and clock >= '15:30')
    )]
    return max(closed) if closed else None


def audit(status, payload, manifest, checked_at=None):
    checked_at = (checked_at or datetime.now(ZONE)).astimezone(ZONE)
    day = payload['date']
    rows = payload['rows']
    codes = [row.get('code') for row in rows]
    entry = next((item for item in manifest.get('dates', []) if item['date'] == day), {})
    target = target_state(status, day)
    ok = sum(row.get('status') == 'ok' and complete(row, day) for row in rows)
    no_trade = sum(row.get('status') == 'suspended' and complete(row, day) for row in rows)
    total = payload.get('universeTotal', payload.get('total'))
    errors = []
    if len(codes) != len(set(codes)) or len(codes) != total:
        errors.append('universe_identity_or_count')
    if entry.get('generatedAt') != payload.get('generatedAt'):
        errors.append('manifest_generation')
    if entry.get('valid') != ok or entry.get('suspended') != no_trade:
        errors.append('manifest_coverage')
    if status.get('targetDate') == day:
        if target.get('calculableCount') != ok or target.get('noTradeCount') != no_trade:
            errors.append('status_coverage')
    unresolved = len(rows) - ok - no_trade
    declared = target.get('dataComplete')
    if declared is not True:
        errors.append('status_incomplete')
    if declared and (unresolved or errors):
        errors.append('false_completion')
    expected = latest_closed_day(status, checked_at)
    if expected is None:
        errors.append('calendar_unavailable')
    elif day != expected:
        errors.append('target_not_latest_closed_day')
    outcome = target.get('outcome')
    if outcome in ('incomplete_checkpointed', 'failed'):
        errors.append('worker_outcome_' + outcome)
    errors = sorted(set(errors))
    healthy = not errors
    return dict(
        date=day,
        expectedClosedDate=expected,
        checkedAt=checked_at.isoformat(),
        health='healthy' if healthy else 'needs_attention',
        total=total,
        calculable=ok,
        confirmedNoTrade=no_trade,
        unresolved=unresolved,
        dataComplete=healthy,
        publicationErrors=errors,
        latestPublicationAt=payload.get('generatedAt'),
        phase=target.get('phase', status.get('phase')),
        outcome=outcome,
        unprocessedCount=target.get('unprocessedCount'),
        retryableCount=target.get('retryableCount'),
        nextRetryAt=target.get('nextRetryAt'),
        exitReason=target.get('exitReason', status.get('exitReason')),
        publishedAt=target.get('publishedAt', target.get('fullyPublishedAt')),
        firstPassCompletedAt=target.get('firstPassCompletedAt'),
        fullyPublishedAt=target.get('fullyPublishedAt'),
        firstDataRequestAt=target.get('firstDataRequestAt'),
        sameDayFirstRequestBy1535=at_or_before(target.get('firstDataRequestAt'), day, '15:35:00'),
        sameDayFirstPassBy1700=at_or_before(target.get('firstPassCompletedAt'), day, '17:00:00'),
        sourceMissingReasons=sorted({row.get('reason', '未说明') for row in rows if not complete(row, day)}),
        independentSchedulerAcceptance='requires_enabled_schedule_and_dispatch_log_readback',
    )


def missing(exc):
    return getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('NoSuchKey', '404', 'NotFound')


def observe(profile='dev', target=None):
    import boto3

    session = boto3.Session(profile_name=profile, region_name='us-east-1')
    if session.client('sts').get_caller_identity()['Account'] != '838775535952':
        raise RuntimeError('Unexpected AWS account')
    s3 = session.client('s3')

    def read(key, optional=False):
        try:
            response = s3.get_object(Bucket=BUCKET, Key=key)
        except Exception as exc:
            if optional and missing(exc):
                return {}
            raise
        try:
            value = json.loads(response['Body'].read())
        finally:
            response['Body'].close()
        if not isinstance(value, dict):
            raise RuntimeError('Invalid private status object')
        return value

    refresh_status = read('collector/refresh-status.json', optional=True)
    publication_status = read('data/collection-status.json')
    status = refresh_status or publication_status
    source = 'collector/refresh-status.json' if refresh_status else 'data/collection-status.json'
    day = target or status.get('targetDate') or status.get('latestDate')
    datetime.strptime(day, '%Y-%m-%d')
    # A publication can advance during the audit. A bounded retry refuses to call
    # a moving snapshot healthy until the manifest, payload, and status agree.
    for _ in range(3):
        manifest = read('data/manifest.json')
        payload = read('data/' + day + '.json')
        refresh_status = read('collector/refresh-status.json', optional=True)
        publication_status = read('data/collection-status.json')
        status = refresh_status or publication_status
        source = 'collector/refresh-status.json' if refresh_status else 'data/collection-status.json'
        result = audit(status, payload, manifest)
        if result['health'] == 'healthy':
            break
    result['statusSource'] = source

    import requests
    response = requests.get(
        'https://api.github.com/repos/' + REPOSITORY + '/actions/workflows/collector.yml/runs?per_page=10',
        timeout=20,
    )
    response.raise_for_status()
    result['workflowRuns'] = [
        {key: run.get(key) for key in ('id', 'status', 'conclusion', 'created_at', 'run_started_at', 'head_sha')}
        for run in response.json()['workflow_runs']
    ]
    try:
        stack = session.client('cloudformation').describe_stacks(StackName='stock-actions-scheduler')['Stacks'][0]
        result['independentSchedulerStack'] = stack['StackStatus']
    except Exception as exc:
        code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
        if code == 'ValidationError' or 'does not exist' in str(exc):
            result['independentSchedulerStack'] = 'not_deployed'
        else:
            raise
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date')
    parser.add_argument('--profile', default='dev')
    args = parser.parse_args()
    report = observe(args.profile, args.date)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['health'] == 'healthy' else 1)
