"""A required tool is called by its name, through the tool map, or reported NOT IMPLEMENTED."""

from types import SimpleNamespace

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import METHOD_NOT_FOUND

from nci_si_acceptance.spec import TOOLS
from nci_si_acceptance.tools import NOT_IMPLEMENTED, Tools, load_toolmap, translate

TOOLMAP = {
    "get_concept_neighborhood": {
        "tool": "ncit_traverse",
        "arguments": {
            "code": {"name": "start_codes", "list": True},
            "depth": "max_depth",
            "kinds": {"name": "edge_types", "values": {"inverseRole": "inverse_role"}},
            "release": {},
            "terminology": {"values": {"ncit": "ncit", "*": None}},
        },
    }
}


class Session:
    """A server session that has `names` and answers with `result`, recording each call."""

    def __init__(self, names, result):
        self.names, self.result, self.calls = names, result, []

    def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in self.names])

    def call_tool(self, name, arguments, meta=None):
        self.calls.append((name, arguments, meta))
        return self.result


def answer(*blocks, structured=None, is_error=False, meta=None):
    return SimpleNamespace(
        content=list(blocks), structured_content=structured, is_error=is_error, meta=meta
    )


def text(value):
    return SimpleNamespace(type="text", text=value)


def test_arguments_are_renamed_wrapped_and_translated_and_the_checked_ones_dropped():
    arguments = {"code": "C3262", "depth": 2, "kinds": ["role", "inverseRole"], "release": "26.09d"}

    translated = translate(arguments, TOOLMAP["get_concept_neighborhood"])

    assert translated == {
        "start_codes": ["C3262"],
        "max_depth": 2,
        "edge_types": ["role", "inverse_role"],
    }
    assert translate({"kinds": "inverseRole"}, TOOLMAP["get_concept_neighborhood"]) == {
        "edge_types": "inverse_role"
    }


def test_a_required_tool_the_server_has_is_called_by_its_own_name():
    session = Session(
        ["get_concept_neighborhood", "ncit_traverse"], answer(structured={"nodes": []})
    )

    result = Tools(session, TOOLMAP).call("get_concept_neighborhood", {"code": "C3262"})

    assert session.calls == [("get_concept_neighborhood", {"code": "C3262"}, None)]
    assert (result.tool, result.is_error, result.content) == (
        "get_concept_neighborhood",
        False,
        {"nodes": []},
    )


def test_the_call_meta_reaches_the_server_and_the_result_meta_comes_back():
    session = Session(["get_concept"], answer(structured={}, meta={"ttlMs": 0}))

    result = Tools(session, {}).call("get_concept", {"code": "C3262"}, {"correlationId": "c-1"})

    assert session.calls == [("get_concept", {"code": "C3262"}, {"correlationId": "c-1"})]
    assert result.meta == {"ttlMs": 0}


def test_an_absent_tool_is_called_through_its_stand_in():
    session = Session(["ncit_traverse"], answer(text('{"nodes": []}'), is_error=True))
    tools = Tools(session, TOOLMAP)

    result = tools.call("get_concept_neighborhood", {"code": "C3262", "depth": 1})

    assert session.calls == [("ncit_traverse", {"start_codes": ["C3262"], "max_depth": 1}, None)]
    assert (result.tool, result.is_error, result.content) == ("ncit_traverse", True, {"nodes": []})
    assert tools.implemented_as("get_concept_neighborhood") == "ncit_traverse"


def test_a_text_answer_that_is_not_json_is_kept_as_text():
    message = "Error executing tool ncit_lookup: 1 validation error"
    session = Session(["get_concept"], answer(text(message), is_error=True))

    result = Tools(session, {}).call("get_concept", {"code": "C3262"})

    assert (result.is_error, result.content) == (True, message)


def test_a_result_without_content_has_none():
    session = Session(["list_terminologies"], answer(SimpleNamespace(type="image")))

    assert Tools(session, {}).call("list_terminologies").content is None


