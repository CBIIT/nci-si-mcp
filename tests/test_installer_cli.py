import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
INSTALLER = Path(sys.executable).with_name("nci-si-install")

ROOT_HELP = """usage: nci-si-install [-h] {plan,check} ...

Plan installation of the NCI Semantic Infrastructure MCP server and optional
components.

positional arguments:
  {plan,check}
    plan        Print the ordered installation plan
    check       Check every prerequisite before installation

options:
  -h, --help    show this help message and exit
"""

PLAN_HELP = """usage: nci-si-install plan [-h] --target {local,aws,cloud-one} [--with-index]
                           [--with-documentation] [--with-companion]

options:
  -h, --help            show this help message and exit
  --target {local,aws,cloud-one}
                        installation target
  --with-index          include an existing NCIt index and its embedding
                        runtime
  --with-documentation  include the static documentation site
  --with-companion      include the validation companion
"""

CHECK_HELP = """usage: nci-si-install check [-h] --target {local,aws,cloud-one} [--with-index]
                            [--with-documentation] [--with-companion]

options:
  -h, --help            show this help message and exit
  --target {local,aws,cloud-one}
                        installation target
  --with-index          include an existing NCIt index and its embedding
                        runtime
  --with-documentation  include the static documentation site
  --with-companion      include the validation companion
"""


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
        code, stdout, stderr = self.run_installer("check", "--target", "aws")

        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("#324", stderr)
        self.assertIn("BLOCKED", stderr)

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

    def test_help_is_exact_and_ignores_forced_colour(self):
        for arguments, expected in (
            (("--help",), ROOT_HELP),
            (("plan", "--help"), PLAN_HELP),
            (("check", "--help"), CHECK_HELP),
        ):
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self.run_installer(*arguments, force_color="1")

                self.assertEqual((code, stdout, stderr), (0, expected, ""))

    def test_the_installed_distribution_exposes_the_installer_command(self):
        self.assertTrue(INSTALLER.is_file())


if __name__ == "__main__":
    unittest.main()
