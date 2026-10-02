"""A server of the evs profile that meets every protocol gate, for the harness's own tests.

`GATE_SERVER_DEFECT` names one defect, so that a test can show the gate concerned failing:

    missing-tool      lists one tool of the profile too few                       (P-1)
    no-error-shape    an outputSchema that refuses the error record                 (P-2)
    declares-no-shape an outputSchema that admits any object                        (P-2)
    placeholder       a description holding TODO                                    (P-3)
    misnamed          a tool whose name is not verb-led and lowercase               (P-4)
    no-ttl            tools/list with ttlMs 0                                       (P-5)
    listing-changes   tools/list loses a tool after the first call                  (P-6)
    hides-tools       tools/list loses a tool while the platform is unavailable     (P-6)
    no-correlation    the correlation identifier is not sent upstream               (P-7)
    destructive       destructiveHint true                                          (P-10)
    parameter-renamed get_concept takes conceptCode in place of code                (P-12)

It asks EVS for `/api/v1/version` at startup, and again on each call of get_concept.
"""

import json
import os
import urllib.request

import anyio
import mcp_types as types
from mcp.server.caching import CacheHint
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

from nci_si_acceptance.spec import RECORDS, parameters, profile_tools

DEFECT = os.environ.get("GATE_SERVER_DEFECT", "")
VERSION = os.environ["NCI_SI_EVS_BASE_URL"] + "/api/v1/version"
calls = []


def _reaches_evs(headers: dict[str, str]) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(VERSION, headers=headers)):  # noqa: S310
            return True
    except OSError:
        return False


PLATFORM_UP = _reaches_evs({})


def _input_schema(name: str) -> dict:
    names, required = parameters(name)
    if DEFECT == "parameter-renamed" and name == "get_concept":
        names, required = names - {"code"} | {"conceptCode"}, required - {"code"} | {"conceptCode"}
    return {
        "type": "object",
        "properties": {key: {} for key in names},
        "required": sorted(required),
    }


# A result is either an error record or a success, which carries provenance.
ERROR = {
    "type": "object",
    "required": ["code", "message"],
    "properties": {"code": {"enum": RECORDS["error"]["fields"]["code"]["values"]}},
}
OUTPUT_SCHEMAS = {
    "": {
        "type": "object",
        "oneOf": [
            {"required": ["error"], "properties": {"error": ERROR}},
            {"required": ["provenance"], "not": {"required": ["error"]}},
        ],
    },
    "no-error-shape": {"type": "object", "required": ["provenance"]},
    "declares-no-shape": {"type": "object"},
}


def _tool(name: str) -> types.Tool:
    return types.Tool(
        name=name,
        description="TODO" if DEFECT == "placeholder" else f"The {name} tool of EVS.",
        input_schema=_input_schema(name),
        output_schema=OUTPUT_SCHEMAS.get(DEFECT, OUTPUT_SCHEMAS[""]),
        annotations=types.ToolAnnotations(
            read_only_hint=True,
            destructive_hint=DEFECT == "destructive",
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )


def _names() -> list[str]:
    names = sorted(profile_tools("evs"))
    if DEFECT == "misnamed":
        names.append("ConceptLookup")
    dropped = {
        "missing-tool": True,
        "listing-changes": bool(calls),
        "hides-tools": not PLATFORM_UP,
    }
    return names[1:] if dropped.get(DEFECT) else names


async def list_tools(_context, _params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[_tool(name) for name in _names()])


async def call_tool(_context, params: types.CallToolRequestParams) -> types.CallToolResult:
    calls.append(params.name)
    correlation = (params.meta or {}).get("correlationId", "")
    _reaches_evs({} if DEFECT == "no-correlation" else {"X-Correlation-ID": correlation})
    content = {"provenance": {"correlationId": correlation}}
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(content))],
        structured_content=content,
    )


SERVER = Server(
    "gate-server",
    cache_hints={
        "tools/list": CacheHint(ttl_ms=0 if DEFECT == "no-ttl" else 86_400_000, scope="public")
    },
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)


async def main() -> None:
    async with stdio_server() as (read, write):
        await SERVER.run(read, write, SERVER.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
