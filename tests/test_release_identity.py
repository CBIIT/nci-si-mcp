"""Manual attestation source identity must equal the image's checked-out main commit."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ReleaseIdentityTest(unittest.TestCase):
    def test_manual_release_refuses_a_different_ref_or_invocation_commit(self):
        value = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
        script = next(s["run"] for s in value["jobs"]["release"]["steps"] if s.get("id") == "guard")
        for ref, sha, expected in (
            ("refs/heads/issue/390", "a" * 40, 1),
            ("refs/heads/main", "b" * 40, 1),
            ("refs/heads/main", "a" * 40, 0),
        ):
            with self.subTest(ref=ref, sha=sha), TemporaryDirectory() as directory:
                root = Path(directory)
                for name, body in {"git": 'print("a" * 40)', "python3": "pass"}.items():
                    executable = root / name
                    executable.write_text(f"#!{sys.executable}\n{body}\n")
                    executable.chmod(0o755)
                result = subprocess.run(  # noqa: S603 - real repository guard, isolated commands
                    ["/bin/bash", "-c", script],
                    env=os.environ
                    | {
                        "PATH": directory + os.pathsep + os.environ["PATH"],
                        "VALIDATED_SHA": "",
                        "GITHUB_SHA": sha,
                        "GITHUB_REF": ref,
                        "GITHUB_OUTPUT": str(root / "outputs"),
                    },
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, expected, result.stderr)
