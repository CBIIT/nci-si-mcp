"""Live-content acceptance rejects errors and invented identity through a real MCP process."""

import pytest
from conftest import SUITE

pytest_plugins = ["pytester"]

CASE = "test_a_discovered_release_identifies_the_concept_and_its_provenance"


@pytest.fixture
def content_suite(compliant):
    source = SUITE / "test_evs.py"
    (compliant.path / "tests/test_evs.py").write_text(source.read_text(), encoding="utf-8")


@pytest.mark.usefixtures("content_suite")
def test_discovery_to_content_passes_with_valid_identity(outcomes):
    assert outcomes(f"tests/test_evs.py::{CASE}") == {CASE: "passed"}


@pytest.mark.parametrize(
    "defect",
    [
        "concept-empty",
        "concept-error",
        "concept-no-name",
        "concept-wrong-uri",
        "concept-null-provenance",
        "concept-list-provenance",
        "wrong-release",
        "wrong-terminology",
        "no-served-by",
        "bad-timestamp",
        "list-result",
        "invalid-result",
    ],
)
@pytest.mark.usefixtures("content_suite")
def test_discovery_to_content_rejects_broken_responses(outcomes, monkeypatch, defect):
    monkeypatch.setenv("COMPLIANT_SERVER_DEFECT", defect)
    assert outcomes(f"tests/test_evs.py::{CASE}") == {CASE: "failed"}
