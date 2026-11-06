"""Selected live provenance survives strict records without inventing historical data."""

import hashlib
import json
import os
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_report import live_test, run

from conftest import STAND_IN
from nci_si_acceptance import environments
from nci_si_acceptance.report import Collector, NativeReport, ReportFieldError, complete, main

pytest_plugins = ["pytester"]

ENVIRONMENT = {
    "name": "evs-only",
    "origins": {
        "evs": "https://api-evsrest.nci.nih.gov",
        "evs-fhir": "https://api-evsrest.nci.nih.gov/fhir/r4",
        "cadsr": None,
        "cadsr-ftp": None,
        "cadsr-contracts": None,
        "ssis": None,
        "ssis-sparql": None,
    },
    "policy_sha256": "a" * 64,
}


def native():
    return {
        "mode": "live",
        "transport": "stdio",
        "failed_gates": [],
        "unrun_gates": [],
        "tools_list_bytes": None,
        "tools": {},
        "tests": {},
        "suite": {"version": "1", "fixture_set": "recorded", "digest": "b" * 64},
    }


def test_native_roundtrip_and_historical_omission():
    old = native()
    assert NativeReport.from_dict(old).to_dict() == old


@pytest.mark.parametrize("worker", [None, {}, ENVIRONMENT | {"policy_sha256": "b" * 64}])
def test_current_workers_cannot_omit_or_conflict_with_controller_selection(worker):
    collector = Collector()
    collector.environment = deepcopy(ENVIRONMENT)
    output = {} if worker is None else {"nci_si_environment": worker}
    collector.pytest_testnodedown(SimpleNamespace(workeroutput=output), None)
    with pytest.raises(ValueError, match="environment"):
        collector.report("live")


@pytest.mark.parametrize("workers", [0, 2])
def test_native_producer_preserves_selection(compliant, monkeypatch, workers):
    for key in [key for key in os.environ if key.startswith("NCI_SI_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "live")
    monkeypatch.setenv(environments.VARIABLE, "evs-only")
    plugin = compliant.path / f"{STAND_IN}.py"
    plugin.write_text(
        "import os\nfor key in list(os.environ):\n"
        "    if key.startswith('PYTHON') or key.lower().endswith('_proxy'): os.environ.pop(key)\n"
        + plugin.read_text()
    )
    (compliant.path / "tests/test_selected.py").write_text(
        "import pytest\n@pytest.mark.live_capable\n@pytest.mark.gate\n"
        "def test_selected(target):\n    assert target.environment.name == 'evs-only'\n"
    )
    output = compliant.path / "native.json"
    result = compliant.runpytest_subprocess(
        "tests/test_selected.py", "-p", STAND_IN, "-n", str(workers), f"--report={output}"
    )
    result.assert_outcomes(passed=1)
    produced = json.loads(output.read_text())
    raw_policy = environments.POLICY.read_bytes()
    expected = ENVIRONMENT | {"policy_sha256": hashlib.sha256(raw_policy).hexdigest()}
    assert produced.get("environment") == expected
    assert NativeReport.from_dict(produced).to_dict() == produced


@pytest.mark.parametrize(
    "change",
    [
        {"name": None},
        {"policy_sha256": "private-marker"},
        {"origins": {"evs": "https://private-marker@example.com/"}},
        {"origins": ENVIRONMENT["origins"] | {"evs": "https://private-marker@example.com/"}},
        {"private-marker": "private-marker"},
    ],
)
def test_invalid_environment_records_fail_at_the_sanitized_field_boundary(change):
    with pytest.raises(ReportFieldError) as error:
        NativeReport.from_dict(native() | {"environment": ENVIRONMENT | change})
    assert str(error.value) == "Invalid acceptance field: environment"
    assert "private-marker" not in str(error.value)


def test_crashed_fixture_worker_retains_its_incomplete_report():
    collector = Collector()
    collector.environment = None
    collector.pytest_testnodedown(SimpleNamespace(workeroutput={}), "worker exited")
    collector.tests["test_fixture"] = {
        "tool": "get_form",
        "gate": False,
        "outcome": "passed",
        "unmatched": [],
    }
    collector.run = {
        "exit_status": 1,
        "selected": 1,
        "finished": 1,
        "worker_crashes": collector.worker_crashes,
    }
    try:
        emitted = collector.report("fixture")
    except ReportFieldError:
        emitted = None
    assert emitted is not None, "fixture worker crash must retain its report"
    assert emitted["run"] == {"exit_status": 1, "selected": 1, "finished": 1, "worker_crashes": 1}
    assert "environment" not in emitted
    assert not complete(emitted)


@pytest.mark.parametrize("selected,reported", [(ENVIRONMENT, None), (None, ENVIRONMENT)])
def test_crash_never_excuses_unknown_live_or_unexpected_fixture_provenance(selected, reported):
    collector = Collector()
    collector.environment = selected
    output = {} if reported is None else {"nci_si_environment": reported}
    collector.pytest_testnodedown(SimpleNamespace(workeroutput=output), "worker exited")
    with pytest.raises(ReportFieldError, match="environment"):
        collector.report("live" if selected else "fixture")


def test_combined_renderer_labels_only_the_supplied_live_environment(tmp_path, capsys):
    fixture = run({"get_form": "PASS"}, {"test.py::one": live_test("get_form", "passed")})
    live = fixture | {"mode": "live", "environment": deepcopy(ENVIRONMENT)}
    first, second = tmp_path / "fixture.json", tmp_path / "synthetic-live.json"
    first.write_text(json.dumps(fixture))
    second.write_text(json.dumps(live))
    assert main([str(first), "--live", str(second)]) == 0
    displayed = capsys.readouterr().out
    assert "Live environment" in displayed
    assert "evs-only" in displayed
    assert ENVIRONMENT["policy_sha256"] in displayed
    assert "environment" not in json.loads(first.read_text())
