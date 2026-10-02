"""Licensed content is neither requested for recording nor left in a payload, and a
terminology nobody decided on stops the recording."""

import pytest

from nci_si_acceptance.licensing import Licensing, named_terminologies

DENY = Licensing.from_manifest(
    {
        "licensed": ["mdr", "MedDRA", "snomedct_us", "http://snomed.info/sct"],
        "allowed": ["NCI", "GDC"],
        "mapsets": ["NCIt_Maps_To_MedDRA"],
    }
)


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/concept/mdr/10000001", {}),
        ("/api/v1/concept/mdr_29_0/10000001", {}),
        ("/api/v1/metadata/snomedct_us/roles", {}),
        ("/api/v1/mapset/NCIt_Maps_To_MedDRA/maps", {}),
        ("/api/v1/concept/search", {"terminology": ["ncit,MDR"]}),
        ("/ConceptMap/ncit_maps_to_meddra_26.09d", {}),
        ("/ConceptMap/$translate", {"url": ["http://x?fhir_cm=NCIt_Maps_To_MedDRA"]}),
        ("/ValueSet/$expand", {"url": ["http://snomed.info/sct?fhir_vs"]}),
        ("/CodeSystem/$lookup", {"system": ["http://snomed.info/sct/731000124108"]}),
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


def test_the_licensing_section_names_licensed_allowed_and_mapsets():
    with pytest.raises(ValueError, match="licensing has licensed, allowed and mapsets"):
        Licensing.from_manifest({"licensed": ["mdr"], "mapsets": []})


def test_every_terminology_a_payload_names_is_found_at_any_depth():
    payload = {
        "source": "NCI",
        "synonyms": [{"source": "CTRP"}, {"source": None}],
        "nested": [[{"target": "GDC", "targetTerminology": "ICDO3"}]],
    }

    assert named_terminologies(payload) == {"NCI", "CTRP", "GDC", "ICDO3"}


def test_a_licensed_name_left_anywhere_is_found_even_where_redaction_cannot_reach():
    payload = {"source": "MDR", "contains": [{"system": "http://snomed.info/sct?fhir_vs"}]}

    assert DENY.licensed_names(DENY.redact(payload)[0]) == ["MDR"]
    assert DENY.redact(payload)[1] == ["/contains/0 (http://snomed.info/sct?fhir_vs)"]


def test_a_terminology_neither_licensed_nor_allowed_is_undecided_in_any_case():
    payload = {"maps": [{"targetTerminology": "gdc"}, {"targetTerminology": "MEDDRA"}]}
    payload["synonyms"] = [{"source": "Snomed CT"}, {"source": "HemOnc"}]

    assert DENY.undecided(payload) == ["HemOnc", "Snomed CT"]
