"""Small, fail-closed Scheduler -> GitHub App dispatcher; never downloads market data.

The collector remains the sole holder of actions-lease.json. This function may
only read collection status and write its own dispatch/retry ledger.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

REPOSITORY = "edwardxuuu-precious/ashare-opening-volume-collector"
WORKFLOW = "collector.yml"
SHANGHAI = ZoneInfo("Asia/Shanghai")
STATE_KEY = "collector/dispatcher-state.json"
BACKOFF_MINUTES = (5, 15, 30)
ACTIVE_STATUSES = ("in_progress", "queued", "waiting", "pending", "requested")


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def instant(value):
    if not value:
        return None
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Status timestamps must include their timezone")
    return result


def count(value):
    return value if type(value) is int and value >= 0 else 0


def pending(target):
    if target.get("dataComplete") is True:
        return False
    if target.get("dataComplete") is False:
        return True
    return any(count(target.get(key)) for key in (
        "unprocessedCount", "retryableCount", "pendingCount", "latestPendingStocks"))


def choose_work(status, phase, now):
    """Return one bounded target, or the reason why a dispatch is not due."""
    if phase not in ("opening", "close", "catchup", "watchdog"):
        raise ValueError("Unknown schedule phase")
    now = now.astimezone(SHANGHAI)
    today, clock = now.date().isoformat(), now.strftime("%H:%M")
    if clock < "07:00" or clock >= "23:55":
        return None, "outside_collection_window"
    if status.get("writerDisabled") is True or status.get("automationEnabled") is False:
        return None, "writer_disabled"
    dates = status.get("calendarDates")
    valid_through = status.get("calendarValidThrough")
    if not isinstance(dates, list) or not dates or not isinstance(valid_through, str):
        return None, "calendar_unavailable"
    # Never turn a weekday guess or an expired trading calendar into authority.
    if valid_through < today:
        return None, "calendar_expired"
    if any(not isinstance(day, str) or datetime.strptime(day, "%Y-%m-%d").date().isoformat() != day for day in dates):
        raise ValueError("Invalid trading calendar")
    targets = dict(status.get("targets") or {})
    current_day = status.get("targetDate") or status.get("latestDate")
    if current_day:
        targets.setdefault(current_day, status)
    is_trading = today in dates
    if phase in ("opening", "close") and not is_trading:
        return None, "non_trading_day"
    if phase == "opening" and not ("09:50" <= clock < "15:20"):
        return None, "outside_opening_window"
    if phase == "close" and clock < "15:30":
        return None, "before_close_refresh"

    # Today's prefetch has priority, but it never publishes an unclosed daily ratio.
    if is_trading and "09:50" <= clock < "15:20" and phase in ("opening", "watchdog"):
        target = targets.get(today, {})
        if target.get("openingComplete") is not True:
            selected = ("opening", today, target, "15:20")
        elif phase == "opening":
            return None, "opening_complete"
        else:
            selected = None
    elif is_trading and clock >= "15:30" and phase in ("close", "watchdog"):
        target = targets.get(today, {})
        if target.get("dataComplete") is not True:
            selected = ("close", today, target, "23:55")
        elif phase == "close":
            return None, "data_complete"
        else:
            selected = None
    else:
        selected = None

    if selected is None:
        if "09:40" <= clock < "09:50" or "15:20" <= clock < "15:30":
            return None, "priority_yield_window"
        if phase not in ("catchup", "watchdog"):
            return None, "phase_complete"
        closed = sorted((day for day in dates if day < today), reverse=True)
        candidates = [day for day in closed if pending(targets.get(day, {}))]
        # The explicit 07:00 trigger may bootstrap yesterday after a broken run.
        # Watchdog does not repeatedly dispatch unknown historical days.
        if not candidates and phase == "catchup" and closed and closed[0] not in targets:
            candidates = [closed[0]]
        if not candidates:
            return None, "no_known_backlog"
        day = candidates[0]
        deadline = "09:40" if clock < "09:40" else ("15:20" if clock < "15:20" else "23:55")
        selected = ("catchup", day, targets.get(day, {}), deadline)

    selected_phase, day, target, deadline = selected
    next_retry = instant(target.get("nextRetryAt"))
    if next_retry and now < next_retry and not count(target.get("unprocessedCount")):
        return None, "source_retry_not_due"
    until = now.replace(hour=int(deadline[:2]), minute=int(deadline[3:]), second=0, microsecond=0)
    minutes = min(285, int((until - now).total_seconds() // 60))
    if minutes < 12:
        return None, "insufficient_safe_runtime"
    return {"phase": selected_phase, "target_date": day, "max_minutes": str(minutes)}, "due"


def read_json(s3, bucket, key, optional=False):
    if optional:
        # Prefix-scoped ListBucket permits a genuine absent/forbidden distinction
        # without giving the dispatcher a listing of all private market objects.
        listing = s3.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
        if not any(item.get("Key") == key for item in listing.get("Contents", [])):
            return {}
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if optional and getattr(exc, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return {}
        raise
    raw = response["Body"].read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Dispatcher status exceeds safe size")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Invalid dispatcher status")
    return result


def save_state(s3, bucket, state):
    s3.put_object(Bucket=bucket, Key=STATE_KEY,
                  Body=json.dumps(state, separators=(",", ":"), allow_nan=False).encode(),
                  ContentType="application/json", ServerSideEncryption="AES256",
                  CacheControl="no-store")


class GitHub:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, payload=None):
        request = Request("https://api.github.com" + path,
                          data=None if payload is None else json.dumps(payload).encode(),
                          method=method, headers={"Authorization": "Bearer " + self.token,
                          "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                          "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "stock-scheduler"})
        try:
            with urlopen(request, timeout=12) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except HTTPError as exc:
            # Do not print GitHub response bodies, JWTs or installation tokens.
            raise RuntimeError("GitHub API HTTP " + str(exc.code)) from None
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("GitHub API response exceeds safe size")
        return json.loads(raw) if raw else {}

    def active(self):
        for status in ACTIVE_STATUSES:
            result = self.request("GET", "/repos/" + REPOSITORY + "/actions/workflows/" + WORKFLOW
                                  + "/runs?branch=main&status=" + status + "&per_page=1")
            if result.get("total_count", 0):
                return True
        return False

    def run(self, run_id):
        return self.request("GET", "/repos/" + REPOSITORY + "/actions/runs/" + str(int(run_id)))

    def dispatch(self, work):
        return self.request("POST", "/repos/" + REPOSITORY + "/actions/workflows/" + WORKFLOW
                            + "/dispatches", {"ref": "main", "inputs": dict(work, mode="collect")})


def installation_token(ssm, env, now):
    """Request a short-lived token narrowed to one immutable repository ID."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    key_text = ssm.get_parameter(Name=env["GITHUB_PRIVATE_KEY_PARAMETER"], WithDecryption=True)["Parameter"]["Value"]
    key = serialization.load_pem_private_key(key_text.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("GitHub App requires an RSA private key of at least 2048 bits")
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=")
    body = b".".join((encode(b'{"alg":"RS256","typ":"JWT"}'),
                      encode(json.dumps({"iat": int(now.timestamp()) - 60,
                                         "exp": int(now.timestamp()) + 540,
                                         "iss": env["GITHUB_APP_ID"]}, separators=(",", ":")).encode())))
    jwt = (body + b"." + encode(key.sign(body, padding.PKCS1v15(), hashes.SHA256()))).decode()
    result = GitHub(jwt).request("POST", "/app/installations/" + str(int(env["GITHUB_INSTALLATION_ID"]))
                                + "/access_tokens", {"repository_ids": [int(env["GITHUB_REPOSITORY_ID"])],
                                "permissions": {"actions": "write"}})
    if result.get("permissions", {}).get("actions") != "write" or not result.get("token"):
        raise RuntimeError("GitHub App Actions permission was not granted")
    if set(result.get("permissions", {})) - {"actions", "metadata"}:
        raise RuntimeError("Installation token has unexpectedly broad permissions")
    api = GitHub(result["token"])
    repo = api.request("GET", "/repos/" + REPOSITORY)
    if repo.get("id") != int(env["GITHUB_REPOSITORY_ID"]) or repo.get("full_name") != REPOSITORY or repo.get("private") is not False:
        raise RuntimeError("Repository identity or free-runner boundary changed")
    return result["token"]


def dispatch_once(event, s3, bucket, github_factory, now):
    phase = event.get("phase", "watchdog")
    status = read_json(s3, bucket, "collector/refresh-status.json", optional=True)
    if not status:
        status = read_json(s3, bucket, "data/collection-status.json", optional=True)
    work, reason = choose_work(status, phase, now)
    if work is None:
        return {"dispatched": False, "reason": reason}
    lease = read_json(s3, bucket, "collector/actions-lease.json", optional=True)
    if lease:
        expires = lease.get("expiresAt")
        if type(expires) not in (int, float):
            raise ValueError("Invalid production writer lease")
        if expires > now.timestamp():
            return {"dispatched": False, "reason": "production_writer_active"}
    ledger = read_json(s3, bucket, STATE_KEY, optional=True)
    key = work["phase"] + ":" + work["target_date"]
    attempts = ledger.setdefault("attempts", {})
    record = attempts.setdefault(key, {})
    next_attempt = instant(record.get("nextAttemptAt"))
    if next_attempt and now < next_attempt:
        return {"dispatched": False, "reason": "dispatch_backoff", "nextAttemptAt": iso(next_attempt)}
    github = github_factory()
    if github.active():
        return {"dispatched": False, "reason": "github_run_active"}
    if record.get("runId") and record.get("observedRunId") != record["runId"]:
        run = github.run(record["runId"])
        if run.get("status") != "completed":
            return {"dispatched": False, "reason": "github_run_not_completed"}
        record["observedRunId"] = record["runId"]
        if run.get("conclusion") != "success":
            record["failures"] = count(record.get("failures")) + 1
            delay = BACKOFF_MINUTES[min(record["failures"] - 1, 2)]
            completed = instant(run.get("updated_at")) or now
            record["nextAttemptAt"] = iso(completed + timedelta(minutes=delay))
            save_state(s3, bucket, ledger)
            if now < instant(record["nextAttemptAt"]):
                return {"dispatched": False, "reason": "failed_run_backoff", "nextAttemptAt": record["nextAttemptAt"]}
        else:
            record["failures"] = 0
    # Save before POST. An uncertain network response is retried only after the
    # cooldown and another live GitHub active-run / production-lease check.
    record.update(lastDispatchAt=iso(now), nextAttemptAt=iso(now + timedelta(minutes=5)))
    ledger.update(version=1, updatedAt=iso(now))
    # Keep a bounded 14-day ledger; it contains no market rows or credentials.
    threshold = (now.astimezone(SHANGHAI).date() - timedelta(days=14)).isoformat()
    ledger["attempts"] = {k: v for k, v in attempts.items() if str(v.get("lastDispatchAt", ""))[:10] >= threshold}
    save_state(s3, bucket, ledger)
    try:
        result = github.dispatch(work)
    except Exception:
        record["failures"] = count(record.get("failures")) + 1
        record["nextAttemptAt"] = iso(now + timedelta(minutes=BACKOFF_MINUTES[min(record["failures"] - 1, 2)]))
        save_state(s3, bucket, ledger)
        raise RuntimeError("Workflow dispatch failed; private retry ledger saved") from None
    if result.get("workflow_run_id"):
        record["runId"] = int(result["workflow_run_id"])
    save_state(s3, bucket, ledger)
    return {"dispatched": True, "phase": work["phase"], "targetDate": work["target_date"],
            "runId": record.get("runId"), "dispatchedAt": iso(now)}


def handler(event, context):
    import boto3
    now = datetime.now(timezone.utc)
    s3, ssm = boto3.client("s3"), boto3.client("ssm")
    result = dispatch_once(event, s3, os.environ["STOCK_BUCKET"],
                           lambda: GitHub(installation_token(ssm, os.environ, now)), now)
    # Aggregate-only operational proof; no status bodies or GitHub responses.
    print(json.dumps(result, separators=(",", ":")))
    return result
