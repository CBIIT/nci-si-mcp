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
    quotes-not-offered a description that assigns a value not offered in backticks (P-11)
    lists-not-offered an input schema whose enum holds a value not offered          (P-11)
    patterns-not-offered an input schema whose pattern admits a value not offered   (P-11)
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
    cursor-offset-only a cursor presented with other arguments is served            (X-17)
    cursor-refuses-default a cursor presented with a default given is refused       (X-17)
    cursor-inherits   a cursor fills arguments left out from the first call          (X-17)
    wrong-default     a left-out argument with a stated default served otherwise    (X-20)
    empty-with-cursor a query that matches nothing answered with nextCursor          (X-4)
    raised-to-one     a bounded argument below one served as one                    (X-18)
    release-defaulted a call without its required release served all the same        (X-22)
    unchecked-identifiers an identifier off its stated form served as given          (X-23)
    checks-after-asking an identifier off its form refused only after a request carried it (X-23)
    unencoded-code    a code put into the path as it is, a slash in it included     (X-23)
    asks-in-path-then-refuses an identifier off its form sent in a path, then refused (X-23)
    asks-in-form-then-refuses an identifier off its form sent in a form, then refused (X-23)
    unencoded-text    free text put into the query string as it is, but for spaces  (X-24)
    text-twice        free text sent as its own field and inside another             (X-24)
    joins-listing     a licensed item with the listing's licence text, given or not (X-19)
    attribution-everywhere every item with the listing's licence text, NCIt's included (X-19)
    drops-attribution the licence text EVS gives with an item left out             (X-19)
    alters-attribution the licence text EVS gives with an item cut short            (X-19)
    attributes-all-but-last the licence text EVS gives left off the last item         (X-19)
    attributes-ncit   NCIt items with a licence text of the server's own            (X-19)
    sticky-attribution an item EVS gave no text with carries the last text it gave   (X-19)

`unpinned-mismatch` is no defect for a tool without a pinned form upstream: it answers an
unknown release with release_mismatch, as such a tool can only verify an unpinned answer (X-2).

A call asks EVS for `/api/v1/version`, or for the concept of a licensed terminology (or its
search, for a call that names no code) with the licence key, unless the same call was
answered before: the server caches by call, as A9.4 allows. An answer that is not content
is an error record: 404 release_not_available, another version than the release asked for
release_mismatch, a timeout timeout, anything else
upstream_unavailable; a 429 is waited out once. Content has items where the tool's `items`
say: the concept asked about and, at depth 1 for a traversal tool, one it reaches. What the
suite's calls (tests/calls.yaml) say of EVS's answers shapes it: their `upstream` fields go
into provenance, their `empty` arguments match nothing, their `truncating` arguments
reach a bound, and under their `paged` arguments a first page carries a cursor to a second,
of other concepts; the cursor carries the arguments it was issued for, as applied (an
optional argument left out as the default the specification states), and presented with
others it is refused. A bounded argument below one is refused, and an item carries the licence
text EVS's answer gives with the concept asked for, and none of its own.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote, urlencode

import anyio
import mcp_types as types
import yaml
from mcp.server.caching import CacheHint
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

from nci_si_acceptance.spec import RECORDS, TOOLS, defaults, parameters, profile_tools

DEFECT = os.environ.get("COMPLIANT_SERVER_DEFECT", "")
EVS = os.environ["NCI_SI_EVS_BASE_URL"]
TIMEOUT = float(os.environ.get("NCI_SI_TIMEOUT_SECONDS", "10"))
LICENCE_KEY = os.environ.get("NCI_SI_EVS_LICENSE_KEY")
LICENSED = {"mdr"}
# The licensed placeholder concept and its child (license/restricted, license/attributed).
LICENSED_CODES = ["10000000", "10000001"]
# What an upstream request that got no HTTP answer counts as.
CLOSED, TIMED_OUT = 0, -1
# How an unknown release is reported: by a tool without a pinned form, as a mismatch (X-2).
UNKNOWN_RELEASE = "release_mismatch" if DEFECT == "unpinned-mismatch" else "release_not_available"
# A failure the defect turns into an empty success.
SWALLOWED = {"unknown-as-empty": "release_not_available", "outage-as-empty": "upstream_unavailable"}
# A request body, where there is one.
type Body = bytes | None
# The suite's calls (tests/calls.yaml): what each tool's upstream answers would say.
CALLS = yaml.safe_load((Path(__file__).parent.parent / "tests" / "calls.yaml").read_text())
# Whether each call so far reached EVS, and EVS's answer to each call already answered.
reached = []
answered: dict[str, dict] = {}
# The licence texts EVS gave so far (the sticky-attribution defect).
given: list[str] = []


