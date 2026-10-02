"""The rules of a suite run that do not depend on pytest: what runs in which mode, and
which upstream requests a test may leave without a fixture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterable

LIVE_CAPABLE = "live_capable"
SCENARIO = "scenario"
FIXTURE_ONLY = "fixture mode only"
# A test marked so expects requests without a fixture, and checks them itself.
UNMATCHED_UPSTREAM = "unmatched_upstream"


class UnmatchedUpstream(pytest.fail.Exception):
    """A test fails because upstream requests found no fixture; `requests` names them.

    The report counts such a failure as NO FIXTURE, for each test it reaches: a server
    whose startup requests lacked fixtures fails every test that uses it.
    """

    def __init__(self, requests: list[str], when: str = "") -> None:
        super().__init__(f"upstream requests without a fixture{when}:\n" + "\n".join(requests))
        self.requests = requests


def scenarios_of(item: pytest.Item) -> tuple[str, ...]:
    """The scenarios a test selects, over all its `scenario` markers."""

    return tuple(name for marker in item.iter_markers(SCENARIO) for name in marker.args)


def skip_fixture_only(items: Iterable[pytest.Item]) -> None:
    """What a live run does: skip every test not marked live-capable, and every scenario."""

    skip = pytest.mark.skip(reason=FIXTURE_ONLY)
    for item in items:
        if item.get_closest_marker(LIVE_CAPABLE) is None or scenarios_of(item):
            item.add_marker(skip)


def unmatched_requests(log: Iterable[dict[str, Any]]) -> list[str]:
    """The upstream requests in `log` that no fixture answered, one line each.

    The server under test may treat the fixture server's refusal as an outage and
    answer with a plausible degraded result, so a test that passed while one of its
    requests found no fixture proves nothing.
    """

    return [
        f"{entry['method']} {entry['surface']} {entry['path']} {entry['params']}"
        for entry in log
        if entry["fixture"] is None
    ]
