"""Pytest evidence and xdist mutant scheduling; not loaded by ordinary test runs."""

import json
import os
from pathlib import Path

import pytest


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    destination = os.environ.get("MUTATION_EVIDENCE")
    if destination:
        assertion = (
            report.when == "call"
            and call.excinfo is not None
            and isinstance(call.excinfo.value, (AssertionError, pytest.fail.Exception))
        )
        row = {
            "node": report.nodeid,
            "phase": report.when,
            "outcome": report.outcome,
            "assertion": assertion,
        }
        with Path(destination).open("a") as stream:
            stream.write(json.dumps(row) + "\n")
    return report


def pytest_collectreport(report):
    destination = os.environ.get("MUTATION_EVIDENCE")
    if destination and report.failed:
        with Path(destination).open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "node": report.nodeid,
                        "phase": "collection",
                        "outcome": "failed",
                        "assertion": False,
                    }
                )
                + "\n"
            )


class MutationItem(pytest.Item):
    def runtest(self):
        from scripts.validation.mutation_gate import execute_job  # noqa: PLC0415

        execute_job(Path(str(self.path)), int(self.name))


class MutationFile(pytest.File):
    def collect(self):
        jobs = json.loads(self.path.read_text())
        for index in range(len(jobs["mutants"])):
            yield MutationItem.from_parent(self, name=str(index))


def pytest_collect_file(file_path, parent):
    if file_path.name == "mutations.json":
        return MutationFile.from_parent(parent, path=file_path)
    return None
