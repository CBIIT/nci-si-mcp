import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nci_si_mcp import installer

ROOT = Path(__file__).parent.parent
INSTALLER = Path(sys.executable).with_name("nci-si-install")
ROOT_HELP = "usage: nci-si-install [-h] {plan,check,run,status,help} ...\n\nPlan installation of the NCI Semantic Infrastructure MCP server and optional\ncomponents.\n\npositional arguments:\n  {plan,check,run,status,help}\n    plan                Print the ordered installation plan\n    check               Check every prerequisite before installation\n    run                 Run and record verified installation steps\n    status              Reverify and show installation status\n    help                Explain an executable installation step\n\noptions:\n  -h, --help            show this help message and exit\n"  # noqa: E501

SELECTION_OPTIONS = """
options:
  -h, --help            show this help message and exit
  --target {local,aws,cloud-one}
                        installation target
  --with-index          include an existing NCIt index and its embedding
                        runtime
  --with-documentation  include the static documentation site
  --with-companion      include the validation companion
"""

RUN_HELP = (
    """usage: nci-si-install run [-h] --target {local,aws,cloud-one} [--with-index]
                          [--with-documentation] [--with-companion]
                          --data-dir DATA_DIR [--restart] [--non-interactive]
"""
    + SELECTION_OPTIONS.rstrip()
    + """
  --data-dir DATA_DIR   installation state directory
  --restart             reset progress for the same selection
  --non-interactive     report unresolved actions without prompting
"""
)

STEP_HELP = "Step: check_prerequisites\nPurpose: Check every selected requirement before installation changes anything.\nWhy: A partial installation must not be created when required inputs are missing.\nAction: Apply every fix reported by `check` or a BLOCKED `run`.\nVerification: Rerun every selected prerequisite; confirmation alone is never accepted.\nCommon failures: missing tools, occupied ports, or unreachable EVS and caDSR endpoints.\nGuide: https://github.com/CBIIT/nci-si-mcp/blob/main/docs/installer-targets.md\n"  # noqa: E501

STATUS_HELP = (
    """usage: nci-si-install status [-h] --target {local,aws,cloud-one}
                             [--with-index] [--with-documentation]
                             [--with-companion] --data-dir DATA_DIR
"""
    + SELECTION_OPTIONS.rstrip()
    + """
  --data-dir DATA_DIR   installation state directory
"""
)


