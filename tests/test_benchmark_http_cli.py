"""Remote benchmark commands require explicit consent and never take credentials in URLs."""

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import benchmark_http_cli


class HTTPBenchmarkCommandTest(unittest.TestCase):
    def test_invalid_target_credentials_are_not_echoed_by_command_validation(self):
        with (
            patch.object(
                sys,
                "argv",
                [
                    "benchmark-http",
                    "--allow-remote",
                    "--target",
                    "https://PRIVATE-CANARY@example.org/mcp",
                    "--allow-target",
                    "https://example.org/mcp",
                    "--output",
                    "unused.json",
                ],
            ),
            self.assertRaises(SystemExit) as caught,
        ):
            benchmark_http_cli.arguments()
        self.assertEqual(caught.exception.code, 2)

    def test_command_passes_fixed_cases_and_environment_header_and_preserves_exit_status(self):
        observed = []

        async def measured(target, allowed, cases, limits, output, **options):
            observed.append((target, allowed, cases, limits, output, options))
            return {"complete": False}

        with (
            patch.object(
                sys,
                "argv",
                [
                    "benchmark-http",
                    "--allow-remote",
                    "--target",
                    "https://example.org/mcp",
                    "--allow-target",
                    "https://example.org/mcp",
                    "--case",
                    "get_concept",
                    "--repetitions",
                    "2",
                    "--output",
                    "unused.json",
                ],
            ),
            patch.dict("os.environ", {"NCI_SI_BENCHMARK_AUTHORIZATION": "Bearer private"}),
            patch.object(benchmark_http_cli, "run_http", side_effect=measured),
        ):
            self.assertEqual(benchmark_http_cli.main(), 1)
        target, allowed, cases, limits, output, options = observed[0]
        self.assertEqual(target, allowed[0])
        self.assertEqual([case["tool"] for case in cases], ["get_concept"])
        self.assertIn("release", cases[0]["arguments"])
        self.assertEqual(limits.repetitions, 2)
        self.assertEqual(output, Path("unused.json"))
        self.assertEqual(options, {"allow_remote": True, "authorization": "Bearer private"})

    def test_remote_probe_refuses_without_opt_in_and_writes_no_success(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            result = subprocess.run(  # noqa: S603 - fixed project command with rejected endpoint
                [
                    sys.executable,
                    "-m",
                    "scripts.benchmark_http_cli",
                    "--target",
                    "https://example.org/mcp",
                    "--allow-target",
                    "https://example.org/mcp",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("explicit operator opt-in", result.stderr)
            self.assertFalse(output.exists())
