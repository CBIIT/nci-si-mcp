"""The run of the suite: the server under test, its upstream, scenarios, and the request log.

A test that selects scenarios (`@pytest.mark.scenario("release/unknown")`) gets a
server process of its own, started with the scenarios active and with their
settings, so that nothing the server keeps between calls outlives them. Requests a
server makes while it starts must find fixtures too.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from nci_si_acceptance.client import Target, open_session, server_environment
from nci_si_acceptance.fixture_server import MANIFEST, FixtureServer, load_fixtures
from nci_si_acceptance.report import COLLECTOR, write_report
from nci_si_acceptance.suite import (
    OWN_SERVER,
    UNMATCHED_UPSTREAM,
    UnmatchedUpstream,
    scenarios_of,
    skip_fixture_only,
    unmatched_requests,
)
from nci_si_acceptance.tools import Tools, load_toolmap

if TYPE_CHECKING:
    from collections.abc import Iterator

pytest_plugins = ["nci_si_acceptance.report"]

FIXTURES = Path(__file__).parent.parent / "fixtures"
TARGET = pytest.StashKey[Target]()


def pytest_configure(config: pytest.Config) -> None:
    try:
        config.stash[TARGET] = Target.from_env()
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.stash[TARGET].mode == "live":
        skip_fixture_only(items)


def pytest_sessionfinish(session: pytest.Session) -> None:
    write_report(session.config, session.config.stash[TARGET].mode)


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
def upstream(target: Target) -> Iterator[FixtureServer | None]:
    """The fixture server in fixture mode; None in live mode."""

    if target.mode == "live":
        yield None
        return
    with FixtureServer(load_fixtures(FIXTURES)) as server:
        yield server


@pytest.fixture(scope="session")
def server(
    pytestconfig: pytest.Config,
    target: Target,
    upstream: FixtureServer | None,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Tools]:
    """The server under test, shared by every test that selects no scenario."""

    with _tools(target, upstream, tmp_path_factory) as tools:
        pytestconfig.stash[COLLECTOR].note_tools(tools)
        yield tools


@contextmanager
def _tools(
    target: Target,
    upstream: FixtureServer | None,
    tmp_path_factory: pytest.TempPathFactory,
    settings: dict[str, str] | None = None,
) -> Iterator[Tools]:
    url = upstream.url if upstream else None
    if upstream:
        upstream.reset()  # what earlier tests left in the log is not this server's
    environment = server_environment(target.mode, tmp_path_factory.mktemp("data"), url)
    log = tmp_path_factory.mktemp("server") / "stderr.log"
    with (
        log.open("w", encoding="utf-8") as errlog,
        open_session(target.command, environment | (settings or {}), errlog) as session,
    ):
        unmatched = _startup_requests(upstream)
        if not unmatched:
            yield Tools(session, load_toolmap(FIXTURES / "baseline_toolmap.yaml"), log)
            return
    # Failing outside the session: inside it, the failure would reach pytest wrapped
    # in the session's exception group.
    raise UnmatchedUpstream(unmatched, " while the server started")


def _startup_requests(upstream: FixtureServer | None) -> list[str]:
    """The requests the server made while it started that found no fixture; the log is reset."""

    if upstream is None:
        return []
    unmatched = unmatched_requests(upstream.log())
    upstream.reset()
    return unmatched


@pytest.fixture
def tools(
    request: pytest.FixtureRequest,
    target: Target,
    upstream: FixtureServer | None,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Tools]:
    """The required tools of the server under test, for one test.

    A scenario test, and one marked `own_server`, does not use the shared server: it runs
    against its own, which nothing an earlier test asked can have filled.
    """

    scenarios = scenarios_of(request.node)
    own = request.node.get_closest_marker(OWN_SERVER) is not None
    if not (scenarios or own) or upstream is None:
        yield request.getfixturevalue("server")
        return
    upstream.activate(*scenarios)
    settings = upstream.fixtures.settings_of(scenarios)
    try:
        with _tools(target, upstream, tmp_path_factory, settings) as own:
            request.config.stash[COLLECTOR].note_tools(own)
            yield own
    finally:
        upstream.activate()


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