class InstallerCliTest(unittest.TestCase):
    def run_installer(self, *arguments, force_color=None):
        environment = os.environ.copy()
        environment["COLUMNS"] = "80"
        if force_color is not None:
            environment["FORCE_COLOR"] = force_color
        process = subprocess.run(  # noqa: S603 - fixed project entry point with test arguments
            [INSTALLER, *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return process.returncode, process.stdout, process.stderr

    def test_local_check_reports_every_failure_without_leaking_observations(self):
        environment = os.environ.copy()
        environment.update(
            {
                "COLUMNS": "80",
                "PATH": "",
                "NCI_SI_EVS_BASE_URL": "http://127.0.0.1:1/PRIVATE-CANARY",
                "NCI_SI_CADSR_BASE_URL": "http://127.0.0.1:2/PRIVATE-CANARY",
                "NCI_SI_DATA_DIR": str(ROOT / "missing-installer-state"),
            }
        )
        process = subprocess.run(  # noqa: S603 - installed project entry point
            [
                INSTALLER,
                "check",
                "--target",
                "local",
                "--with-documentation",
                "--with-companion",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual((process.returncode, process.stderr), (1, ""))
        report = json.loads(process.stdout)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(
            [result["name"] for result in report["results"]],
            [
                "supported_os",
                "git",
                "pdm",
                "container_runtime",
                "compose",
                "node",
                "port_8080",
                "port_8081",
                "evs_reachable",
                "cadsr_reachable",
            ],
        )
        failed = {result["name"] for result in report["results"] if result["status"] == "failed"}
        self.assertLessEqual(
            {
                "git",
                "pdm",
                "container_runtime",
                "compose",
                "node",
                "evs_reachable",
                "cadsr_reachable",
            },
            failed,
        )
        self.assertNotIn("PRIVATE-CANARY", process.stdout + process.stderr)
        self.assertFalse((ROOT / "missing-installer-state").exists())

    def test_unselected_components_contribute_no_component_prerequisites(self):
        code, stdout, stderr = self.run_installer("check", "--target", "local")
        self.assertIn(code, (0, 1))
        self.assertEqual(stderr, "")
        self.assertEqual(
            [result["name"] for result in json.loads(stdout)["results"]],
            ["supported_os", "git", "pdm", "evs_reachable", "cadsr_reachable"],
        )

    def test_aws_checks_are_blocked_until_their_own_issue(self):
        for command in ("check", "run", "status"):
            arguments = [command, "--target", "aws"]
            if command in ("run", "status"):
                arguments.extend(("--data-dir", "unused"))
            with self.subTest(command=command):
                code, stdout, stderr = self.run_installer(*arguments)
                observed = code, stdout, "#324" in stderr, "BLOCKED" in stderr
                self.assertEqual(observed, (2, "", True, True))

    def test_unexpected_value_and_runtime_bugs_propagate_from_main(self):
        for error in (ValueError("PRIVATE-CANARY"), RuntimeError("PRIVATE-CANARY")):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(installer, "_state_command", side_effect=error),
                self.assertRaisesRegex(type(error), "PRIVATE-CANARY"),
            ):
                installer.main(["status", "--target", "local", "--data-dir", "unused"])
        with patch.dict(os.environ, {"NCI_SI_EVS_BASE_URL": "not-a-url"}, clear=True):
            code, stdout, stderr = self.run_installer("check", "--target", "local")
        self.assertEqual((code, stdout, "NCI_SI_EVS_BASE_URL" in stderr), (2, "", True))

    def test_run_records_and_status_reverifies_the_real_prerequisite_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "PATH": "",
                "NCI_SI_EVS_BASE_URL": "http://127.0.0.1:1",
                "NCI_SI_CADSR_BASE_URL": "http://127.0.0.1:2",
            }
            with patch.dict(os.environ, environment, clear=False):
                run = self.run_installer("run", "--target", "local", "--data-dir", directory)
                status = self.run_installer("status", "--target", "local", "--data-dir", directory)
            self.assertEqual((run[0], run[2]), (1, ""))
            self.assertEqual((status[0], status[2]), (1, ""))
            expected = {
                "components": ["server"],
                "completed": [],
                "failed": [],
                "blocked": [
                    {
                        "step": "check_prerequisites",
                        "missing": [
                            "Install git and fetch release tags as QUICKSTART.md documents.",
                            "Install PDM as QUICKSTART.md documents.",
                            "Restore network access to NCI_SI_EVS_BASE_URL.",
                            "Restore network access to NCI_SI_CADSR_BASE_URL.",
                        ],
                    }
                ],
                "pending": [],
                "nextAction": (
                    "Resolve the failed prerequisites: Install git and fetch release tags as "
                    "QUICKSTART.md documents.; Install PDM as QUICKSTART.md documents.; Restore "
                    "network access to NCI_SI_EVS_BASE_URL.; Restore network access to "
                    "NCI_SI_CADSR_BASE_URL."
                ),
            }
            self.assertEqual(json.loads(run[1]), expected)
            self.assertEqual(json.loads(status[1]), expected)

    def test_restart_cannot_adopt_another_component_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_installer("run", "--target", "local", "--data-dir", directory)
            code, stdout, stderr = self.run_installer(
                "run",
                "--target",
                "local",
                "--with-index",
                "--data-dir",
                directory,
                "--restart",
            )
            self.assertEqual((code, stdout), (2, ""))
            self.assertIn("new --data-dir", stderr)

    def test_run_and_status_refuse_semantically_invalid_state_without_rewriting(self):
        content = (
            b'{"version":1,"target":"local","components":["server"],'
            b'"outcomes":[{"step":"unknown","status":"completed"}]}'
        )
        for command in ("run", "status"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "installer-state.json"
                path.write_bytes(content)
                arguments = [command, "--target", "local", "--data-dir", directory]
                if command == "run":
                    arguments.append("--non-interactive")
                code, stdout, stderr = self.run_installer(*arguments)
                self.assertEqual((code, stdout), (2, ""))
                self.assertIn("malformed", stderr)
                self.assertEqual(path.read_bytes(), content)

    def test_status_without_recorded_state_does_not_create_the_directory(self):
        with tempfile.TemporaryDirectory() as parent:
            data_dir = Path(parent) / "absent"
            code, stdout, stderr = self.run_installer(
                "status", "--target", "local", "--data-dir", str(data_dir)
            )
            self.assertEqual((code, stdout), (2, ""))
            self.assertIn(f"no installation recorded in {data_dir}", stderr)
            self.assertIn("run `run` first", stderr)
            self.assertFalse(data_dir.exists())

    def test_server_only_and_optional_components_have_one_ordered_plan(self):
        cases = (
            ((), ["server"]),
            (("--with-index",), ["server", "index"]),
            (
                ("--with-companion", "--with-documentation", "--with-index"),
                ["server", "index", "documentation", "companion"],
            ),
            (
                ("--with-index", "--with-companion", "--with-documentation"),
                ["server", "index", "documentation", "companion"],
            ),
        )
        for flags, components in cases:
            with self.subTest(flags=flags):
                code, stdout, stderr = self.run_installer("plan", "--target", "local", *flags)
                self.assertEqual((code, stderr), (0, ""))
                self.assertEqual(
                    json.loads(stdout),
                    {
                        "target": "local",
                        "components": components,
                        "stages": ["check_prerequisites", "install", "configure", "verify"],
                    },
                )

    def test_supported_aws_plan_excludes_the_unapproved_companion(self):
        code, stdout, stderr = self.run_installer(
            "plan", "--target", "aws", "--with-index", "--with-documentation"
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(
            json.loads(stdout),
            {
                "target": "aws",
                "components": ["server", "index", "documentation"],
                "stages": ["check_prerequisites", "install", "configure", "verify"],
            },
        )

    def test_blocked_targets_and_components_name_the_missing_decision(self):
        for arguments, issue in (
            (("plan", "--target", "cloud-one"), "#292"),
            (("plan", "--target", "aws", "--with-companion"), "#197"),
        ):
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self.run_installer(*arguments)
                self.assertEqual((code, stdout), (2, ""))
                self.assertIn(issue, stderr)
                self.assertIn("BLOCKED", stderr)

    def test_future_and_unknown_targets_are_not_cli_values(self):
        for target in ("azure", "gcp", "other"):
            with self.subTest(target=target):
                code, stdout, stderr = self.run_installer("plan", "--target", target)
                self.assertEqual((code, stdout), (2, ""))
                self.assertIn("invalid choice", stderr)

    def test_step_help_is_side_effect_free_and_unimplemented_steps_name_phase_33(self):
        with tempfile.TemporaryDirectory() as parent:
            data_dir = Path(parent) / "empty"
            environment = os.environ | {
                "PATH": "",
                "NCI_SI_EVS_BASE_URL": "http://127.0.0.1:1",
                "NCI_SI_CADSR_BASE_URL": "http://127.0.0.1:2",
            }
            with patch.dict(os.environ, environment, clear=True):
                process = subprocess.run(  # noqa: S603 - installed project entry point
                    [INSTALLER, "help", "check_prerequisites", "--target", "local"],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            self.assertEqual(
                (process.returncode, process.stdout, process.stderr), (0, STEP_HELP, "")
            )
            self.assertFalse(data_dir.exists())
        for step in ("install", "configure", "verify", "unknown"):
            with self.subTest(step=step):
                code, stdout, stderr = self.run_installer("help", step, "--target", "local")
                self.assertEqual((code, stdout), (2, ""))
                self.assertIn("#320", stderr)

    def test_help_is_exact_and_ignores_forced_colour(self):
        for arguments, expected in (
            (("--help",), ROOT_HELP),
            (("run", "--help"), RUN_HELP),
            (("status", "--help"), STATUS_HELP),
        ):
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self.run_installer(*arguments, force_color="1")
                self.assertEqual((code, stdout, stderr), (0, expected, ""))

    def test_the_installed_distribution_exposes_the_installer_command(self):
        self.assertTrue(INSTALLER.is_file())
