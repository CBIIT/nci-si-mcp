"""A remote server under test: the harness connects to a streamable-HTTP endpoint, announces what
the operator sets on the server, probes it before the first test, and gets a server of its own
for a test from the operator's restart command.

The server is the compliant server over streamable HTTP (`COMPLIANT_SERVER_TRANSPORT=http`),
started once for the tests of this module (a restart command, restart_stub.py, starts it and
restarts it with a scenario's settings).
"""

import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import LICENCE_KEY, STAND_IN
from restart_stub import stop

from nci_si_acceptance.client import Target, open_remote_session, wait_for_endpoint
from nci_si_acceptance.fixture_server import FixtureServer, FixtureSet
from nci_si_acceptance.remote import Restarter, announcement, probe
from nci_si_acceptance.tools import Process

pytest_plugins = ["pytester"]

RESTART_STUB = Path(__file__).parent / "restart_stub.py"
CREDENTIAL = "Bearer selftest-credential"
PROTOCOL = "tests/test_protocol.py::"
TOOLS_LIST = f"{PROTOCOL}test_tools_list_names_the_tools_of_the_profile_and_no_other"
OWN_SERVER = f"{PROTOCOL}test_a_correlation_identifier_goes_upstream_and_comes_back"
PREPARED = f"{PROTOCOL}test_the_index_manifest_states_what_the_index_holds[index_manifest]"
UNKNOWN_RELEASE = (
    "tests/test_crosscutting.py::test_a_release_the_platform_does_not_serve_fails_closed"
    "[get_concept]"
)
LICENCE_REACHES = (
    "tests/test_crosscutting.py::"
    "test_the_licence_key_reaches_the_platform_and_nothing_the_server_returns_or_logs"
)
# A test that fails with the credential in its message, as a server that echoed it back would.
USAGE_ERROR = 4
ECHO = """
import os

def test_echo(tools):
    assert "echoed" == os.environ["NCI_SI_ACCEPTANCE_AUTHORIZATION"]
"""


def free_port() -> int:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        return taken.getsockname()[1]


@dataclass(frozen=True)
class Stub:
    """The compliant server over HTTP, with what it and its restart command are given."""

    url: str
    fixture_port: int
    environment: dict[str, str]
    credentials: Path
    record: Path


@pytest.fixture(scope="module")
def stub(tmp_path_factory):
    """The compliant server, running over HTTP on a port of its own, which reaches a fixture
    server at `fixture_port`, answers 401 to a request without the credential and keeps the
    credential each request brought."""

    directory = tmp_path_factory.mktemp("stub")
    port, fixture_port = free_port(), free_port()
    environment = {
        "NCI_SI_EVS_BASE_URL": f"http://127.0.0.1:{fixture_port}/evs",
        "COMPLIANT_SERVER_PORT": str(port),
        "COMPLIANT_SERVER_AUTHORIZATION": CREDENTIAL,
        "COMPLIANT_SERVER_AUTH_LOG": str(directory / "credentials.log"),
        "COMPLIANT_SERVER_PROFILE": "evs",
        "COMPLIANT_SERVER_NO_CACHE": "1",
        "RESTART_PID_FILE": str(directory / "server.pid"),
        "RESTART_RECORD": str(directory / "restarts.log"),
    }
    started = Stub(
        f"http://127.0.0.1:{port}/mcp",
        fixture_port,
        environment,
        directory / "credentials.log",
        directory / "restarts.log",
    )
    subprocess.run(  # noqa: S603
        [sys.executable, str(RESTART_STUB)], env=os.environ | environment, check=True
    )
    assert wait_for_endpoint(started.url, CREDENTIAL, 60)
    yield started
    stop(Path(environment["RESTART_PID_FILE"]))


@pytest.fixture
def remote(compliant, monkeypatch, stub):
    """The suite's tests, run against the remote server: `run(*arguments)` gives the result of
    the run and its report."""

    monkeypatch.delenv("NCI_SI_ACCEPTANCE_SERVER")
    monkeypatch.delenv("NCI_SI_ACCEPTANCE_PREPARE")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_URL", stub.url)
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_AUTHORIZATION", CREDENTIAL)
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_FIXTURE_BIND", f"127.0.0.1:{stub.fixture_port}")
    # What the operator's restart command is given by the environment it runs in.
    for name, value in stub.environment.items():
        monkeypatch.setenv(name, value)
    (compliant.path / "tests" / "test_echo.py").write_text(ECHO, encoding="utf-8")

    def run(*arguments):
        report = compliant.path / "report.json"
        result = compliant.runpytest_subprocess(
            *arguments, "-p", "no:cacheprovider", "-p", STAND_IN, f"--report={report}", "-rs"
        )
        found = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
        return result, found

    return run


def outcomes_of(report):
    return {nodeid.partition("::")[2]: test["outcome"] for nodeid, test in report["tests"].items()}


def test_a_remote_server_is_tested_over_streamable_http_with_the_credential_on_every_request(
    remote, stub
):
    _, report = remote(TOOLS_LIST, "tests/test_echo.py")

    assert report["transport"] == "streamable-http"
    assert outcomes_of(report)[TOOLS_LIST.partition("::")[2]] == "passed"
    sent = stub.credentials.read_text(encoding="utf-8").splitlines()
    assert sent
    assert set(sent) == {CREDENTIAL}


