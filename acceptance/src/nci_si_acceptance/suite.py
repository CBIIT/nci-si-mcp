"""The rules of a suite run that do not depend on pytest: what runs in which mode, and
which upstream requests a test may leave without a fixture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterable

LIVE_CAPABLE = "live_capable"
# A test marked so expects requests without a fixture, and checks them itself.
UNMATCHED_UPSTREAM = "unmatched_upstream"


def skip_fixture_only(items: Iterable[pytest.Item]) -> None:
    """Skip every test that is not marked live-capable: what a live run does."""

    skip = pytest.mark.skip(reason="fixture mode only")
    for item in items:
        if item.get_closest_marker(LIVE_CAPABLE) is None:
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
