"""An upstream that answers from fixtures and records every request it receives.

The server under test is pointed at this server instead of the live services, one
path prefix per upstream surface (`/evs`, `/cadsr`, ...). Every request is recorded,
answered or not, whatever its method:

    GET    /_log    the requests received since the last reset, as JSON
    DELETE /_log    reset: clear the log and rewind every response sequence

A request is answered by the fixture whose surface, method, path, query parameters
and body match it. Paths and values are compared decoded, the order of different
parameters does not matter, and repeated values of one parameter keep their order.
A fixture without a `body` matches any request body. A request that no fixture
answers gets HTTP 501.

A fixture is a JSON file:

    {
      "kind": "recorded",                 # or "crafted"
      "recorded_on": "2026-10-02",        # recorded: when it was captured
      "requirement": "C-1",               # crafted: the requirement it stands in for
      "request": {"surface": "evs", "method": "GET", "path": "/api/v1/version",
                  "params": {"include": ["summary"]}, "body": "..."},
      "response": {"status": 200, "headers": {}, "body": {...}}
    }

`responses` (a list) in place of `response` answers successive requests in order,
the last one repeating; the sequence starts again at each reset. A response may wait
`delay_seconds` before it is sent, or be the fault `{"fault": "reset"}`: the
connection is closed without an answer. A string `body` is sent as HTML, any other
body as JSON, and no body as nothing. The fixture's headers are sent too and override
the content type; headers that frame the message (`Content-Length`, ...) are the
server's and are refused.

Fixtures under `scenarios/<group>/<name>/` belong to the scenario `<group>/<name>`
and answer only while it is active, in place of an ordinary fixture for the same
request. Two fixtures for the same request in one scenario, or outside the
scenarios, are refused.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import parse_qs, unquote, urlsplit

if TYPE_CHECKING:
    from pathlib import Path

# The upstream surfaces, each served under its own path prefix.
SURFACES = ("evs", "evs-fhir", "cadsr", "cadsr-ftp", "ssis", "ssis-sparql")
SCENARIOS = "scenarios"
LOG_PATH = "/_log"
FRAMING_HEADERS = frozenset(
    {"content-length", "transfer-encoding", "content-encoding", "connection"}
)
FAULTS = frozenset({"reset"})
_JSON = {"Content-Type": "application/json"}

type Params = dict[str, list[str]]
type Key = tuple[str, str, str, tuple[tuple[str, tuple[str, ...]], ...], str | None]
type Fixtures = dict[Key, Fixture]


def request_key(
    surface: str, method: str, path: str, params: Params, body: str | None = None
) -> Key:
    """What a request is matched on; `body` None stands for any body."""

    query = tuple(sorted((name, tuple(values)) for name, values in params.items()))
    return (surface, method.upper(), path, query, body)


def _merge(defaults: dict[str, str], headers: dict[str, str]) -> dict[str, str]:
    """`defaults` overridden by `headers`, matching header names in any case."""

    overridden = {name.lower() for name in headers}
    kept = {name: value for name, value in defaults.items() if name.lower() not in overridden}
    return kept | headers


@dataclass(frozen=True, slots=True)
class Response:
    status: int = HTTPStatus.OK
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None
    delay_seconds: float = 0
    fault: str | None = None

    def encode(self) -> tuple[bytes, dict[str, str]]:
        """The response body, and its headers with a content type that fits the body."""

        if self.body is None:
            return b"", self.headers
        if isinstance(self.body, str):
            html = {"Content-Type": "text/html; charset=utf-8"}
            return self.body.encode(), _merge(html, self.headers)
        return json.dumps(self.body).encode(), _merge(_JSON, self.headers)


@dataclass(frozen=True, slots=True)
class Fixture:
    name: str
    responses: tuple[Response, ...]


@dataclass(frozen=True, slots=True)
class FixtureSet:
    """The ordinary fixtures, and the fixtures of each scenario."""

    ordinary: Fixtures
    scenarios: dict[str, Fixtures]


def _response_problem(response: dict[str, Any]) -> str | None:
    framing = sorted(set(map(str.lower, response.get("headers", {}))) & FRAMING_HEADERS)
    if framing:
        return f"the server frames the response; remove {', '.join(framing)}"
    if response.get("fault", "reset") not in FAULTS:
        return f"a fault is one of {', '.join(sorted(FAULTS))}"
    return None


def _problem(document: dict[str, Any]) -> str | None:
    """What makes a fixture document unusable, if anything."""

    if ("response" in document) == ("responses" in document):
        return "a fixture has either a response or responses"
    responses = document.get("responses") or [document["response"]]
    problems = (_response_problem(response) for response in responses)
    return next((problem for problem in problems if problem), None) or _provenance_problem(document)


def _provenance_problem(document: dict[str, Any]) -> str | None:
    kind = document.get("kind")
    if kind == "recorded":
        return None if document.get("recorded_on") else "a recorded fixture names recorded_on"
    if kind == "crafted":
        return None if document.get("requirement") else "a crafted fixture names its requirement"
    return "kind is recorded or crafted"


def _read_fixture(path: Path, root: Path) -> tuple[Key, Fixture]:
    document = json.loads(path.read_text(encoding="utf-8"))
    name = path.relative_to(root).as_posix()
    if problem := _problem(document):
        raise ValueError(f"{name}: {problem}")
    request = document["request"]
    key = request_key(
        request["surface"],
        request["method"],
        request["path"],
        request.get("params", {}),
        request.get("body"),
    )
    responses = document.get("responses") or [document["response"]]
    return key, Fixture(name, tuple(Response(**response) for response in responses))


def _scenario_of(path: Path, root: Path) -> str | None:
    parts = path.relative_to(root).parts
    if parts[0] != SCENARIOS:
        return None
    if len(parts) < 4:  # noqa: PLR2004 - scenarios/<group>/<name>/<fixture>
        raise ValueError(
            f"{path.relative_to(root)}: a scenario fixture lies in scenarios/<group>/<name>/"
        )
    return f"{parts[1]}/{parts[2]}"


def load_fixtures(root: Path) -> FixtureSet:
    """Every fixture below `root`, sorted into the ordinary ones and those of each scenario."""

    if not root.is_dir():
        raise ValueError(f"{root} is not a fixture directory")
    fixtures = FixtureSet({}, {})
    for path in sorted(root.rglob("*.json")):
        scenario = _scenario_of(path, root)
        table = (
            fixtures.ordinary if scenario is None else fixtures.scenarios.setdefault(scenario, {})
        )
        key, fixture = _read_fixture(path, root)
        if key in table:
            raise ValueError(f"{fixture.name} and {table[key].name} answer the same request")
        table[key] = fixture
    return fixtures


class FixtureServer:
    """The fixture upstream on a free local port, served from a background thread."""

    def __init__(self, fixtures: FixtureSet) -> None:
        self.fixtures = fixtures
        self._active: Fixtures = fixtures.ordinary
        self._served: dict[str, int] = {}
        self._log: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._http = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self.url = f"http://127.0.0.1:{self._http.server_port}"
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._http.shutdown()
        self._http.server_close()
        self._thread.join()

    def base_url(self, surface: str) -> str:
        return f"{self.url}/{surface}"

    def activate(self, *scenarios: str) -> None:
        """Answer from these scenarios' fixtures first; no argument ends them. The log stays."""

        unknown = sorted(set(scenarios) - set(self.fixtures.scenarios))
        if unknown:
            raise ValueError(f"no such scenario: {', '.join(unknown)}")
        active = dict(self.fixtures.ordinary)
        for scenario in scenarios:
            active |= self.fixtures.scenarios[scenario]
        with self._lock:
            self._active = active

    def log(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._log)

    def reset(self) -> None:
        with self._lock:
            self._log.clear()
            self._served.clear()

    def _next(self, key: Key) -> tuple[Fixture | None, Response | None]:
        """The fixture for a request and its response in turn; a body-less fixture as fallback."""

        fixture = self._active.get(key) or self._active.get((*key[:4], None))
        if fixture is None:
            return None, None
        turn = self._served.get(fixture.name, 0)
        self._served[fixture.name] = turn + 1
        return fixture, fixture.responses[min(turn, len(fixture.responses) - 1)]

    def answer(self, method: str, target: str, headers: dict[str, str], body: str) -> Response:
        """Choose the response to one upstream request, and record the request."""

        url = urlsplit(target)
        surface, _, rest = unquote(url.path).removeprefix("/").partition("/")
        path, params = f"/{rest}", parse_qs(url.query, keep_blank_values=True)
        entry = {"surface": surface, "method": method, "path": path, "params": params}
        with self._lock:
            fixture, response = self._next(request_key(surface, method, path, params, body))
            entry |= {"headers": headers, "body": body, "fixture": fixture and fixture.name}
            self._log.append(entry)
        if response is None:
            message = {"error": "no fixture answers this request", **entry}
            return Response(HTTPStatus.NOT_IMPLEMENTED, _JSON, message)
        return response


def _handler(server: FixtureServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _upstream(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8", "replace")
            response = server.answer(self.command, self.path, dict(self.headers), body)
            time.sleep(response.delay_seconds)
            if response.fault == "reset":
                self.close_connection = True
                return
            self._send(response.status, *response.encode())

        def do_GET(self) -> None:
            if self.path == LOG_PATH:
                self._send(HTTPStatus.OK, json.dumps(server.log()).encode(), _JSON)
            else:
                self._upstream()

        def do_DELETE(self) -> None:
            if self.path == LOG_PATH:
                server.reset()
                self._send(HTTPStatus.NO_CONTENT, b"", {})
            else:
                self._upstream()

        # http.server finds a handler by these names.
        do_HEAD = do_POST = do_PUT = do_PATCH = do_OPTIONS = _upstream  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:
            """Silent: the request log is the record."""

    return Handler
