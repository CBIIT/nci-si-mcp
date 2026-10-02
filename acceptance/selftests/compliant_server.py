"""A server of the evs profile that meets the protocol gates and the cross-cutting
requirements, for the harness's own tests.

`COMPLIANT_SERVER_DEFECT` names one defect, so that a test can show the test concerned failing:

    missing-tool      lists one tool of the profile too few                       (P-1)
    no-error-shape    an outputSchema that refuses the error record                 (P-2)
    declares-no-shape an outputSchema that admits any object                        (P-2)
    one-code          an outputSchema whose error code admits one value only        (P-2)
    placeholder       a description holding TODO                                    (P-3)
    misnamed          a tool whose name is not verb-led and lowercase               (P-4)
    no-ttl            tools/list with ttlMs 0                                       (P-5)
    listing-changes   tools/list loses a tool after the first call                  (P-6)
    redescribed       a tool's description changes after the first call             (P-6)
    hides-tools       tools/list loses a tool once a call finds the platform down   (P-6)
    no-correlation    the correlation identifier is not sent upstream               (P-7)
    destructive       destructiveHint true                                          (P-10)
    not-idempotent    idempotentHint false                                          (P-10)
    closed-world      openWorldHint false                                           (P-10)
    parameter-renamed get_concept takes conceptCode in place of code                (P-12)
    release-optional  get_concept does not require release                          (P-12)
    wrong-release     items name another release than the one requested             (X-1)
    wrong-terminology items name another terminology than the one requested         (X-1)
    invalid-result    a result its outputSchema refuses                             (X-6)
    no-served-by      provenance without servedBy                                   (X-7)
    bad-timestamp     a retrievedAt that is no ISO-8601 timestamp                   (X-7)
    no-polarity       an item reached by traversal without polarity                 (X-7)
    nothing-reached   a traversal whose items are all at depth 0                    (X-7)
    prefixed-code     codes written NCIT:C4817                                      (X-9)
    repeated-request  each call asks EVS twice for the same thing                   (X-11)
    asks-nothing      calls that ask EVS nothing                                    (X-11)
    uncached-result   results with ttlMs 0                                          (X-13)
    private-scope     results with cacheScope private                               (X-13)
    list-result       a result that is a bare list of its items                     (X-14)
    unknown-as-empty  an unknown release answered as an empty success               (X-2)
    accepts-mismatch  content of another release served as asked                    (X-3)
    outage-as-empty   an unavailable EVS answered as an empty success               (X-5)
    unshaped-error    an error that is no error record                              (X-6)
    keyless           the licence key is not sent                                   (X-12)
    logs-key          the request headers, licence key included, written to the log (X-12)
    leaks-key         EVS's refusal, which repeats the request headers, in the error (X-12)
    files-key         the request headers written to a file in the data directory  (X-12)
    key-in-text       the licence key in a success's text block                     (X-12)
    key-in-meta       the licence key in every result's _meta                       (X-12)
    content-with-error content beside the error record                              (X-2)
    empty-as-error    a query that matches nothing answered as not_found            (X-4)
    empty-without-provenance an empty result without its provenance                 (X-4)
    upstream-renamed  EVS's origin fields passed on under other names               (X-8)
    truncation-flag-only a bound reached reported as occurred and nothing else      (X-10)
    omitted-unknown   how much was left out given as "unknown"                      (X-10)
    exact-missing     a bound reached reported without exact                        (X-10)
    reached-over      more counted against a bound than its limit                  (X-10)
    upstream-altered  EVS's origin fields passed on with other values               (X-8)
    gives-up          no retry after a 429                                          (X-16)
    no-backoff        a retry after a 429 without the wait it asks for              (X-16)
    no-next-cursor    a page smaller than the result without nextCursor            (X-17)
    cursor-repeats    the cursor's page repeats the page before                     (X-17)
    cursor-other-release the cursor's page names another release                    (X-17)
    cursor-empty      the cursor's page is empty                                    (X-17)
    cursor-ignores-release a cursor presented with another release is served        (X-17)
    empty-with-cursor a query that matches nothing answered with nextCursor          (X-4)

`unpinned-mismatch` is no defect for a tool without a pinned form upstream: it answers an
unknown release with release_mismatch, as such a tool can only verify an unpinned answer (X-2).

A call asks EVS for `/api/v1/version`, or for the concept of a licensed terminology with the
licence key, unless the same call was answered before: the server caches by call, as A9.4
allows. An answer that is not content is an error record: 404 release_not_available, another
version than the release asked for release_mismatch, a timeout timeout, anything else
upstream_unavailable; a 429 is waited out once. Content has items where the tool's `items`
say: the concept asked about and, at depth 1 for a traversal tool, one it reaches. What the
suite's calls (tests/calls.yaml) say of EVS's answers shapes it: their `upstream` fields go
into provenance, their `empty` arguments match nothing, their `truncating` arguments
reach a bound, and a `paged` call's first page carries a cursor to a second, of another
concept; the cursor names the release it was issued for, and with another it is refused.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path

import anyio
import mcp_types as types
import yaml
from mcp.server.caching import CacheHint
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

from nci_si_acceptance.spec import RECORDS, TOOLS, parameters, profile_tools

DEFECT = os.environ.get("COMPLIANT_SERVER_DEFECT", "")
EVS = os.environ["NCI_SI_EVS_BASE_URL"]
TIMEOUT = float(os.environ.get("NCI_SI_TIMEOUT_SECONDS", "10"))
LICENCE_KEY = os.environ.get("NCI_SI_EVS_LICENSE_KEY")
LICENSED = {"mdr"}
# What an upstream request that got no HTTP answer counts as.
CLOSED, TIMED_OUT = 0, -1
# How an unknown release is reported: by a tool without a pinned form, as a mismatch (X-2).
UNKNOWN_RELEASE = "release_mismatch" if DEFECT == "unpinned-mismatch" else "release_not_available"
# A failure the defect turns into an empty success.
SWALLOWED = {"unknown-as-empty": "release_not_available", "outage-as-empty": "upstream_unavailable"}
# The suite's calls (tests/calls.yaml): what each tool's upstream answers would say.
CALLS = yaml.safe_load((Path(__file__).parent.parent / "tests" / "calls.yaml").read_text())
# Whether each call so far reached EVS, and the calls already answered.
reached = []
answered = set()


def _ask(path: str, headers: dict[str, str]) -> tuple[int, dict, dict]:
    """One request to EVS: its status (or CLOSED, TIMED_OUT), body and headers."""

    request = urllib.request.Request(EVS + path, headers=headers)  # noqa: S310 - the fixtures
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}"), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}"), dict(error.headers)
    except OSError as error:
        # A timeout comes as itself, or inside a URLError as its reason.
        reason = getattr(error, "reason", error)
        return (TIMED_OUT if isinstance(reason, TimeoutError) else CLOSED), {}, {}


def _request(arguments: dict, correlation: str) -> tuple[str, dict[str, str]]:
    """The path a call asks EVS for, and its headers: the licence key only for licensed
    content (A7.5)."""

    headers = {} if DEFECT == "no-correlation" else {"X-Correlation-ID": correlation}
    terminology = arguments.get("terminology")
    if terminology not in LICENSED:
        return "/api/v1/version", headers
    if LICENCE_KEY and DEFECT != "keyless":
        headers["X-EVSRESTAPI-License-Key"] = LICENCE_KEY
    if DEFECT == "logs-key":
        sys.stderr.write(f"asking with {headers}\n")
        sys.stderr.flush()
    if DEFECT == "files-key":
        (Path(os.environ["NCI_SI_DATA_DIR"]) / "requests.txt").write_text(str(headers))
    return (
        f"/api/v1/concept/{terminology}_{arguments.get('release')}/{arguments.get('code')}",
        headers,
    )


def _asked(arguments: dict, correlation: str) -> tuple[int, dict]:
    """EVS's answer to a call, after one wait and retry on 429 (A6.5)."""

    path, headers = _request(arguments, correlation)
    status, body, answer_headers = _ask(path, headers)
    if status == HTTPStatus.TOO_MANY_REQUESTS and DEFECT != "gives-up":
        time.sleep(0 if DEFECT == "no-backoff" else float(answer_headers.get("Retry-After", 0)))
        status, body, _ = _ask(path, headers)
    if DEFECT == "repeated-request":
        _ask(path, headers)
    reached.append(status == HTTPStatus.OK)
    return status, body


