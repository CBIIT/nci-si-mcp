"""The specification read as data: the parameters each tool takes, and the tools of a profile."""

import pytest

from nci_si_acceptance.spec import REQUIRED_TOOLS, parameters, profile_tools


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
        (
            "get_data_element",
            {"publicId", "version", "longName", "questionText", "include"},
            {"publicId"},
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
