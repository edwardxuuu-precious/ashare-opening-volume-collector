"""Offline contracts for the once-daily, bounded cycle Scheduler dispatch."""
import json
import unittest
from datetime import timedelta, timezone
from unittest.mock import patch

from deploy.actions import dispatcher
from deploy.actions.scheduler import template
from test_dispatcher import MemoryS3, FakeGitHub, at


def event(day="2026-11-14", clock="22:40"):
    return {"phase": "cycle", "scheduled_time": at(clock, day).astimezone(timezone.utc).isoformat()}


class CycleGitHub(FakeGitHub):
    def __init__(self, active=False):
        super().__init__(active)
        self.cycles = []
        self.checked = []

    def active(self, workflow="collector.yml"):
        self.checked.append(workflow)
        return self.is_active

    def dispatch_cycle(self):
        if self.error:
            raise RuntimeError("private response must not escape")
        self.cycles.append("cycle")
        return {"workflow_run_id": 1001}


class CycleDispatchTests(unittest.TestCase):
    def dispatch(self, saved=None, github=None, incoming=None, now=None):
        return dispatcher.dispatch_cycle_once(
            event() if incoming is None else incoming,
            MemoryS3({}) if saved is None else saved, "bucket",
            lambda: github if github is not None else CycleGitHub(),
            at("22:40", "2026-11-14") if now is None else now)

    def assert_rejected(self, incoming, now):
        saved, github = MemoryS3({}), CycleGitHub()
        try:
            result = self.dispatch(saved, github, incoming, now)
        except ValueError:
            pass
        else:
            self.assertFalse(result["dispatched"])
        self.assertFalse(github.cycles)
        self.assertFalse(saved.writes)

    def test_claim_precedes_post_and_only_cycle_workflow_is_checked(self):
        saved, github = MemoryS3({}), CycleGitHub()
        original = github.dispatch_cycle
        def post():
            record = saved.data[dispatcher.STATE_KEY]["attempts"]["cycle:2026-11-14"]
            self.assertEqual(record["state"], "claimed")
            return original()
        github.dispatch_cycle = post
        self.assertTrue(self.dispatch(saved, github)["dispatched"])
        self.assertEqual(github.checked, [dispatcher.CYCLE_WORKFLOW])
        self.assertTrue(all(write["Key"] == dispatcher.STATE_KEY for write in saved.writes))

    def test_duplicate_same_day_is_never_posted_twice(self):
        saved, github = MemoryS3({}), CycleGitHub()
        self.assertTrue(self.dispatch(saved, github)["dispatched"])
        self.assertFalse(self.dispatch(saved, github, now=at("22:44", "2026-11-14"))["dispatched"])
        self.assertEqual(github.cycles, ["cycle"])

    def test_weekend_and_expired_collector_calendar_do_not_block_cycle(self):
        # Saturday: cycle sources may publish late and do not use collector targets.
        saved = MemoryS3({"calendarValidThrough": "2020-01-01", "calendarDates": []})
        self.assertTrue(self.dispatch(saved)["dispatched"])

    def test_each_china_calendar_day_has_an_independent_slot(self):
        saved, github = MemoryS3({}), CycleGitHub()
        self.assertTrue(self.dispatch(saved, github)["dispatched"])
        self.assertTrue(self.dispatch(saved, github, event("2026-11-15"), at("22:40", "2026-11-15"))["dispatched"])
        self.assertEqual(len(github.cycles), 2)
        self.assertIn("cycle:2026-11-14", saved.data[dispatcher.STATE_KEY]["attempts"])
        self.assertIn("cycle:2026-11-15", saved.data[dispatcher.STATE_KEY]["attempts"])

    def test_utc_clock_uses_china_window_and_slot(self):
        saved = MemoryS3({})
        now = at("22:40", "2026-11-14").astimezone(timezone.utc)
        self.assertTrue(self.dispatch(saved, now=now)["dispatched"])
        self.assertIn("cycle:2026-11-14", saved.data[dispatcher.STATE_KEY]["attempts"])

    def test_window_boundaries_are_half_open(self):
        for clock in ("00:00", "14:40", "22:39", "22:45", "23:59"):
            with self.subTest(clock=clock):
                self.assert_rejected(event(), at(clock, "2026-11-14"))
        self.assertTrue(self.dispatch(now=at("22:44", "2026-11-14") + timedelta(seconds=59))["dispatched"])

    def test_naive_now_is_rejected(self):
        self.assert_rejected(event(), at("22:40", "2026-11-14").replace(tzinfo=None))

    def test_missing_invalid_and_naive_event_timestamp_are_rejected(self):
        for timestamp in (None, "", "invalid", "2026-11-14T22:40:00", 123):
            with self.subTest(timestamp=timestamp):
                incoming = {"phase": "cycle"}
                if timestamp is not None:
                    incoming["scheduled_time"] = timestamp
                self.assert_rejected(incoming, at("22:40", "2026-11-14"))

    def test_stale_foreign_day_wrong_minute_and_future_events_are_rejected(self):
        for incoming in (event("2026-11-13"), event("2026-11-15"),
                         event(clock="22:39"), event(clock="22:41")):
            with self.subTest(incoming=incoming):
                self.assert_rejected(incoming, at("22:40", "2026-11-14"))
        future = event()
        future["scheduled_time"] = (at("22:40", "2026-11-14") + timedelta(seconds=1)).isoformat()
        self.assert_rejected(future, at("22:40", "2026-11-14"))

    def test_non_cycle_phases_never_claim_cycle_slot(self):
        for phase in (None, "watchdog", "catchup", "opening", "close", "unexpected"):
            with self.subTest(phase=phase):
                incoming = event()
                if phase is None:
                    incoming.pop("phase")
                else:
                    incoming["phase"] = phase
                self.assert_rejected(incoming, at("22:40", "2026-11-14"))

    def test_active_run_does_not_claim_or_post(self):
        saved, github = MemoryS3({}), CycleGitHub(active=True)
        self.assertFalse(self.dispatch(saved, github)["dispatched"])
        self.assertFalse(saved.writes)
        self.assertFalse(github.cycles)

    def test_claim_cas_conflict_never_posts_or_clobbers_other_owner(self):
        saved, github = MemoryS3({}), CycleGitHub()
        def active(workflow):
            saved.data[dispatcher.STATE_KEY] = {"attempts": {"foreign": {"lastDispatchAt": at("22:40", "2026-11-14").isoformat()}}}
            return False
        github.active = active
        with self.assertRaisesRegex(RuntimeError, "PreconditionFailed"):
            self.dispatch(saved, github)
        self.assertFalse(github.cycles)
        self.assertIn("foreign", saved.data[dispatcher.STATE_KEY]["attempts"])

    def test_unknown_outcome_consumes_slot_and_redacts_remote_error(self):
        saved, github = MemoryS3({}), CycleGitHub()
        github.error = True
        with self.assertRaises(RuntimeError) as raised:
            self.dispatch(saved, github)
        self.assertNotIn("private response", str(raised.exception))
        self.assertEqual(saved.data[dispatcher.STATE_KEY]["attempts"]["cycle:2026-11-14"]["state"], "outcome_unknown")
        github.error = False
        self.assertFalse(self.dispatch(saved, github)["dispatched"])
        self.assertFalse(github.cycles)

    def test_process_death_after_claim_never_reposts(self):
        saved, github = MemoryS3({}), CycleGitHub()
        with patch.object(github, "dispatch_cycle", side_effect=SystemExit("stopped")):
            with self.assertRaises(SystemExit):
                self.dispatch(saved, github)
        self.assertEqual(saved.data[dispatcher.STATE_KEY]["attempts"]["cycle:2026-11-14"]["state"], "claimed")
        self.assertFalse(self.dispatch(saved, github)["dispatched"])
        self.assertFalse(github.cycles)

    def test_other_ledgers_preserved_and_not_reused_as_cycle_claim(self):
        saved, github = MemoryS3({}), CycleGitHub()
        previous = {"close:2026-11-14": {"lastDispatchAt": at("15:30", "2026-11-14").isoformat(), "runId": 17},
                    "chips:2026-11-14:evening": {"lastDispatchAt": at("21:20", "2026-11-14").isoformat(), "state": "claimed"}}
        saved.data[dispatcher.STATE_KEY] = {"attempts": dict(previous)}
        self.assertTrue(self.dispatch(saved, github)["dispatched"])
        for key, value in previous.items():
            self.assertEqual(saved.data[dispatcher.STATE_KEY]["attempts"][key], value)

    def test_accepted_empty_response_still_consumes_daily_slot(self):
        saved, github = MemoryS3({}), CycleGitHub()
        with patch.object(github, "dispatch_cycle", return_value={}) as post:
            self.assertTrue(self.dispatch(saved, github)["dispatched"])
            self.assertFalse(self.dispatch(saved, github)["dispatched"])
        post.assert_called_once()
        self.assertEqual(saved.data[dispatcher.STATE_KEY]["attempts"]["cycle:2026-11-14"]["state"], "submitted")

    def test_post_success_then_ledger_conflict_never_reposts(self):
        saved, github = MemoryS3({}), CycleGitHub()
        original = github.dispatch_cycle
        def post():
            result = original()
            saved.data[dispatcher.STATE_KEY]["attempts"]["foreign"] = {"lastDispatchAt": at("22:40", "2026-11-14").isoformat()}
            return result
        github.dispatch_cycle = post
        with self.assertRaisesRegex(RuntimeError, "PreconditionFailed"):
            self.dispatch(saved, github)
        self.assertFalse(self.dispatch(saved, github)["dispatched"])
        self.assertEqual(github.cycles, ["cycle"])
        self.assertIn("foreign", saved.data[dispatcher.STATE_KEY]["attempts"])

    def test_cycle_event_only_routes_to_cycle_handler(self):
        with patch.object(dispatcher, "dispatch_cycle_once", return_value={"dispatched": True}) as cycle, \
             patch.object(dispatcher, "dispatch_once") as collector, \
             patch.object(dispatcher, "dispatch_chips_once") as chips:
            result = dispatcher.dispatch_all(event(), MemoryS3({}), "bucket", CycleGitHub, at("22:40", "2026-11-14"))
        self.assertEqual(result, {"cycle": {"dispatched": True}})
        cycle.assert_called_once()
        collector.assert_not_called()
        chips.assert_not_called()

    def test_other_phases_retain_existing_routes_and_never_call_cycle(self):
        for phase in ("watchdog", "opening", "close", "catchup"):
            with self.subTest(phase=phase), \
                 patch.object(dispatcher, "dispatch_cycle_once") as cycle, \
                 patch.object(dispatcher, "dispatch_once", return_value={"dispatched": False}) as collector, \
                 patch.object(dispatcher, "dispatch_chips_once", return_value={"dispatched": False}) as chips:
                result = dispatcher.dispatch_all({"phase": phase}, MemoryS3({}), "bucket", CycleGitHub, at("22:40", "2026-11-14"))
                self.assertEqual(set(result), {"chips", "collector"})
                cycle.assert_not_called()
                collector.assert_called_once()
                chips.assert_called_once()


