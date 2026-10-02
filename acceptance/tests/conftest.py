"""The run of the suite: the server under test, its upstream, and the request log."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from nci_si_acceptance.client import Session, Target, open_session, server_environment
from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures

if TYPE_CHECKING:
    from collections.abc import Iterator

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="session")
def target() -> Target:
    return Target.from_env()


@pytest.fixture(scope="session")
def upstream(target: Target) -> Iterator[FixtureServer | None]:
    """The fixture server in fixture mode; None in live mode."""

    if target.mode == "live":
        yield None
        return
    with FixtureServer(load_fixtures(FIXTURES)) as server:
        yield server


@pytest.fixture(scope="session")
def mcp(
    target: Target, upstream: FixtureServer | None, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Session]:
    url = upstream.url if upstream else None
    environment = server_environment(target.mode, tmp_path_factory.mktemp("data"), url)
    with open_session(target.command, environment) as session:
        yield session


@pytest.fixture(autouse=True)
def fresh_log(upstream: FixtureServer | None) -> None:
    """Each test reads only the upstream requests it caused."""

    if upstream is not None:
        upstream.clear_log()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """In live mode only the tests marked live_capable run."""

    if Target.from_env().mode != "live":
        return
    skip = pytest.mark.skip(reason="fixture mode only")
    for item in items:
        if "live_capable" not in item.keywords:
            item.add_marker(skip)
