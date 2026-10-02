"""A required tool is called by its name, through the tool map, or reported NOT IMPLEMENTED."""

from types import SimpleNamespace

import pytest

from nci_si_acceptance.tools import NOT_IMPLEMENTED, Tools, load_toolmap, translate

TOOLMAP = {
    "get_concept_neighborhood": {
        "tool": "ncit_traverse",
        "arguments": {
            "code": {"name": "start_codes", "list": True},
            "depth": "max_depth",
            "kinds": {"name": "edge_types", "values": {"inverseRole": "inverse_role"}},
        },
    }
}


class Session:
    """A server session that has `names` and answers with `result`, recording each call."""

    def __init__(self, names, result):
        self.names, self.result, self.calls = names, result, []

    def list_tools(self):
        return [SimpleNamespace(name=name) for name in self.names]

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def answer(*blocks, structured=None, is_error=False):
    return SimpleNamespace(content=list(blocks), structured_content=structured, is_error=is_error)


def text(value):
    return SimpleNamespace(type="text", text=value)


def test_arguments_are_renamed_wrapped_and_translated_and_the_rest_dropped():
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

    assert session.calls == [("get_concept_neighborhood", {"code": "C3262"})]
    assert (result.tool, result.is_error, result.content) == (
        "get_concept_neighborhood",
        False,
        {"nodes": []},
    )


def test_an_absent_tool_is_called_through_its_stand_in():
    session = Session(["ncit_traverse"], answer(text('{"nodes": []}'), is_error=True))
    tools = Tools(session, TOOLMAP)

    result = tools.call("get_concept_neighborhood", {"code": "C3262", "depth": 1})

    assert session.calls == [("ncit_traverse", {"start_codes": ["C3262"], "max_depth": 1})]
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
        ({"direction": ["parents", "pathsToRoot"]}, "direction=pathsToRoot"),
        ({"channel": "weekly"}, "channel=weekly"),
    ],
)
def test_a_capability_the_stand_in_lacks_is_not_implemented(arguments, unsupported):
    entry = {
        "tool": "ncit_traverse",
        "arguments": {
            "cursor": None,
            "direction": {
                "name": "edge_types",
                "values": {"parents": "parent", "pathsToRoot": None},
            },
            "channel": {"values": {"weekly": None}},
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
