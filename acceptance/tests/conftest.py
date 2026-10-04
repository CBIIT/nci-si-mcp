"""The run of the suite: the server under test, its upstream, scenarios, and the request log.

A test that selects scenarios (`@pytest.mark.scenario("release/unknown")`) gets a
server process of its own, started with the scenarios active and with their
settings, so that nothing the server keeps between calls outlives them. Requests a
server makes while it starts must find fixtures too.

Against a remote server (`NCI_SI_ACCEPTANCE_URL`) the harness starts no process: a probe makes
sure the server answers and reaches the fixture server before the first test, and the operator's
restart command, where there is one, gives a scenario's tests, and each `own_server` test, the
server of its own they need; without it those tests are skipped. A remote server's prepare step is
the operator's (`NCI_SI_ACCEPTANCE_PREPARED`).

The operator's prepare command, where one is given, runs once, before the first test that
starts a server, in the server's environment, with the codes of the index set listed in the file
`NCI_SI_ACCEPTANCE_INDEX_CODES` names; every server then starts from a copy of the data
directory it produced. A prepare command that fails, or whose requests find no fixture,
ends the run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from nci_si_acceptance.client import (
    INDEX_CODES_VARIABLE,
    Target,
    open_remote_session,
    open_session,
    server_environment,
)
from nci_si_acceptance.fixture_server import MANIFEST, FixtureServer, load_fixtures
from nci_si_acceptance.remote import Restarter, announcement, probe
from nci_si_acceptance.report import COLLECTOR, write_report
from nci_si_acceptance.suite import (
    NOT_DECLARED_PREPARED,
    NOT_PREPARED,
    OWN_SERVER,
    UNMATCHED_UPSTREAM,
    UNPREPARED,
    UnmatchedUpstream,
    index_set,
    order_for_restarts,
    scenarios_of,
    skip_fixture_only,
    skip_remote_own_servers,
    skip_unprepared,
    unmatched_requests,
)
from nci_si_acceptance.tools import Process, Tools, load_toolmap

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from nci_si_acceptance.client import Session

pytest_plugins = ["nci_si_acceptance.report"]

FIXTURES = Path(__file__).parent.parent / "fixtures"
TARGET = pytest.StashKey[Target]()


def pytest_configure(config: pytest.Config) -> None:
    try:
        config.stash[TARGET] = Target.from_env()
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    target = config.stash[TARGET]
    if target.mode == "live":
        skip_fixture_only(items)
    if not target.has_index:
        skip_unprepared(items, NOT_DECLARED_PREPARED if target.url else NOT_PREPARED)
    if target.url and target.mode == "fixture":
        skip_remote_own_servers(items, target.restart is not None)
        if target.restart:
            order_for_restarts(items)


def pytest_sessionfinish(session: pytest.Session) -> None:
    target = session.config.stash[TARGET]
    session.config.stash[COLLECTOR].transport = target.transport
    write_report(session.config, target.mode)


@pytest.fixture(scope="session")
def target(pytestconfig: pytest.Config) -> Target:
    return pytestconfig.stash[TARGET]


@pytest.fixture(scope="session")
def pinned() -> dict[str, str]:
    """The terminology and release the fixture set is pinned to, as a caller names them."""

    manifest = yaml.safe_load((FIXTURES / MANIFEST).read_text(encoding="utf-8"))
    terminology, _, release = manifest["evs"]["release"].partition("_")
    return {"terminology": terminology, "release": release}


@pytest.fixture(scope="session")
def recorded() -> Callable[[str], Any]:
    """A fixture file by its path under fixtures/, read as JSON: a test derives what it expects
    from the recording, never from a copy of it."""

    return lambda name: json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def upstream(target: Target) -> Iterator[FixtureServer | None]:
    """The fixture server in fixture mode; None in live mode."""

    if target.mode == "live":
        yield None
        return
    with FixtureServer(load_fixtures(FIXTURES), target.fixture_bind or ("127.0.0.1", 0)) as server:
        yield server


@pytest.fixture(scope="session", autouse=True)
def remote_ready(
    request: pytest.FixtureRequest,
    target: Target,
    upstream: FixtureServer | None,
    restarter: Restarter | None,
) -> None:
    """Before any test, a remote server is told what to reach, and is found to answer and,
    against fixtures, to reach the fixture server; otherwise the run stops. Where the operator
    gave a restart command, the server is restarted first: one that answers from a cache filled
    before the run asks the fixture server nothing, and would fail the probe."""

    if target.url is None:
        return
    if upstream:
        reporter = request.config.pluginmanager.get_plugin("terminalreporter")
        for line in announcement(target, upstream):
            reporter.write_line(line)
    if restarter:
        restarter.serve((), {}, fresh=True)
    toolmap = load_toolmap(FIXTURES / "baseline_toolmap.yaml")
    pinned = request.getfixturevalue("pinned")
    probe(target.url, target.authorization, upstream, toolmap, pinned)


@pytest.fixture(scope="session")
def restarter(
    target: Target, upstream: FixtureServer | None, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Restarter | None]:
    """The operator's restart command, where there is one and a fixture server to restart for."""

    if target.restart is None or upstream is None:
        yield None
        return
    restarting = Restarter(target, upstream, tmp_path_factory.mktemp("restart") / "output.log")
    yield restarting
    restarting.restore()


