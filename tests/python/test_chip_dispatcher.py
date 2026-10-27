import unittest
from unittest.mock import patch

from deploy.actions.dispatcher import (
    CHIP_WORKFLOW, GitHub, STATE_KEY, choose_chip_work, dispatch_all, dispatch_chips_once,
    read_ledger, save_state,
)
from test_dispatcher import MemoryS3, FakeGitHub, at, status


class ChipGitHub(FakeGitHub):
    def __init__(self, active=False, error=False):
        super().__init__(active)
        self.error = error
        self.workflows_checked = []
        self.chips = []

    def active(self, workflow="collector.yml"):
        self.workflows_checked.append(workflow)
        return self.is_active if workflow == CHIP_WORKFLOW else False

    def dispatch_chips(self, work):
        if self.error:
            raise RuntimeError("remote private response must not escape")
        self.chips.append(work)
        return {"workflow_run_id": 1001}


class ChipMilestoneTests(unittest.TestCase):
    def test_exact_boundaries_and_fixed_budget(self):
        for clock, slot, day in (
            ("15:40", "primary", "2026-09-08"),
            ("21:19", "primary", "2026-09-08"),
            ("21:20", "evening", "2026-09-08"),
            ("23:57", "evening", "2026-09-08"),
            ("02:20", "overnight", "2026-09-07"),
            ("07:02", "overnight", "2026-09-07"),
            ("15:39", "overnight", "2026-09-07"),
        ):
            with self.subTest(clock=clock):
                work, reason = choose_chip_work(status(), at(clock))
                self.assertEqual(reason, "due")
                self.assertEqual(work, {"mode": "collect", "as_of": day,
                    "auto_slot": slot, "automatic_origin": "aws-watchdog", "max_minutes": "285"})
        self.assertEqual(choose_chip_work(status(), at("02:19"))[1], "before_overnight_window")

    def test_official_calendar_and_expiry_no_weekday_or_stale_date_guess(self):
        self.assertEqual(choose_chip_work(status(), at("21:20", "2026-09-06"))[1], "non_trading_target")
        self.assertEqual(choose_chip_work(status(), at("02:20", "2026-09-07"))[1], "non_trading_target")
        self.assertEqual(choose_chip_work(status(), at("02:20", "2026-09-10"))[1], "calendar_expired")
        self.assertEqual(choose_chip_work(status(writerDisabled=True), at("21:20"))[1], "writer_disabled")
        self.assertEqual(choose_chip_work(status(calendarDates=[]), at())[1], "calendar_unavailable")

    def test_valid_overnight_on_non_trading_next_day(self):
        saved = status(calendarDates=["2026-09-04"], calendarValidThrough="2026-09-07")
        self.assertEqual(choose_chip_work(saved, at("02:20", "2026-09-05"))[0]["as_of"], "2026-09-04")
        self.assertEqual(choose_chip_work(saved, at("02:20", "2026-09-06"))[1], "non_trading_target")

    def test_invalid_calendar_or_naive_clock_never_creates_a_target(self):
        for invalid in ("not-a-date", "2026-09-09T00:00:00"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                choose_chip_work(status(calendarValidThrough=invalid), at("21:22"))
        with self.assertRaises(ValueError):
            choose_chip_work(status(), at("21:22").replace(tzinfo=None))


class ChipWakeupTests(unittest.TestCase):
    def test_one_dispatch_per_slot_and_independent_collector_ledger(self):
        s3, github = MemoryS3(status()), ChipGitHub()
        s3.data[STATE_KEY] = {"attempts": {"close:2026-09-08": {
            "lastDispatchAt": at().isoformat(), "runId": 88}}}
        first = dispatch_chips_once({}, s3, "bucket", lambda: github, at("15:42"))
        duplicate = dispatch_chips_once({}, s3, "bucket", lambda: github, at("15:47"))
        evening = dispatch_chips_once({}, s3, "bucket", lambda: github, at("21:22"))
        self.assertTrue(first["dispatched"])
        self.assertEqual(duplicate["reason"], "chip_slot_already_claimed")
        self.assertTrue(evening["dispatched"])
        self.assertEqual(len(github.chips), 2)
        self.assertEqual(s3.data[STATE_KEY]["attempts"]["close:2026-09-08"]["runId"], 88)
        self.assertTrue(all(call["Key"] == STATE_KEY for call in s3.writes))
        self.assertEqual(github.workflows_checked, [CHIP_WORKFLOW, CHIP_WORKFLOW])

    def test_active_chips_keeps_allowance_available_for_next_tick(self):
        s3, github = MemoryS3(status()), ChipGitHub(active=True)
        result = dispatch_chips_once({}, s3, "bucket", lambda: github, at("21:22"))
        self.assertEqual(result["reason"], "chip_run_active")
        self.assertFalse(s3.writes)
        github.is_active = False
        result = dispatch_chips_once({}, s3, "bucket", lambda: github, at("21:27"))
        self.assertTrue(result["dispatched"])
        self.assertEqual(len(github.chips), 1)

    def test_uncertain_post_consumes_slot_and_is_not_retried(self):
        s3, github = MemoryS3(status()), ChipGitHub(error=True)
        with self.assertRaisesRegex(RuntimeError, "outcome unknown; slot remains consumed"):
            dispatch_chips_once({}, s3, "bucket", lambda: github, at("21:22"))
        record = s3.data[STATE_KEY]["attempts"]["chips:2026-09-08:evening"]
        self.assertEqual(record["state"], "outcome_unknown")
        github.error = False
        self.assertEqual(dispatch_chips_once({}, s3, "bucket", lambda: github, at("21:27"))["reason"],
                         "chip_slot_already_claimed")
        self.assertFalse(github.chips)

    def test_claim_is_saved_before_post_and_claim_conflict_posts_nothing(self):
        s3, github = MemoryS3(status()), ChipGitHub()
        old_dispatch = github.dispatch_chips
        def assert_claim(work):
            self.assertEqual(s3.data[STATE_KEY]["attempts"]["chips:2026-09-08:evening"]["state"], "claimed")
            return old_dispatch(work)
        github.dispatch_chips = assert_claim
        self.assertTrue(dispatch_chips_once({}, s3, "bucket", lambda: github, at("21:22"))["dispatched"])
        fresh_s3, fresh_github = MemoryS3(status()), ChipGitHub()
        def conflicting_active(workflow):
            fresh_s3.data[STATE_KEY] = {"attempts": {"foreign": {"lastDispatchAt": at().isoformat()}}}
            return False
        fresh_github.active = conflicting_active
        with self.assertRaisesRegex(RuntimeError, "PreconditionFailed"):
            dispatch_chips_once({}, fresh_s3, "bucket", lambda: fresh_github, at("21:22"))
        self.assertFalse(fresh_github.chips)
        self.assertIn("foreign", fresh_s3.data[STATE_KEY]["attempts"])

    def test_ledger_stale_update_cannot_clobber_another_owner(self):
        s3 = MemoryS3(status())
        s3.data[STATE_KEY] = {"attempts": {}}
        stale, etag = read_ledger(s3, "bucket")
        s3.data[STATE_KEY]["attempts"]["foreign"] = {"lastDispatchAt": at().isoformat()}
        with self.assertRaisesRegex(RuntimeError, "PreconditionFailed"):
            save_state(s3, "bucket", stale, etag)
        self.assertIn("foreign", s3.data[STATE_KEY]["attempts"])

    def test_collector_claim_uses_cas_before_post_too(self):
        s3, github = MemoryS3(status()), ChipGitHub()
        def conflicting_active(workflow="collector.yml"):
            s3.data[STATE_KEY] = {"attempts": {"chips:2026-09-08:primary": {
                "lastDispatchAt": at().isoformat(), "state": "claimed"}}}
            return False
        github.active = conflicting_active
        from deploy.actions.dispatcher import dispatch_once
        with self.assertRaisesRegex(RuntimeError, "PreconditionFailed"):
            dispatch_once({}, s3, "bucket", lambda: github, at("15:42"))
        self.assertFalse(github.dispatched)
        self.assertIn("chips:2026-09-08:primary", s3.data[STATE_KEY]["attempts"])

    def test_process_death_after_claim_cannot_repeat_post(self):
        s3, github = MemoryS3(status()), ChipGitHub()
        with patch.object(github, "dispatch_chips", side_effect=SystemExit("process stopped")):
            with self.assertRaises(SystemExit):
                dispatch_chips_once({}, s3, "bucket", lambda: github, at("21:22"))
        self.assertEqual(s3.data[STATE_KEY]["attempts"]["chips:2026-09-08:evening"]["state"], "claimed")
        self.assertEqual(dispatch_chips_once({}, s3, "bucket", lambda: github, at("21:27"))["reason"],
                         "chip_slot_already_claimed")
        self.assertFalse(github.chips)

    def test_same_tick_independent_when_collector_complete_or_fails(self):
        s3, github = MemoryS3(status(targets={"2026-09-08": {
            "dataComplete": True, "priceDataComplete": True}})), ChipGitHub()
        results = dispatch_all({}, s3, "bucket", lambda: github, at("21:22"))
        self.assertTrue(results["chips"]["dispatched"])
        self.assertEqual(results["collector"]["reason"], "no_known_backlog")
        with patch("deploy.actions.dispatcher.dispatch_once", side_effect=RuntimeError("private")):
            result = dispatch_all({}, MemoryS3(status()), "bucket", lambda: ChipGitHub(), at("21:22"))
        self.assertTrue(result["chips"]["dispatched"])
        self.assertEqual(result["collector"], {"dispatched": False, "reason": "dispatch_error", "errorType": "RuntimeError"})

    def test_overnight_never_runs_collector_and_ledger_bounded(self):
        s3, github = MemoryS3(status()), ChipGitHub()
        s3.data[STATE_KEY] = {"attempts": {"chips:old:primary": {"lastDispatchAt": "2026-08-01T00:00:00Z"}}}
        result = dispatch_all({}, s3, "bucket", lambda: github, at("02:22"))
        self.assertTrue(result["chips"]["dispatched"])
        self.assertEqual(result["collector"]["reason"], "outside_collection_window")
        self.assertNotIn("chips:old:primary", s3.data[STATE_KEY]["attempts"])
        self.assertFalse(github.dispatched)

    def test_earlier_exact_collector_triggers_do_not_consume_chip_allowance(self):
        for phase in ("opening", "close"):
            s3, github = MemoryS3(status()), ChipGitHub()
            self.assertEqual(dispatch_chips_once({"phase": phase}, s3, "bucket", lambda: github,
                at("21:22"))["reason"], "not_chip_wakeup_phase")
            self.assertFalse(s3.writes)


class ChipApiContractTests(unittest.TestCase):
    def test_workflow_allowlist_and_dispatch_input_contract(self):
        calls = []
        def request(api, method, path, payload=None):
            calls.append((method, path, payload))
            return {"total_count": 0}
        work = choose_chip_work(status(), at("21:22"))[0]
        with patch.object(GitHub, "request", request):
            github = GitHub("fixture")
            self.assertFalse(github.active(CHIP_WORKFLOW))
            github.dispatch_chips(work)
            with self.assertRaises(ValueError):
                github.active("arbitrary.yml")
        self.assertTrue(all("/workflows/chips.yml/" in call[1] for call in calls))
        self.assertEqual(calls[-1][2], {"ref": "main", "inputs": work})


if __name__ == "__main__":
    unittest.main()
