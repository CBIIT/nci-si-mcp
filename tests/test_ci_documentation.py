"""One same-commit site build feeds images; browser caching never omits OS setup."""

import json
import unittest
from pathlib import Path

import yaml

JOBS = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())["jobs"]


class DocumentationWorkflowTest(unittest.TestCase):
    def test_companions_consume_the_always_available_documentation_artifact(self):
        companion = JOBS["companions"]
        self.assertIn("documentation", companion["needs"])
        self.assertNotIn("if", JOBS["documentation"])
        self.assertIn("!cancelled()", companion["if"])
        guard = next(
            step
            for step in companion["steps"]
            if step.get("if") == "needs.documentation.result != 'success'"
        )
        self.assertIn("exit 1", guard["run"])
        commands = "\n".join(step.get("run", "") for step in companion["steps"])
        self.assertNotIn("npm ", commands)
        self.assertIn("scripts.companion_context --site tmp/docs-site", commands)
        self.assertFalse(any("setup-node" in step.get("uses", "") for step in companion["steps"]))
        download = next(
            step["with"]
            for step in companion["steps"]
            if step.get("uses", "").startswith("actions/download-artifact@")
        )
        self.assertEqual(
            download, {"name": "documentation-preview-${{ github.sha }}", "path": "tmp/docs-site"}
        )

    def test_browser_cache_is_versioned_and_system_dependencies_are_always_installed(self):
        steps = JOBS["documentation"]["steps"]
        caches = [step for step in steps if step.get("id") == "playwright-cache"]
        self.assertEqual(len(caches), 1, "Cache only the pinned browser binaries")
        cache = caches[0]
        self.assertTrue(cache["uses"].startswith("actions/cache@"))
        self.assertEqual(cache["with"]["path"], "~/.cache/ms-playwright")
        self.assertEqual(
            cache["with"]["key"],
            "playwright-${{ runner.os }}-${{ runner.arch }}-"
            "${{ hashFiles('docs/site-assets/package-lock.json') }}",
        )
        self.assertNotIn("restore-keys", cache["with"])
        system = next(
            step for step in steps if "playwright install-deps chromium" in step.get("run", "")
        )
        self.assertNotIn("if", system)
        browser = next(
            step for step in steps if "playwright install chromium" in step.get("run", "")
        )
        self.assertEqual(browser["if"], "steps.playwright-cache.outputs.cache-hit != 'true'")

    def test_node_contract_matches_the_ci_major_in_package_and_operator_docs(self):
        package = json.loads(Path("docs/site-assets/package.json").read_text())
        self.assertEqual(package.get("engines", {}).get("node"), ">=24 <25")
        setup = next(
            step for step in JOBS["documentation"]["steps"] if "setup-node@" in step.get("uses", "")
        )
        self.assertEqual(setup["with"]["node-version"], "24")
        lock = json.loads(Path("docs/site-assets/package-lock.json").read_text())
        self.assertEqual(lock["packages"][""]["engines"]["node"], ">=24 <25")
        for path in ("docs/companion-containers.md", "docs/documentation-site.md"):
            self.assertIn("Node.js 24", Path(path).read_text())
