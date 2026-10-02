"""Call a required tool by its name: directly, through the baseline tool map, or not at all.

A test calls the tool the *MCP API Specification* names. When the server lacks it, the
baseline tool map (`fixtures/baseline_toolmap.yaml`, MCP Behavioral Acceptance Suite
§6) may name a tool of the prototype that stands in for it; an entry applies only
while the required tool is absent. When neither exists the test is skipped as NOT
IMPLEMENTED, which the per-tool report counts as such.

    search_concepts:                      # the required tool
      tool: ncit_search                   # the tool that stands in for it
      arguments:                          # required argument: the stand-in's argument
        query: query
        mode: {name: mode, values: {semantic: hybrid}}   # values translated, also in a list
        code: {name: start_codes, list: true}            # the value wrapped in a list

An argument without an entry is not passed on: the stand-in has no such parameter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
import yaml

if TYPE_CHECKING:
    from pathlib import Path

    from mcp import types

    from nci_si_acceptance.client import Session

NOT_IMPLEMENTED = "NOT IMPLEMENTED"

type ToolMap = dict[str, dict[str, Any]]


def load_toolmap(path: Path) -> ToolMap:
    """The tool map at `path`; no file is an empty map."""

    if not path.exists():
        return {}
    toolmap = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for required, entry in toolmap.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("tool"), str):
            raise ValueError(f"{path.name}: {required} names the tool that stands in for it")
    return toolmap


def _value(rule: dict[str, Any], value: Any) -> Any:
    values = rule.get("values", {})
    if isinstance(value, list):
        value = [values.get(item, item) for item in value]
    else:
        value = values.get(value, value)
    return [value] if rule.get("list") else value


def translate(arguments: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """The required tool's arguments as the stand-in takes them."""

    translated = {}
    for name, value in arguments.items():
        rule = entry.get("arguments", {}).get(name)
        if isinstance(rule, str):
            translated[rule] = value
        elif rule is not None:
            translated[rule["name"]] = _value(rule, value)
    return translated


@dataclass(frozen=True, slots=True)
class Result:
    """A tool's answer: the tool that gave it, whether it is an error, and its content."""

    tool: str
    is_error: bool
    content: Any


def _content(result: types.CallToolResult) -> Any:
    """The structured content, or the first text block: its JSON, or the text itself."""

    if result.structured_content is not None:
        return result.structured_content
    text = next((block.text for block in result.content if block.type == "text"), None)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


class Tools:
    """The required tools of one server session."""

    def __init__(self, session: Session, toolmap: ToolMap) -> None:
        self._session = session
        self._toolmap = toolmap
        self.available = {tool.name: tool for tool in session.list_tools()}

    def implemented_as(self, name: str) -> str | None:
        """The tool that answers for `name`: itself, its stand-in, or none."""

        if name in self.available:
            return name
        stand_in = self._toolmap.get(name, {}).get("tool")
        return stand_in if stand_in in self.available else None

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Result:
        """Call the required tool `name`; skip the test as NOT IMPLEMENTED when nothing answers."""

        arguments = arguments or {}
        tool = self.implemented_as(name)
        if tool is None:
            pytest.skip(f"{NOT_IMPLEMENTED}: {name}")
        if tool != name:
            arguments = translate(arguments, self._toolmap[name])
        result = self._session.call_tool(tool, arguments)
        return Result(tool, bool(result.is_error), _content(result))
