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


def _under(schema: object, keys: tuple[str, ...]) -> list:
    """The values a schema holds under any of `keys`, at any depth."""

    if isinstance(schema, list):
        return [found for value in schema for found in _under(value, keys)]
    if not isinstance(schema, dict):
        return []
    own = [schema[key] for key in keys if key in schema]
    return own + _under(list(schema.values()), keys)


def _descriptions(schema: object) -> list[str]:
    """Every description a schema holds, at any depth."""

    return [text for text in _under(schema, ("description",)) if isinstance(text, str)]


def _content_calls(server, profile: str, pinned: dict) -> list[tuple[str, dict]]:
    """A content call of each group a server of `profile` serves and implements: a concept of
    the pinned release from EVS, a data element from caDSR, the unified profile making both.
    A server that implements neither gets the first, which the suite reports NOT IMPLEMENTED."""

    calls = [
        call
        for group, call in {
            "evs": ("get_concept", {**pinned, "code": "C4817"}),
            "cadsr": ("get_data_element", {"publicId": "2200604"}),
        }.items()
        if profile in (group, "unified")
    ]
    return [call for call in calls if server.implemented_as(call[0])] or calls[:1]


def _header(entry: dict, name: str) -> str | None:
    return {key.lower(): value for key, value in entry["headers"].items()}.get(name)


def _surface(tool: str) -> str:
    """The upstream surface a gate's content call reaches."""

    return "cadsr" if tool == "get_data_element" else "evs"


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


def _not_offered(name):
    """What spec/tools.yaml says the platform has and the tool `name` does not expose."""

    return {value for values in TOOLS[name].get("not_offered", {}).values() for value in values}


def _leaves(value) -> list:
    """The scalars a value holds, inside lists and objects too."""

    if isinstance(value, list):
        return [leaf for item in value for leaf in _leaves(item)]
    if isinstance(value, dict):
        return _leaves(list(value.values()))
    return [value]


# Where a schema offers values: those it lists, fixes, defaults to or gives as examples.
OFFERING = ("enum", "const", "default", "examples", "example")
# Spans of a text that show what they hold as a value: backticks, double and typographic quotes.
QUOTED = re.compile('`[^`]*`|"[^"]*"|\u201c[^\u201d]*\u201d|\u2018[^\u2019]*\u2019')


def _shown_as_value(value, texts, patterns):
    """Whether a text names `value` as a value: as a word inside a quoted span or a schema
    pattern, between single quotes, or assigned (retired=exclude); a sentence that says,
    unquoted, that the value is not offered does not."""

    word = re.compile(rf"\b{re.escape(value)}\b")
    assigned = re.compile(rf"'{re.escape(value)}'|=\s*{re.escape(value)}\b")
    spans = [*patterns, *(span for text in texts for span in QUOTED.findall(text))]
    return any(word.search(span) for span in spans) or any(assigned.search(t) for t in texts)


def _shown(name, tool):
    """The values `name` does not offer that its implementation `tool` shows as offered."""

    texts = [tool.description or "", *_descriptions([tool.input_schema, tool.output_schema])]
    values = _leaves(_under(tool.input_schema, OFFERING))
    patterns = [text for text in _under(tool.input_schema, ("pattern",)) if isinstance(text, str)]
    return [
        (name, value)
        for value in sorted(_not_offered(name))
        if value in values or _shown_as_value(value, texts, patterns)
    ]


@pytest.mark.gate
@pytest.mark.live_capable
@pytest.mark.requirement("P-11")
def test_no_description_or_schema_shows_what_the_tool_does_not_offer(server):
    implemented = {
        name: server.available.get(server.implemented_as(name) or "")
        for name in TOOLS
        if _not_offered(name)
    }

    shown = [found for name, tool in implemented.items() if tool for found in _shown(name, tool)]

    assert shown == []


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
def test_tools_list_is_the_same_after_a_content_call(server, target, pinned):
    before = _listing(server.listing)

    called = [server.call(*call) for call in _content_calls(server, target.profile, pinned)]

    assert [result.content for result in called if result.is_error] == []
    assert _listing(server.list_again()) == before


@pytest.mark.gate
@pytest.mark.scenario("upstream/unavailable")
@pytest.mark.requirement("P-6")
def test_tools_list_is_the_same_while_the_platform_is_unavailable(server, target, tools, pinned):
    # A server may notice the outage only when a call fails, so one is made first.
    for call in _content_calls(tools, target.profile, pinned):
        tools.call(*call)

    assert _listing(tools.list_again()) == _listing(server.listing)


@pytest.mark.gate
# A server that answered the same call before may serve it from its cache, asking nothing.
@pytest.mark.own_server
@pytest.mark.requirement("P-7")
def test_a_correlation_identifier_goes_upstream_and_comes_back(tools, target, upstream, pinned):
    meta = {"correlationId": CORRELATION}

    calls = _content_calls(tools, target.profile, pinned)

    results = [tools.call(*call, meta) for call in calls]

    sent = {(entry["surface"], _header(entry, CORRELATION_HEADER)) for entry in upstream.log()}
    # Each group's requests carry it: the unified profile's caDSR requests as much as its EVS.
    assert {surface for surface, _ in sent} >= {_surface(name) for name, _ in calls}
    assert {value for _, value in sent} == {CORRELATION}
    assert [result.content for result in results if result.is_error] == []
    assert {result.content["provenance"]["correlationId"] for result in results} == {CORRELATION}


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
