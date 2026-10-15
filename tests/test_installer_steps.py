import tempfile
import unittest
from pathlib import Path

from nci_si_mcp import installer_state

API = installer_state


class Actions:
    step_names = ("first", "second", "third")

    def __init__(self):
        self.present = set()
        self.missing = {}
        self.calls = []
        self.bug: str | None = None

    def missing_inputs(self, step):
        return tuple(self.missing.get(step, ()))

    def apply(self, step):
        self.calls.append(("apply", step))
        if self.bug == step:
            raise AssertionError("PRIVATE-CANARY")
        self.present.add(step)

    def verify(self, step):
        self.calls.append(("verify", step))
        return step in self.present


class InstallerStepsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_dir = Path(directory.name)
        self.actions = Actions()

    def reconcile(self, restart=False):
        self.assertEqual(self.actions.step_names, ("first", "second", "third"))
        self.assertEqual(self.actions.missing_inputs("first"), ())
        return API.reconcile(
            self.data_dir,
            "local",
            ("server",),
            self.actions,
            restart=restart,
        )

    def test_resume_reverifies_from_the_first_step_and_restarts_at_the_first_stale_step(self):
        self.reconcile()
        self.actions.calls.clear()
        self.actions.present.remove("second")

        state = self.reconcile()

        self.assertEqual(
            self.actions.calls,
            [
                ("verify", "first"),
                ("verify", "second"),
                ("apply", "second"),
                ("verify", "second"),
                ("apply", "third"),
                ("verify", "third"),
            ],
        )
        self.assertEqual([outcome.status for outcome in state.outcomes], ["completed"] * 3)

    def test_running_every_change_twice_converges_to_the_same_state_and_outcome(self):
        first = self.reconcile()
        calls_after_first = tuple(self.actions.calls)
        second = self.reconcile()

        self.assertEqual(first, second)
        self.assertEqual(self.actions.present, {"first", "second", "third"})
        self.assertEqual(
            self.actions.calls[len(calls_after_first) :],
            [("verify", "first"), ("verify", "second"), ("verify", "third")],
        )

    def test_blocked_is_not_pending_and_names_the_missing_input(self):
        self.actions.missing["second"] = ("approved credential",)

        state = self.reconcile()
        status = API.project_status(state, self.actions)

        self.assertEqual(
            status,
            {
                "components": ["server"],
                "completed": ["first"],
                "blocked": [{"step": "second", "missing": ["approved credential"]}],
                "pending": ["third"],
                "nextAction": "Provide approved credential for second.",
            },
        )

    def test_restart_resets_progress_for_the_same_selection_without_uninstall(self):
        self.reconcile()
        self.actions.calls.clear()

        state = self.reconcile(restart=True)

        self.assertEqual([call[0] for call in self.actions.calls], ["apply", "verify"] * 3)
        self.assertEqual([outcome.status for outcome in state.outcomes], ["completed"] * 3)
        self.assertFalse(any(call[0] == "uninstall" for call in self.actions.calls))

    def test_an_action_bug_propagates_without_recording_clean_completion(self):
        self.actions.bug = "second"

        with self.assertRaisesRegex(AssertionError, "PRIVATE-CANARY"):
            self.reconcile()

        state = API.read_state(self.data_dir, "local", ("server",))
        self.assertEqual(state.outcomes, (API.StepOutcome("first", "completed"),))


if __name__ == "__main__":
    unittest.main()
