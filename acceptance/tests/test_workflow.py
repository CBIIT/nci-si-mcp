"""The workflow tools: ground_value, expand_cohort and harmonize_data_dictionary. Each answer
is read from the recordings of the surfaces it rests on."""

import json

import pytest

from nci_si_acceptance.results import bare_code, element_ids, error_code, sparql_rows, value_ids
from nci_si_acceptance.spec import TOOLS, defaults

GROUND = "ground_value"
COHORT = "expand_cohort"
HARMONIZE = "harmonize_data_dictionary"
HIERARCHY = "get_concept_hierarchy"
NEIGHBORHOOD = "get_concept_neighborhood"
SPARQL = "recorded/ssis-sparql"
# Ewing Sarcoma: 36 data elements and 29 permissible values in the caDSR graph; Disease or
# Disorder: 1,001 data element rows, more than a hop holds, and 461 values (the recordings).
EWING, DISEASE = "C4817", "C2991"
# What a hop holds at most: find_data_elements_for_concept's maximum (ground_value's summary).
HOP_MAXIMUM = TOOLS["find_data_elements_for_concept"]["bounds"]["limit"]["maximum"]
UNPINNED_REGISTRY = {"registry": "cadsr"}


def _ok(result):
    assert not result.is_error, result.content
    return result.content


def _rows(recorded, name):
    return sparql_rows(recorded(f"{SPARQL}/{name}.json"))


def _ground(tools, pinned, **arguments):
    return tools.call(GROUND, {"conceptCode": EWING, "release": pinned["release"]} | arguments)


@pytest.mark.tool(GROUND)
@pytest.mark.requirement("ground_value-1")
def test_one_envelope_names_the_release_asked_for_and_the_registry_unpinned(tools, pinned):
    provenance = _ok(_ground(tools, pinned))["provenance"]

    release = provenance["release"]
    assert (release["terminology"], release["identifier"]) == (
        pinned["terminology"],
        pinned["release"],
    )
    assert provenance["registry"] == UNPINNED_REGISTRY


@pytest.mark.scenario("release/mismatch")
@pytest.mark.tool(GROUND)
@pytest.mark.requirement("ground_value-1")
def test_content_of_another_ncit_release_fails_closed(tools, pinned):
    assert error_code(_ground(tools, pinned)) == "release_mismatch"


@pytest.mark.tool(GROUND)
@pytest.mark.requirement("ground_value-2")
def test_each_hop_is_what_its_surface_answers_for_the_concept(tools, pinned, recorded):
    elements = {(row["id"], row["version"]) for row in _rows(recorded, "data-elements-c4817")}
    values = {
        (row["id"], row["version"], row["value"], bare_code(row["concept"]))
        for row in _rows(recorded, "values-c4817")
    }
    # The concept has both, or the hops would show nothing.
    assert (bool(elements), bool(values)) == (True, True)

    content = _ok(_ground(tools, pinned))

    assert content["concept"]["code"] == EWING
    assert element_ids(content["dataElements"]) == elements
    assert value_ids(content["permissibleValues"]) == values
    # Without commons the stored value hop does not run, and says so by its absence (A2.6).
    assert "storedValues" not in content


@pytest.mark.tool(GROUND)
@pytest.mark.requirement("ground_value-2")
def test_with_a_commons_the_stored_values_are_the_ones_it_uses_for_the_concept(
    tools, pinned, recorded
):
    maps = recorded("recorded/evs/mapset-gdc-maps-code.json")["response"]["body"]["maps"]
    assert maps

    content = _ok(_ground(tools, pinned, commons="GDC"))

    assert {(value["value"], value["field"]) for value in content["storedValues"]} == {
        (each["targetName"], each["targetCode"]) for each in maps
    }


@pytest.mark.tool(GROUND)
@pytest.mark.requirement("ground_value-3")
def test_a_text_resolves_to_the_first_concept_lexical_search_finds(tools, pinned, recorded):
    search = recorded("recorded/evs/search-contains.json")
    text = search["request"]["params"]["term"][0]

    content = _ok(tools.call(GROUND, {"text": text, "release": pinned["release"]}))

    assert content["concept"]["code"] == search["response"]["body"]["concepts"][0]["code"]