def _failure(status: int, body: dict, arguments: dict) -> str | None:
    """The error code of EVS's answer, or None for content of the release asked for."""

    if status == TIMED_OUT:
        return "timeout"
    if status == HTTPStatus.NOT_FOUND:
        return UNKNOWN_RELEASE
    if status != HTTPStatus.OK:
        return "upstream_unavailable"
    return _mismatch(body, arguments)


def _mismatch(body: dict, arguments: dict) -> str | None:
    asked = arguments.get("release")
    mismatched = asked is not None and body.get("version", asked) != asked
    return "release_mismatch" if mismatched and DEFECT != "accepts-mismatch" else None


def _input_schema(name: str) -> dict:
    names, required = parameters(name)
    if DEFECT == "parameter-renamed" and name == "get_concept":
        names, required = names - {"code"} | {"conceptCode"}, required - {"code"} | {"conceptCode"}
    if DEFECT == "release-optional" and name == "get_concept":
        required -= {"release"}
    return {
        "type": "object",
        "properties": {key: {} for key in names},
        "required": sorted(required),
    }


# A result is either an error record or a success.
CODES = RECORDS["error"]["fields"]["code"]["values"]
ERROR = {
    "type": "object",
    "required": ["code", "message"],
    "properties": {"code": {"enum": CODES[:1] if DEFECT == "one-code" else CODES}},
}
# A success is anything but an error record, so that a list result reaches the suite (X-14).
OUTPUT_SCHEMAS = {
    "": {
        "oneOf": [
            {"type": "object", "required": ["error"], "properties": {"error": ERROR}},
            {"not": {"type": "object", "required": ["error"]}},
        ],
    },
    "no-error-shape": {"type": "object", "required": ["provenance"]},
    "declares-no-shape": {"type": "object"},
}


