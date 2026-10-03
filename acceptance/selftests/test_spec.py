"""The specification read as data: where a result's items are, the parameters each tool takes,
and the tools of a profile."""

import json
import re

import pytest

from nci_si_acceptance.client import CREDENTIAL_VARIABLES, INDEX_CODES_VARIABLE
from nci_si_acceptance.fixture_server import HARNESS_VARIABLES, SCENARIOS, SETTINGS
from nci_si_acceptance.record import FIXTURES
from nci_si_acceptance.spec import (
    PROFILES,
    PROMPTS,
    RECORDS,
    REQUIRED_TOOLS,
    RESOURCES,
    SPEC,
    TOOLS,
    alternatives,
    defaults,
    items_of,
    parameters,
    profile_tools,
    prompts_of,
    resource_call,
    resources_of,
    tools_named,
    uri_variables,
)

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
        ("get_data_element", {"publicId": "2200604"}, [{"publicId": "2200604"}]),
        # A tool that declares no items: its result is a record of the registry, not an item.
        ("resolve_registry_release", {"published": False}, []),
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
        (
            "get_form",
            {"publicId", "keyword", "version", "includeModules", "registryRelease"},
            set(),
        ),
        # Three ways to name a data element, of which a caller gives one.
        (
            "get_data_element",
            {"publicId", "version", "longName", "questionText", "include", "registryRelease"},
            set(),
        ),
        # An alternative of several parameters, in braces.
        (
            "get_concept_for_permissible_value",
            {"permissibleValueId", "dataElementId", "value", "release"},
            {"release"},
        ),
        # A list of objects is one parameter; its fields are not.
        (
            "harmonize_data_dictionary",
            {"columns", "registryRelease", "filters"},
            {"columns"},
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


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_a_tool_s_defaults_are_its_stated_defaults_and_its_bounds_defaults(tool):
    entry = TOOLS[tool]
    bounds = entry.get("bounds", {})

    found = defaults(tool)

    assert {name: found.get(name) for name in entry.get("defaults", {})} == entry.get(
        "defaults", {}
    )
    # A bound without a default (budgetPerKind) gives none.
    assert {name for name in bounds if name in found} == {
        name for name, bound in bounds.items() if "default" in bound
    }
    assert {name: found[name] for name in bounds if name in found} == {
        name: bound["default"] for name, bound in bounds.items() if "default" in bound
    }


def _settings_rows():
    """Each setting the specification says the suite gives a server, with when it does."""

    rows = (SPEC / "acceptance.md").read_text(encoding="utf-8").splitlines()
    cells = [row.split(" | ") for row in rows if row.startswith("| `NCI_SI_")]
    return {name: when for first, when, _ in cells for name in re.findall(r"`(NCI_SI_\w+)`", first)}


def _scenario_settings():
    """Each setting a scenario's settings.json gives its server, with the scenarios that do."""

    found = {}
    for path in sorted((FIXTURES / SCENARIOS).glob(f"*/*/{SETTINGS}")):
        name = path.parent.relative_to(FIXTURES / SCENARIOS).as_posix()
        for setting in json.loads(path.read_text(encoding="utf-8")):
            found.setdefault(setting, []).append(name)
    return found


def test_the_specification_names_every_setting_the_suite_gives_a_server_and_when():
    rows, scenarios = _settings_rows(), _scenario_settings()
    given = {*HARNESS_VARIABLES, *CREDENTIAL_VARIABLES, INDEX_CODES_VARIABLE, *scenarios}

    assert set(rows) == given
    # A row names exactly the scenarios that set its setting, so that a server's team can read
    # when each is set.
    named = {setting: set(re.findall(r"`(\w+/[\w-]+)`", when)) for setting, when in rows.items()}
    assert named == {setting: set(scenarios.get(setting, [])) for setting in rows}


def _list_inputs(tool):
    """The parameters `tool` takes as lists: those its inputs write with brackets."""

    return set(re.findall(r"(\w+)\[", TOOLS[tool]["inputs"]))


def _returned_fields(tool):
    """The names what `tool` returns holds: those its returns text names, and the fields of
    each record it names."""

    names = set(re.findall(r"\w+", TOOLS[tool]["returns"]))
    return names.union(*(set(RECORDS[name]["fields"]) for name in names & set(RECORDS)))


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_what_a_tool_states_of_its_arguments_and_items_names_what_it_takes_and_returns(tool):
    entry, (names, _) = TOOLS[tool], parameters(tool)
    stated = {*entry.get("defaults", {}), *entry.get("bounds", {}), *entry.get("values", {})}
    stated |= {*entry.get("patterns", {})}
    # A free-text path names a parameter, and within a list's elements a field its inputs name.
    texts = {path.partition("[")[0] for path in entry.get("free_text", [])}
    fields = {
        path.split(".")[1].removesuffix("[]") for path in entry.get("free_text", []) if "." in path
    }
    assert {field for field in fields if not re.search(rf"\b{field}\b", entry["inputs"])} == set()
    steps = {step.removesuffix("[]") for path in entry.get("items", []) for step in path.split(".")}

    assert (stated | texts) - names == set()
    assert set(entry.get("lists", {})) - _list_inputs(tool) == set()
    assert steps - {""} - _returned_fields(tool) == set()


@pytest.mark.parametrize(
    ("tool", "name", "others"),
    [
        ("get_data_element", "longName", {"publicId", "questionText"}),
        # value is given together with dataElementId, in place of permissibleValueId.
        ("get_concept_for_permissible_value", "value", {"permissibleValueId"}),
        ("get_concept_for_permissible_value", "permissibleValueId", {"dataElementId", "value"}),
        ("search_concepts", "query", set()),
    ],
)
def test_the_alternatives_of_a_parameter_are_those_given_in_its_place(tool, name, others):
    assert alternatives(tool, name) == others


def _variables(value):
    return uri_variables(value) if isinstance(value, str) else []


@pytest.mark.parametrize("key", sorted(RESOURCES))
def test_a_resource_names_a_tool_of_its_group_and_fills_the_tool_s_parameters(key):
    resource = RESOURCES[key]
    names, required = parameters(resource["tool"])
    arguments = set(resource["arguments"])
    used = {variable for value in resource["arguments"].values() for variable in _variables(value)}
    selected = set(resource.get("selects", []))
    templated = {variable for uri in resource["uri"] for variable in uri_variables(uri)}

    assert REQUIRED_TOOLS[resource["tool"]] == resource["group"]
    # The arguments are the tool's, and give those it requires.
    assert arguments <= names
    assert required <= arguments
    # Each variable of a template fills an argument or selects the release, not both.
    assert templated == used | selected
    assert not used & selected
    # A record of records.yaml, or the shape of a result as tools.yaml writes it.
    assert resource["returns"] in RECORDS or resource["returns"].startswith("{")


def test_a_uri_template_belongs_to_one_resource_and_a_resource_has_its_mime_type():
    templates = [uri for resource in RESOURCES.values() for uri in resource["uri"]]

    assert len(templates) == len(set(templates))
    assert {resource["mime"] for resource in RESOURCES.values()} == {"application/json"}


def test_a_profile_serves_the_resources_of_its_group_and_unified_serves_all_six():
    assert set(resources_of("evs")) == {"concept", "release", "index_manifest"}
    assert set(resources_of("cadsr")) == {"data_element", "registry", "crosswalk"}
    assert resources_of("unified") == RESOURCES


@pytest.mark.parametrize(
    ("key", "template", "values", "call"),
    [
        (
            "concept",
            "ncit://concept/{release}/{code}",
            {"release": "26.09d", "code": "C4817"},
            {"terminology": "ncit", "release": "26.09d", "code": "C4817"},
        ),
        # The latest version names none; the template that names one passes it on.
        ("data_element", "cadsr://data-element/{publicId}", {"publicId": "88"}, {"publicId": "88"}),
        (
            "data_element",
            "cadsr://data-element/{publicId}/{version}",
            {"publicId": "88", "version": "5.1"},
            {"publicId": "88", "version": "5.1"},
        ),
        # A variable that only selects the release is no argument.
        ("release", "ncit://release/{version}", {"version": "26.09d"}, {"terminology": "ncit"}),
        ("registry", "cadsr://registry/release", {}, {}),
    ],
)
def test_a_resource_is_read_by_calling_its_tool_with_the_template_filled(
    key, template, values, call
):
    tool, arguments = resource_call(key, template, values)

    assert tool == RESOURCES[key]["tool"]
    assert {name: value for name, value in arguments.items() if name != "include"} == call


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_a_prompt_names_its_tools_in_order_and_fills_its_arguments(name):
    prompt = PROMPTS[name]
    declared = {each["name"] for each in prompt["arguments"]}

    # The tools its text names, in the order it first names them, starting from the release.
    assert tools_named(prompt["template"]) == prompt["tools"]
    assert prompt["tools"][0] == "resolve_release"
    assert set(prompt["tools"]) <= profile_tools("unified")
    # A placeholder is a declared argument, and an argument is used. Each argument is plain:
    # a name, whether it is required, and its description.
    assert set(uri_variables(prompt["template"])) == declared
    assert all(set(each) == {"name", "required", "description"} for each in prompt["arguments"])
    assert any(each["required"] for each in prompt["arguments"])


def test_a_prompt_is_listed_only_in_a_profile_with_every_tool_it_names():
    modules = [prompts_of(profile) for profile in PROFILES if profile != "unified"]

    assert modules == [{}, {}]
    assert prompts_of("unified") == PROMPTS
    assert set(PROMPTS) == {
        "protocol_authoring",
        "crdc_model_alignment",
        "uscdi_cancer_curation",
        "cross_program_harmonization",
    }


def test_the_names_a_text_holds_are_the_required_tools_once_each_in_order():
    text = "Call get_form, then resolve_release, get_form again, ncit_traverse and plain_words."

    assert tools_named(text) == ["get_form", "resolve_release"]
