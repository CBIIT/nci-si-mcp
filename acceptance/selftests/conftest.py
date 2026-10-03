"""What the self-tests that run the suite against compliant_server.py share: a copy of the
suite and its fixture set, and the outcome of each test of a run."""

import json
import sys
from pathlib import Path

import pytest

SUITE = Path(__file__).parent.parent / "tests"
COMPLIANT_SERVER = Path(__file__).parent / "compliant_server.py"
VERSION = {"surface": "evs", "method": "GET", "path": "/api/v1/version"}
LICENCE_KEY = "selftest-licence-key"
CURRENT = {"status": 200, "body": {"version": "26.09d"}}
# The answers to the compliant server's requests: EVS's version, which names the release it
# serves, in each scenario the suite uses; and the licensed concept, only with the key. The
# terminology listing is read by the suite itself (X-17), never asked.
ANSWERS = {
    "recorded/evs/terminologies.json": {
        "request": VERSION | {"path": "/api/v1/metadata/terminologies"},
        "response": {
            "status": 200,
            "body": [
                {"terminology": "ncit", "version": "26.09d"},
                {"terminology": "ncit", "version": "26.08e"},
                {
                    "terminology": "mdr",
                    "version": "29_0",
                    "metadata": {"licenseText": "MedDRA is licensed to its subscribers."},
                },
            ],
        },
    },
    "crafted/version.json": {"response": CURRENT},
    "scenarios/retired/with-replacement/version.json": {"response": CURRENT},
    "scenarios/traversal/deep-fanout/version.json": {"response": CURRENT},
    "scenarios/release/unknown/version.json": {"response": {"status": 404, "body": {}}},
    "scenarios/release/mismatch/version.json": {
        "response": {"status": 200, "body": {"version": "26.08e"}}
    },
    "scenarios/upstream/unavailable/version.json": {"response": {"status": 503, "body": {}}},
    "scenarios/upstream/rate-limited/version.json": {
        "responses": [{"status": 429, "headers": {"Retry-After": "1"}, "body": {}}, CURRENT]
    },
    "scenarios/license/restricted/concept.json": {
        "request": VERSION
        | {
            "path": "/api/v1/concept/mdr_29_0/10000000",
            "headers": {"X-EVSRESTAPI-License-Key": LICENCE_KEY},
        },
        "response": {"status": 200, "body": {}},
    },
    "scenarios/license/restricted/refused.json": {
        "request": VERSION | {"path": "/api/v1/concept/mdr_29_0/10000000"},
        "response": {"status": 403, "body": {}},
    },
}


def _fixture(path: Path, answer: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"kind": "crafted", "requirement": "self-test", "request": VERSION}
    path.write_text(json.dumps(document | answer), encoding="utf-8")


@pytest.fixture
def compliant(pytester, monkeypatch):
    """The suite's tests against the compliant server, with fixtures for its requests."""

    fixtures = pytester.mkdir("fixtures")
    # The prepare step indexes nothing here: no tool of the stub searches an index.
    manifest = "evs:\n  release: ncit_26.09d\nrecord:\n  concepts: {}\n"
    (fixtures / "manifest.yaml").write_text(manifest, encoding="utf-8")
    for path, answer in ANSWERS.items():
        _fixture(fixtures / path, answer)
    settings = fixtures / "scenarios" / "license" / "restricted" / "settings.json"
    settings.write_text(json.dumps({"NCI_SI_EVS_LICENSE_KEY": LICENCE_KEY}), encoding="utf-8")
    tests = pytester.mkdir("tests")
    for name in ("conftest.py", "test_protocol.py", "test_crosscutting.py", "calls.yaml"):
        (tests / name).write_text((SUITE / name).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "fixture")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_SERVER", f"{sys.executable} {COMPLIANT_SERVER}")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_PROFILE", "evs")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_PREPARE", "true")
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