def _description(name: str) -> str:
    if DEFECT == "placeholder":
        return "TODO"
    when = " Called before." if DEFECT == "redescribed" and reached else ""
    return f"The {name} tool of EVS.{when}"


def _tool(name: str) -> types.Tool:
    return types.Tool(
        name=name,
        description=_description(name),
        input_schema=_input_schema(name),
        output_schema=OUTPUT_SCHEMAS.get(DEFECT, OUTPUT_SCHEMAS[""]),
        annotations=types.ToolAnnotations(
            read_only_hint=True,
            destructive_hint=DEFECT == "destructive",
            idempotent_hint=DEFECT != "not-idempotent",
            open_world_hint=DEFECT != "closed-world",
        ),
    )


def _names() -> list[str]:
    names = sorted(profile_tools("evs"))
    if DEFECT == "misnamed":
        names.append("ConceptLookup")
    dropped = {
        "missing-tool": True,
        "listing-changes": bool(reached),
        "hides-tools": False in reached,
    }
    return names[1:] if dropped.get(DEFECT) else names


async def list_tools(_context, _params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[_tool(name) for name in _names()])


def _provenance(name: str, arguments: dict, correlation: str) -> dict:
    release = "26.08e" if DEFECT == "wrong-release" else arguments.get("release")
    terminology = "mdr" if DEFECT == "wrong-terminology" else arguments.get("terminology")
    provenance = {
        "release": {"terminology": terminology, "identifier": release},
        "source": "evs_rest",
        "retrievedAt": "today" if DEFECT == "bad-timestamp" else datetime.now(UTC).isoformat(),
        "servedBy": "live",
        "correlationId": correlation,
        "upstream": _upstream(name, arguments),
    }
    if DEFECT == "no-served-by":
        del provenance["servedBy"]
    return provenance