class CycleApiAndInfrastructureTests(unittest.TestCase):
    def test_exact_workflow_main_ref_fixed_origin_no_new_permissions(self):
        calls = []
        def request(api, method, path, payload=None):
            calls.append((method, path, payload))
            return {"total_count": 0}
        with patch.object(dispatcher.GitHub, "request", request):
            github = dispatcher.GitHub("fixture")
            self.assertFalse(github.active(dispatcher.CYCLE_WORKFLOW))
            github.dispatch_cycle()
            with self.assertRaises(ValueError):
                github.active("arbitrary.yml")
        self.assertEqual(dispatcher.CYCLE_WORKFLOW, "cycle-bottom.yml")
        self.assertTrue(all("/workflows/cycle-bottom.yml/" in call[1] for call in calls))
        self.assertEqual(calls[-1][0], "POST")
        self.assertEqual(calls[-1][2], {"ref": "main", "inputs": {"automatic_origin": "aws-scheduler"}})

    def test_workflow_migration_gate_keeps_automatic_origins_mutually_exclusive(self):
        from pathlib import Path
        import yaml
        workflow = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/cycle-bottom.yml").read_text())
        condition = workflow["jobs"]["cycle"]["if"]
        for owner in ("", "aws"):
            for disabled in ("", "true"):
                for name, origin in (("schedule", ""), ("workflow_dispatch", "aws-scheduler"), ("workflow_dispatch", "")):
                    with self.subTest(owner=owner, disabled=disabled, event=name, origin=origin):
                        expression = condition
                        values = {"github.ref": "refs/heads/main", "github.event.repository.private": False,
                                  "github.event_name": name, "inputs.automatic_origin": origin,
                                  "vars.CYCLE_SCHEDULER": owner, "vars.DISABLE_CYCLE_BOTTOM_UPDATE": disabled}
                        for key, value in values.items():
                            expression = expression.replace(key, repr(value))
                        expression = expression.replace("&&", " and ").replace("||", " or ").replace("== false", "== False")
                        actual = eval(expression, {"__builtins__": {}}, {})
                        manual = name == "workflow_dispatch" and not origin
                        automatic = ((name == "schedule" and owner != "aws") or
                                     (origin == "aws-scheduler" and owner == "aws")) and disabled != "true"
                        self.assertEqual(actual, manual or automatic)

    def build(self, **kwargs):
        return template("fixture-bucket", "collector/dispatcher-code/fixture.zip", "/stock/scheduler/fixture", "1", "2", "3", **kwargs)["Resources"]

    def test_cycle_schedule_is_daily_shanghai_no_flexible_window_or_retry(self):
        properties = self.build()["CycleSchedule"]["Properties"]
        self.assertEqual(properties["ScheduleExpression"], "cron(40 22 * * ? *)")
        self.assertEqual(properties["ScheduleExpressionTimezone"], "Asia/Shanghai")
        self.assertEqual(properties["FlexibleTimeWindow"], {"Mode": "OFF"})
        self.assertEqual(properties["State"], "DISABLED")
        self.assertEqual(json.loads(properties["Target"]["Input"]),
                         {"phase": "cycle", "scheduled_time": "<aws.scheduler.scheduled-time>"})
        self.assertEqual(properties["Target"]["RetryPolicy"],
                         {"MaximumEventAgeInSeconds": 300, "MaximumRetryAttempts": 0})

    def test_cycle_enable_switch_does_not_enable_other_schedules_or_change_roles(self):
        baseline = self.build()
        cycle_only = self.build(cycle_enabled=True)
        collector_only = self.build(enabled=True)
        self.assertEqual(cycle_only["CycleSchedule"]["Properties"]["State"], "ENABLED")
        self.assertEqual(collector_only["CycleSchedule"]["Properties"]["State"], "DISABLED")
        for name in ("OpeningSchedule", "CloseSchedule", "CatchupSchedule", "WatchdogSchedule"):
            self.assertEqual(cycle_only[name], baseline[name])
            self.assertEqual(collector_only[name]["Properties"]["State"], "ENABLED")
        for name in ("DispatcherRole", "ScheduleRole", "Dispatcher", "LogGroup"):
            self.assertEqual(cycle_only[name], baseline[name])
            self.assertEqual(collector_only[name], baseline[name])
        statements = baseline["DispatcherRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        actions = {a for statement in statements for a in
                   ([statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"])}
        self.assertEqual(actions, {"ssm:GetParameter", "s3:ListBucket", "s3:GetObject", "s3:PutObject", "logs:CreateLogStream", "logs:PutLogEvents"})


if __name__ == "__main__":
    unittest.main()
