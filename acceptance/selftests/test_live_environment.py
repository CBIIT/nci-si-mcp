"""A live selection must fail safely before any process or request can start."""

import os
import sys

import pytest

from nci_si_acceptance.client import Target

ENVIRONMENT = "NCI_SI_ACCEPTANCE_ENVIRONMENT"


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    for key in os.environ:
        if key.startswith(("NCI_SI_", "PYTHON")) or key.lower().endswith("_proxy"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "live")


@pytest.mark.parametrize("name", [None, "", "stage", "production", "unknown"])
def test_selection_precedes_process_or_request(monkeypatch, name):
    if name is not None:
        monkeypatch.setenv(ENVIRONMENT, name)
    with pytest.raises(ValueError, match=ENVIRONMENT):
        Target.from_env()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("NCI_SI_EVS_BASE_URL", "http://127.0.0.1:1"),
        ("NCI_SI_CADSR_BASE_URL", "http://127.0.0.1:1"),
        ("NCI_SI_CADSR_CREDENTIAL", "private-marker"),
        ("HTTP_PROXY", "http://127.0.0.1:1"),
        ("hTtPs_PrOxY", "http://127.0.0.1:1"),
        ("NO_PROXY", "*"),
        ("PYTHONPATH", "/external/module-substitution"),
        ("NCI_SI_ACCEPTANCE_SERVER", "python alternate-server.py"),
        ("NCI_SI_ACCEPTANCE_SERVER", "nci-si-mcp serve; alternate"),
        (
            "NCI_SI_ACCEPTANCE_PREPARE",
            'nci-si-mcp index-sample $(cat "$NCI_SI_ACCEPTANCE_INDEX_CODES")',
        ),
        ("NCI_SI_ACCEPTANCE_PREPARE", "nci-si-mcp index-sample C2991"),
        ("NCI_SI_ACCEPTANCE_PROFILE", "evs"),
        ("NCI_SI_ACCEPTANCE_SECURITY_SERVER", "python alternate.py"),
        ("NCI_SI_ACCEPTANCE_URL", "http://127.0.0.1:1/mcp"),
    ],
)
def test_unsupported_live_commands_and_overrides_are_refused(monkeypatch, key, value):
    monkeypatch.setenv(ENVIRONMENT, "evs-only")
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError) as error:
        Target.from_env()
    assert key in str(error.value)
    assert value not in str(error.value)


def test_fixture_mode_cannot_claim_a_live_selection(monkeypatch):
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "fixture")
    monkeypatch.setenv(ENVIRONMENT, "evs-only")
    with pytest.raises(ValueError, match=ENVIRONMENT):
        Target.from_env()


def test_selected_live_server_uses_only_the_furnished_isolated_launcher(monkeypatch):
    monkeypatch.setenv(ENVIRONMENT, "evs-only")
    assert Target.from_env().command == [
        sys.executable,
        "-I",
        "-m",
        "nci_si_acceptance.live_launcher",
        "serve",
    ]
