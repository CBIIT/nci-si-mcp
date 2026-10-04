"""The baseline tool map fits the prototype: every stand-in, argument and value exists, and a
call through the map is one the prototype accepts."""

import json
import sys

import pytest

from nci_si_acceptance.client import open_session, server_environment
from nci_si_acceptance.record import FIXTURES
from nci_si_acceptance.results import error_code
from nci_si_acceptance.spec import REQUIRED_TOOLS
from nci_si_acceptance.tools import Tools, load_toolmap

TOOLMAP = load_toolmap(FIXTURES / "baseline_toolmap.yaml")
PROTOTYPE = [sys.executable, "-m", "nci_si_mcp.cli", "serve"]


@pytest.fixture(scope="module")
def prototype(tmp_path_factory):
    """A session with the prototype, its upstream an address that refuses every connection."""

    environment = server_environment(
        "fixture", tmp_path_factory.mktemp("data"), "http://127.0.0.1:9"
    )
    with open_session(PROTOTYPE, environment) as session:
        yield session


@pytest.fixture(scope="module")
def schemas(prototype):
    """The input schema of each prototype tool, as its tools/list declares it."""

    return {tool.name: tool.input_schema for tool in prototype.list_tools().tools}


def enums(schema):
    """Every value an argument's schema enumerates, wherever it nests them."""

    if isinstance(schema, dict):
        own = set(schema.get("enum", []))
        return own.union(*(enums(value) for value in schema.values()))
    if isinstance(schema, list):
        return set().union(*(enums(value) for value in schema))
    return set()


def types(schema):
    """Every JSON type an argument's schema allows, wherever it nests them."""

    if isinstance(schema, dict):
        own = {schema["type"]} if isinstance(schema.get("type"), str) else set()
        return own.union(*(types(value) for value in schema.values()))
    if isinstance(schema, list):
        return set().union(*(types(value) for value in schema))
    return set()


def sent(rule):
    """The stand-in argument a rule sends to, and the values it sends; None if it sends none."""

    if isinstance(rule, str):
        return rule, set()
    if isinstance(rule, dict) and "name" in rule:
        return rule["name"], {
            value for value in rule.get("values", {}).values() if value is not None
        }
    return None


def targets(entry):
    """(stand-in argument, values it is sent) for each mapped and fixed argument of an entry."""

    mapped = [sent(rule) for rule in entry.get("arguments", {}).values()]
    fixed = [(name, {value}) for name, value in entry.get("fixed", {}).items()]
    return [target for target in mapped if target] + fixed


def test_the_map_is_for_required_tools():
    assert set(TOOLMAP) <= set(REQUIRED_TOOLS)
    assert set(TOOLMAP) == {
        "resolve_release",
        "get_concept",
        "get_concept_hierarchy",
        "get_concept_neighborhood",
    }


@pytest.mark.parametrize("required", sorted(TOOLMAP))
def test_each_stand_in_takes_the_arguments_and_values_it_is_sent(schemas, required):
    entry = TOOLMAP[required]
    properties = schemas[entry["tool"]].get("properties", {})

    for argument, values in targets(entry):
        assert argument in properties, f"{entry['tool']} has no argument {argument}"
        allowed = enums(properties[argument])
        assert not allowed or values <= allowed, f"{argument}: {values - allowed} not in {allowed}"


@pytest.mark.parametrize("required", sorted(TOOLMAP))
def test_each_stand_in_gets_its_required_arguments_and_a_list_where_it_takes_one(schemas, required):
    entry = TOOLMAP[required]
    schema = schemas[entry["tool"]]
    rules = {
        rule["name"]: rule
        for rule in entry["arguments"].values()
        if isinstance(rule, dict) and "name" in rule
    }

    assert set(schema.get("required", [])) <= {argument for argument, _ in targets(entry)}
    for argument, rule in rules.items():
        if rule.get("list"):
            assert "array" in types(schema["properties"][argument]), argument


UPSTREAM_ERRORS = {"upstream_unavailable", "timeout", "release_not_available"}
CALLS = {
    "resolve_release": {"terminology": "ncit"},
    "get_concept": {"terminology": "ncit", "release": "26.09d", "code": "C3262"},
    "get_concept_hierarchy": {
        "terminology": "ncit",
        "code": "C3262",
        "direction": "parent",
        "depth": 1,
    },
    "get_concept_neighborhood": {
        "terminology": "ncit",
        "code": "C3262",
        "depth": 1,
        "kinds": ["role", "inverseRole"],
        "maxNodes": 10,
        "maxEdges": 10,
        "includeNegative": False,
    },
}


@pytest.mark.parametrize("required", sorted(CALLS))
def test_a_call_through_the_map_passes_the_prototypes_validation(prototype, required):
    try:
        result = Tools(prototype, TOOLMAP).call(required, CALLS[required])
    except pytest.skip.Exception as skipped:
        pytest.fail(f"a supported call was reported {skipped}")

    # Offline, the prototype can only fail upstream: never on the arguments it was sent.
    assert result.tool == TOOLMAP[required]["tool"]
    assert not result.is_error or error_code(result) in UPSTREAM_ERRORS, json.dumps(result.content)
