"""One same-commit site build feeds images; browser caching never omits OS setup."""

import json
import unittest
from pathlib import Path

import yaml

JOBS = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())["jobs"]


class DocumentationWorkflowTest(unittest.TestCase):
    def test_companions_consume_the_always_available_documentation_artifact(self):
        companion = JOBS["companions"]
        self.assertEqual(companion["needs"], ["changes", "documentation"])
        self.assertNotIn("if", JOBS["documentation"])
        self.assertEqual(
            companion["if"],
            "${{ !cancelled() && (github.event_name != 'pull_request' || "
            "needs.changes.outputs.companions == 'true') }}",
        )
        guard = next(
            step
            for step in companion["steps"]
            if step.get("if") == "needs.documentation.result != 'success'"
        )
        self.assertEqual(
            guard["run"],
            'echo "::error::The documentation artifact producer did not succeed."\nexit 1\n',
        )

    def test_companions_reuse_site_without_a_second_build(self):
        companion = JOBS["companions"]
        commands = "\n".join(step.get("run", "") for step in companion["steps"])
        self.assertNotIn("npm ", commands)
        self.assertNotIn("docs-build", commands)
        reuse = next(
            step["run"]
            for step in companion["steps"]
            if "scripts.companion_context" in step.get("run", "")
        )
        self.assertEqual(reuse, "pdm run python -m scripts.companion_context --site tmp/docs-site")
        self.assertFalse(any("setup-node" in step.get("uses", "") for step in companion["steps"]))
        download = next(
            step["with"]
            for step in companion["steps"]
            if step.get("uses", "").startswith("actions/download-artifact@")
        )
        self.assertEqual(
            download, {"name": "documentation-preview-${{ github.sha }}", "path": "tmp/docs-site"}
        )

    def test_companion_steps_cannot_ignore_a_missing_artifact(self):
        for step in JOBS["companions"]["steps"]:
            self.assertNotIn("continue-on-error", step)

    def test_documentation_upload_is_the_same_run_site_and_nothing_else(self):
        upload = next(
            step["with"]
            for step in JOBS["documentation"]["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        self.assertEqual(
            upload,
            {
                "name": "documentation-preview-${{ github.sha }}",
                "path": "tmp/docs-site/",
                "if-no-files-found": "error",
                "retention-days": 30,
            },
        )
        download = next(
            step["with"]
            for step in JOBS["companions"]["steps"]
            if step.get("uses", "").startswith("actions/download-artifact@")
        )
        self.assertEqual(upload["name"], download["name"])

    def test_browser_cache_is_versioned_and_system_dependencies_are_always_installed(self):
        steps = JOBS["documentation"]["steps"]
        caches = [step for step in steps if step.get("id") == "playwright-cache"]
        self.assertEqual(len(caches), 1, "Cache only the pinned browser binaries")
        cache = caches[0]
        self.assertEqual(cache["uses"], "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9")
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
        self.assertEqual(
            system["run"], "npm exec --prefix docs/site-assets -- playwright install-deps chromium"
        )
        browser = next(
            step for step in steps if "playwright install chromium" in step.get("run", "")
        )
        self.assertEqual(browser["if"], "steps.playwright-cache.outputs.cache-hit != 'true'")
        self.assertEqual(
            browser["run"], "npm exec --prefix docs/site-assets -- playwright install chromium"
        )

    def test_node_contract_matches_the_ci_major_in_package_and_operator_docs(self):
        package = json.loads(Path("docs/site-assets/package.json").read_text())
        self.assertEqual(package.get("engines", {}).get("node"), ">=24 <25")
        versions = {
            step["with"]["node-version"]
            for job in JOBS.values()
            for step in job["steps"]
            if "setup-node@" in step.get("uses", "")
        }
        self.assertEqual(versions, {"24"})
        lock = json.loads(Path("docs/site-assets/package-lock.json").read_text())
        self.assertEqual(lock["packages"][""]["engines"]["node"], ">=24 <25")
        for path in ("docs/companion-containers.md", "docs/documentation-site.md"):
            self.assertIn("Node.js 24", Path(path).read_text())
