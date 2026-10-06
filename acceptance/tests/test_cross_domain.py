"""The cross-domain tools' own requirements (spec/requirements.yaml), each test against its tool.

A tool answers from any surface that can (caDSR SOW v1.1 item 6), and the surfaces answer from
their own content: the Shared SI graph is the caDSR export of 1 July 2026, the caDSR API is
live. So a test reads the surface the result's provenance names and checks the answer against
that surface's recording, never one surface against another. The release a call requires can be
verified only against the Shared SI Service's NCIt graph, so the Shared SI Service answers.

What a test expects it reads from the recordings (the `recorded` fixture). A fact it cannot
read there is named beside it, with the fixture file that holds it.
"""

from datetime import date, datetime
from itertools import combinations

import pytest

from nci_si_acceptance.results import (
    EXPORT_LISTING,
    bare_code,
    element_ids,
    error_code,
    export_date,
    sparql_rows,
    value_ids,
)
from nci_si_acceptance.spec import TOOLS, defaults

FIND = "find_data_elements_for_concept"
PERMISSIBLE = "get_concept_for_permissible_value"
STORED = "resolve_stored_value"
ALIGNMENT = "get_release_alignment"
# Gender: 17 data elements in the caDSR graph, 27 with its descendants (the recordings).
GENDER = "C17357"
SPARQL = "recorded/ssis-sparql"
THESAURUS = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.rdf"
CADSR_GRAPH = "http://cbiit.nci.nih.gov/caDSR"
CROSSWALK = "recorded/cadsr/crdc-list.json"


def _ok(result):
    assert not result.is_error, result.content
    return result.content


def _rows(recorded, name):
    return sparql_rows(recorded(f"{SPARQL}/{name}.json"))


def _sources(items):
    return {(item.get("provenance") or {}).get("source") for item in items}


def _find(tools, pinned, **arguments):
    return tools.call(FIND, {"conceptCode": GENDER} | pinned | arguments)


@pytest.mark.tool(FIND)
@pytest.mark.requirement("find_data_elements_for_concept-1")
@pytest.mark.parametrize(
    ("expand", "recording"),
    [(False, "data-elements-c17357"), (True, "data-elements-c17357-descendants")],
)
def test_the_data_elements_are_the_concept_s_and_with_expansion_its_descendants_too(
    tools, pinned, recorded, expand, recording
):
    expected = {(row["id"], row["version"]) for row in _rows(recorded, recording)}

    content = _ok(_find(tools, pinned, expandDescendants=expand))

    # The release is verified against the NCIt graph, so the Shared SI Service answers.
    assert _sources(content["dataElements"]) == {"ssis_sparql"}
    assert element_ids(content["dataElements"]) == expected
    assert "permissibleValues" not in content


@pytest.mark.tool(FIND)
@pytest.mark.requirement("find_data_elements_for_concept-2")
@pytest.mark.parametrize(
    ("expand", "recording"), [(False, "values-c17357"), (True, "values-c17357-descendants")]
)
def test_with_the_flag_the_permissible_values_that_stand_for_the_concept_come_too(
    tools, pinned, recorded, expand, recording
):
    expected = {
        (row["id"], row["version"], row["value"], bare_code(row["concept"]))
        for row in _rows(recorded, recording)
    }

    content = _ok(_find(tools, pinned, expandDescendants=expand, includePermissibleValues=True))

    assert _sources(content["permissibleValues"]) == {"ssis_sparql"}
    assert value_ids(content["permissibleValues"]) == expected


def _iso(text):
    """A graph's date as ISO-8601: the NCIt graphs give it as "September 28, 2026", the caDSR
    graph as "2026-07-01" (graph-identities.json)."""

    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return datetime.strptime(text, "%B %d, %Y").date().isoformat()