def test_the_credential_appears_in_no_output_of_the_harness(remote):
    result, report = remote("tests/test_echo.py")

    assert outcomes_of(report) == {"test_echo": "failed"}
    written = result.stdout.str() + result.stderr.str() + json.dumps(report)
    assert CREDENTIAL not in written
    assert "[authorization withheld]" in result.stdout.str()


def test_the_harness_tells_the_operator_what_to_set_on_the_server(remote, stub):
    result, _ = remote(TOOLS_LIST)

    assert result.ret == 0
    result.stdout.fnmatch_lines(
        [
            "settings of the server under test:",
            "*NCI_SI_UPSTREAM_MODE=fixture",
            f"*NCI_SI_EVS_BASE_URL=http://127.0.0.1:{stub.fixture_port}/evs",
        ]
    )


def test_without_a_restart_command_the_tests_that_need_a_server_of_their_own_are_not_run(remote):
    result, report = remote(TOOLS_LIST, UNKNOWN_RELEASE, OWN_SERVER)

    result.stdout.fnmatch_lines(["SKIPPED * needs a server of its own (NCI_SI_ACCEPTANCE_RESTART)"])
    assert outcomes_of(report) == {
        TOOLS_LIST.partition("::")[2]: "passed",
        UNKNOWN_RELEASE.partition("::")[2]: "skipped",
        OWN_SERVER.partition("::")[2]: "skipped",
    }
    # A tool whose only tests did not run is not accepted: its row reads NOT RUN.
    assert report["tools"]["get_concept"]["outcome"] == "NOT RUN"
    assert report["tools"]["get_concept"]["counts"] == {"skipped": 1}


def test_a_test_that_needs_the_index_is_not_run_until_the_operator_declares_it_prepared(remote):
    result, report = remote(PREPARED)

    result.stdout.fnmatch_lines(
        ["SKIPPED * NOT RUN: the server is not declared prepared (NCI_SI_ACCEPTANCE_PREPARED)"]
    )
    assert outcomes_of(report) == {PREPARED.partition("::")[2]: "skipped"}


def test_the_restart_command_gives_scenario_and_own_server_tests_a_server_of_their_own(
    remote, stub, monkeypatch
):
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_RESTART", f"{sys.executable} {RESTART_STUB}")
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_PREPARED", "1")
    chosen = (UNKNOWN_RELEASE, LICENCE_REACHES, OWN_SERVER, TOOLS_LIST, PREPARED)

    _, report = remote(*chosen)

    assert {test["outcome"] for test in report["tests"].values()} == {"passed"}
    assert len(report["tests"]) == len(chosen)
    # The server is restarted before the probe; the tests on it as the operator left it run
    # first, then the test that needs a server of its own, then each scenario; and the server
    # is left as it was found. The licence key is the setting of the license/restricted
    # scenario, and the harness's credential is not given to the command.
    seen = [json.loads(line) for line in stub.record.read_text(encoding="utf-8").splitlines()]
    assert [each["licenceKey"] for each in seen[1:]] == [None, None, LICENCE_KEY, None, None]
    assert {each["credentialGiven"] for each in seen} == {False}


def test_a_run_ends_at_once_when_the_server_does_not_reach_the_fixture_server(
    remote, monkeypatch, stub
):
    elsewhere = free_port()
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_FIXTURE_BIND", f"127.0.0.1:{elsewhere}")

    result, report = remote(TOOLS_LIST)

    assert result.ret == 1
    result.stdout.fnmatch_lines(["*the server under test does not reach the fixture server*"])
    assert report["tests"] == {}


def test_a_run_ends_at_once_when_the_server_does_not_answer(remote, monkeypatch):
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_URL", f"http://127.0.0.1:{free_port()}/mcp")

    result, report = remote(TOOLS_LIST)

    assert result.ret == 1
    result.stdout.fnmatch_lines(["*the server under test does not answer (*"])
    assert report["tests"] == {}


def test_a_live_run_only_needs_the_server_to_answer(remote, monkeypatch):
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "live")

    result, report = remote(TOOLS_LIST)

    assert (report["mode"], report["transport"]) == ("live", "streamable-http")
    assert outcomes_of(report) == {TOOLS_LIST.partition("::")[2]: "passed"}
    assert "settings of the server under test" not in result.stdout.str()


def test_naming_a_command_and_an_endpoint_stops_the_run_with_a_usage_error(compliant, monkeypatch):
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_URL", "http://127.0.0.1:9/mcp")

    result = compliant.runpytest_subprocess("tests", "-p", "no:cacheprovider", "-p", STAND_IN)

    assert result.ret == USAGE_ERROR
    result.stderr.fnmatch_lines(
        ["*NCI_SI_ACCEPTANCE_URL and NCI_SI_ACCEPTANCE_SERVER are both set; name one server*"]
    )


# ---- the restart command, called directly


