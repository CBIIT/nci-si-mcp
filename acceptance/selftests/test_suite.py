"""The rules of a run: what a live run skips, which scenarios a test selects, and which
requests lacked a fixture."""

import pytest

from nci_si_acceptance.suite import (
    LIVE_CAPABLE,
    NOT_PREPARED,
    PREPARED,
    UnmatchedUpstream,
    index_set,
    scenarios_of,
    skip_fixture_only,
    skip_unprepared,
    unmatched_requests,
)


class Item:
    """The part of a collected test that the rules read and write."""

    def __init__(self, *markers):
        self.markers = [getattr(marker, "mark", marker) for marker in markers]

    def iter_markers(self, name):
        return (marker for marker in self.markers if marker.name == name)

    def get_closest_marker(self, name):
        return next(self.iter_markers(name), None)

    def add_marker(self, marker):
        self.markers.append(marker.mark)


def test_a_live_run_skips_every_test_not_marked_live_capable_and_every_scenario():
    live = Item(getattr(pytest.mark, LIVE_CAPABLE))
    fixture_only = Item()
    scenario = Item(getattr(pytest.mark, LIVE_CAPABLE), pytest.mark.scenario("release/unknown"))

    skip_fixture_only([live, fixture_only, scenario])

    assert live.get_closest_marker("skip") is None
    assert fixture_only.get_closest_marker("skip").kwargs == {"reason": "fixture mode only"}
    assert scenario.get_closest_marker("skip").kwargs == {"reason": "fixture mode only"}


def test_a_test_selects_the_scenarios_of_all_its_markers():
    item = Item(pytest.mark.scenario("a/one", "a/two"), pytest.mark.scenario("b/three"))

    assert scenarios_of(item) == ("a/one", "a/two", "b/three")
    assert scenarios_of(Item()) == ()


def test_a_failure_for_want_of_fixtures_names_the_requests_and_when():
    failure = UnmatchedUpstream(["GET evs /a {}", "GET evs /b {}"], " while the server started")

    assert failure.requests == ["GET evs /a {}", "GET evs /b {}"]
    assert str(failure).splitlines() == [
        "upstream requests without a fixture while the server started:",
        "GET evs /a {}",
        "GET evs /b {}",
    ]


def test_requests_without_a_fixture_are_named_one_per_line():
    log = [
        {
            "method": "GET",
            "surface": "evs",
            "path": "/api/v1/version",
            "params": {},
            "fixture": "v.json",
        },
        {
            "method": "GET",
            "surface": "evs",
            "path": "/api/v1/x",
            "params": {"a": ["1"]},
            "fixture": None,
        },
    ]

    assert unmatched_requests(log) == ["GET evs /api/v1/x {'a': ['1']}"]


def test_without_a_prepare_command_a_test_that_needs_it_is_skipped_as_not_run():
    needs = Item(getattr(pytest.mark, PREPARED))
    other = Item()

    skip_unprepared([needs, other])

    assert needs.get_closest_marker("skip").kwargs == {"reason": NOT_PREPARED}
    assert other.get_closest_marker("skip") is None


def test_the_index_set_is_every_concept_recorded_with_its_summary_in_the_manifest_s_order():
    manifest = {
        "record": {
            "concepts": {
                "full": ["C4817", "C17049"],
                "minimal": ["C2991"],
                "parents,summary": ["C3262"],
                "children": ["C9118"],
            }
        }
    }

    assert index_set(manifest) == ["C4817", "C17049", "C3262"]
