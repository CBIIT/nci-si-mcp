"""Selected live provenance survives strict records without inventing historical data."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from nci_si_acceptance.report import Collector, NativeReport, ReportFieldError, complete

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
    current = old | {"environment": deepcopy(ENVIRONMENT)}
    rejected = None
    try:
        actual = NativeReport.from_dict(current).to_dict()
    except ReportFieldError as error:
        rejected = str(error)
        actual = None
    assert rejected is None, rejected
    assert actual == current
    assert "environment" not in NativeReport.from_dict(old).to_dict()


@pytest.mark.parametrize("worker", [None, {}, ENVIRONMENT | {"policy_sha256": "b" * 64}])
def test_current_workers_cannot_omit_or_conflict_with_controller_selection(worker):
    collector = Collector()
    collector.environment = deepcopy(ENVIRONMENT)
    output = {} if worker is None else {"nci_si_environment": worker}
    collector.pytest_testnodedown(SimpleNamespace(workeroutput=output), None)
    with pytest.raises(ValueError, match="environment"):
        collector.report("live")


def test_current_workers_preserve_the_exact_controller_selection():
    collector = Collector()
    collector.environment = deepcopy(ENVIRONMENT)
    collector.pytest_testnodedown(
        SimpleNamespace(workeroutput={"nci_si_environment": ENVIRONMENT}), None
    )
    assert collector.report("live").get("environment") == ENVIRONMENT


@pytest.mark.parametrize(
    "change",
    [
        {"name": None},
        {"policy_sha256": "private-marker"},
        {"origins": {"evs": "https://private-marker@example.com/"}},
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
