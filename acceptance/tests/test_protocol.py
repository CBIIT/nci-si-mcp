"""Protocol-level gates (MCP Behavioral Acceptance Suite §3), run once per server."""

import pytest


@pytest.mark.live_capable
def test_tools_list_names_each_tool_with_its_input_schema(server):
    assert server.available
    for name, tool in server.available.items():
        assert tool.name == name
        assert tool.input_schema["type"] == "object"