@pytest.mark.tool(GROUND)
@pytest.mark.requirement("ground_value-4")
def test_a_hop_that_reaches_its_bound_leaves_the_others_complete(tools, pinned, recorded):
    elements = {(row["id"], row["version"]) for row in _rows(recorded, "data-elements-c2991")}
    values = {
        (row["id"], row["version"], row["value"], bare_code(row["concept"]))
        for row in _rows(recorded, "values-c2991")
    }
    # The data element hop exceeds the maximum and the value hop does not.
    assert (len(elements) > HOP_MAXIMUM, len(values) <= HOP_MAXIMUM) == (True, True)

    content = _ok(_ground(tools, pinned, conceptCode=DISEASE))

    truncation = content["truncation"]
    hop = truncation["perHop"]["dataElements"]
    assert truncation["occurred"] is True
    assert (hop["occurred"], hop["bound"], hop["limit"], hop["reached"], hop["exact"]) == (
        True,
        "results",
        HOP_MAXIMUM,
        HOP_MAXIMUM,
        False,
    )
    assert hop["omitted"] >= 1
    assert element_ids(content["dataElements"]) <= elements
    assert len(content["dataElements"]) == HOP_MAXIMUM
    # The values are asked by the concept, not by the data elements cut: all of them come.
    assert truncation["perHop"].get("permissibleValues", {}).get("occurred", False) is False
    assert value_ids(content["permissibleValues"]) == values


def _cohort_parts(tools, pinned):
    """expand_cohort's composition: the hierarchy below the concept to the default maxDepth,
    and the exclusion assertions of the concept itself."""

    depth = defaults(COHORT)["maxDepth"]
    hierarchy = _ok(
        tools.call(HIERARCHY, pinned | {"code": EWING, "direction": "child", "depth": depth})
    )
    neighborhood = _ok(
        tools.call(NEIGHBORHOOD, pinned | {"code": EWING, "depth": 1, "kinds": ["role"]})
    )
    descendants = {node["code"] for node in hierarchy["nodes"]}
    exclusions = {
        edge["targetCode"]: edge["provenance"]["relationship"]["code"]
        for edge in neighborhood["edges"]
        if edge["sourceCode"] == EWING and edge["provenance"]["polarity"] == "negative"
    }
    return descendants, exclusions


@pytest.mark.scenario("traversal/exclusions")
@pytest.mark.tool(COHORT)
@pytest.mark.requirement("expand_cohort-1")
@pytest.mark.parametrize("negative", [False, True])
def test_the_cohort_is_the_concept_and_its_descendants_less_its_own_exclusions(
    tools, pinned, negative
):
    descendants, exclusions = _cohort_parts(tools, pinned)
    # The scenario's concept excludes one of its own descendants, so withholding shows.
    assert descendants & set(exclusions)

    content = _ok(tools.call(COHORT, pinned | {"conceptCode": EWING, "includeNegative": negative}))

    members = {EWING} | descendants
    assert set(content["codes"]) == (members if negative else members - set(exclusions))
    assert {
        each["code"]: each["edge"]["provenance"]["relationship"]["code"]
        for each in content["excluded"]
    } == exclusions


# A column CDE Match matches (2200604 among others), with a sample value vmMatch aligns, and one
# it matches to nothing (both crafted to the contract, cadsr/credentialed).
COLUMNS = [{"name": "Patient Gender", "sampleValues": ["Male"]}, {"name": "Freezer Shelf Label"}]


def _entities(entry):
    """The entities a logged CDE Match request asks for: one object, or an array of them."""

    body = json.loads(entry["body"])
    return [each["entity"] for each in (body if isinstance(body, list) else [body])]


@pytest.mark.scenario("cadsr/credentialed")
@pytest.mark.tool(HARMONIZE)
@pytest.mark.requirement("harmonize_data_dictionary-1")
def test_each_column_is_matched_once_and_the_unmatched_are_listed(tools, upstream, recorded):
    unmatched = recorded("scenarios/cadsr/credentialed/cde-match-unmatched.json")
    assert unmatched["response"]["body"]["matchResults"]["matches"] == []

    content = _ok(tools.call(HARMONIZE, {"columns": COLUMNS}))

    # Each column is asked for once, alone or in a batch.
    matching = [entry for entry in upstream.log() if entry["path"].endswith("/cdeMatch")]
    asked = sorted(entity for entry in matching for entity in _entities(entry))
    assert asked == sorted(column["name"] for column in COLUMNS)
    assert content["unmatched"] == ["Freezer Shelf Label"]
    matched = {column["name"]: column for column in content["columns"]}["Patient Gender"]
    registries = [match["dataElement"]["provenance"]["release"] for match in matched["matches"]]
    assert registries
    assert registries == [UNPINNED_REGISTRY] * len(registries)


@pytest.mark.scenario("cadsr/credentialed")
@pytest.mark.tool(HARMONIZE)
@pytest.mark.requirement("harmonize_data_dictionary-1")
def test_a_column_s_sample_values_align_to_the_value_meanings_vmmatch_gives(tools, recorded):
    (result,) = recorded("recorded/cadsr/vm-match-male.json")["response"]["body"]["matchResults"]
    expected = {(match["itemId"], match["concept"]) for match in result["matches"]}

    content = _ok(tools.call(HARMONIZE, {"columns": COLUMNS}))

    matched = {column["name"]: column for column in content["columns"]}["Patient Gender"]
    aligned = matched["permissibleValueAlignment"]
    assert {(each["item"]["publicId"], each["item"].get("concept")) for each in aligned} == expected