@pytest.mark.tool(FIND)
@pytest.mark.requirement("find_data_elements_for_concept-3")
def test_an_answer_of_the_shared_si_service_names_both_graphs_and_both_content_states(
    tools, pinned, recorded
):
    graphs = {row["graph"]: row for row in _rows(recorded, "graph-identities")}
    expected = [
        {"graph": THESAURUS, "version": graphs[THESAURUS]["version"]}
        | {"date": _iso(graphs[THESAURUS]["date"])},
        {"graph": CADSR_GRAPH, "date": _iso(graphs[CADSR_GRAPH]["date"])},
    ]

    content = _ok(_find(tools, pinned))

    provenances = [use["provenance"] for use in content["dataElements"]]
    assert {
        (each["release"]["terminology"], each["release"]["identifier"]) for each in provenances
    } == {(pinned["terminology"], pinned["release"])}
    # caDSR publishes no registry release: the registry state names neither (A3.8.1).
    assert [each.get("registry") for each in provenances] == [{"registry": "cadsr"}] * len(
        provenances
    )
    by_graph = {"key": lambda graph: graph["graph"]}
    assert all(
        sorted(each["graphs"], **by_graph) == sorted(expected, **by_graph) for each in provenances
    )


@pytest.mark.tool(FIND)
@pytest.mark.requirement("find_data_elements_for_concept-4")
def test_a_release_other_than_the_ncit_graph_s_fails_closed(tools, pinned, recorded):
    graphs = {row["graph"]: row for row in _rows(recorded, "graph-identities")}
    listing = recorded("recorded/evs/terminologies.json")["response"]["body"]
    # Another monthly release EVS serves, which the graph is not (terminologies.json).
    other = next(
        row["version"]
        for row in listing
        if row["terminology"] == pinned["terminology"]
        and row["version"] != graphs[THESAURUS]["version"]
    )

    result = _find(tools, pinned | {"release": other})

    assert error_code(result) == "release_mismatch", result.content


@pytest.mark.tool(FIND)
@pytest.mark.requirement("find_data_elements_for_concept-4")
def test_a_terminology_other_than_ncit_is_an_invalid_request(tools, pinned):
    result = _find(tools, pinned | {"terminology": "mdr"})

    assert error_code(result) == "invalid_request", result.content


@pytest.mark.tool(FIND)
@pytest.mark.requirement("find_data_elements_for_concept-5")
def test_descendants_beyond_the_tool_s_maximum_are_truncated_with_how_much_was_left_out(
    tools, pinned, recorded
):
    maximum = TOOLS[FIND]["bounds"]["limit"]["maximum"]
    # The query asks one row more than the maximum, and Disease or Disorder fills it.
    assert len(_rows(recorded, "data-elements-c2991-descendants")) == maximum + 1

    content = _ok(_find(tools, pinned, conceptCode="C2991", expandDescendants=True, limit=maximum))

    record = content["truncation"]
    assert len(content["dataElements"]) == maximum
    assert (record["occurred"], record["bound"], record["limit"], record["reached"]) == (
        True,
        "results",
        maximum,
        maximum,
    )
    assert (record["omitted"] >= 1, record["exact"]) == (True, False)
    assert "nextCursor" not in content


# The data element and value the tool is asked about, and the surfaces that can answer: the
# Shared SI Service's permissible values of the data element, asked by its id, or caDSR REST's
# data element with its value meanings, the concept record from EVS (caDSR SOW item 6).
ELEMENT, VALUE = "2200604", "Male"
CADSR_ELEMENT = f"recorded/cadsr/data-element-{ELEMENT}.json"


def _value_concept(recorded, source):
    """The concept VALUE of ELEMENT stands for, and the data element's version, as the surface
    a result's provenance names answers them."""

    if source == "ssis_sparql":
        rows = _rows(recorded, f"values-of-{ELEMENT}")
        (row,) = [
            row for row in rows if row["value"] == VALUE and row["role"].endswith("main_concept")
        ]
        return bare_code(row["concept"]), row["version"]
    element = recorded(CADSR_ELEMENT)["response"]["body"]["DataElement"]
    (code,) = _primary_concepts(element)
    return code, element["version"]


def _primary_concepts(element):
    """The primary concepts of the value meaning of VALUE in a caDSR data element."""

    meanings = [
        value["ValueMeaning"]
        for value in element["ValueDomain"]["PermissibleValues"]
        if value["value"] == VALUE
    ]
    concepts = [concept for meaning in meanings for concept in meaning["Concepts"]]
    return [concept["conceptCode"] for concept in concepts if concept["primaryIndicator"] == "Yes"]