def _upstream(name: str, arguments: dict) -> dict:
    """What EVS says of the origin of the call's items, as the suite's calls list it, the
    pin (`$terminology`, `$release`) taken from the call."""

    listed = CALLS.get(name, {}).get("upstream", {})
    supplied = {
        key: arguments.get(value[1:]) if str(value).startswith("$") else value
        for key, value in listed.items()
    }
    if DEFECT == "upstream-renamed":
        return {f"evs{key.title()}": value for key, value in supplied.items()}
    if DEFECT == "upstream-altered":
        return {key: f"{value}+" for key, value in supplied.items()}
    return supplied


def _matches_nothing(name: str, arguments: dict) -> bool:
    empty = CALLS.get(name, {}).get("empty")
    return bool(empty) and all(arguments.get(key) == value for key, value in empty.items())


def _truncation(name: str, arguments: dict) -> dict:
    """The truncation field of a tool that bounds its result: a bound reached where the call
    sets one of the suite's truncating arguments."""

    if "truncation" not in TOOLS[name]["returns"]:
        return {}
    truncating = CALLS.get(name, {}).get("truncating", {"arguments": {}})
    limits = [arguments[key] for key in truncating["arguments"] if key in arguments]
    if not limits:
        return {"truncation": {"occurred": False}}
    return {"truncation": _reached(truncating["bound"], limits[0])}


def _reached(bound: str, limit: int) -> dict:
    """The truncation record of a bound reached, with one item left out."""

    if DEFECT == "truncation-flag-only":
        return {"occurred": True}
    reached = limit + 1 if DEFECT == "reached-over" else limit
    record = {"occurred": True, "bound": bound, "limit": limit, "reached": reached}
    record |= {"omitted": "unknown" if DEFECT == "omitted-unknown" else 1}
    return record | ({} if DEFECT == "exact-missing" else {"exact": True})


def _items(name: str, provenance: dict, code: str = "C4817") -> list[dict]:
    code = f"NCIT:{code}" if DEFECT == "prefixed-code" else code
    items = [{"code": code, "terminology": "ncit", "provenance": provenance}]
    if TOOLS[name].get("traversal"):
        how = {"relationship": {"code": "R101"}, "direction": "outward", "polarity": "positive"}
        if DEFECT == "no-polarity":
            del how["polarity"]
        items[0]["provenance"] = provenance | {"depth": 0}
        depth = 0 if DEFECT == "nothing-reached" else 1
        items.append(
            {
                "code": "C3262",
                "terminology": "ncit",
                "provenance": provenance | {"depth": depth} | how,
            }
        )
    return items


def _placed(steps: list[str], items: list[dict]) -> object:
    """`items` placed under `steps`: none is the first item itself, and a step ending in []
    spreads them over a list."""

    if not steps:
        return items[0]
    step, rest = steps[0], steps[1:]
    if step.endswith("[]"):
        value = [_placed(rest, [item]) for item in items]
    else:
        value = _placed(rest, items)
    return {step.removesuffix("[]"): value}


def _content(name: str, arguments: dict, correlation: str) -> object:
    if DEFECT == "invalid-result":
        return {"error": "not an error record"}
    provenance = _provenance(name, arguments, correlation)
    items = [] if _matches_nothing(name, arguments) else _page(name, arguments, provenance)
    if DEFECT == "list-result":
        return items
    content = _shaped(name, items)
    # With no item to carry it, the result carries the provenance itself (M3.2).
    if not items and DEFECT != "empty-without-provenance":
        content["provenance"] = provenance
    return content | _truncation(name, arguments) | _next_cursor(name, arguments, items)


