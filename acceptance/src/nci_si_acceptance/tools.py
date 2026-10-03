"""Call a required tool by its name: directly, through the baseline tool map, or not at all.

A test calls the tool the specification (`spec/tools.yaml`) names. When the server lacks it, the
baseline tool map (`fixtures/baseline_toolmap.yaml`, docs/SPEC.md §9.4) may
name a tool of the prototype that stands in for it; an entry applies only
while the required tool is absent. When neither exists the test is skipped as NOT
IMPLEMENTED, which the per-tool report counts as such.

    get_concept_hierarchy:                # the required tool
      tool: ncit_traverse                 # the tool that stands in for it
      fixed: {direction: both}            # passed on every call, unless an argument maps to it
      arguments:                          # required argument: the stand-in's argument
        depth: max_depth
        code: {name: start_codes, list: true}       # the value wrapped in a list
        direction:                                  # values translated, also in a list
          {name: edge_types, list: true, values: {parent: parent, pathsToRoot: null}}
        cursor: null                                # the stand-in cannot do this
        terminology: {values: {ncit: ncit, "*": null}}   # checked, not passed on

A rule is null, the stand-in's argument name, or a mapping of `name`, `list` and
`values`; without `name` the argument is checked and not passed on. A value the rule's
`values` do not list passes unchanged, unless `"*"` names what every other value becomes.
An argument or a value mapped to null, and an argument the entry does not list, is a
capability the stand-in lacks: a call that uses it is skipped as NOT IMPLEMENTED, so that
the report does not count as a failure what the prototype never offered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from mcp.shared.exceptions import MCPError

from nci_si_acceptance.spec import TOOLS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from mcp import types

    from nci_si_acceptance.client import Session

NOT_IMPLEMENTED = "NOT IMPLEMENTED"


class Unsupported(Exception):  # noqa: N818 - names the outcome, as NOT IMPLEMENTED does
    """An argument or value the stand-in has no capability for."""


type ToolMap = dict[str, dict[str, Any]]


def load_toolmap(path: Path) -> ToolMap:
    """The tool map at `path`; no file is an empty map."""

    if not path.exists():
        return {}
    toolmap = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for required, entry in toolmap.items():
        if problem := _entry_problem(entry):
            raise ValueError(f"{path.name}: {required} {problem}")
    return toolmap


RULE_FIELDS = frozenset({"name", "list", "values"})


def _entry_problem(entry: Any) -> str | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("tool"), str):
        return "names the tool that stands in for it"
    if not all(isinstance(entry.get(part, {}), dict) for part in ("arguments", "fixed")):
        return "maps its arguments and fixed values by name"
    return _rules_problem(entry.get("arguments", {}))


def _rules_problem(rules: dict[str, Any]) -> str | None:
    if bad := sorted(name for name, rule in rules.items() if not _usable(rule)):
        return f"has a rule for {', '.join(bad)} that is not null, a name, or name, list and values"
    return None


def _usable(rule: Any) -> bool:
    if rule is None or isinstance(rule, str):
        return True
    return (
        isinstance(rule, dict)
        and set(rule) <= RULE_FIELDS
        and isinstance(rule.get("values", {}), dict)
    )


def _one(values: dict[Any, Any], name: str, value: Any) -> Any:
    translated = values.get(value, values.get("*", value))
    if translated is None:
        raise Unsupported(f"{name}={value}")
    return translated


def _value(name: str, rule: dict[str, Any], value: Any) -> Any:
    values = rule.get("values", {})
    if isinstance(value, list):
        value = [_one(values, name, item) for item in value]
    else:
        value = _one(values, name, value)
    return [value] if rule.get("list") else value


def translate(arguments: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """The required tool's arguments as the stand-in takes them; Unsupported when the
    call uses a capability the stand-in lacks."""

    rules = entry.get("arguments", {})
    translated = dict(entry.get("fixed", {}))
    for name, value in arguments.items():
        rule = rules.get(name)
        if rule is None:
            raise Unsupported(name)
        if isinstance(rule, str):
            translated[rule] = value
            continue
        converted = _value(name, rule, value)
        if "name" in rule:
            translated[rule["name"]] = converted
    return translated


@dataclass(frozen=True, slots=True)
class Result:
    """A tool's answer: the tool that gave it, whether it is an error, its content, its
    `_meta`, and every text block."""

    tool: str
    is_error: bool
    content: Any
    meta: dict[str, Any]
    texts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Process:
    """What the harness keeps of a server process: its standard error, its data directory,
    and the upstream requests it made while it started."""

    log: Path
    data: Path
    startup: tuple[dict[str, Any], ...]

    def written(self) -> str:
        """Everything the process wrote: its standard error and every file of its data."""

        files = [self.log, *sorted(path for path in self.data.rglob("*") if path.is_file())]
        return "".join(path.read_bytes().decode("utf-8", "replace") for path in files)


def _texts(result: types.CallToolResult) -> tuple[str, ...]:
    return tuple(block.text for block in result.content if block.type == "text")


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


@dataclass(frozen=True, slots=True)
class Read:
    """What resources/read gave for a URI: the MIME type of each content, the first content as
    JSON (or its text), and the caching hint the result carries, with whether both fields of it
    came on the wire (an omitted one is read as its default)."""

    mime_types: tuple[str | None, ...]
    content: Any
    ttl_ms: int
    cache_scope: str
    carried: bool


def _read(result: types.ReadResourceResult) -> Read:
    first = result.contents[0] if result.contents else None
    text = getattr(first, "text", None)
    try:
        content = json.loads(text) if text is not None else None
    except ValueError:
        content = text
    return Read(
        tuple(each.mime_type for each in result.contents),
        content,
        result.ttl_ms,
        result.cache_scope,
        {"ttl_ms", "cache_scope"} <= result.model_fields_set,
    )


def _answered[T](method: str, request: Callable[[], T]) -> T:
    """The server's answer to `request`; its refusal, such as no such method because the server
    lacks the capability, fails the test and says so."""

    try:
        return request()
    except MCPError as refusal:
        pytest.fail(f"{method} was refused: {refusal.message}")


class Tools:
    """The required tools of one server session."""

    def __init__(
        self,
        session: Session,
        toolmap: ToolMap,
        process: Process | None = None,
        requests: Callable[[], int] | None = None,
    ) -> None:
        self._session = session
        self._toolmap = toolmap
        self.process = process
        # How many upstream requests the server has made so far, where the harness can tell.
        self._requests = requests
        self.listing = session.list_tools()
        self.available = {tool.name: tool for tool in self.listing.tools}

    @property
    def listing_bytes(self) -> int:
        """The size of the tools/list result as JSON: what every client session reads."""

        return len(self.listing.model_dump_json(by_alias=True, exclude_none=True).encode())

    def list_again(self) -> types.ListToolsResult:
        """A fresh tools/list of the same session."""

        return self._session.list_tools()

    def _check_requests(self, name: str, before: int) -> None:
        """Fail the test when a call made more upstream requests than its tool's stated bound,
        retries included (A5.3)."""

        bound = TOOLS.get(name, {}).get("requests")
        if bound is None or self._requests is None:
            return
        made = self._requests() - before
        if made > bound:
            pytest.fail(f"{name} made {made} upstream requests, more than its bound of {bound}")

    def list_prompts(self) -> types.ListPromptsResult:
        """prompts/list; a server without prompts fails the test."""

        return _answered("prompts/list", self._session.list_prompts)

    def get_prompt(self, name: str, arguments: dict[str, str]) -> types.GetPromptResult:
        return _answered("prompts/get", lambda: self._session.get_prompt(name, arguments))

    def listed_resources(self) -> set[str]:
        """The URIs and URI templates resources/list and resources/templates/list name."""

        resources = _answered("resources/list", self._session.list_resources)
        templates = _answered("resources/templates/list", self._session.list_resource_templates)
        return {each.uri for each in resources.resources} | {
            each.uri_template for each in templates.resource_templates
        }

    def read_resource(self, uri: str) -> Read:
        """resources/read of `uri`; a refusal fails the test."""

        return _read(_answered("resources/read", lambda: self._session.read_resource(uri)))

    def implemented_as(self, name: str) -> str | None:
        """The tool that answers for `name`: itself, its stand-in, or none."""

        if name in self.available:
            return name
        stand_in = self._toolmap.get(name, {}).get("tool")
        return stand_in if stand_in in self.available else None

    def call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        meta: types.RequestParamsMeta | None = None,
    ) -> Result:
        """Call the required tool `name`, with `meta` as the call's `_meta`; skip the test as
        NOT IMPLEMENTED when nothing answers."""

        arguments = arguments or {}
        tool = self.implemented_as(name)
        if tool is None:
            pytest.skip(f"{NOT_IMPLEMENTED}: {name}")
        if tool != name:
            try:
                arguments = translate(arguments, self._toolmap[name])
            except Unsupported as unsupported:
                pytest.skip(f"{NOT_IMPLEMENTED}: {name} with {unsupported} (stand-in {tool})")
        before = self._requests() if self._requests else 0
        result = self._session.call_tool(tool, arguments, meta)
        self._check_requests(name, before)
        return Result(
            tool, bool(result.is_error), _content(result), result.meta or {}, _texts(result)
        )