def _permissible(tools, pinned, value=VALUE):
    return tools.call(
        PERMISSIBLE, {"dataElementId": ELEMENT, "value": value, "release": pinned["release"]}
    )


@pytest.mark.tool(PERMISSIBLE)
@pytest.mark.requirement("get_concept_for_permissible_value-1")
def test_a_value_resolves_to_the_concept_it_stands_for_naming_both_content_states(
    tools, pinned, recorded
):
    content = _ok(_permissible(tools, pinned))

    provenance = content["provenance"]
    assert provenance["source"] in {"ssis_sparql", "cadsr_rest", "evs_rest"}
    code, version = _value_concept(recorded, provenance["source"])
    concept = recorded(f"recorded/evs/concepts/{code}.json")["response"]["body"]
    assert (content["code"], content["name"]) == (concept["code"], concept["name"])
    assert (provenance["release"]["terminology"], provenance["release"]["identifier"]) == (
        pinned["terminology"],
        pinned["release"],
    )
    assert provenance["registry"] == {"registry": "cadsr"}
    assert content["permissibleValue"] == {
        "dataElement": {"publicId": ELEMENT, "version": version},
        "value": VALUE,
    }


@pytest.mark.tool(PERMISSIBLE)
@pytest.mark.requirement("get_concept_for_permissible_value-3")
@pytest.mark.parametrize("value", [VALUE.lower(), VALUE.upper(), f" {VALUE}", f"{VALUE} "])
def test_a_value_the_data_element_does_not_have_as_given_is_not_found(
    tools, pinned, recorded, value
):
    # Each surface holds the value only as given, so no surface's answer has this one.
    rows = _rows(recorded, f"values-of-{ELEMENT}")
    element = recorded(CADSR_ELEMENT)["response"]["body"]["DataElement"]
    held = {row["value"] for row in rows} | {
        each["value"] for each in element["ValueDomain"]["PermissibleValues"]
    }
    assert (VALUE in held, value in held) == (True, False)

    assert error_code(_permissible(tools, pinned, value)) == "not_found"


@pytest.mark.tool(PERMISSIBLE)
@pytest.mark.requirement("get_concept_for_permissible_value-2")
def test_a_permissible_value_by_its_identifier_is_unavailable(tools, pinned, recorded):
    asked = recorded("recorded/cadsr/permissible-value-9192925.json")
    # The platform's path for a value by its identifier answers 404 (OP-C10).
    assert asked["response"]["status"] == 404  # noqa: PLR2004 - HTTP Not Found
    identifier = asked["request"]["path"].rpartition("/")[2]

    result = tools.call(
        PERMISSIBLE, {"permissibleValueId": identifier, "release": pinned["release"]}
    )

    assert error_code(result) == "capability_unavailable", result.content


@pytest.mark.tool(STORED)
@pytest.mark.requirement("resolve_stored_value-1")
def test_a_gdc_value_resolves_through_the_mapset_its_source_names(tools, pinned, recorded):
    maps = recorded("recorded/evs/mapset-gdc-maps-code.json")["response"]["body"]["maps"]
    # EVS term search also returns the unrelated prefix hit C48177.
    maps = [row for row in maps if row["sourceCode"] == "C4817"]
    mapset = recorded("recorded/evs/mapset-gdc.json")["response"]["body"]
    source = {"mapset": mapset["code"], "version": mapset["version"]}

    content = _ok(
        tools.call(STORED, {"conceptCode": "C4817", "commons": "GDC", "release": pinned["release"]})
    )

    stored = content["storedValues"]
    assert {(value["value"], value["field"]) for value in stored} == {
        (each["targetName"], each["targetCode"]) for each in maps
    }
    evidence = content["evidence"]
    assert [value["source"] for value in stored] == [source] * len(stored)
    assert (content["confidence"], source in evidence["sources"]) == ("asserted", True)
    assert (evidence["valueLevelBinding"], evidence["coverage"]) == (True, len(stored))


def _bound(crosswalk, commons, code):
    """The values of the crosswalk's data elements a commons uses that stand for `code`, each
    with its data element's CRDC name, public id and version."""

    return {
        (
            value["Permissible Value"],
            element["CRDC Name"],
            element["CDE Public ID"],
            element["Version"],
        )
        for element in crosswalk
        if commons in _users(element)
        # A data element without enumerated values has no permissibleValues (crdc-list.json).
        for value in element.get("permissibleValues", [])
        if code in (value["Concept Code"] or "").split(":")
    }


