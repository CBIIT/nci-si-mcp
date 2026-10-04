"""The harness starts the server under test against the run mode's upstream."""

import sys
from pathlib import Path

import pytest
from mcp.shared.exceptions import MCPError

from nci_si_acceptance import client
from nci_si_acceptance.client import (
    MODE_VARIABLE,
    PROFILE_VARIABLE,
    SERVER_VARIABLE,
    Target,
    open_session,
    server_environment,
)
from nci_si_acceptance.fixture_server import UPSTREAM_VARIABLES, FixtureServer, FixtureSet
from nci_si_acceptance.spec import PROMPTS, resources_listed

# The furnished server, started from the environment the tests run in.
BASELINE_SERVER = [sys.executable, "-m", "nci_si_mcp.cli", "serve"]
COMPLIANT_SERVER = Path(__file__).parent / "compliant_server.py"


def test_the_default_target_is_the_installed_server_against_fixtures(monkeypatch):
    monkeypatch.delenv(MODE_VARIABLE, raising=False)
    monkeypatch.delenv(SERVER_VARIABLE, raising=False)
    monkeypatch.delenv(PROFILE_VARIABLE, raising=False)

    assert Target.from_env() == Target("fixture", ["nci-si-mcp", "serve"], "unified")


def test_the_target_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(MODE_VARIABLE, "live")
    monkeypatch.setenv(SERVER_VARIABLE, "python -m 'my server' --flag")
    monkeypatch.setenv(PROFILE_VARIABLE, "evs")

    assert Target.from_env() == Target("live", ["python", "-m", "my server", "--flag"], "evs")


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        (MODE_VARIABLE, "both", "must be fixture or live, not 'both'"),
        (SERVER_VARIABLE, "  ", "must name a command"),
        (PROFILE_VARIABLE, "both", "must be one of evs, cadsr, unified"),
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

    # The upstream settings of docs/SPEC.md §8, each naming its fixture surface.
    assert {name: environment[name] for name in UPSTREAM_VARIABLES} == {
        "NCI_SI_EVS_BASE_URL": "http://127.0.0.1:9/evs",
        "NCI_SI_EVS_FHIR_BASE_URL": "http://127.0.0.1:9/evs-fhir",
        "NCI_SI_CADSR_BASE_URL": "http://127.0.0.1:9/cadsr",
        "NCI_SI_CADSR_FTP_URL": "http://127.0.0.1:9/cadsr-ftp",
        "NCI_SI_SSIS_FACADE_URL": "http://127.0.0.1:9/ssis",
        "NCI_SI_SSIS_SPARQL_URL": "http://127.0.0.1:9/ssis-sparql",
    }
    assert (environment["NCI_SI_UPSTREAM_MODE"], environment["NCI_SI_DATA_DIR"]) == (
        "fixture",
        str(tmp_path),
    )
    assert "NCI_SI_LOG_LEVEL" not in environment


def test_in_live_mode_the_server_keeps_its_upstream_and_gets_the_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("NCI_SI_EVS_BASE_URL", "https://developer.example")
    # The credentials of docs/SPEC.md §8.
    credentials = {"NCI_SI_EVS_LICENSE_KEY": "evs key", "NCI_SI_CADSR_CREDENTIAL": "cadsr key"}
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)

    live = server_environment("live", tmp_path, None)
    fixture = server_environment("fixture", tmp_path, "http://127.0.0.1:9")

    assert not set(UPSTREAM_VARIABLES) & set(live)
    assert live["NCI_SI_UPSTREAM_MODE"] == "live"
    assert {name: live.get(name) for name in credentials} == credentials
    assert not set(credentials) & set(fixture)


def test_fixture_mode_needs_the_fixture_server(tmp_path):
    with pytest.raises(ValueError, match="needs the fixture server's URL"):
        server_environment("fixture", tmp_path, None)


def test_every_upstream_request_of_the_server_under_test_is_recorded(tmp_path):
    with FixtureServer(FixtureSet({}, {})) as upstream:
        environment = server_environment("fixture", tmp_path, upstream.url)
        with open_session(BASELINE_SERVER, environment) as session:
            tools = session.list_tools().tools
            # A tool without required arguments that reaches upstream; with no
            # fixtures every request is refused, and each attempt is recorded.
            reaching = [tool.name for tool in tools if not tool.input_schema.get("required")]
            results = [session.call_tool(name, {}) for name in reaching]

    assert tools
    assert results
    log = upstream.log()
    assert log
    assert {entry["surface"] for entry in log} <= set(UPSTREAM_VARIABLES.values())
    assert all(entry["fixture"] is None for entry in log)


def test_a_session_lists_and_gets_prompts_and_lists_and_reads_resources(tmp_path):
    command = [sys.executable, str(COMPLIANT_SERVER)]
    with FixtureServer(FixtureSet({}, {})) as upstream:
        environment = server_environment("fixture", tmp_path, upstream.url)
        environment |= {"COMPLIANT_SERVER_PROFILE": "unified"}
        with open_session(command, environment) as session:
            prompts = session.list_prompts().prompts
            got = session.get_prompt("crdc_model_alignment", {"field": "f", "commons": "GDC"})
            resources = session.list_resources().resources
            templates = session.list_resource_templates().resource_templates
            # The upstream has no fixture, so the server refuses the read; the refusal arrives.
            with pytest.raises(MCPError, match="upstream_unavailable"):
                session.read_resource("cadsr://registry/release")

    assert sorted(prompt.name for prompt in prompts) == sorted(PROMPTS)
    assert "the commons GDC" in got.messages[0].content.text
    assert {resource.uri for resource in resources} == {
        "cadsr://registry/release",
        "cadsr://crosswalk/crdc",
    }
    assert "ncit://concept/{release}/{code}" in {each.uri_template for each in templates}


def test_a_session_follows_the_cursor_of_every_list_it_reads_to_the_last_page(tmp_path):
    command = [sys.executable, str(COMPLIANT_SERVER)]
    with FixtureServer(FixtureSet({}, {})) as upstream:
        environment = server_environment("fixture", tmp_path, upstream.url)
        # One item to a page: without the cursor a list would hold its first item only.
        environment |= {"COMPLIANT_SERVER_PROFILE": "unified", "COMPLIANT_SERVER_PAGE_SIZE": "1"}
        with open_session(command, environment) as session:
            prompts = session.list_prompts()
            resources = session.list_resources()
            templates = session.list_resource_templates()

    assert sorted(prompt.name for prompt in prompts.prompts) == sorted(PROMPTS)
    assert len(resources.resources) == len(resources_listed("unified").uris)
    assert len(templates.resource_templates) == len(resources_listed("unified").templates)
    # The list is whole: no cursor is left to follow, and the hint is the first page's.
    assert [each.next_cursor for each in (prompts, resources, templates)] == [None] * 3
    assert prompts.ttl_ms > 0


def test_a_server_that_never_answers_ends_the_session_with_a_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "READ_TIMEOUT_SECONDS", 1)

    with (
        pytest.raises(ExceptionGroup) as raised,
        open_session(["sleep", "30"], server_environment("live", tmp_path, None)) as session,
    ):
        session.list_tools()

    assert raised.group_contains(MCPError, match="timed out", depth=None)
