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


FIELDS = RECORDS["error"]["fields"]
CODES = FIELDS["code"]["values"]
# The error record with its required fields, each holding text.
RECORD = {name: "text" for name, field in FIELDS.items() if not field.get("optional")}
# Results holding an error record, one for each code and one with details; and two that hold
# none: a code outside the closed set, and no code.
ERRORS = [{"error": RECORD | {"code": code}} for code in CODES]
ERRORS.append({"error": RECORD | {"code": CODES[0], "details": {}}})
MALFORMED = [
    {"error": RECORD | {"code": "no_such_code"}},
    {"error": {name: value for name, value in RECORD.items() if name != "code"}},
]


def _admitting(validators: dict, results: list[dict], admits=all) -> list[str]:
    """The tools whose schema admits `results`, all of them or, with `any`, one of them."""

    return [name for name, check in validators.items() if admits(map(check.is_valid, results))]


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
def test_every_output_schema_admits_the_error_record_and_refuses_a_malformed_one(server):
    schemas = {name: tool.output_schema for name, tool in server.available.items()}

    assert [name for name, schema in schemas.items() if schema is None] == []
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    validators = {name: Draft202012Validator(schema) for name, schema in schemas.items()}
    assert _admitting(validators, ERRORS) == list(validators)
    # A schema that admits anything declares no shape at all (M3.1).
    assert _admitting(validators, MALFORMED, any) == []


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

    pinning = server.call("get_concept", {**pinned, "code": "C4817"})

    assert not pinning.is_error
    assert _listing(server.list_again()) == before


@pytest.mark.gate
@pytest.mark.scenario("upstream/unavailable")
@pytest.mark.requirement("P-6")
def test_tools_list_is_the_same_while_the_platform_is_unavailable(server, tools, pinned):
    # A server may notice the outage only when a call fails, so one is made first.
    tools.call("get_concept", {**pinned, "code": "C4817"})

    assert _listing(tools.list_again()) == _listing(server.listing)


@pytest.mark.gate
# A server that answered the same call before may serve it from its cache, asking nothing.
@pytest.mark.own_server
@pytest.mark.requirement("P-7")
def test_a_correlation_identifier_goes_upstream_and_comes_back(tools, upstream, pinned):
    result = tools.call("get_concept", {**pinned, "code": "C4817"}, {"correlationId": CORRELATION})

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

    specified = {name: tool for name, tool in server.available.items() if name in TOOLS}
    if not specified:
        pytest.skip("the server lists no tool of the specification")
    differing = {
        name: declared(tool)
        for name, tool in specified.items()
        if declared(tool) != parameters(name)
    }

    assert differing == {}
