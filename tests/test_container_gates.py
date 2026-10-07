import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import container_lock, image_scan


class ContainerGateTest(unittest.TestCase):
    def test_generation_keeps_cpu_roots_and_hashes_for_each_resolved_dependency(self):
        pins = {"torch": "2.14.1", "numpy": "2.5.0", "sentence-transformers": "5.1.0"}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "generated"
            lock = target / "requirements.txt"
            with (
                patch.object(container_lock, "versions", return_value=pins),
                patch.object(container_lock, "LOCK", lock),
                patch("sys.argv", ["container_lock.py", "prepare", str(target)]),
            ):
                container_lock.main()
                roots = (target / "roots.txt").read_text().splitlines()
                self.assertEqual(roots[:3], ["mcp", "numpy", "sentence-transformers"])
                self.assertEqual(
                    set((target / "constraints.txt").read_text().splitlines()),
                    {"torch==2.14.1", "numpy==2.5.0", "sentence-transformers==5.1.0"},
                )
                report = {
                    "install": [
                        self.resolved("torch", "2.14.1+cpu", roots[-1].split(" @ ")[1], "a"),
                        self.resolved("NumPy", "2.5.0", "https://example.test/numpy.whl", "b"),
                    ]
                }
                resolution = target / "resolution.json"
                resolution.write_text(json.dumps(report))
                with patch("sys.argv", ["container_lock.py", "write", str(resolution)]):
                    container_lock.main()
                rendered = lock.read_text()
                requirements = {line for line in rendered.splitlines() if not line.startswith("#")}
                self.assertEqual(
                    requirements,
                    {
                        roots[-1] + " --hash=sha256:" + "a" * 64,
                        "numpy==2.5.0 --hash=sha256:" + "b" * 64,
                    },
                )
                with patch("sys.argv", ["container_lock.py", "check"]):
                    container_lock.main()
                report["install"][0]["metadata"]["version"] = "wrong-version"
                resolution.write_text(json.dumps(report))
                with (
                    patch("sys.argv", ["container_lock.py", "write", str(resolution)]),
                    self.assertRaisesRegex(ValueError, "PDM requires"),
                ):
                    container_lock.main()
                self.assertEqual(lock.read_text(), rendered)

    @staticmethod
    def resolved(name, version, url, digest):
        return {
            "metadata": {"name": name, "version": version},
            "download_info": {
                "url": url,
                "archive_info": {"hashes": {"sha256": digest * 64}},
            },
        }

    def test_scan_command_blocks_publication_with_a_nonzero_exit_status(self):
        for severity, status in (("HIGH", 1), ("CRITICAL", 1), ("MEDIUM", 0)):
            with self.subTest(severity=severity), tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / "scan.json"
                report.write_text(
                    json.dumps(
                        {
                            "Results": [
                                {
                                    "Vulnerabilities": [
                                        {
                                            "Severity": severity,
                                            "VulnerabilityID": "example",
                                            "PkgName": "package",
                                            "FixedVersion": "",
                                        }
                                    ]
                                }
                            ]
                        }
                    )
                )
                process = subprocess.run(  # noqa: S603 - local scanner with a generated report
                    [sys.executable, image_scan.__file__, str(report)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(process.returncode, status, process.stderr)
                self.assertIn(f"Publication gate: {status} High/Critical findings", process.stdout)

    def test_current_lock_checks_and_version_or_hash_drift_fails(self):
        container_lock.check()
        original = container_lock.LOCK.read_text()
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "requirements.txt"
            with patch.object(container_lock, "LOCK", lock):
                for changed in (
                    original.replace("# PDM: ", "# PDM: stale"),
                    original.replace("mcp==2.2.0", "mcp==0.0.0"),
                    original.replace("--hash=sha256:", "--hash=sha256:invalid", 1),
                    original.replace("torch-2.14.1%2Bcpu", "torch-0.0.0%2Bcpu"),
                ):
                    with self.subTest(lock=changed[:80]):
                        lock.write_text(changed)
                        with self.assertRaises(ValueError):
                            container_lock.check()

    def test_resolved_version_must_match_pdm_and_torch_must_be_cpu(self):
        report = {
            "install": [
                {
                    "metadata": {"name": "torch", "version": "2.14.1+cpu"},
                    "download_info": {
                        "url": "https://download.pytorch.org/whl/cpu/torch.whl",
                        "archive_info": {"hashes": {"sha256": "a" * 64}},
                    },
                }
            ]
        }
        with patch.object(container_lock, "versions", return_value={"torch": "2.14.1"}):
            text = container_lock.render(report)
            self.assertIn("torch @ https://download.pytorch.org/whl/cpu/", text)
            self.assertIn("--hash=sha256:" + "a" * 64, text)
            report["install"][0]["metadata"]["version"] = "2.14.0+cpu"
            with self.assertRaisesRegex(ValueError, "PDM requires"):
                container_lock.render(report)
            report["install"][0]["metadata"]["version"] = "2.14.1+cpu"
            report["install"][0]["download_info"]["url"] = "https://example.org/torch.whl"
            with self.assertRaisesRegex(ValueError, "CPU wheel index"):
                container_lock.render(report)

    def test_scan_blocks_high_and_critical_even_without_a_fix(self):
        vulnerabilities = [
            {"Severity": "HIGH", "VulnerabilityID": "one", "FixedVersion": ""},
            {"Severity": "CRITICAL", "VulnerabilityID": "two", "FixedVersion": "1.1"},
            {"Severity": "MEDIUM", "VulnerabilityID": "three"},
        ]
        report = {"Results": [{"Vulnerabilities": vulnerabilities}]}
        self.assertEqual(
            [v["VulnerabilityID"] for v in image_scan.findings(report)], ["one", "two"]
        )
        self.assertEqual(image_scan.findings({"Results": [{"Target": "clean"}]}), [])
        with self.assertRaises(ValueError):
            image_scan.findings(json.loads('{"Results": []}'))
