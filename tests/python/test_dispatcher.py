import base64
import io
import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.actions.dispatcher import SHANGHAI, STATE_KEY, choose_work, dispatch_once, installation_token
from deploy.actions.scheduler import template

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
except ImportError:
    rsa = None


def at(clock="15:30", day="2026-09-08"):
    return datetime.fromisoformat(day + "T" + clock + ":00").replace(tzinfo=SHANGHAI)


def status(**values):
    base = {"calendarDates": ["2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09"],
            "calendarValidThrough": "2026-09-09", "targets": {}}
    base.update(values)
    return base


class MemoryS3:
    def __init__(self, value):
        self.data = {"collector/refresh-status.json": value}
        self.writes = []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {"Contents": [{"Key": key} for key in sorted(self.data) if key.startswith(Prefix)][:MaxKeys]}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(json.dumps(self.data[Key]).encode())}

    def put_object(self, **kwargs):
        self.writes.append(kwargs)
        self.data[kwargs["Key"]] = json.loads(kwargs["Body"])


class FakeGitHub:
    def __init__(self, active=False):
        self.is_active = active
        self.dispatched = []
        self.runs = {}
        self.error = False

    def active(self):
        return self.is_active

    def dispatch(self, work):
        if self.error:
            raise RuntimeError("remote failure")
        self.dispatched.append(work)
        return {"workflow_run_id": 900 + len(self.dispatched)}

    def run(self, run_id):
        return self.runs[run_id]


class DispatcherDecisionTests(unittest.TestCase):
    def test_1530_boundary_and_target_inputs(self):
        self.assertEqual(choose_work(status(), "close", at("15:29"))[1], "before_close_refresh")
        work, reason = choose_work(status(), "close", at())
        self.assertEqual(reason, "due")
        self.assertEqual(work, {"phase": "close", "target_date": "2026-09-08", "max_minutes": "285"})

    def test_holiday_uses_calendar_not_weekdays(self):
        saved = status(calendarDates=["2026-09-07", "2026-09-09"])
        self.assertEqual(choose_work(saved, "close", at())[1], "non_trading_day")

    def test_missing_and_stale_calendars_fail_closed(self):
        self.assertEqual(choose_work({}, "close", at())[1], "calendar_unavailable")
        self.assertEqual(choose_work(status(calendarValidThrough="2026-09-07"), "close", at())[1], "calendar_expired")

    def test_cutoff_and_priority_yield(self):
        saved = status(targets={"2026-09-07": {"unprocessedCount": 2713}})
        for clock in ("06:59", "23:55", "23:59"):
            self.assertEqual(choose_work(saved, "watchdog", at(clock))[1], "outside_collection_window")
        for clock in ("09:40", "09:49", "15:20", "15:29"):
            self.assertEqual(choose_work(saved, "watchdog", at(clock))[1], "priority_yield_window")
        work, _ = choose_work(saved, "catchup", at("07:00"))
        self.assertEqual(work["target_date"], "2026-09-07")
        self.assertEqual(work["max_minutes"], "160")

    def test_opening_prefetch_has_priority_and_does_not_dispatch_close(self):
        saved = status(targets={"2026-09-07": {"unprocessedCount": 10}})
        self.assertEqual(choose_work(saved, "watchdog", at("09:50"))[0]["phase"], "opening")
        saved["targets"]["2026-09-08"] = {"openingComplete": True}
        self.assertEqual(choose_work(saved, "opening", at("10:00"))[1], "opening_complete")
        self.assertEqual(choose_work(saved, "watchdog", at("11:30"))[0]["phase"], "catchup")

    def test_unknown_history_not_repeated_by_watchdog(self):
        self.assertEqual(choose_work(status(), "watchdog", at("07:00"))[1], "no_known_backlog")
        self.assertEqual(choose_work(status(), "catchup", at("07:00"))[0]["target_date"], "2026-09-07")

    def test_next_source_retry_blocks_even_with_unprocessed_rows(self):
        target = {"retryableCount": 5, "unprocessedCount": 0, "nextRetryAt": "2026-09-08T08:00:00Z"}
        saved = status(targets={"2026-09-08": target})
        self.assertEqual(choose_work(saved, "watchdog", at())[1], "source_retry_not_due")
        target["unprocessedCount"] = 1
        self.assertEqual(choose_work(saved, "watchdog", at())[1], "source_retry_not_due")

    def test_green_first_pass_does_not_mean_complete(self):
        saved = status(targets={"2026-09-08": {"firstPassCompletedAt": "2026-09-08T08:00:00Z",
                       "unprocessedCount": 0, "retryableCount": 6, "dataComplete": False}})
        self.assertEqual(choose_work(saved, "watchdog", at("17:00"))[1], "due")
        saved["targets"]["2026-09-08"]["dataComplete"] = True
        self.assertEqual(choose_work(saved, "close", at("17:00"))[1], "data_complete")

    def test_disabled_writer_is_never_dispatched(self):
        self.assertEqual(choose_work(status(writerDisabled=True), "close", at())[1], "writer_disabled")