@pytest.fixture(scope="session")
def prepared(
    target: Target, upstream: FixtureServer | None, tmp_path_factory: pytest.TempPathFactory
) -> Path | None:
    """The data directory the operator's prepare command produced, or None without one."""

    if target.prepare is None:
        return None
    data = tmp_path_factory.mktemp("prepared")
    codes = tmp_path_factory.mktemp("index") / "codes.txt"
    manifest = yaml.safe_load((FIXTURES / MANIFEST).read_text(encoding="utf-8"))
    codes.write_text("\n".join(index_set(manifest)) + "\n", encoding="utf-8")
    url = upstream.url if upstream else None
    environment = server_environment(target.mode, data, url) | {INDEX_CODES_VARIABLE: str(codes)}
    if upstream:
        upstream.reset()
    # The operator's own command line, as a shell runs it (the acceptance README); its output
    # is kept for its failure, not charged to the first test that starts a server.
    ran = subprocess.run(  # noqa: S602
        target.prepare, shell=True, env=environment, check=False, capture_output=True, text=True
    )
    if ran.returncode:
        said = (ran.stdout + ran.stderr)[-2000:]
        pytest.exit(
            f"the prepare command failed with exit status {ran.returncode}:\n{said}", returncode=1
        )
    if unmatched := unmatched_requests(_startup_requests(upstream)):
        pytest.exit(str(UnmatchedUpstream(unmatched, " while preparing")), returncode=1)
    return data


@pytest.fixture(scope="session")
def server(
    pytestconfig: pytest.Config,
    target: Target,
    upstream: FixtureServer | None,
    prepared: Path | None,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Tools]:
    """The server under test, shared by every test that selects no scenario."""

    if target.url:
        with _remote_tools(target, upstream) as tools:
            pytestconfig.stash[COLLECTOR].note_tools(tools)
            yield tools
        return
    with _tools(target, upstream, tmp_path_factory, prepared) as tools:
        pytestconfig.stash[COLLECTOR].note_tools(tools)
        yield tools


def _make_tools(session: Session, upstream: FixtureServer | None, process: Process) -> Tools:
    toolmap = load_toolmap(FIXTURES / "baseline_toolmap.yaml")
    counted = (lambda: len(upstream.log())) if upstream else None
    return Tools(session, toolmap, process, counted)


@contextmanager
def _remote_tools(
    target: Target, upstream: FixtureServer | None, startup: tuple[dict[str, Any], ...] = ()
) -> Iterator[Tools]:
    """A session with the remote server, which the harness neither started nor can read the
    standard error or data directory of."""

    with open_remote_session(target.url, target.authorization) as session:
        yield _make_tools(session, upstream, Process(None, None, startup))


