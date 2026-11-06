"""One same-commit site build feeds images; cold browser verification remains fail-closed."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

JOBS = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())["jobs"]


def timed_command(command: str, label: str) -> str:
    """Independent expected shell contract: preserve first failure before reporting errors."""
    return (
        "started=$SECONDS\n"
        "set +e\n"
        f"{{ time {command}; }} > tmp/documentation-ci/browser-{label}.log 2>&1\n"
        "gate_status=$?\n"
        f"cat tmp/documentation-ci/browser-{label}.log\n"
        "display_status=$?\n"
        f"printf '{label} exit=%d elapsed_seconds=%d\\n' "
        '"$gate_status" "$((SECONDS - started))" >> tmp/documentation-ci/phase-timings.txt\n'
        "timing_status=$?\n"
        'if [ "$gate_status" -ne 0 ]; then exit "$gate_status"; fi\n'
        'if [ "$display_status" -ne 0 ]; then exit "$display_status"; fi\n'
        'exit "$timing_status"\n'
    )


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
            if step.get("with", {}).get("name") == "documentation-preview-${{ github.sha }}"
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

    def test_cold_browser_directory_and_job_budget_are_explicit(self):
        job = JOBS["documentation"]
        self.assertEqual(job["timeout-minutes"], 15)
        self.assertEqual(
            job.get("env"),
            {
                "PLAYWRIGHT_BROWSERS_PATH": "${{ runner.temp }}/nci-si-documentation-browsers-"
                "${{ github.run_id }}-${{ github.run_attempt }}",
                "PLAYWRIGHT_JUNIT_OUTPUT_FILE": (
                    "${{ github.workspace }}/tmp/documentation-ci/browser-junit.xml"
                ),
            },
        )
        self.assertEqual(
            [step["timeout-minutes"] for step in job["steps"]],
            [1, 2, 1, 1, 1, 2, 2, 1, 1, 1],
        )
        self.assertEqual(
            [step.get("name") for step in job["steps"]],
            [
                None,
                None,
                None,
                "Prepare cold browser verification",
                "Build validated site",
                "Install pinned Chromium",
                "Run browser journeys",
                "Record browser identity",
                "Retain documentation diagnostics",
                "Upload validated site",
            ],
        )
        for step in job["steps"]:
            self.assertNotIn("continue-on-error", step)

    def test_browser_install_and_test_are_unconditional(self):
        steps = JOBS["documentation"]["steps"]
        self.assertEqual(
            [step.get("uses") for step in steps],
            [
                "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                "$/.github/actions/setup",
                "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1",
                None,
                None,
                None,
                None,
                None,
                "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
                "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
            ],
        )
        for name in ("Install pinned Chromium", "Run browser journeys"):
            step = next(step for step in steps if step.get("name") == name)
            self.assertNotIn("if", step)
            self.assertEqual(step.get("shell"), "bash")

    def test_diagnostics_cannot_be_consumed_as_a_usable_site(self):
        steps = JOBS["documentation"]["steps"]
        diagnostics = next(
            (step for step in steps if step.get("name") == "Retain documentation diagnostics"),
            None,
        )
        self.assertIsNotNone(diagnostics, "Failure diagnostics need a separate bounded upload")
        self.assertEqual(
            diagnostics,
            {
                "name": "Retain documentation diagnostics",
                "uses": "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
                "if": "${{ always() }}",
                "timeout-minutes": 1,
                "with": {
                    "name": "documentation-diagnostics-${{ github.sha }}",
                    "path": "tmp/documentation-ci/identity.json\n"
                    "tmp/documentation-ci/phase-timings.txt\n"
                    "tmp/documentation-ci/browser-install.log\n"
                    "tmp/documentation-ci/browser-test.log\n"
                    "tmp/documentation-ci/browser-junit.xml\n",
                    "if-no-files-found": "warn",
                    "retention-days": 30,
                },
            },
        )
        self.assertEqual(steps[-1].get("if"), "${{ success() }}")

    def test_actual_timing_wrappers_keep_command_and_reporting_failures_visible(self):
        cases = {
            "Install pinned Chromium": (
                "npm exec --prefix docs/site-assets -- playwright install chromium",
                "install",
            ),
            "Run browser journeys": (
                "npm run test:browser --prefix docs/site-assets -- "
                "--global-timeout=90000 --reporter=list,junit",
                "test",
            ),
        }
        for name, (command, label) in cases.items():
            actual = next(
                (
                    step.get("run", "")
                    for step in JOBS["documentation"]["steps"]
                    if step.get("name") == name
                ),
                "",
            )
            self.assertEqual(actual, timed_command(command, label))
            for code, broken_report in ((0, False), (7, False), (0, True), (7, True)):
                with self.subTest(name=name, code=code, broken_report=broken_report):
                    self.check_wrapper(actual, code, broken_report)

    def check_wrapper(self, script: str, code: int, broken_report: bool) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / "tmp/documentation-ci"
            logs.mkdir(parents=True)
            binary = root / "npm"
            binary.write_text(f"#!/bin/bash\nprintf 'captured output\\n'\nexit {code}\n")
            binary.chmod(0o700)
            if broken_report:
                (logs / "phase-timings.txt").mkdir()
            result = subprocess.run(  # noqa: S603 - exact repository-owned shell contract
                ["/bin/bash", "-euo", "pipefail", "-c", script],
                cwd=root,
                env=os.environ | {"PATH": f"{root}:" + os.environ["PATH"]},
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, code or int(broken_report), result.stderr)
            self.assertEqual(len(list(logs.glob("browser-*.log"))), 1)
            self.assertIn("captured output", next(logs.glob("browser-*.log")).read_text())

    def test_failed_documentation_guard_really_exits_nonzero(self):
        guard = next(
            step
            for step in JOBS["companions"]["steps"]
            if step.get("if") == "needs.documentation.result != 'success'"
        )
        result = subprocess.run(  # noqa: S603 - exact repository-owned shell contract
            ["/bin/bash", "-euo", "pipefail", "-c", guard["run"]],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stdout, "::error::The documentation artifact producer did not succeed.\n"
        )

    def test_node_contract_matches_the_ci_major_in_package_and_operator_docs(self):
        package = json.loads(Path("docs/site-assets/package.json").read_text())
        self.assertEqual(package.get("engines", {}).get("node"), ">=24")
        versions = {
            step["with"]["node-version"]
            for job in JOBS.values()
            for step in job["steps"]
            if "setup-node@" in step.get("uses", "")
        }
        self.assertEqual(versions, {"24"})
        lock = json.loads(Path("docs/site-assets/package-lock.json").read_text())
        self.assertEqual(lock["packages"][""]["engines"]["node"], ">=24")
        for path in ("QUICKSTART.md", "docs/documentation-site.md"):
            self.assertIn("Node.js 24 or newer", Path(path).read_text())
