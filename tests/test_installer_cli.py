import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
INSTALLER = Path(sys.executable).with_name("nci-si-install")

ROOT_HELP = """usage: nci-si-install [-h] {plan} ...

Plan installation of the NCI Semantic Infrastructure MCP server and optional
components.

positional arguments:
  {plan}
    plan      Print the ordered installation plan

options:
  -h, --help  show this help message and exit
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
        ):
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self.run_installer(*arguments, force_color="1")

                self.assertEqual((code, stdout, stderr), (0, expected, ""))

    def test_the_installed_distribution_exposes_the_installer_command(self):
        self.assertTrue(INSTALLER.is_file())


if __name__ == "__main__":
    unittest.main()
