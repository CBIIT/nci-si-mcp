import subprocess
import unittest
from email.message import Message
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

from nci_si_mcp import installer

API: Any = installer


class AggregatePrerequisiteTest(unittest.TestCase):
    @staticmethod
    def local_check(name, components=("server", "documentation")):
        return next(check for check in API.local_prerequisites(components) if check.name == name)

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

    def test_supported_platforms_and_http_reachability_are_observed(self):
        os_check = self.local_check("supported_os", ("server",))
        for system, expected in (("Darwin", True), ("Linux", True), ("Windows", False)):
            with (
                self.subTest(system=system),
                patch("nci_si_mcp.installer.platform.system", return_value=system),
            ):
                self.assertEqual(os_check.observe(), expected)
        http_error = HTTPError("http://example", 401, "denied", Message(), None)
        with patch("nci_si_mcp.installer.urlopen", side_effect=http_error):
            self.assertTrue(API.reachability_prerequisite("x", "http://example", "fix").observe())

    def test_occupied_selected_port_fails_with_its_static_fix(self):
        check = self.local_check("port_8080")
        with patch("nci_si_mcp.installer.socket.socket") as socket:
            socket.return_value.__enter__.return_value.bind.side_effect = OSError(48, "in use")
            result = API.run_prerequisites((check,))[0]
        self.assertEqual(
            API.result_record(result),
            {
                "name": "port_8080",
                "status": "failed",
                "fix": "Free loopback port 8080 for documentation.",
            },
        )

    def test_companion_only_needs_its_container_disk_and_port_but_not_node_or_docs_port(self):
        checks = API.local_prerequisites(("server", "companion"))

        self.assertEqual(
            [check.name for check in checks],
            [
                "supported_os",
                "git",
                "pdm",
                "container_runtime",
                "compose",
                "port_8081",
                "evs_reachable",
                "cadsr_reachable",
            ],
        )

    def test_incomplete_fixture_configuration_never_falls_back_to_production_urls(self):
        with (
            patch.dict("os.environ", {"NCI_SI_UPSTREAM_MODE": "fixture"}, clear=True),
            patch("nci_si_mcp.installer.urlopen") as opened,
            self.assertRaisesRegex(ValueError, "NCI_SI_EVS_BASE_URL"),
        ):
            API.local_prerequisites(("server",))

        opened.assert_not_called()

    def test_node_requires_major_version_24_and_a_parseable_successful_answer(self):
        cases = (
            (None, False),
            (subprocess.CompletedProcess(["node", "--version"], 1, stdout="v24.0.0"), False),
            (subprocess.CompletedProcess(["node", "--version"], 0, stdout="not-a-version"), False),
            (subprocess.CompletedProcess(["node", "--version"], 0, stdout="v23.9.0"), False),
            (subprocess.CompletedProcess(["node", "--version"], 0, stdout="v24.0.0"), True),
            (subprocess.CompletedProcess(["node", "--version"], 0, stdout="v25.1.2"), True),
        )
        for answer, expected in cases:
            with self.subTest(answer=answer):
                with patch("nci_si_mcp.installer.subprocess.run") as run:
                    if answer is None:
                        run.side_effect = FileNotFoundError
                    else:
                        run.return_value = answer
                    result = self.local_check("node").observe()

                self.assertEqual(result, expected)

    def test_a_hung_compose_command_is_an_expected_failed_prerequisite(self):
        with (
            patch("nci_si_mcp.installer._container_runtimes", return_value=("docker",)),
            patch(
                "nci_si_mcp.installer.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["docker", "compose", "version"], 10),
            ),
        ):
            result = self.local_check("compose", ("server", "companion")).observe()

        self.assertFalse(result)

    def test_compose_passes_when_any_available_runtime_supports_it(self):
        answers = (
            subprocess.CompletedProcess(["docker", "compose", "version"], 1),
            subprocess.CompletedProcess(["podman", "compose", "version"], 0),
        )
        with (
            patch("nci_si_mcp.installer._container_runtimes", return_value=("docker", "podman")),
            patch("nci_si_mcp.installer.subprocess.run", side_effect=answers),
        ):
            result = self.local_check("compose", ("server", "companion")).observe()

        self.assertTrue(result)
