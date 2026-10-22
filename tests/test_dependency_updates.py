"""Dependency selection and update automation cover every committed ecosystem."""

import re
import subprocess
import tomllib
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


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


class DependencyLockPolicyTest(unittest.TestCase):
    def test_lock_command_uses_only_the_relative_five_day_cutoff(self):
        scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pdm"]["scripts"]

        self.assertEqual(scripts["lock"], "pdm lock --exclude-newer 5d")
        self.assertIsNone(re.search(r"20\d\d-\d\d-\d\d|T\d\d:", scripts["lock"]))


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

    def test_compose_uses_only_images_built_in_this_repository(self):
        compose = read_yaml("container/compose.local.yaml")
        images = {service["image"] for service in compose["services"].values()}

        self.assertEqual(images, {"nci-si-admin:local", "nci-si-docs:local", "nci-si-mcp:verified"})
        self.assertNotIn("docker-compose", {entry["package-ecosystem"] for entry in self.entries()})

    def test_python_uses_the_age_checked_pdm_path(self):
        text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

        self.assertNotIn("pip", {entry["package-ecosystem"] for entry in self.entries()})
        self.assertIn("Dependabot cannot update pdm.lock", text)
