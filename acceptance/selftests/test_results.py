"""What the suite's tests read from a result and from the upstream request log."""

import json

import pytest

from nci_si_acceptance.record import FIXTURES
from nci_si_acceptance.results import (
    CARRIED,
    EXPORT_LISTING,
    bare_code,
    element_ids,
    error_code,
    export_date,
    identity,
    is_timestamp,
    pinned_release,
    provenance_of,
    release_of,
    requests_naming,
    sparql_rows,
    value_ids,
    wrong_fields,
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
        # A record of a release has a version in place of a code, and the registry's state is
        # known by whether it publishes a release and its date.
        ({"terminology": "ncit", "version": "26.09d", "channel": "monthly"}, ("ncit", "26.09d")),
        ({"published": False, "generatedAt": "2026-07-01"}, (False, None, "2026-07-01")),
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
    # A cross-domain tool without a release names the NCIt release its content rests on.
    assert pinned_release("resolve_stored_value", pinned) == ("ncit", "26.09d")


def test_the_export_date_is_the_one_the_folder_listing_gives_the_export():
    listing = json.loads((FIXTURES / EXPORT_LISTING).read_text(encoding="utf-8"))
    # The listing dates releasedCDEsXML-OD.zip 2026-07-01 22:19 (cde-xml-listing.json).
    assert export_date(listing["response"]["body"]) == "2026-07-01"
    with pytest.raises(ValueError, match="unpack"):
        export_date("<a>README</a> 2026-07-01")


def test_a_recorded_sparql_answer_reads_as_rows_of_values_and_bare_codes():
    rows = sparql_rows(
        json.loads((FIXTURES / "recorded/ssis-sparql/values-c4817.json").read_text())
    )

    # The recording's first row (values-c4817.json), each variable by its value.
    assert set(rows[0]) == {"id", "version", "value", "concept"}
    assert {bare_code(row["concept"]) for row in rows} == {"C4817"}


def test_uses_are_named_by_their_data_element_and_value():
    use = {"dataElement": {"publicId": "2200604", "version": "4"}, "value": "Male"}

    assert element_ids([use]) == {("2200604", "4")}
    assert value_ids([use | {"conceptCode": "C20197"}]) == {("2200604", "4", "Male", "C20197")}


PROVENANCE = {
    "release": {"terminology": "ncit", "identifier": "26.09d"},
    "source": "evs_rest",
    "servedBy": "live",
    "retrievedAt": "2026-10-04T10:00:00+00:00",
}


def test_a_provenance_record_lacking_a_carried_field_or_holding_one_off_its_set_is_named():
    assert wrong_fields(PROVENANCE, CARRIED) == []
    assert wrong_fields(PROVENANCE | {"source": "somewhere"}, CARRIED) == ["source"]
    assert wrong_fields({k: v for k, v in PROVENANCE.items() if k != "release"}, CARRIED) == [
        "release"
    ]


@pytest.mark.parametrize(
    ("item", "provenance"),
    [({"provenance": PROVENANCE}, PROVENANCE), ({"provenance": None}, {}), ("text", {})],
)
def test_an_item_s_provenance_is_its_record_or_none(item, provenance):
    assert provenance_of(item) == provenance


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("2026-10-04T10:00:00+00:00", True),
        ("2026-10-04T10:00:00", False),
        ("today", False),
        (None, False),
    ],
)
def test_a_timestamp_is_iso_8601_with_a_time_zone(value, valid):
    assert is_timestamp(value) is valid
