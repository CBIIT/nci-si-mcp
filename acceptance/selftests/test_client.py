"""The harness starts the server under test against the run mode's upstream."""

import sys

import pytest

from nci_si_acceptance.client import (
    MODE_VARIABLE,
    SERVER_VARIABLE,
    UPSTREAM_VARIABLES,
    Target,
    open_session,
    server_environment,
)
from nci_si_acceptance.fixture_server import SURFACES, FixtureServer

# The furnished server, started from the environment the tests run in.
BASELINE_SERVER = [sys.executable, "-m", "nci_si_mcp.cli", "serve"]


def test_the_default_target_is_the_installed_server_against_fixtures(monkeypatch):
    monkeypatch.delenv(MODE_VARIABLE, raising=False)
    monkeypatch.delenv(SERVER_VARIABLE, raising=False)

    assert Target.from_env() == Target("fixture", ["nci-si-mcp", "serve"])


def test_the_target_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(MODE_VARIABLE, "live")
    monkeypatch.setenv(SERVER_VARIABLE, "python -m 'my server' --flag")

    assert Target.from_env() == Target("live", ["python", "-m", "my server", "--flag"])


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        (MODE_VARIABLE, "both", "must be fixture or live, not 'both'"),
        (SERVER_VARIABLE, "  ", "must name a command"),
    ],
)
def test_a_wrong_target_is_refused_naming_its_variable(monkeypatch, variable, value, message):
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=f"{variable} {message}"):
        Target.from_env()


def test_in_fixture_mode_every_upstream_names_the_fixture_server(monkeypatch, tmp_path):
    monkeypatch.setenv("NCI_SI_EVS_BASE_URL", "https://developer.example")
    monkeypatch.setenv("NCI_SI_LOG_LEVEL", "DEBUG")

    environment = server_environment("fixture", tmp_path, "http://127.0.0.1:9")

    assert environment["NCI_SI_EVS_BASE_URL"] == "http://127.0.0.1:9/evs"
    assert {name: environment[name] for name in UPSTREAM_VARIABLES} == {
        name: f"http://127.0.0.1:9/{surface}" for name, surface in UPSTREAM_VARIABLES.items()
    }
    assert set(UPSTREAM_VARIABLES.values()) == set(SURFACES)
    assert (environment["NCI_SI_UPSTREAM_MODE"], environment["NCI_SI_DATA_DIR"]) == (
        "fixture",
        str(tmp_path),
    )
    assert "NCI_SI_LOG_LEVEL" not in environment


def test_in_live_mode_the_server_keeps_its_own_upstream(monkeypatch, tmp_path):
    monkeypatch.setenv("NCI_SI_EVS_BASE_URL", "https://developer.example")

    environment = server_environment("live", tmp_path, None)

    assert not set(UPSTREAM_VARIABLES) & set(environment)
    assert environment["NCI_SI_UPSTREAM_MODE"] == "live"


def test_fixture_mode_needs_the_fixture_server(tmp_path):
    with pytest.raises(ValueError, match="needs the fixture server's URL"):
        server_environment("fixture", tmp_path, None)


def test_every_upstream_request_of_the_server_under_test_is_recorded(tmp_path):
    with FixtureServer({}) as upstream:
        environment = server_environment("fixture", tmp_path, upstream.url)
        with open_session(BASELINE_SERVER, environment) as session:
            tools = session.list_tools()
            # A tool without required arguments that reaches upstream; with no
            # fixtures every request is refused, and each attempt is recorded.
            reaching = [tool.name for tool in tools if not tool.input_schema.get("required")]
            results = [session.call_tool(name, {}) for name in reaching]

    assert tools
    assert results
    log = upstream.log()
    assert log
    assert {entry["surface"] for entry in log} == {"evs"}
    assert all(entry["fixture"] is None for entry in log)
