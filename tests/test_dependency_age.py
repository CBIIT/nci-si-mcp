"""Every registry package in every committed lock has cleared the rolling age window."""

import importlib.util
import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dependency_age.py"
NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def checker():
    if not SCRIPT.is_file():
        raise AssertionError("scripts/dependency_age.py must provide the dependency-age gate")
    spec = importlib.util.spec_from_file_location("dependency_age", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("scripts/dependency_age.py must be loadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pypi(uploaded):
    return {"urls": [{"upload_time_iso_8601": uploaded}]}


class DependencyAgeTest(unittest.TestCase):
    def test_five_day_boundary_is_inclusive(self):
        module = checker()
        packages = [module.Package("pypi", "old", "1"), module.Package("pypi", "new", "2")]
        metadata = {
            "https://pypi.org/pypi/old/1/json": pypi((NOW - timedelta(days=5)).isoformat()),
            "https://pypi.org/pypi/new/2/json": pypi(
                (NOW - timedelta(days=5) + timedelta(microseconds=1)).isoformat()
            ),
        }

        self.assertEqual(
            module.age_findings(packages, NOW, metadata.__getitem__),
            ["pypi:new==2 was uploaded inside the five-day window"],
        )

    def test_all_committed_lock_formats_are_read_and_duplicates_are_checked_once(self):
        module = checker()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pdm.lock").write_text(
                '[[package]]\nname = "Shared_Name"\nversion = "1.0"\n'
                '[[package]]\nname = "shared-name"\nversion = "1.0"\n',
                encoding="utf-8",
            )
            container = root / "container"
            container.mkdir()
            requirement = "other_pkg==2.0 \\\n+    --hash=sha256:a\n"
            for name in ("requirements-amd64.txt", "companion-requirements.txt"):
                (container / name).write_text(requirement, encoding="utf-8")
            assets = root / "docs" / "site-assets"
            assets.mkdir(parents=True)
            (assets / "package-lock.json").write_text(
                json.dumps(
                    {
                        "packages": {
                            "": {"name": "root"},
                            "node_modules/@scope/tool": {"version": "3.0"},
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                module.locked_packages(root),
                [
                    module.Package("npm", "@scope/tool", "3.0"),
                    module.Package("pypi", "other-pkg", "2.0"),
                    module.Package("pypi", "shared-name", "1.0"),
                ],
            )

    def test_pypi_and_npm_use_their_registry_metadata(self):
        module = checker()
        packages = [
            module.Package("pypi", "demo", "1.0"),
            module.Package("npm", "@scope/tool", "2.0"),
        ]
        seen = []

        def fetch(url):
            seen.append(url)
            if "pypi.org" in url:
                return pypi("2026-10-01T00:00:00Z")
            return {"time": {"2.0": "2026-10-01T00:00:00Z"}}

        self.assertEqual(module.age_findings(packages, NOW, fetch), [])
        self.assertEqual(
            seen,
            [
                "https://pypi.org/pypi/demo/1.0/json",
                "https://registry.npmjs.org/%40scope%2Ftool",
            ],
        )

    def test_unverifiable_release_fails_closed(self):
        module = checker()
        cases = (
            ({"urls": []}, "has no distribution upload time"),
            ({"urls": [{"upload_time_iso_8601": "bad"}]}, "has an invalid upload time"),
            ({"time": {}}, "has no release upload time"),
        )
        packages = (
            module.Package("pypi", "empty", "1"),
            module.Package("pypi", "bad", "1"),
            module.Package("npm", "missing", "1"),
        )
        for package, (metadata, reason) in zip(packages, cases, strict=True):
            with self.subTest(package=package.name):
                self.assertEqual(
                    module.age_findings([package], NOW, lambda _url, metadata=metadata: metadata),
                    [f"{package.ecosystem}:{package.name}==1 {reason}"],
                )

    def test_fetch_failure_is_a_finding_without_package_index_content(self):
        module = checker()

        def unavailable(_url):
            raise OSError("PRIVATE RESPONSE")

        self.assertEqual(
            module.age_findings([module.Package("pypi", "demo", "1")], NOW, unavailable),
            ["pypi:demo==1 metadata could not be verified (OSError)"],
        )