def _users(element):
    """The contexts and commons the crosswalk says use a data element."""

    return [user.strip() for user in (element["Used By"] or "").split(",")]


def _crosswalked(value):
    element = value["source"]["dataElement"]
    return value["value"], value["field"], element["publicId"], element["version"]


@pytest.mark.tool(STORED)
@pytest.mark.requirement("resolve_stored_value-2")
def test_another_commons_value_resolves_through_the_crdc_crosswalk(tools, pinned, recorded):
    crosswalk = recorded(CROSSWALK)["response"]["body"]["CRDCDataElements"]
    # Sex at Birth binds Male to C20197 for Pediatric Cancer (crdc-list.json).
    expected = _bound(crosswalk, "Pediatric Cancer", "C20197")
    assert expected

    arguments = {"conceptCode": "C20197", "commons": "Pediatric Cancer"}
    content = _ok(tools.call(STORED, arguments | {"release": pinned["release"]}))

    stored = content["storedValues"]
    evidence = content["evidence"]
    assert set(map(_crosswalked, stored)) == expected
    assert {value["source"]["crosswalk"] for value in stored} == {"CRDC"}
    assert all(value["source"] in evidence["sources"] for value in stored)
    assert (content["confidence"], evidence["valueLevelBinding"]) == ("asserted", True)
    assert evidence["coverage"] == len(stored)


@pytest.mark.tool(STORED)
@pytest.mark.requirement("resolve_stored_value-3")
def test_a_commons_without_a_value_level_binding_stores_no_value_and_says_so(
    tools, pinned, recorded
):
    crosswalk = recorded(CROSSWALK)["response"]["body"]["CRDCDataElements"]
    # No data element of the crosswalk is used by PDC (crdc-list.json).
    assert not [element for element in crosswalk if "PDC" in _users(element)]

    content = _ok(
        tools.call(STORED, {"conceptCode": "C4817", "commons": "PDC", "release": pinned["release"]})
    )

    evidence = content["evidence"]
    assert (content["storedValues"], content["confidence"]) == ([], "none")
    assert (evidence["valueLevelBinding"], evidence["coverage"]) == (False, 0)


def _datasets(recorded):
    """Each dataset's version and ISO date, as its source gives them."""

    (ncit,) = recorded("recorded/evs/release-monthly.json")["response"]["body"]
    graphs = {row["graph"]: row for row in _rows(recorded, "graph-identities")}
    return {
        "ncit": (ncit["version"], ncit["date"]),
        "ssis_ncit_graph": (graphs[THESAURUS]["version"], _iso(graphs[THESAURUS]["date"])),
        "ssis_cadsr_graph": (None, _iso(graphs[CADSR_GRAPH]["date"])),
        "cadsr_export": (None, export_date(recorded(EXPORT_LISTING)["response"]["body"])),
    }


@pytest.mark.tool(ALIGNMENT)
@pytest.mark.requirement("get_release_alignment-1")
@pytest.mark.parametrize("threshold", [None, 120])
def test_every_dataset_is_dated_with_the_largest_interval_and_a_warning_above_the_threshold(
    tools, recorded, threshold
):
    expected = _datasets(recorded)
    interval = max(
        abs((date.fromisoformat(one) - date.fromisoformat(other)).days)
        for (_, one), (_, other) in combinations(expected.values(), 2)
    )
    maximum = threshold or defaults(ALIGNMENT)["maxIntervalDays"]

    result = tools.call(ALIGNMENT, {} if threshold is None else {"maxIntervalDays": threshold})

    content = _ok(result)
    found = {each["name"]: (each.get("version"), each["date"]) for each in content["datasets"]}
    assert found == expected
    assert content["intervalDays"] == interval
    # The recorded dates are 89 days apart: above the default of 31, below 120.
    assert ("warning" in content, str(maximum) in content.get("warning", "")) == (
        interval > maximum,
        interval > maximum,
    )
    assert result.meta.get("ttlMs") == 0
