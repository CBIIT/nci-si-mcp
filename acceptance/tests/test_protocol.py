"""Protocol gates (the P requirements of spec/requirements.yaml), run once per server; a gate
that fails fails every tool."""

import re

import pytest
from jsonschema import Draft202012Validator

from nci_si_acceptance.spec import RECORDS, TOOLS, parameters, profile_tools

# M7.1: where a caller's correlation identifier goes, and the header that carries it upstream.
CORRELATION = "acceptance-correlation-0001"
CORRELATION_HEADER = "x-correlation-id"
# Text that marks a description as unfinished (A2.4).
UNFINISHED = re.compile(
    r"\b(TODO|FIXME|XXX|TBD|HACK|lorem ipsum|placeholder|debug)\b|\{\{|\}\}", re.IGNORECASE
)
VERBS = {name.partition("_")[0] for name in TOOLS}
NAME = re.compile(r"[a-z]+(_[a-z]+)*")
READ_ONLY = {
    "read_only_hint": True,
    "destructive_hint": False,
    "idempotent_hint": True,
    "open_world_hint": True,
}


def _error_record() -> dict[str, str]:
    """An error record with its required fields, each holding a value of its kind."""

    fields = RECORDS["error"]["fields"]
    return {
        name: field["values"][0] if "values" in field else "text"
        for name, field in fields.items()
        if not field.get("optional")
    }


def _descriptions(schema: object) -> list[str]:
    """Every description a schema holds, at any depth."""

    if isinstance(schema, list):
        return [text for value in schema for text in _descriptions(value)]
    if not isinstance(schema, dict):
        return []
    own, inner = schema.get("description"), _descriptions(list(schema.values()))
    return [own, *inner] if isinstance(own, str) else inner


def _listing(result) -> list[dict]:
    return [tool.model_dump(by_alias=True) for tool in result.tools]


@pytest.mark.gate
@pytest.mark.live_capable
@pytest.mark.requirement("P-1")
def test_tools_list_names_the_tools_of_the_profile_and_no_other(server, target):
    assert set(server.available) == profile_tools(target.profile)


@pytest.mark.gate
@pytest.mark.live_capable
@pytest.mark.requirement("P-2")
def test_every_tool_declares_an_output_schema_that_admits_the_error_record(server):
    schemas = {name: tool.output_schema for name, tool in server.available.items()}
    error = {"error": _error_record()}

    assert [name for name, schema in schemas.items() if schema is None] == []
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    refusing = [
        name for name, schema in schemas.items() if not Draft202012Validator(schema).is_valid(error)
    ]
    assert refusing == []


@pytest.mark.gate
@pytest.mark.live_capable
@pytest.mark.requirement("P-3")
def test_no_description_holds_placeholder_or_debug_text(server):
    unfinished = {
        name: match[0]
        for name, tool in server.available.items()
        for text in [
            tool.description or "",
            *_descriptions([tool.input_schema, tool.output_schema]),
        ]
        if (match := UNFINISHED.search(text))
    }

    assert unfinished == {}


@pytest.mark.gate
@pytest.mark.live_capable
@pytest.mark.requirement("P-4")
def test_tool_names_are_verb_led_lowercase_and_underscore_separated(server):
    misnamed = [
        name
        for name in server.available
        if not NAME.fullmatch(name) or name.partition("_")[0] not in VERBS
    ]

    assert misnamed == []


@pytest.mark.gate
@pytest.mark.live_capable
@pytest.mark.requirement("P-5")
def test_tools_list_may_be_cached_and_shared(server):
    assert server.listing.ttl_ms > 0
    assert server.listing.cache_scope == "public"


@pytest.mark.gate
@pytest.mark.requirement("P-6")
def test_tools_list_is_the_same_after_a_call_that_pins_a_terminology_and_release(server, pinned):
    before = _listing(server.listing)

    server.call("get_concept", {**pinned, "code": "C4817"})

    assert _listing(server.list_again()) == before


@pytest.mark.gate
@pytest.mark.scenario("upstream/unavailable")
@pytest.mark.requirement("P-6")
def test_tools_list_is_the_same_while_the_platform_is_unavailable(server, tools):
    assert _listing(tools.listing) == _listing(server.listing)


@pytest.mark.gate
@pytest.mark.requirement("P-7")
def test_a_correlation_identifier_goes_upstream_and_comes_back(server, upstream, pinned):
    result = server.call("get_concept", {**pinned, "code": "C4817"}, {"correlationId": CORRELATION})

    sent = [
        {name.lower(): value for name, value in entry["headers"].items()}.get(CORRELATION_HEADER)
        for entry in upstream.log()
    ]
    assert sent
    assert set(sent) == {CORRELATION}
    assert not result.is_error
    assert result.content["provenance"]["correlationId"] == CORRELATION


@pytest.mark.gate
@pytest.mark.live_capable
@pytest.mark.requirement("P-10")
def test_every_tool_is_annotated_read_only_idempotent_and_open_world(server):
    def hints(tool):
        annotations = tool.annotations
        return annotations and {name: getattr(annotations, name) for name in READ_ONLY}

    assert [name for name, tool in server.available.items() if hints(tool) != READ_ONLY] == []


@pytest.mark.gate
@pytest.mark.live_capable
@pytest.mark.requirement("P-12")
def test_each_tool_takes_the_parameters_the_specification_names(server):
    def declared(tool):
        schema = tool.input_schema
        return set(schema.get("properties", {})), set(schema.get("required", []))

    differing = {
        name: declared(tool)
        for name, tool in server.available.items()
        if name in TOOLS and declared(tool) != parameters(name)
    }

    assert differing == {}
