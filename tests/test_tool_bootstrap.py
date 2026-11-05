"""The shipped bootstrap guard rejects an unsafe or unrelated tool environment."""

import json
import os
import subprocess
import unittest
import venv
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

ROOT = Path(__file__).resolve().parents[1]
GUARD_RUN = """"$PDM_TOOL_HOME/venv/bin/python" - <<'PYTHON'
import json
import os
from importlib.metadata import PackageNotFoundError, distributions, version
from pathlib import Path
import shutil
import sys

home = Path(os.environ["PDM_TOOL_HOME"])
selected = shutil.which("pdm")
if Path(sys.prefix).resolve() != (home / "venv").resolve():
    raise SystemExit("Unexpected PDM tool interpreter")
if selected is None or Path(selected).resolve() != (home / "bin/pdm").resolve():
    raise SystemExit("Unexpected PDM executable")
expected: dict[str, str] = {
    "pdm": "2.29.2",
    "hishel": "1.2.1",
    "unearth": "0.18.2",
    "virtualenv": "21.15.2",
}
for name, required in expected.items():
    try:
        installed = version(name)
    except PackageNotFoundError:
        raise SystemExit("Missing PDM tool distribution") from None
    if installed != required:
        raise SystemExit("Unsupported PDM tool distribution")
print(json.dumps(sorted((item.metadata["Name"], item.version) for item in distributions())))
PYTHON
"""
GUARD_ENV = {"PDM_TOOL_HOME": "${{ runner.temp }}/pdm-tool"}


class ToolBootstrapTest(unittest.TestCase):
    def guard(self):
        setup = yaml.safe_load((ROOT / ".github/actions/setup/action.yml").read_text())
        found = [
            s for s in setup["runs"]["steps"] if s.get("name") == "Verify PDM tool environment"
        ]
        self.assertEqual(
            len(found), 1, "The actual bootstrap must run its guard before installation"
        )
        return found[0]["run"]

    def execute(
        self,
        versions=None,
        *,
        wrong_executable=False,
        missing_interpreter=False,
        wrong_interpreter=False,
    ):
        with TemporaryDirectory() as directory:
            home = Path(directory)
            environment = home / "venv"
            venv.EnvBuilder(with_pip=False).create(environment)
            site = next((environment / "lib").glob("python*/site-packages"))
            for name, value in (versions or {}).items():
                metadata = site / f"{name}-{value}.dist-info"
                metadata.mkdir()
                (metadata / "METADATA").write_text(f"Name: {name}\nVersion: {value}\n")
            (home / "bin").mkdir()
            (home / "bin/pdm").write_text("#!/bin/sh\nexit 0\n")
            (home / "bin/pdm").chmod(0o700)
            if missing_interpreter:
                (environment / "bin/python").unlink()
            if wrong_interpreter:
                (environment / "pyvenv.cfg").unlink()
            path = str(home / "bin") if not wrong_executable else "/usr/bin"
            return subprocess.run(  # noqa: S603 - the actual repository-owned guard in an isolated environment.
                ["/bin/bash", "-euo", "pipefail", "-c", self.guard()],
                env=os.environ | {"PDM_TOOL_HOME": str(home), "PATH": path + ":/usr/bin:/bin"},
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )

    def supported(self):
        return {"pdm": "2.29.2", "hishel": "1.2.1", "unearth": "0.18.2", "virtualenv": "21.15.2"}

    def test_supported_tool_reports_the_complete_actual_inventory(self):
        result = self.execute(self.supported())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), [[k, v] for k, v in sorted(self.supported().items())]
        )

    def test_vulnerable_or_incompatible_distribution_fails_before_installation(self):
        for name, value in (
            ("virtualenv", "21.3.1"),
            ("pdm", "2.29.6"),
            ("hishel", "2.0.0"),
            ("unearth", "0.17.5"),
        ):
            with self.subTest(name=name):
                result = self.execute(self.supported() | {name: value})
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, "Unsupported PDM tool distribution\n")

    def test_missing_distribution_fails_before_installation(self):
        for name in self.supported():
            with self.subTest(name=name):
                versions = self.supported()
                del versions[name]
                result = self.execute(versions)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, "Missing PDM tool distribution\n")

    def test_unrelated_pdm_executable_fails_before_installation(self):
        result = self.execute(self.supported(), wrong_executable=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "Unexpected PDM executable\n")

    def test_missing_interpreter_fails_before_installation(self):
        result = self.execute(self.supported(), missing_interpreter=True)
        self.assertEqual(result.returncode, 127)
        self.assertIn("/venv/bin/python: No such file or directory", result.stderr)

    def test_unrelated_interpreter_fails_before_installation(self):
        result = self.execute(self.supported(), wrong_interpreter=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "Unexpected PDM tool interpreter\n")
