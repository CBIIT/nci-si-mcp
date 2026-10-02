"""What the self-tests that run the suite against compliant_server.py share: a copy of the
suite and its fixture set, and the outcome of each test of a run."""

import json
import sys
from pathlib import Path

import pytest

SUITE = Path(__file__).parent.parent / "tests"
COMPLIANT_SERVER = Path(__file__).parent / "compliant_server.py"
VERSION = {"surface": "evs", "method": "GET", "path": "/api/v1/version"}
# Where the server's one request is answered: everywhere, and with 503 while the platform is
# unavailable.
ANSWERS = {
    "crafted/version.json": 200,
    "scenarios/upstream/unavailable/version.json": 503,
    "scenarios/retired/with-replacement/version.json": 200,
}


def _fixture(path: Path, status: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"kind": "crafted", "requirement": "self-test", "request": VERSION}
    path.write_text(json.dumps(document | {"response": {"status": status, "body": {}}}))


@pytest.fixture
def compliant(pytester, monkeypatch):
    """The suite's tests against the compliant server, with fixtures for its one request."""

    fixtures = pytester.mkdir("fixtures")
    (fixtures / "manifest.yaml").write_text("evs:\n  release: ncit_26.09d\n", encoding="utf-8")
    for path, status in ANSWERS.items():
        _fixture(fixtures / path, status)
    tests = pytester.mkdir("tests")
    for name in ("conftest.py", "test_protocol.py", "test_crosscutting.py", "calls.yaml"):
        (tests / name).write_text((SUITE / name).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "fixture")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_SERVER", f"{sys.executable} {COMPLIANT_SERVER}")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_PROFILE", "evs")
    monkeypatch.delenv("COMPLIANT_SERVER_DEFECT", raising=False)
    return pytester


@pytest.fixture
def outcomes(compliant):
    """Run the suite's tests in a file, or those `-k` selects, and return each outcome by name."""

    def run(*arguments):
        report = compliant.path / "report.json"
        compliant.runpytest_subprocess(*arguments, "-p", "no:cacheprovider", f"--report={report}")
        tests = json.loads(report.read_text(encoding="utf-8"))["tests"]
        return {nodeid.partition("::")[2]: test["outcome"] for nodeid, test in tests.items()}

    return run
