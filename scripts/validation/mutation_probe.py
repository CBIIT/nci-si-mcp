"""Pytest evidence and xdist mutant scheduling; not loaded by ordinary test runs."""

import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

CALLS = pytest.StashKey[list[dict]]()
DESTINATION: str | None = None


@contextmanager
def child_context(item, phase):
    # Each xdist worker has its own environment; subprocess coverage reads the
    # static context at startup, while pytest-cov labels this interpreter dynamically.
    name = "MUTATION_COVERAGE_CONTEXT"
    previous = os.environ.get(name)
    serialized = os.environ.get("COVERAGE_PROCESS_CONFIG")
    # coverage 7.16 passes an already-expanded config to subprocesses. Prefer
    # rereading our config so each child expands this phase's context instead.
    if os.environ.get("COVERAGE_PROCESS_START"):
        os.environ.pop("COVERAGE_PROCESS_CONFIG", None)
    os.environ[name] = f"{item.nodeid}|{phase}"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
        if serialized is not None:
            os.environ["COVERAGE_PROCESS_CONFIG"] = serialized


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item):
    with child_context(item, "setup"):
        return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    with child_context(item, "run"):
        return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item):
    with child_context(item, "teardown"):
        return (yield)


def pytest_configure(config):
    global DESTINATION  # noqa: PLW0603 - one pytest invocation owns the process-local sink
    # Consume the launch-only variable: arbitrary child pytest invocations must not
    # inherit this run's destination. Only explicit xdist workers share its sink.
    destination = os.environ.pop("MUTATION_EVIDENCE", None)
    DESTINATION = getattr(config, "workerinput", {}).get("mutation_evidence", destination)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    node.workerinput["mutation_evidence"] = DESTINATION


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    destination = DESTINATION
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
    # Retain one call per item; mixed failures are error kills, not assertion kills.
    failed = [row for row in rows if row["outcome"] == "failed"]
    if failed:
        return {**failed[0], "assertion": all(row["assertion"] for row in failed)}
    return next((row for row in rows if row["outcome"] == "passed"), rows[0])


def pytest_collectreport(report):
    destination = DESTINATION
    if destination and report.failed:
        with Path(destination).open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "node": report.nodeid,
                        "phase": "collection",
                        "outcome": "failed",
                        "assertion": False,
                        **collection_traceback(report),
                    }
                )
                + "\n"
            )


def collection_traceback(report) -> dict:
    representation = getattr(report, "longrepr", None)
    traceback = getattr(representation, "reprtraceback", None)
    if traceback is None:
        return {}
    paths = [
        str(Path(entry.reprfileloc.path).resolve())
        for entry in traceback.reprentries
        if getattr(entry, "reprfileloc", None) is not None
    ]
    crash = getattr(representation, "reprcrash", None)
    return {"traceback_paths": paths, "exception": getattr(crash, "message", "")}


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
