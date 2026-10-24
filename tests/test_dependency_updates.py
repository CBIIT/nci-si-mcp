"""Dependency selection and update automation cover every committed ecosystem."""

import importlib.util
import re
import subprocess
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCK_SCRIPT = ROOT / "scripts" / "dependency_lock.py"


def read_yaml(name):
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def tracked(pattern):
    result = subprocess.run(  # noqa: S603 - Git lists this repository's tracked manifests.
        ["git", "ls-files", pattern],  # noqa: S607 - Git is the repository boundary.
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [ROOT / name for name in result.stdout.splitlines()]


def lock_script():
    if not LOCK_SCRIPT.is_file():
        raise AssertionError("scripts/dependency_lock.py must own the lock command")
    spec = importlib.util.spec_from_file_location("dependency_lock", LOCK_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("scripts/dependency_lock.py must be loadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compose_manifests(root=ROOT):
    patterns = ("**/*compose*.yaml", "**/*compose*.yml")
    ignored = {".git", ".venv", "tmp"}
    return {
        path
        for pattern in patterns
        for path in root.glob(pattern)
        if not ignored.intersection(path.parts)
    }


def registry_images(path):
    compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    images = {
        service["image"] for service in compose.get("services", {}).values() if "image" in service
    }
    local = {"nci-si-admin:local", "nci-si-docs:local", "nci-si-mcp:verified"}
    return images - local


class DependencyLockPolicyTest(unittest.TestCase):
    def test_lock_command_uses_only_the_relative_five_day_cutoff(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        scripts = project["tool"]["pdm"]["scripts"]

        self.assertEqual(scripts["lock"], "python -m scripts.dependency_lock")
        self.assertIsNone(re.search(r"20\d\d-\d\d-\d\d|T\d\d:", scripts["lock"]))
        self.assertEqual(
            project["tool"]["nci-si"]["dependency-age"]["exceptions"],
            {
                "certifi": {
                    "version": "2026.10.19",
                    "advisory": "GHSA-6q48-6c7w-9gw2",
                },
                "setuptools": {
                    "version": "84.2.1",
                    "advisory": "GHSA-xmj3-9gf7-h52w",
                },
            },
        )

        def metadata(url):
            uploaded = "2026-10-20T19:01:00Z" if "setuptools" in url else "2026-10-20T02:24:00Z"
            return {"urls": [{"upload_time_iso_8601": uploaded}]}

        self.assertEqual(
            lock_script().command(ROOT, metadata),
            [
                "pdm",
                "lock",
                "--exclude-newer",
                "5d",
                "--exclude-newer-override",
                "certifi=2026-10-20T02:24:00+00:00",
                "--exclude-newer-override",
                "setuptools=2026-10-20T19:01:00+00:00",
            ],
        )

    def test_lock_refuses_unverifiable_exception_upload_times(self):
        module = lock_script()
        with self.assertRaisesRegex(ValueError, "certifi.*upload time"):
            module.command(ROOT, lambda _: {"urls": []})


class DependencyAuditWorkflowTest(unittest.TestCase):
    def test_audit_checks_age_then_vulnerabilities(self):
        steps = read_yaml(".github/workflows/audit.yml")["jobs"]["pip-audit"]["steps"]
        runs = [step for step in steps if "run" in step]

        self.assertEqual(
            runs,
            [
                {
                    "name": "Reject dependencies released inside five days",
                    "run": "pdm run python scripts/dependency_age.py",
                },
                {
                    "name": "Audit the runtime dependencies",
                    "run": (
                        "pdm export -G server -G embeddings --prod --without-hashes "
                        '-o "$RUNNER_TEMP/requirements.txt"\n'
                        'pdm run pip-audit -r "$RUNNER_TEMP/requirements.txt" '
                        "--no-deps --disable-pip --strict\n"
                    ),
                },
            ],
        )


class DependabotTest(unittest.TestCase):
    def test_new_compose_files_cannot_escape_registry_image_discovery(self):
        with TemporaryDirectory(prefix="tmp") as directory:
            root = Path(directory)
            root = root / "tmp"
            root.mkdir()
            manifest = root / "compose.new.yml"
            manifest.write_text("services:\n  app:\n    image: nci-si-other:latest\n")

            self.assertEqual(compose_manifests(root), {manifest})
            self.assertEqual(registry_images(manifest), {"nci-si-other:latest"})

    def entries(self):
        return read_yaml(".github/dependabot.yml")["updates"]

    def test_every_update_entry_has_the_exact_policy(self):
        expected = {
            ("github-actions", "/"): "actions",
            ("docker", "/"): "docker",
            ("docker", "/container"): "docker",
            ("npm", "/docs/site-assets"): "npm",
        }
        actual = {}
        for entry in self.entries():
            key = (entry["package-ecosystem"], entry["directory"])
            (group,) = entry["groups"]
            self.assertEqual(
                {k: entry[k] for k in ("schedule", "cooldown", "groups", "commit-message")},
                {
                    "schedule": {"interval": "weekly"},
                    "cooldown": {"default-days": 7},
                    "groups": {group: {"patterns": ["*"]}},
                    "commit-message": {"prefix": "ci"},
                },
            )
            actual[key] = group
        self.assertEqual(actual, expected)

    def test_every_dockerfile_directory_is_covered(self):
        entries = {(row["package-ecosystem"], row["directory"]) for row in self.entries()}
        docker_directories = {
            "/" + str(path.parent.relative_to(ROOT)).replace(".", "").strip("/")
            for path in tracked("*Dockerfile*")
        }
        docker_directories = {directory or "/" for directory in docker_directories}

        self.assertEqual(
            docker_directories,
            {directory for kind, directory in entries if kind == "docker"},
        )

    def test_every_npm_lock_directory_is_covered(self):
        entries = {(row["package-ecosystem"], row["directory"]) for row in self.entries()}
        npm_directories = {
            "/" + str(path.parent.relative_to(ROOT)) for path in tracked("*package-lock.json")
        }

        self.assertEqual(
            npm_directories,
            {directory for kind, directory in entries if kind == "npm"},
        )

    def test_every_compose_manifest_is_local_or_covered(self):
        entries = {(row["package-ecosystem"], row["directory"]) for row in self.entries()}
        manifests = compose_manifests()
        self.assertTrue(manifests)
        for path in manifests:
            with self.subTest(path=path.relative_to(ROOT)):
                images = registry_images(path)
                directory = "/" + str(path.parent.relative_to(ROOT))
                self.assertTrue(
                    not images or ("docker-compose", directory) in entries,
                    f"{path.relative_to(ROOT)} registry images: {sorted(images)}",
                )

    def test_python_uses_the_age_checked_pdm_path(self):
        text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

        self.assertNotIn("pip", {entry["package-ecosystem"] for entry in self.entries()})
        self.assertIn("Dependabot cannot update pdm.lock", text)
