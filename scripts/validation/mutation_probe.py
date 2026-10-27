"""Pytest evidence and xdist mutant scheduling; not loaded by ordinary test runs."""

import json
import os
from pathlib import Path

import pytest

CALLS = pytest.StashKey[list[dict]]()


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
        if report.when == "call":
            item.stash.setdefault(CALLS, []).append(row)
        else:
            with Path(destination).open("a") as stream:
                if report.when == "teardown" and CALLS in item.stash:
                    stream.write(json.dumps(call_evidence(item.stash[CALLS])) + "\n")
                stream.write(json.dumps(row) + "\n")
    return report


def call_evidence(rows: list[dict]) -> dict:
    # pytest 9 reports each unittest subtest before the enclosing call completes.
    # Retain one call per item; a non-assertion failure must never become a kill.
    failed = [row for row in rows if row["outcome"] == "failed"]
    if failed:
        return {**failed[0], "assertion": all(row["assertion"] for row in failed)}
    return next((row for row in rows if row["outcome"] == "passed"), rows[0])


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