@contextmanager
def _tools(
    target: Target,
    upstream: FixtureServer | None,
    tmp_path_factory: pytest.TempPathFactory,
    prepared: Path | None,
    settings: dict[str, str] | None = None,
) -> Iterator[Tools]:
    url = upstream.url if upstream else None
    if upstream:
        upstream.reset()  # what earlier tests left in the log is not this server's
    data = tmp_path_factory.mktemp("data")
    if prepared is not None:
        shutil.copytree(prepared, data, dirs_exist_ok=True)
    environment = server_environment(target.mode, data, url)
    log = tmp_path_factory.mktemp("server") / "stderr.log"
    try:
        with (
            log.open("w", encoding="utf-8") as errlog,
            open_session(target.command, environment | (settings or {}), errlog) as session,
        ):
            startup = _startup_requests(upstream)
            if not (unmatched := unmatched_requests(startup)):
                yield _make_tools(session, upstream, Process(log, data, tuple(startup)))
                return
    finally:
        # The server's standard error, shown with a failing test's other output.
        sys.stderr.write(log.read_text(encoding="utf-8", errors="replace"))
    # Failing outside the session: inside it, the failure would reach pytest wrapped
    # in the session's exception group.
    raise UnmatchedUpstream(unmatched, " while the server started")


def _startup_requests(upstream: FixtureServer | None) -> list[dict[str, Any]]:
    """The requests the server made while it started; the log is reset."""

    if upstream is None:
        return []
    startup = upstream.log()
    upstream.reset()
    return startup


@pytest.fixture
def tools(
    request: pytest.FixtureRequest,
    target: Target,
    upstream: FixtureServer | None,
    prepared: Path | None,
    restarter: Restarter | None,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Tools]:
    """The required tools of the server under test, for one test.

    A scenario test, and one marked `own_server`, does not use the shared server: it runs
    against its own, which nothing an earlier test asked can have filled. A remote server is
    restarted by the operator's command for it: once for a scenario's tests, for every
    `own_server` test.
    """

    scenarios = scenarios_of(request.node)
    unprepared = request.node.get_closest_marker(UNPREPARED) is not None
    own = unprepared or request.node.get_closest_marker(OWN_SERVER) is not None
    if not (scenarios or own) or upstream is None:
        yield request.getfixturevalue("server")
        return
    upstream.activate(*scenarios)
    settings = upstream.fixtures.settings_of(scenarios)
    try:
        if target.url:
            ours = _restarted_tools(target, upstream, restarter, scenarios, settings)
        else:
            data = None if unprepared else prepared
            ours = _tools(target, upstream, tmp_path_factory, data, settings)
        with ours as its_own:
            request.config.stash[COLLECTOR].note_tools(its_own)
            yield its_own
    finally:
        upstream.activate()


@contextmanager
def _restarted_tools(
    target: Target,
    upstream: FixtureServer,
    restarter: Restarter,
    scenarios: tuple[str, ...],
    settings: dict[str, str],
) -> Iterator[Tools]:
    """The remote server as the operator's restart command leaves it for these scenarios (a
    server of its own, where there are none)."""

    started = restarter.serve(scenarios, settings, fresh=not scenarios)
    if unmatched := unmatched_requests(started):
        raise UnmatchedUpstream(unmatched, " while the server started")
    with _remote_tools(target, upstream, started) as tools:
        yield tools


@pytest.fixture(autouse=True)
def upstream_log(request: pytest.FixtureRequest, upstream: FixtureServer | None) -> Iterator[None]:
    """Each test reads only the upstream requests it caused, and each had a fixture."""

    if upstream is None:
        yield
        return
    upstream.reset()
    yield
    unmatched = unmatched_requests(upstream.log())
    if unmatched and request.node.get_closest_marker(UNMATCHED_UPSTREAM) is None:
        raise UnmatchedUpstream(unmatched)
