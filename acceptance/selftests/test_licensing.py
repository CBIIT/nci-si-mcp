"""Licensed content is neither requested for recording nor left in a payload."""

import pytest

from nci_si_acceptance.licensing import DenyList

DENY = DenyList.from_manifest(
    {"terminologies": ["mdr", "MedDRA", "snomedct_us"], "mapsets": ["NCIt_Maps_To_MedDRA"]}
)


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/concept/mdr/10000001", {}),
        ("/api/v1/concept/mdr_29_0/10000001", {}),
        ("/api/v1/metadata/snomedct_us/roles", {}),
        ("/api/v1/mapset/NCIt_Maps_To_MedDRA/maps", {}),
        ("/api/v1/concept/search", {"terminology": ["ncit,MDR"]}),
    ],
)
def test_a_request_naming_licensed_content_is_refused_unless_the_service_refused_it(path, params):
    assert "names licensed content" in DENY.request_problem(path, params, 200)
    assert DENY.request_problem(path, params, 403) is None


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/concept/ncit_26.09d/C4817", {"include": ["maps"]}),
        ("/api/v1/mapset/NCIt_Maps_To_GDC", {}),
        ("/api/v1/concept/mdrx/C1", {}),
    ],
)
def test_other_requests_may_be_recorded(path, params):
    assert DENY.request_problem(path, params, 200) is None


def test_licensed_items_are_removed_wherever_they_are_and_each_is_listed():
    payload = {
        "code": "C4817",
        "maps": [
            {"targetName": "x", "targetTerminology": "GDC"},
            {"targetName": "Ewing's sarcoma", "targetTerminology": "MedDRA"},
        ],
        "synonyms": [{"name": "y", "source": "mdr"}, {"name": "z", "source": "NCI"}],
        "properties": [{"type": "Contributing_Source", "value": "MedDRA"}],
        "nested": [[{"target": "SNOMEDCT_US"}]],
    }

    kept, removed = DENY.redact(payload)

    assert kept == {
        "code": "C4817",
        "maps": [{"targetName": "x", "targetTerminology": "GDC"}],
        "synonyms": [{"name": "z", "source": "NCI"}],
        "properties": [{"type": "Contributing_Source", "value": "MedDRA"}],
        "nested": [[]],
    }
    assert removed == ["/maps/1 (MedDRA)", "/synonyms/0 (mdr)", "/nested/0/0 (SNOMEDCT_US)"]
    assert DENY.redact(kept) == (kept, [])


def test_a_payload_without_lists_is_kept_as_it_is():
    assert DENY.redact("<html>no JSON</html>") == ("<html>no JSON</html>", [])


def test_the_deny_list_names_terminologies_and_mapsets():
    with pytest.raises(ValueError, match="deny has terminologies and mapsets"):
        DenyList.from_manifest({"terminologies": ["mdr"]})
