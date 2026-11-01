"""Reserve the concentration tail within the existing 310-minute job."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import math
import os
import re

START_ENV = 'STOCK_CHIP_JOB_STARTED_EPOCH'
SOURCE_MINUTES = 282
SOURCE_CLEANUP_MINUTES = 5
DERIVE_STEP_MINUTES = 21
OUTER_SLACK_MINUTES = 7


def source_deadline(now, environ=None):
    values = os.environ if environ is None else environ
    value = values.get(START_ENV)
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,12}', value):
        raise ValueError('INVALID_CHIP_WORKFLOW_START')
    stamp = int(value)
    if stamp <= 0 or stamp > now.timestamp() + 30:
        raise ValueError('INVALID_CHIP_WORKFLOW_START')
    return datetime.fromtimestamp(stamp, timezone.utc) + timedelta(minutes=SOURCE_MINUTES)


def compute_minutes(started, now, requested=285, environ=None):
    if (now.tzinfo is None or started.tzinfo is None or now < started
            or type(requested) is not int or not 2 <= requested <= 285):
        raise ValueError('INVALID_CHIP_COMPUTE_BUDGET')
    deadline = started + timedelta(minutes=290)
    pinned = source_deadline(now, environ)
    if pinned is not None:
        deadline = min(deadline, pinned)
    available = math.floor((deadline - now).total_seconds() / 60) - SOURCE_CLEANUP_MINUTES
    return max(0, min(requested, available))


def source_plan(now, requested, environ=None):
    deadline = source_deadline(now, environ)
    if deadline is None:
        raise ValueError('CHIP_WORKFLOW_START_REQUIRED')
    minutes = compute_minutes(now, now, requested, environ)
    step_minutes = math.floor((deadline - now).total_seconds() / 60) - 1
    if minutes < 2 or step_minutes < minutes + 2:
        raise ValueError('CHIP_SOURCE_BUDGET_EXHAUSTED')
    return {'compute_minutes': minutes, 'step_minutes': step_minutes}


def verification_seconds(now, environ=None):
    deadline = source_deadline(now, environ)
    if deadline is None:
        raise ValueError('CHIP_WORKFLOW_START_REQUIRED')
    seconds = min(90, math.floor((deadline - now).total_seconds()) - 10)
    if seconds < 1:
        raise ValueError('CHIP_VERIFICATION_BUDGET_EXHAUSTED')
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('source-plan', 'verification-seconds'))
    parser.add_argument('--requested', type=int, default=285)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.action == 'verification-seconds':
        print(verification_seconds(now))
    else:
        plan = source_plan(now, args.requested)
        target = os.environ.get('GITHUB_OUTPUT')
        if not target:
            raise ValueError('GITHUB_OUTPUT_REQUIRED')
        with open(target, 'a', encoding='utf-8') as stream:
            for key, value in plan.items():
                stream.write(f'{key}={value}\n')


if __name__ == '__main__':
    main()