def _page(name: str, arguments: dict, provenance: dict) -> list[dict]:
    """The items of a call: with a cursor, those of the page after the first."""

    if "cursor" not in arguments or DEFECT == "cursor-repeats":
        return _items(name, provenance)
    if DEFECT == "cursor-empty":
        return []
    if DEFECT == "cursor-other-release":
        provenance = provenance | {"release": provenance["release"] | {"identifier": "26.08e"}}
    return _items(name, provenance, "C2991")


def _next_cursor(name: str, arguments: dict, items: list[dict]) -> dict:
    """The cursor of a paged call's first page, which is smaller than its result: it names
    the release the page was pinned to."""

    found = items or DEFECT == "empty-with-cursor"
    first = found and CALLS.get(name, {}).get("paged") and "cursor" not in arguments
    cursor = f"page-2@{arguments.get('release')}"
    return {"nextCursor": cursor} if first and DEFECT != "no-next-cursor" else {}


def _cursor_refused(arguments: dict) -> bool:
    """Whether a cursor is presented with another release than the one it was issued for."""

    cursor = arguments.get("cursor")
    if cursor is None or DEFECT == "cursor-ignores-release":
        return False
    return cursor.partition("@")[2] != arguments.get("release")


def _shaped(name: str, items: list[dict]) -> dict:
    """A result of `name` with `items` where its `items` paths say."""

    paths = TOOLS[name].get("items", ["."])
    placed = [_placed([step for step in path.split(".") if step], items) for path in paths]
    return placed[0] if len(placed) == 1 else _merged(placed)


def _merged(parts: list) -> dict:
    """Results of several item paths, such as nodes and edges, as one."""

    return {key: value for part in parts for key, value in part.items()}


def _error(code: str, status: int, body: dict, correlation: str) -> dict:
    said = f": {json.dumps(body)}" if DEFECT == "leaks-key" else ""
    record = {"code": code, "message": f"EVS answered {status}{said}", "correlationId": correlation}
    beside = {"concept": {"code": "C4817"}} if DEFECT == "content-with-error" else {}
    return {"error": "EVS failed" if DEFECT == "unshaped-error" else record} | beside


def _refusal(name: str, arguments: dict, correlation: str) -> dict | None:
    """The error a call gets without asking EVS, if any."""

    if DEFECT == "empty-as-error" and _matches_nothing(name, arguments):
        return _error("not_found", HTTPStatus.NOT_FOUND, {}, correlation)
    if _cursor_refused(arguments):
        return _error("invalid_request", HTTPStatus.BAD_REQUEST, {}, correlation)
    return None


def _answer(name: str, arguments: dict, correlation: str) -> tuple[object, bool]:
    """The content of a call and whether it is an error; a call answered before is not
    asked again, as A9.4 allows."""

    call = json.dumps([name, arguments], sort_keys=True)
    if refusal := _refusal(name, arguments, correlation):
        return refusal, True
    if call not in answered and DEFECT != "asks-nothing":
        status, body = _asked(arguments, correlation)
        if code := _failure(status, body, arguments):
            if code == SWALLOWED.get(DEFECT):
                return {}, False
            return _error(code, status, body, correlation), True
    answered.add(call)
    return _content(name, arguments, correlation), False


def _meta() -> dict:
    meta = {
        "ttlMs": 0 if DEFECT == "uncached-result" else 86_400_000,
        "cacheScope": "private" if DEFECT == "private-scope" else "public",
    }
    return meta | ({"licence": LICENCE_KEY} if DEFECT == "key-in-meta" else {})


async def call_tool(_context, params: types.CallToolRequestParams) -> types.CallToolResult:
    correlation = (params.meta or {}).get("correlationId", "")
    content, failed = _answer(params.name, params.arguments or {}, correlation)
    text = json.dumps(content) + (f" {LICENCE_KEY}" if DEFECT == "key-in-text" else "")
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structured_content=content,
        is_error=failed,
        _meta=_meta(),
    )


SERVER = Server(
    "compliant-server",
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