class DispatcherExecutionTests(unittest.TestCase):
    def test_duplicate_event_only_posts_once_and_writes_own_state(self):
        s3, github = MemoryS3(status()), FakeGitHub()
        first = dispatch_once({"phase": "close"}, s3, "bucket", lambda: github, at())
        second = dispatch_once({"phase": "close"}, s3, "bucket", lambda: github, at() + timedelta(seconds=30))
        self.assertTrue(first["dispatched"])
        self.assertEqual(second["reason"], "dispatch_backoff")
        self.assertEqual(len(github.dispatched), 1)
        self.assertTrue(all(write["Key"] == STATE_KEY and write["ServerSideEncryption"] == "AES256" for write in s3.writes))

    def test_github_active_or_private_lease_prevents_dispatch(self):
        s3, github = MemoryS3(status()), FakeGitHub(active=True)
        self.assertEqual(dispatch_once({}, s3, "bucket", lambda: github, at())["reason"], "github_run_active")
        s3.data["collector/actions-lease.json"] = {"expiresAt": at().timestamp() + 3600}
        def forbidden():
            self.fail("Do not mint a token when a production lease is active")
        self.assertEqual(dispatch_once({}, s3, "bucket", forbidden, at())["reason"], "production_writer_active")
        self.assertFalse(github.dispatched)

    def test_workflow_failure_has_5_15_30_minute_backoff(self):
        s3, github = MemoryS3(status()), FakeGitHub()
        now = at()
        for failures, delay in ((0, 5), (1, 15), (2, 30), (3, 30)):
            s3.data[STATE_KEY] = {"attempts": {"close:2026-09-08": {"runId": 22, "failures": failures}}}
            github.runs[22] = {"status": "completed", "conclusion": "failure", "updated_at": now.isoformat()}
            result = dispatch_once({}, s3, "bucket", lambda: github, now)
            self.assertEqual(result["reason"], "failed_run_backoff")
            expected = (now + timedelta(minutes=delay)).astimezone(SHANGHAI)
            self.assertEqual(datetime.fromisoformat(result["nextAttemptAt"].replace("Z", "+00:00")), expected)
        self.assertFalse(github.dispatched)

    def test_uncertain_dispatch_is_saved_before_error_without_secret_output(self):
        s3, github = MemoryS3(status()), FakeGitHub()
        github.error = True
        with self.assertRaisesRegex(RuntimeError, "private retry ledger saved"):
            dispatch_once({}, s3, "bucket", lambda: github, at())
        record = s3.data[STATE_KEY]["attempts"]["close:2026-09-08"]
        self.assertEqual(record["failures"], 1)
        self.assertIn("nextAttemptAt", record)

    def test_completed_run_success_still_resumes_actual_missing_rows(self):
        s3, github = MemoryS3(status()), FakeGitHub()
        s3.data[STATE_KEY] = {"attempts": {"close:2026-09-08": {"runId": 22, "failures": 2}}}
        github.runs[22] = {"status": "completed", "conclusion": "success"}
        result = dispatch_once({}, s3, "bucket", lambda: github, at())
        self.assertTrue(result["dispatched"])
        self.assertEqual(s3.data[STATE_KEY]["attempts"]["close:2026-09-08"]["failures"], 0)


