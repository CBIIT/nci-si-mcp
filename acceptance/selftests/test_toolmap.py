"""The baseline tool map fits the prototype: every stand-in, argument and value exists."""

import sys

import pytest

from nci_si_acceptance.client import open_session, server_environment
from nci_si_acceptance.inventory import REQUIRED_TOOLS
from nci_si_acceptance.record import FIXTURES
from nci_si_acceptance.tools import load_toolmap

TOOLMAP = load_toolmap(FIXTURES / "baseline_toolmap.yaml")
PROTOTYPE = [sys.executable, "-m", "nci_si_mcp.cli", "serve"]


@pytest.fixture(scope="module")
def schemas(tmp_path_factory):
    """The input schema of each prototype tool, as its tools/list declares it."""

    environment = server_environment(
        "fixture", tmp_path_factory.mktemp("data"), "http://127.0.0.1:9"
    )
    with open_session(PROTOTYPE, environment) as session:
        return {tool.name: tool.input_schema for tool in session.list_tools()}


def enums(schema):
    """Every value an argument's schema enumerates, wherever it nests them."""

    if isinstance(schema, dict):
        own = set(schema.get("enum", []))
        return own.union(*(enums(value) for value in schema.values()))
    if isinstance(schema, list):
        return set().union(*(enums(value) for value in schema))
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
        "search_concepts",
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