@pytest.mark.parametrize("names", [[], ["ncit_lookup"]])
def test_a_tool_without_a_stand_in_on_the_server_is_not_implemented(names):
    tools = Tools(Session(names, None), TOOLMAP)

    with pytest.raises(pytest.skip.Exception, match=f"{NOT_IMPLEMENTED}: get_concept_neighborhood"):
        tools.call("get_concept_neighborhood", {"code": "C3262"})
    assert tools.implemented_as("get_concept_neighborhood") is None


def test_the_tool_map_is_read_from_yaml_and_is_empty_without_a_file(tmp_path):
    path = tmp_path / "baseline_toolmap.yaml"
    assert load_toolmap(path) == {}

    path.write_text("resolve_release:\n  tool: ncit_release_info\n", encoding="utf-8")

    assert load_toolmap(path) == {"resolve_release": {"tool": "ncit_release_info"}}


def test_a_tool_map_entry_names_its_stand_in(tmp_path):
    path = tmp_path / "baseline_toolmap.yaml"
    path.write_text("resolve_release:\n  arguments: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="resolve_release names the tool that stands in for it"):
        load_toolmap(path)


def test_fixed_arguments_are_passed_and_a_checked_argument_is_not():
    entry = {
        "tool": "ncit_traverse",
        "fixed": {"direction": "both"},
        "arguments": {"code": "code", "channel": {"values": {"weekly": None}}},
    }

    assert translate({"code": "C1", "channel": "monthly"}, entry) == {
        "direction": "both",
        "code": "C1",
    }


@pytest.mark.parametrize(
    ("arguments", "unsupported"),
    [
        ({"cursor": "abc"}, "cursor"),
        ({"direction": "pathsToRoot"}, "direction=pathsToRoot"),
        ({"direction": ["parent", "pathsToRoot"]}, "direction=pathsToRoot"),
        ({"channel": "weekly"}, "channel=weekly"),
        ({"correlationId": "abc"}, "correlationId"),
        ({"terminology": "snomedct_us"}, "terminology=snomedct_us"),
    ],
)
def test_a_capability_the_stand_in_lacks_is_not_implemented(arguments, unsupported):
    entry = {
        "tool": "ncit_traverse",
        "arguments": {
            "cursor": None,
            "direction": {
                "name": "edge_types",
                "values": {"parent": "parent", "pathsToRoot": None},
            },
            "channel": {"values": {"weekly": None}},
            "terminology": {"values": {"ncit": "ncit", "*": None}},
        },
    }
    session = Session(["ncit_traverse"], answer(structured={}))

    expected = f"{NOT_IMPLEMENTED}: get_concept_hierarchy with {unsupported} \\(stand-in"
    with pytest.raises(pytest.skip.Exception, match=expected):
        Tools(session, {"get_concept_hierarchy": entry}).call("get_concept_hierarchy", arguments)
    assert session.calls == []


def test_a_tool_map_entry_maps_its_arguments_and_fixed_values_by_name(tmp_path):
    path = tmp_path / "baseline_toolmap.yaml"
    path.write_text(
        "resolve_release:\n  tool: ncit_release_info\n  fixed: [direction]\n", encoding="utf-8"
    )

    with pytest.raises(
        ValueError, match="resolve_release maps its arguments and fixed values by name"
    ):
        load_toolmap(path)


@pytest.mark.parametrize(
    "rule",
    ["{nmae: edge_types}", "{name: edge_types, values: [parent]}", "[edge_types]", "3"],
)
def test_a_rule_that_is_not_null_a_name_or_name_list_and_values_is_refused(tmp_path, rule):
    path = tmp_path / "baseline_toolmap.yaml"
    path.write_text(
        f"get_concept_neighborhood:\n  tool: ncit_traverse\n  arguments:\n    kinds: {rule}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="get_concept_neighborhood has a rule for kinds that is"):
        load_toolmap(path)


class Counted(Session):
    """A session whose every call makes `made` upstream requests, as the harness counts them."""

    def __init__(self, names, result, made):
        super().__init__(names, result)
        self.made, self.requests = made, 0

    def call_tool(self, name, arguments, meta=None):
        self.requests += self.made
        return super().call_tool(name, arguments, meta)


def test_a_call_over_its_tool_s_request_bound_fails_the_test_and_one_within_passes():
    bound = TOOLS["get_concept_neighborhood"]["requests"]
    over = Counted(["get_concept_neighborhood"], answer(structured={"nodes": []}), bound + 1)
    within = Counted(["get_concept_neighborhood"], answer(structured={"nodes": []}), bound)

    with pytest.raises(pytest.fail.Exception, match=f"{bound + 1} upstream requests"):
        Tools(over, {}, requests=lambda: over.requests).call("get_concept_neighborhood")
    result = Tools(within, {}, requests=lambda: within.requests).call("get_concept_neighborhood")

    assert result.content == {"nodes": []}


class Surface(Session):
    """A session that answers the prompt and resource methods, or refuses them."""

    def __init__(self, refusal=False):
        super().__init__(["get_concept"], None)
        self.refusal = refusal

    def _answered(self, result):
        if self.refusal:
            raise MCPError(METHOD_NOT_FOUND, "Method not found")
        return result

    def list_prompts(self):
        return self._answered(SimpleNamespace(prompts=["a prompt"]))

    def get_prompt(self, name, arguments):
        return self._answered(SimpleNamespace(messages=[name, arguments]))

    def list_resources(self):
        return self._answered(SimpleNamespace(resources=[SimpleNamespace(uri="cadsr://a/b")]))

    def list_resource_templates(self):
        template = SimpleNamespace(uri_template="ncit://c/{x}")
        return self._answered(SimpleNamespace(resource_templates=[template]))

    def read_resource(self, uri):
        return self._answered(READS[uri])


def read_result(*texts, mime="application/json", fields=("ttl_ms", "cache_scope")):
    contents = [SimpleNamespace(text=each, mime_type=mime) for each in texts]
    return SimpleNamespace(
        contents=contents, ttl_ms=5, cache_scope="public", model_fields_set=set(fields)
    )


READS = {
    "json": read_result('{"code": "C4817"}'),
    "prose": read_result("not json", mime="text/plain"),
    "bare": read_result(fields=()),
}


def test_a_resource_read_gives_its_json_its_mime_type_and_the_hint_it_carries():
    tools = Tools(Surface(), {})

    read = tools.read_resource("json")

    assert (read.content, read.mime_types) == ({"code": "C4817"}, ("application/json",))
    assert (read.ttl_ms, read.cache_scope, read.carried) == (5, "public", True)
    # Content that is no JSON is its text; no content is none; a hint left out is not carried.
    assert tools.read_resource("prose").content == "not json"
    assert tools.read_resource("bare").content is None
    assert not tools.read_resource("bare").carried


def test_the_listings_of_resources_and_of_templates_are_one_set_of_uris():
    assert Tools(Surface(), {}).listed_resources() == {"cadsr://a/b", "ncit://c/{x}"}


def test_the_prompts_of_a_server_are_listed_and_got_by_name_with_their_arguments():
    tools = Tools(Surface(), {})

    assert tools.list_prompts().prompts == ["a prompt"]
    assert tools.get_prompt("p", {"a": "b"}).messages == ["p", {"a": "b"}]


@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("prompts/list", lambda tools: tools.list_prompts()),
        ("prompts/get", lambda tools: tools.get_prompt("p", {})),
        ("resources/list", lambda tools: tools.listed_resources()),
        ("resources/read", lambda tools: tools.read_resource("json")),
    ],
)
def test_a_server_that_refuses_a_prompt_or_resource_method_fails_the_test_naming_it(method, call):
    tools = Tools(Surface(refusal=True), {})

    with pytest.raises(pytest.fail.Exception, match=f"{method} was refused: Method not found"):
        call(tools)
