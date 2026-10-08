"""Fixed local validation profiles do not inherit production secrets or serving data."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.benchmark_limits import Limits
from scripts.operator_worker import _server, clean_environment, fixture_benchmark


class OperatorEnvironmentTest(unittest.TestCase):
    def test_child_imports_come_from_the_owned_source_root(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "src/nci_si_mcp"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("SOURCE_MARKER = 'owned snapshot'\n")
            with patch("scripts.operator_worker.acceptance_http.ROOT", root):
                environment = clean_environment(root)
            result = subprocess.run(
                [sys.executable, "-c", "import nci_si_mcp; print(nci_si_mcp.SOURCE_MARKER)"],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "owned snapshot")

    def test_readiness_timeout_reaps_owned_child(self):
        spawn = subprocess.Popen
        children = []

        def sleeping(_command, **options):
            child = spawn([sys.executable, "-c", "import time; time.sleep(300)"], **options)
            children.append(child)
            return child

        with TemporaryDirectory() as directory:
            with (
                patch("scripts.operator_worker.subprocess.Popen", side_effect=sleeping),
                patch("scripts.operator_worker.time.monotonic", side_effect=(0, 16, 16)),
                patch("scripts.operator_worker.socket.create_connection", side_effect=OSError),
                self.assertRaisesRegex(TimeoutError, "did not become ready"),
                _server(Path(directory), {}),
            ):
                self.fail("Unready process must not be measured")
            self.assertIsNotNone(children[0].poll())

    def test_fixed_benchmark_command_writes_report_and_returns_success(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            result = subprocess.run(  # noqa: S603 - fixed project worker in an owned directory
                [
                    sys.executable,
                    "-m",
                    "scripts.operator_worker",
                    "benchmark-http-fixture",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            report = json.loads(output.read_text())
            self.assertTrue(report["complete"])
            self.assertEqual(len(report["cases"]), 10)

    def test_unrecorded_fixture_request_invalidates_completed_report(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with (
                patch("scripts.operator_worker.unmatched_requests", return_value=["missing"]),
                self.assertRaisesRegex(RuntimeError, "unrecorded"),
            ):
                fixture_benchmark(
                    output, limits=Limits(repetitions=1, warmups=0), names=["get_concept"]
                )
            report = json.loads(output.read_text())
            self.assertFalse(report["complete"])
            self.assertEqual(report["state"], "failed")

    def test_fixture_benchmark_runs_owned_http_server_and_removes_working_data(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with patch.dict(
                "os.environ",
                {"NCI_SI_DATA_DIR": "/production/index", "AWS_SECRET_ACCESS_KEY": "PRIVATE-CANARY"},
            ):
                result = fixture_benchmark(
                    output,
                    limits=Limits(repetitions=1, warmups=0),
                    names=["get_concept", "harmonize_data_dictionary"],
                )
            self.assertTrue(result["complete"])
            self.assertEqual(result["mode"], "fixture")
            self.assertEqual(len(result["cases"]), 2)
            self.assertTrue(
                all(case["warm"]["summary"]["errorRate"] == 0 for case in result["cases"])
            )
            self.assertEqual(json.loads(output.read_text()), result)
            self.assertEqual([path.name for path in Path(directory).iterdir()], ["report.json"])
            self.assertNotIn("PRIVATE-CANARY", output.read_text())

    def test_child_environment_uses_owned_home_and_drops_credentials_proxies_and_settings(self):
        parent = {
            "PATH": "/runtime/bin",
            "HOME": "/human/home",
            "LANG": "en_US.UTF-8",
            "AWS_SECRET_ACCESS_KEY": "PRIVATE-CANARY",
            "HTTPS_PROXY": "http://proxy",
            "NCI_SI_DATA_DIR": "/production/index",
            "NCI_SI_CADSR_API_KEY": "PRIVATE-CANARY",
            "PYTHONPATH": "/untrusted/imports",
            "SSH_AUTH_SOCK": "/agent/socket",
        }
        with TemporaryDirectory() as directory, patch.dict("os.environ", parent, clear=True):
            environment = clean_environment(Path(directory))
        self.assertEqual(environment["PATH"], "/runtime/bin")
        self.assertEqual(environment["HOME"], directory)
        self.assertEqual(environment["TMPDIR"], directory)
        self.assertEqual(environment["LANG"], "en_US.UTF-8")
        self.assertNotIn("PRIVATE-CANARY", str(environment))
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertNotIn("/untrusted/imports", environment.get("PYTHONPATH", ""))
        self.assertNotIn("SSH_AUTH_SOCK", environment)
        self.assertNotIn("NCI_SI_DATA_DIR", environment)
