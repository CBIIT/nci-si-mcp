"""The run of the suite: the server under test, its upstream, scenarios, and the request log.

A test that selects scenarios (`@pytest.mark.scenario("release/unknown")`) gets a
server process of its own, started with the scenarios active, so that nothing the
server keeps between calls outlives them.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from nci_si_acceptance.client import Target, open_session, server_environment
from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures
from nci_si_acceptance.report import COLLECTOR, write_report
from nci_si_acceptance.suite import UNMATCHED_UPSTREAM, skip_fixture_only, unmatched_requests
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
    target: Target, upstream: FixtureServer | None, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Tools]:
    url = upstream.url if upstream else None
    environment = server_environment(target.mode, tmp_path_factory.mktemp("data"), url)
    with open_session(target.command, environment) as session:
        yield Tools(session, load_toolmap(FIXTURES / "baseline_toolmap.yaml"))


@pytest.fixture
def tools(
    request: pytest.FixtureRequest,
    server: Tools,
    target: Target,
    upstream: FixtureServer | None,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Tools]:
    """The required tools of the server under test, for one test."""

    marker = request.node.get_closest_marker("scenario")
    if marker is None or upstream is None:
        yield server
        return
    upstream.activate(*marker.args)
    try:
        with _tools(target, upstream, tmp_path_factory) as own:
            upstream.reset()
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
        request.node.user_properties.append((UNMATCHED_UPSTREAM, unmatched))
        pytest.fail("upstream requests without a fixture:\n" + "\n".join(unmatched))
