"""CI retains failed-run JUnit evidence without changing producer failure or cancellation."""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
UPLOAD = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"


def report_positions(steps):
    setup = next(
        index for index, step in enumerate(steps) if step.get("uses") == "$/.github/actions/setup"
    )
    producer = next(
        index for index, step in enumerate(steps) if "--junitxml=" in step.get("run", "")
    )
    names = [step.get("name") for step in steps]
    return (
        setup,
        producer,
        names.index("Summarize test results"),
        names.index("Upload test results"),
    )


class TestReportWorkflowTest(unittest.TestCase):
    def test_report_steps_have_exact_paths_conditions_and_artifact_names(self):
        jobs = yaml.safe_load(ROOT.joinpath(".github/workflows/ci.yml").read_text())["jobs"]
        for job, path, artifact, title in (
            ("test", "tmp/test-results/unit.xml", "unit-test-results", "Unit tests"),
            (
                "selftest",
                "acceptance/tmp/test-results/selftest.xml",
                "selftest-results-${{ strategy.job-index }}",
                "Harness self-tests",
            ),
        ):
            with self.subTest(job=job):
                steps = jobs[job]["steps"]
                setup, producer, summary, upload = report_positions(steps)
                self.assertLess(setup, producer)
                self.assertEqual((summary, upload), (producer + 1, producer + 2))
                reports = [
                    step
                    for step in jobs[job]["steps"]
                    if step.get("name") in {"Summarize test results", "Upload test results"}
                ]
                self.assertEqual(
                    reports,
                    [
                        {
                            "name": "Summarize test results",
                            "if": "${{ !cancelled() }}",
                            "run": f'pdm run python scripts/junit_summary.py {path} "{title}" '
                            '>> "$GITHUB_STEP_SUMMARY"',
                        },
                        {
                            "name": "Upload test results",
                            "if": "${{ !cancelled() }}",
                            "uses": UPLOAD,
                            "with": {
                                "name": artifact,
                                "path": path,
                                "if-no-files-found": "error",
                                "retention-days": 30,
                            },
                        },
                    ],
                )

    def test_unit_producer_still_fails_normally_and_writes_junit(self):
        jobs = yaml.safe_load(ROOT.joinpath(".github/workflows/ci.yml").read_text())["jobs"]
        producer = next(
            step
            for step in jobs["test"]["steps"]
            if step.get("name") == "Test, including recorded retrieval evaluation"
        )
        self.assertEqual(
            producer,
            {
                "name": "Test, including recorded retrieval evaluation",
                "run": "pdm run test --junitxml=tmp/test-results/unit.xml",
            },
        )
