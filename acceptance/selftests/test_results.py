"""What the suite's tests read from a result and from the upstream request log."""

import pytest

from nci_si_acceptance.results import (
    error_code,
    identity,
    pinned_release,
    release_of,
    requests_naming,
)
from nci_si_acceptance.tools import Result

RECORD = {"code": "invalid_request", "message": "no such include value"}


@pytest.mark.parametrize(
    ("is_error", "content", "code"),
    [
        (True, {"error": RECORD}, "invalid_request"),
        # Content beside the record, a record that is no object, or no error flag: no code.
        (True, {"error": RECORD, "concept": {"code": "C4817"}}, None),
        (True, {"error": "invalid_request"}, None),
        (True, ["invalid_request"], None),
        (True, "Error executing tool", None),
        (False, {"error": RECORD}, None),
    ],
)
def test_an_error_result_has_a_code_only_as_one_error_record(is_error, content, code):
    assert error_code(Result("get_concept", is_error, content, {})) == code


def entry(path, **params):
    return {"path": path, "params": {name: [value] for name, value in params.items()}}


def test_a_request_names_a_code_in_its_path_or_a_parameter_but_not_inside_another_code():
    log = [
        entry("/api/v1/concept/ncit_26.09d", list="C4817,C12578", include="summary"),
        entry("/api/v1/concept/ncit_26.09d/C116977"),
        entry("/api/v1/concept/ncit_26.09d/C48170"),
        entry("/api/v1/metadata/terminologies", terminology="ncit"),
    ]

    assert requests_naming(log, ["C4817", "C116977"]) == log[:2]


ELEMENT = {"publicId": "2200604", "version": "4", "longName": "Person Sex Text Type"}


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        # EVS items keep their identities.
        ({"terminology": "ncit", "code": "C4817", "name": "Ewing Sarcoma"}, ("ncit", "C4817")),
        ({"terminology": "ncit", "release": "26.09d"}, ("ncit", None)),
        (
            {
                "sourceCode": "C1",
                "targetCode": "C2",
                "provenance": {"relationship": {"code": "R1"}},
            },
            ("C1", "C2", "R1"),
        ),
        # caDSR items: a data element and a form by public id and version, a code map by its
        # data element's, a value meaning match's item likewise, a context by its name.
        (ELEMENT, ("2200604", "4")),
        ({"dataElement": {"publicId": "88", "version": "5.1"}, "usedBy": ["GDC"]}, ("88", "5.1")),
        ({"name": "NCIP", "provenance": {}}, "NCIP"),
        # A data element that is no record names nothing to identify the item by but its name.
        ({"dataElement": "2200604", "name": "a code map"}, "a code map"),
        ("bare", "bare"),
    ],
)
def test_an_item_is_identified_by_what_it_is(item, expected):
    assert identity(item) == expected


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        ({"terminology": "ncit", "identifier": "26.09d"}, ("ncit", "26.09d")),
        ({"registry": "cadsr"}, ("cadsr", None)),
        ({"registry": "cadsr", "identifier": "2026.07.02"}, ("cadsr", "2026.07.02")),
        (None, (None, None)),
    ],
)
def test_an_item_s_release_is_its_terminology_or_registry_and_identifier(release, expected):
    item = {"provenance": {"release": release}} if release else {"provenance": None}

    assert release_of(item) == expected


def test_a_call_names_the_release_it_pins_or_for_cadsr_the_registry_alone():
    pinned = {"terminology": "ncit", "release": "26.09d"}

    assert pinned_release("get_concept", pinned) == ("ncit", "26.09d")
    assert pinned_release("get_data_element", pinned) == ("cadsr", None)