class SchedulerTemplateTests(unittest.TestCase):
    def build(self, **kwargs):
        return template("example-stock-bucket", "collector/dispatcher-code/release.zip",
                        "/stock/scheduler/github-app-key", 123, 456, 789, **kwargs)

    def test_precise_shanghai_schedules_disabled_until_explicit_enable(self):
        resources = self.build()["Resources"]
        expected = {"OpeningSchedule": "cron(50 9 * * ? *)", "CloseSchedule": "cron(30 15 * * ? *)",
                    "CatchupSchedule": "cron(0 7 * * ? *)", "WatchdogSchedule": "cron(2/5 7-23 * * ? *)"}
        for key, expression in expected.items():
            props = resources[key]["Properties"]
            self.assertEqual(props["ScheduleExpression"], expression)
            self.assertEqual(props["ScheduleExpressionTimezone"], "Asia/Shanghai")
            self.assertEqual(props["FlexibleTimeWindow"], {"Mode": "OFF"})
            self.assertEqual(props["State"], "DISABLED")
        self.assertEqual(self.build(enabled=True)["Resources"]["CloseSchedule"]["Properties"]["State"], "ENABLED")

    def test_no_data_write_or_lease_ownership_or_expensive_compute(self):
        value = self.build()
        statements = value["Resources"]["DispatcherRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        writes = [s for s in statements if "s3:PutObject" in s["Action"]]
        self.assertEqual([s["Resource"] for s in writes], ["arn:aws:s3:::example-stock-bucket/collector/dispatcher-state.json"])
        self.assertNotIn("s3:DeleteObject", str(statements))
        self.assertNotIn("AWS::EC2", str(value))
        self.assertNotIn("AWS::DynamoDB", str(value))
        self.assertNotIn("AWS::KMS::Key", str(value))
        self.assertNotIn("ReservedConcurrentExecutions", value["Resources"]["Dispatcher"]["Properties"])

    def test_parameter_and_artifact_scope(self):
        for name in ("/other/app-key", "/stock/scheduler/../secret", "*"):
            with self.assertRaises(ValueError):
                template("example-stock-bucket", "collector/dispatcher-code/release.zip", name, 1, 2, 3)
        for code in ("data/release.zip", "collector/dispatcher-code/../private.zip"):
            with self.assertRaises(ValueError):
                template("example-stock-bucket", code, "/stock/scheduler/key", 1, 2, 3)


@unittest.skipIf(rsa is None, "Lambda cryptography dependency is not installed in the project environment")
class AppTokenTests(unittest.TestCase):
    def setUp(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode()
        self.parameter_requests = []
        requests = self.parameter_requests
        class SSM:
            def get_parameter(self, **kwargs):
                requests.append(kwargs)
                return {"Parameter": {"Value": pem}}
        self.ssm = SSM()
        self.env = {"GITHUB_PRIVATE_KEY_PARAMETER": "/stock/scheduler/key", "GITHUB_APP_ID": "123",
                    "GITHUB_INSTALLATION_ID": "456", "GITHUB_REPOSITORY_ID": "789"}

    def token(self, permissions=None, private=False):
        requests = []
        def request(api, method, path, payload=None):
            requests.append((api.token, method, path, payload))
            if path.startswith("/app/installations/"):
                return {"token": "non-secret-test-fixture", "permissions": permissions or {"actions": "write", "metadata": "read"}}
            return {"id": 789, "full_name": "edwardxuuu-precious/ashare-opening-volume-collector", "private": private}
        with patch("deploy.actions.dispatcher.GitHub.request", request):
            result = installation_token(self.ssm, self.env, at())
        return result, requests

    def test_jwt_signature_and_exact_repo_permission_narrowing(self):
        result, requests = self.token()
        self.assertEqual(result, "non-secret-test-fixture")
        token, method, path, payload = requests[0]
        self.assertEqual((method, path), ("POST", "/app/installations/456/access_tokens"))
        self.assertEqual(payload, {"repository_ids": [789], "permissions": {"actions": "write"}})
        header, body, signature = token.split(".")
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        self.key.public_key().verify(decode(signature), (header + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256())
        claims = json.loads(decode(body))
        self.assertEqual(claims["iss"], "123")
        self.assertLessEqual(claims["exp"] - claims["iat"], 600)
        self.assertEqual(self.parameter_requests, [{"Name": "/stock/scheduler/key", "WithDecryption": True}])

    def test_broad_token_permission_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "unexpectedly broad"):
            self.token(permissions={"actions": "write", "contents": "write"})

    def test_private_repo_paid_runner_boundary_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "free-runner boundary changed"):
            self.token(private=True)


if __name__ == "__main__":
    unittest.main()
