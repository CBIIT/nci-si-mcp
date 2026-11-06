"""A live selection must fail safely before any process or request can start."""

import os
import subprocess
import sys
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import pytest
import yaml

from nci_si_acceptance import environments
from nci_si_acceptance.client import Target, server_environment
from nci_si_acceptance.denied_surfaces import DeniedSurfaces
from nci_si_acceptance.fixture_server import UPSTREAM_VARIABLES, FixtureServer, load_fixtures

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


def test_selected_destinations_and_credentials_are_one_private_snapshot(monkeypatch, tmp_path):
    fixtures = Path(__file__).parents[1] / "fixtures"
    with FixtureServer(load_fixtures(fixtures)) as upstream, DeniedSurfaces() as denied:
        policy = yaml.safe_load(environments.POLICY.read_bytes())
        for surface in ("evs", "evs-fhir"):
            policy["allowed"][surface] = upstream.base_url(surface)
            policy["environments"]["evs-only"][surface] = upstream.base_url(surface)
        path = tmp_path / "policy.yaml"
        path.write_text(yaml.safe_dump(policy))
        monkeypatch.setattr(environments, "POLICY", path)
        monkeypatch.setenv(ENVIRONMENT, "evs-only")
        monkeypatch.setenv("NCI_SI_EVS_LICENSE_KEY", "selected-private-marker")
        target = Target.from_env()
        monkeypatch.setenv("NCI_SI_EVS_LICENSE_KEY", "later-private-marker")
        env = server_environment(
            "live", tmp_path / "data", None, environment=target.environment, denied_url=denied
        )
        assert env["NCI_SI_EVS_LICENSE_KEY"] == "selected-private-marker"
        assert "selected-private-marker" not in repr(target)
        # This assertion guards the old/red and mutated implementation from nonlocal sends.
        for key in UPSTREAM_VARIABLES:
            assert env[key].startswith("http://127.0.0.1:")
        ran = subprocess.run(  # noqa: S603 - fixed furnished interpreter and guarded local bases.
            [*environments.LAUNCHER, "index-sample", "C4817"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert ran.returncode == 0, ran.stderr
        received = upstream.log()
        assert received and all(row["surface"] == "evs" for row in received)
        assert all(
            row["headers"]["X-EVSRESTAPI-License-Key"] == "selected-private-marker"
            for row in received
        )
        with pytest.raises(HTTPError) as error:
            build_opener(ProxyHandler({})).open(
                env["NCI_SI_CADSR_BASE_URL"] + "/blocked", timeout=2
            )
        assert error.value.code == HTTPStatus.FORBIDDEN
        assert upstream.log() == received