def _ask(
    path: str, headers: dict[str, str], body: bytes | None = None, method: str = "GET"
) -> tuple[int, dict, dict]:
    """One request to EVS: its status (or CLOSED, TIMED_OUT), body and headers."""

    request = urllib.request.Request(EVS + path, body, headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}"), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}"), dict(error.headers)
    except OSError as error:
        # A timeout comes as itself, or inside a URLError as its reason.
        reason = getattr(error, "reason", error)
        return (TIMED_OUT if isinstance(reason, TimeoutError) else CLOSED), {}, {}


def _request(name: str, arguments: dict, correlation: str) -> tuple[str, dict[str, str], Body]:
    """The path a call asks EVS for, its headers, and its free text as a JSON body (A7.7): for
    NCIt the release query; for another terminology the concept, its code one encoded segment
    (A7.6), with the licence key where the terminology is licensed."""

    headers = {} if DEFECT == "no-correlation" else {"X-Correlation-ID": correlation}
    terminology = arguments.get("terminology")
    query, body = _texts(name, arguments, headers)
    if terminology in (None, "ncit"):
        return "/api/v1/version" + query, headers, body
    if terminology in LICENSED:
        _licensed_headers(headers)
    # The concept a call names, the first of the codes it names, or else the search.
    code = arguments.get("code") or next(iter(arguments.get("codes", [])), "search")
    segment = code if DEFECT == "unencoded-code" else quote(code, safe="")
    path = f"/api/v1/concept/{terminology}_{arguments.get('release')}/{segment}{query}"
    return path, headers, body


def _licensed_headers(headers: dict[str, str]) -> None:
    if LICENCE_KEY and DEFECT != "keyless":
        headers["X-EVSRESTAPI-License-Key"] = LICENCE_KEY
    _leak(headers)


def _texts(name: str, arguments: dict, headers: dict[str, str]) -> tuple[str, Body]:
    """The call's free-text arguments as a query string and a body: a JSON body of one field
    each, sent with the request (a GET, as EVS's fixtures are asked)."""

    texts = {
        key: arguments[key]
        for key in TOOLS[name].get("free_text", [])
        if isinstance(arguments.get(key), str)
    }
    if not texts:
        return "", None
    if DEFECT == "unencoded-text":
        query = "&".join(f"{key}={text.replace(' ', '%20')}" for key, text in texts.items())
        return "?" + query, None
    if DEFECT == "text-twice":
        texts |= {"filter": "name:" + next(iter(texts.values()))}
    headers["Content-Type"] = "application/json"
    return "", json.dumps(texts).encode()


def _leak(headers: dict[str, str]) -> None:
    """The request headers, licence key included, written where the defect says (X-12)."""

    if DEFECT == "logs-key":
        sys.stderr.write(f"asking with {headers}\n")
        sys.stderr.flush()
    if DEFECT == "files-key":
        (Path(os.environ["NCI_SI_DATA_DIR"]) / "requests.txt").write_text(str(headers))


