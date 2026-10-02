"""The rules of a run: what a live run skips, and which requests lacked a fixture."""

from nci_si_acceptance.suite import LIVE_CAPABLE, skip_fixture_only, unmatched_requests


class Item:
    """The part of a collected test that the rules read and write."""

    def __init__(self, *markers):
        self.markers = list(markers)

    def get_closest_marker(self, name):
        return next(
            (marker for marker in self.markers if getattr(marker, "name", marker) == name), None
        )

    def add_marker(self, marker):
        self.markers.append(marker)


def test_a_live_run_skips_every_test_not_marked_live_capable():
    live, fixture_only = Item(LIVE_CAPABLE), Item()

    skip_fixture_only([live, fixture_only])

    assert live.get_closest_marker("skip") is None
    assert fixture_only.get_closest_marker("skip").kwargs == {"reason": "fixture mode only"}


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