def restarter(stub, tmp_path, command, timeout=30):
    target = Target(
        "fixture",
        [],
        "evs",
        url=stub.url,
        authorization=CREDENTIAL,
        restart=command,
        restart_timeout=timeout,
    )
    return Restarter(target, FixtureServer(FixtureSet({}, {})), tmp_path / "output.log")


def test_a_scenario_is_restarted_for_once_and_a_server_of_its_own_every_time(stub, tmp_path):
    runs = tmp_path / "runs.txt"
    restart = restarter(stub, tmp_path, f'echo "$SETTING" >> {runs}')

    restart.serve(("a/one",), {"SETTING": "one"})
    restart.serve(("a/one",), {"SETTING": "one"})
    restart.serve((), {"SETTING": "own"}, fresh=True)
    restart.serve((), {"SETTING": "own"}, fresh=True)
    restart.serve(("a/two",), {"SETTING": "two"})
    restart.restore()
    restart.restore()

    assert runs.read_text(encoding="utf-8").splitlines() == ["one", "own", "own", "two", ""]


def test_a_restart_command_that_fails_ends_the_run_with_what_it_said_less_the_credential(
    stub, tmp_path, monkeypatch
):
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_AUTHORIZATION", CREDENTIAL)
    command = f'echo "told {CREDENTIAL}, has [$NCI_SI_ACCEPTANCE_AUTHORIZATION]"; exit 3'

    with pytest.raises(pytest.exit.Exception) as ended:
        restarter(stub, tmp_path, command).serve(("a/one",), {})

    said = ended.value.msg
    assert "failed with exit status 3" in said
    assert "told [authorization withheld], has []" in said


def test_a_restart_command_that_does_not_return_ends_the_run(stub, tmp_path):
    with pytest.raises(pytest.exit.Exception, match=r"did not return within 0\.3 s"):
        restarter(stub, tmp_path, "sleep 5", timeout=0.3).serve(("a/one",), {})


def test_a_server_that_does_not_come_back_ends_the_run(tmp_path):
    gone = Stub(f"http://127.0.0.1:{free_port()}/mcp", 0, {}, tmp_path, tmp_path)

    with pytest.raises(pytest.exit.Exception, match=r"does not answer 0\.3 s after the restart"):
        restarter(gone, tmp_path, "true", timeout=0.3).serve(("a/one",), {})


def test_a_session_with_a_server_that_refuses_the_credential_fails(stub):
    with pytest.raises(ExceptionGroup), open_remote_session(stub.url, "Bearer wrong"):
        pass


def test_a_remote_process_has_no_standard_error_or_data_directory_to_read():
    assert Process(None, None, ()).written() == ""


def test_the_fixture_server_listens_where_it_is_told(stub):
    with FixtureServer(FixtureSet({}, {}), ("127.0.0.1", stub.fixture_port)) as server:
        assert server.url == f"http://127.0.0.1:{stub.fixture_port}"
    with FixtureServer(FixtureSet({}, {}), ("0.0.0.0", 0)) as anywhere:  # noqa: S104
        assert anywhere.url.startswith("http://127.0.0.1:")


# ---- the checks before the first test, called directly

TOOLMAP: dict = {}
PINNED = {"terminology": "ncit"}


def test_the_operator_is_told_the_urls_the_server_reaches_the_fixture_server_by(stub):
    named = Target("fixture", [], url=stub.url, fixture_url="https://fixtures.example")
    unnamed = Target("fixture", [], url=stub.url)

    with FixtureServer(FixtureSet({}, {})) as upstream:
        told, default = announcement(named, upstream), announcement(unnamed, upstream)

    assert told[:3] == [
        "settings of the server under test:",
        "  NCI_SI_UPSTREAM_MODE=fixture",
        "  NCI_SI_EVS_BASE_URL=https://fixtures.example/evs",
    ]
    assert f"  NCI_SI_EVS_BASE_URL={upstream.url}/evs" in default


def test_a_server_that_asks_the_fixture_server_passes_the_probe_and_leaves_its_log_clean(stub):
    with FixtureServer(FixtureSet({}, {}), ("127.0.0.1", stub.fixture_port)) as upstream:
        probe(stub.url, CREDENTIAL, upstream, TOOLMAP, PINNED)

        assert upstream.log() == []


def test_a_server_that_asks_nothing_of_the_fixture_server_fails_the_probe(stub):
    with (
        FixtureServer(FixtureSet({}, {}), ("127.0.0.1", free_port())) as upstream,
        pytest.raises(pytest.exit.Exception, match="does not reach the fixture server"),
    ):
        probe(stub.url, CREDENTIAL, upstream, TOOLMAP, PINNED)


def test_a_server_that_gives_no_answer_fails_the_probe_without_its_message(stub):
    with pytest.raises(pytest.exit.Exception) as ended:
        probe(f"http://127.0.0.1:{free_port()}/mcp", CREDENTIAL, None, TOOLMAP, PINNED)

    assert ended.value.msg.startswith("the server under test does not answer (")
    assert CREDENTIAL not in ended.value.msg


def test_without_a_fixture_server_the_probe_asks_only_for_an_answer(stub):
    assert probe(stub.url, CREDENTIAL, None, TOOLMAP, PINNED) is None