def _asked(name: str, arguments: dict, correlation: str) -> tuple[int, dict]:
    """EVS's answer to a call, after one wait and retry on 429 (A6.5)."""

    path, headers, sent = _request(name, arguments, correlation)
    status, body, answer_headers = _ask(path, headers, sent)
    if status == HTTPStatus.TOO_MANY_REQUESTS and DEFECT != "gives-up":
        time.sleep(0 if DEFECT == "no-backoff" else float(answer_headers.get("Retry-After", 0)))
        status, body, _ = _ask(path, headers, sent)
    if DEFECT == "repeated-request":
        _ask(path, headers, sent)
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
    listed = {
        "lists-not-offered": {"enum": _not_offered(name)},
        "patterns-not-offered": {"pattern": f"^({'|'.join(_not_offered(name))})$"},
    }.get(DEFECT, {})
    return {
        "type": "object",
        "properties": {key: dict(listed) for key in names},
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
    # What the tool does not offer may be said in words, never shown as a value (P-11).
    lacking = "".join(f" It does not offer {value}." for value in _not_offered(name))
    if DEFECT == "quotes-not-offered":
        lacking = "".join(f" Try `key={value}`." for value in _not_offered(name))
    return f"The {name} tool of EVS.{when}{lacking}"


def _not_offered(name: str) -> list[str]:
    return [value for values in TOOLS[name].get("not_offered", {}).values() for value in values]


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


def _provenance(name: str, arguments: dict, correlation: str, answer: dict) -> dict:
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
    return provenance | _attribution(terminology, answer)


def _attribution(terminology: str | None, answer: dict) -> dict:
    """The licence text EVS's answer gives with the concept asked for, as the item's
    attribution (X-19)."""

    text = _remembered(_own_text(terminology) or _given_text(answer))
    if not text or DEFECT == "drops-attribution":
        return {}
    return {"attribution": text[:40] if DEFECT == "alters-attribution" else text}


def _own_text(terminology: str | None) -> str | None:
    """The licence text a defect attaches where EVS gave none."""

    if DEFECT == "attributes-ncit" and terminology not in LICENSED:
        return "Licensed by this server."
    joined = DEFECT == "joins-listing" and terminology in LICENSED
    return _licence_text(LICENSED) if joined or DEFECT == "attribution-everywhere" else None


def _remembered(text: str | None) -> str | None:
    """`text`; under the sticky defect, the last text given where there is none."""

    if text:
        given.append(text)
        return text
    return given[-1] if DEFECT == "sticky-attribution" and given else None


def _given_text(answer: dict) -> str | None:
    """The licence text EVS gives with the concept, or the first concept of a search."""

    return (answer.get("concepts") or [answer])[0].get("licenseText")


def _licence_text(terminologies: set[str]) -> str:
    """The licence text the terminology listing gives the first of `terminologies`."""

    _, rows, _ = _ask("/api/v1/metadata/terminologies", {})
    return next(
        row["metadata"]["licenseText"] for row in rows if row["terminology"] in terminologies
    )


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
    limits = [
        value for key, value in truncating["arguments"].items() if arguments.get(key) == value
    ]
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


def _items(name: str, provenance: dict, codes: tuple[str, str] = ("C4817", "C3262")) -> list[dict]:
    """The concept `codes` names first and, for a traversal tool, the one it names second,
    reached at depth 1."""

    code, reached = codes
    code = f"NCIT:{code}" if DEFECT == "prefixed-code" else code
    items = [{"code": code, "terminology": "ncit", "provenance": provenance}]
    if TOOLS[name].get("traversal"):
        how = _how()
        items[0]["provenance"] = provenance | {"depth": 0}
        depth = 0 if DEFECT == "nothing-reached" else 1
        items.append(
            {
                "code": reached,
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


def _content(name: str, arguments: dict, correlation: str, answer: dict) -> object:
    if DEFECT == "invalid-result":
        return {"error": "not an error record"}
    provenance = _provenance(name, arguments, correlation, answer)
    items = _items_of_call(name, arguments, provenance)
    if DEFECT == "list-result":
        return items
    content = _shaped(name, items)
    # With no item to carry it, the result carries the provenance itself (M3.2).
    if not items and DEFECT != "empty-without-provenance":
        content["provenance"] = provenance
    return content | _truncation(name, arguments) | _next_cursor(name, arguments, items)


def _items_of_call(name: str, arguments: dict, provenance: dict) -> list[dict]:
    if _matches_nothing(name, arguments):
        return []
    if arguments.get("terminology") in LICENSED:
        return _licensed_items(name, arguments, provenance)
    return _page(name, arguments, provenance)


def _licensed_items(name: str, arguments: dict, provenance: dict) -> list[dict]:
    """The licensed concepts a call asks for: its code or codes, both placeholders for a search;
    a traversal reaches the child of 10000000 (license/restricted)."""

    if TOOLS[name].get("traversal"):
        reached = provenance | {"depth": 1} | _how()
        items = [{"code": "10000001", "terminology": "mdr", "provenance": reached}]
    else:
        codes = (
            [arguments["code"]] if "code" in arguments else arguments.get("codes", LICENSED_CODES)
        )
        items = [{"code": code, "terminology": "mdr", "provenance": provenance} for code in codes]
    if DEFECT == "attributes-all-but-last":
        last = items[-1]["provenance"]
        items[-1] = items[-1] | {
            "provenance": {k: v for k, v in last.items() if k != "attribution"}
        }
    return items


def _how() -> dict:
    """How an item reached by traversal was reached."""

    how = {"relationship": {"code": "R101"}, "direction": "outward", "polarity": "positive"}
    if DEFECT == "no-polarity":
        del how["polarity"]
    return how


def _page(name: str, arguments: dict, provenance: dict) -> list[dict]:
    """The items of a call: with a cursor, those of the page after the first."""

    if DEFECT == "wrong-default" and set(defaults(name)) - set(arguments):
        return _items(name, provenance, ("C2991", "C9118"))
    if "cursor" not in arguments or DEFECT == "cursor-repeats":
        return _items(name, provenance)
    if DEFECT == "cursor-empty":
        return []
    if DEFECT == "cursor-other-release":
        provenance = provenance | {"release": provenance["release"] | {"identifier": "26.08e"}}
    return _items(name, provenance, ("C2991", "C9118"))


def _next_cursor(name: str, arguments: dict, items: list[dict]) -> dict:
    """The cursor of a paged call's first page, which is smaller than its result: it carries
    the arguments the page was asked with, as applied."""

    cursor = {"nextCursor": f"page-2@{json.dumps(_applied(name, arguments), sort_keys=True)}"}
    if DEFECT == "empty-with-cursor" and not items:
        return cursor
    first = items and _paging(name, arguments) and "cursor" not in arguments
    return cursor if first and DEFECT != "no-next-cursor" else {}


def _paging(name: str, arguments: dict) -> bool:
    """Whether a call sets the suite's paged arguments, under which a page is smaller than
    the result."""

    paged = CALLS.get(name, {}).get("paged", [])
    return any(all(arguments.get(k) == v for k, v in entry["arguments"].items()) for entry in paged)


def _applied(name: str, arguments: dict) -> dict:
    """A call's arguments as applied: an optional argument left out is its default."""

    stated = {} if DEFECT == "cursor-refuses-default" else defaults(name)
    return stated | {key: value for key, value in arguments.items() if key != "cursor"}


def _cursor_refused(name: str, arguments: dict) -> bool:
    """Whether a cursor is presented with other arguments than those it was issued for."""

    cursor = arguments.get("cursor")
    if cursor is None or DEFECT == "cursor-offset-only":
        return False
    issued, now = json.loads(cursor.partition("@")[2]), _applied(name, arguments)
    if DEFECT == "cursor-inherits":
        now = issued | {key: value for key, value in arguments.items() if key != "cursor"}
    if DEFECT == "cursor-ignores-release":
        issued, now = issued | {"release": None}, now | {"release": None}
    return issued != now


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
    if (
        _cursor_refused(name, arguments)
        or _below_one(name, arguments)
        or _unpinned(name, arguments)
        or _malformed(name, arguments)
    ):
        return _error("invalid_request", HTTPStatus.BAD_REQUEST, {}, correlation)
    return None


def _malformed(name: str, arguments: dict) -> bool:
    """Whether an identifier is off the form its tool states (A7.6)."""

    if DEFECT == "unchecked-identifiers":
        return False
    forms = TOOLS[name].get("patterns", {}).items()
    off = [key for key, form in forms if not _formed(form, arguments, key)]
    if off:
        _probe(arguments)
    return bool(off)


def _probe(arguments: dict) -> None:
    """The requests a defect makes with the call's values before it refuses the call."""

    for value in _strings(arguments):
        if DEFECT == "checks-after-asking":
            _ask("/api/v1/version?" + urlencode({"probe": value}), {})
        if DEFECT == "asks-in-path-then-refuses":
            # Only what a URL cannot carry is encoded: a space, a fragment mark, a newline.
            encoded = value.replace(" ", "%20").replace("#", "%23").replace("\n", "%0A")
            _ask("/api/v1/version/" + encoded, {})
        if DEFECT == "asks-in-form-then-refuses":
            form = {"Content-Type": "application/x-www-form-urlencoded"}
            _ask("/api/v1/version", form, urlencode({"probe": value}).encode(), "POST")


def _strings(arguments: dict) -> list[str]:
    """The call's string values, a list's elements included."""

    values = [each for value in arguments.values() for each in _listed(value)]
    return [value for value in values if isinstance(value, str)]


def _listed(value: object) -> list:
    return value if isinstance(value, list) else [value]


def _formed(form: str | dict, arguments: dict, key: str) -> bool:
    """Whether the argument has its form: by the call's terminology where the form is."""

    pattern = form.get(arguments.get("terminology")) if isinstance(form, dict) else form
    given = arguments.get(key)
    values = given if isinstance(given, list) else [given]
    return pattern is None or given is None or all(re.fullmatch(pattern, str(v)) for v in values)


def _unpinned(name: str, arguments: dict) -> bool:
    required = parameters(name)[1]
    return "release" in required - arguments.keys() and DEFECT != "release-defaulted"


def _below_one(name: str, arguments: dict) -> bool:
    bounded = TOOLS[name].get("bounds", {})
    below = [key for key in bounded if key in arguments and arguments[key] < 1]
    return bool(below) and DEFECT != "raised-to-one"


def _answer(name: str, arguments: dict, correlation: str) -> tuple[object, bool]:
    """The content of a call and whether it is an error; a call answered before is not
    asked again, as A9.4 allows."""

    call = json.dumps([name, arguments], sort_keys=True)
    if refusal := _refusal(name, arguments, correlation):
        return refusal, True
    if call not in answered and DEFECT != "asks-nothing":
        status, body = _asked(name, arguments, correlation)
        if code := _failure(status, body, arguments):
            if code == SWALLOWED.get(DEFECT):
                return {}, False
            return _error(code, status, body, correlation), True
        answered[call] = body
    return _content(name, arguments, correlation, answered.get(call, {})), False


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
