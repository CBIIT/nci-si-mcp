"""The specification read as data: where a result's items are, the parameters each tool takes,
and the tools of a profile."""

import pytest

from nci_si_acceptance.spec import REQUIRED_TOOLS, items_of, parameters, profile_tools

CONCEPT = {"code": "C4817"}


@pytest.mark.parametrize(
    ("tool", "result", "items"),
    [
        ("get_concept", CONCEPT, [CONCEPT]),
        ("get_concepts", {"concepts": [CONCEPT, CONCEPT], "missing": []}, [CONCEPT, CONCEPT]),
        (
            "resolve_retired_code",
            {**CONCEPT, "replacements": [CONCEPT]},
            [{**CONCEPT, "replacements": [CONCEPT]}, CONCEPT],
        ),
        ("search_concepts", {"results": [{"concept": CONCEPT, "score": 1}]}, [CONCEPT]),
        (
            "get_concept_neighborhood",
            {"nodes": [CONCEPT], "edges": [{"code": "R1"}]},
            [CONCEPT, {"code": "R1"}],
        ),
        # A result without the parts its items name has none: the tests then fail, saying so.
        ("search_concepts", {"hits": [CONCEPT]}, []),
        ("search_concepts", {"results": [{"concept": None}]}, []),
        ("get_concepts", "an error, in words", []),
        ("get_concept", None, []),
        # A tool that declares no items yet.
        ("get_data_element", {"publicId": "2200604"}, []),
    ],
)
def test_the_items_of_a_result_are_where_the_tool_says(tool, result, items):
    assert items_of(tool, result) == items


@pytest.mark.parametrize(
    ("tool", "names", "required"),
    [
        (
            "get_concept",
            {"terminology", "release", "code", "include"},
            {"terminology", "release", "code"},
        ),
        ("resolve_release", {"terminology", "channel"}, {"terminology"}),
        # Alternatives: none of them is required.
        (
            "expand_value_set",
            {"terminology", "release", "valueSet", "code", "count", "offset", "activeOnly"},
            {"terminology", "release"},
        ),
        ("get_form", {"publicId", "keyword", "version", "includeModules"}, set()),
        # Three ways to name a data element, of which a caller gives one.
        (
            "get_data_element",
            {"publicId", "version", "longName", "questionText", "include"},
            set(),
        ),
        # An alternative of several parameters, in braces.
        (
            "get_concept_for_permissible_value",
            {"permissibleValueId", "dataElementId", "value"},
            set(),
        ),
        # A list of objects is one parameter; its fields are not.
        (
            "harmonize_data_dictionary",
            {"columns", "registryRelease", "filters"},
            {"columns", "registryRelease"},
        ),
        ("list_terminologies", set(), set()),
    ],
)
def test_a_tool_takes_the_parameters_its_inputs_name(tool, names, required):
    assert parameters(tool) == (names, required)


def test_a_module_profile_serves_its_own_group_and_unified_serves_every_tool():
    evs, cadsr, unified = (profile_tools(profile) for profile in ("evs", "cadsr", "unified"))

    assert {REQUIRED_TOOLS[name] for name in evs} == {"evs"}
    assert {REQUIRED_TOOLS[name] for name in cadsr} == {"cadsr"}
    assert (len(evs), len(cadsr), unified) == (12, 10, set(REQUIRED_TOOLS))
