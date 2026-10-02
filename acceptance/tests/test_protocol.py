"""Protocol-level gates (MCP Behavioral Acceptance Suite §3), run once per server."""

import pytest


@pytest.mark.live_capable
def test_tools_list_names_each_tool_with_its_input_schema(mcp):
    tools = mcp.list_tools()

    assert tools
    for tool in tools:
        assert tool.name
        assert tool.input_schema["type"] == "object"
