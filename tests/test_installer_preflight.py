import unittest
from typing import Any
from unittest.mock import patch

from nci_si_mcp import installer

API: Any = installer


class AggregatePrerequisiteTest(unittest.TestCase):
    def test_every_check_runs_and_failures_keep_static_fixes(self):
        observed = []

        def check(name, passes):
            def observe():
                observed.append(name)
                return passes

            return API.Prerequisite(name, f"fix {name}", observe)

        results = API.run_prerequisites(
            (check("first", False), check("second", True), check("third", False))
        )

        self.assertEqual(observed, ["first", "second", "third"])
        self.assertEqual(
            [API.result_record(result) for result in results],
            [
                {"name": "first", "status": "failed", "fix": "fix first"},
                {"name": "second", "status": "passed"},
                {"name": "third", "status": "failed", "fix": "fix third"},
            ],
        )

    def test_an_observer_bug_propagates_and_stops_later_checks(self):
        observed = []

        def broken():
            raise AssertionError("PRIVATE-CANARY")

        def later():
            observed.append("later")
            return True

        checks = (
            API.Prerequisite("broken", "repair the check", broken),
            API.Prerequisite("later", "fix later", later),
        )

        with self.assertRaisesRegex(AssertionError, "PRIVATE-CANARY"):
            API.run_prerequisites(checks)
        self.assertEqual(observed, [])

    def test_expected_observer_failures_return_false_without_leaking_details(self):
        canary = "PRIVATE-CANARY"
        with (
            patch("nci_si_mcp.installer.shutil.which", return_value=None),
            patch("nci_si_mcp.installer.urlopen", side_effect=OSError(canary)),
        ):
            results = API.run_prerequisites(
                (
                    API.executable_prerequisite("pdm", "Install PDM."),
                    API.reachability_prerequisite(
                        "evs_reachable", f"https://example.invalid/{canary}", "Reach EVS."
                    ),
                )
            )

        records = [API.result_record(result) for result in results]
        self.assertEqual(
            records,
            [
                {"name": "pdm", "status": "failed", "fix": "Install PDM."},
                {"name": "evs_reachable", "status": "failed", "fix": "Reach EVS."},
            ],
        )
        self.assertNotIn(canary, repr(records))

    def test_companion_only_needs_its_container_disk_and_port_but_not_node_or_docs_port(self):
        checks = API.local_prerequisites(("server", "companion"))

        self.assertEqual(
            [check.name for check in checks],
            [
                "supported_os",
                "pdm",
                "container_runtime",
                "compose",
                "disk_space",
                "port_8081",
                "evs_reachable",
                "cadsr_reachable",
            ],
        )


if __name__ == "__main__":
    unittest.main()
