"""An upstream that answers from fixtures and records every request it receives.

The server under test is pointed at this server instead of the live services, one
path prefix per upstream surface (`/evs`, `/cadsr`, ...). A request is answered by the
fixture whose surface, method, path and query parameters match it exactly; paths and
values are compared decoded, and the order of different parameters does not matter.
A request without a fixture gets HTTP 501. Every request is recorded, answered or
not, whatever its method:

    GET    /_log    the requests received since the log was last cleared, as JSON
    DELETE /_log    clear the log

A fixture is a JSON file:

    {
      "kind": "recorded",                 # or "crafted"
      "recorded_on": "2026-10-02",        # recorded: when it was captured
      "requirement": "C-1",               # crafted: the requirement it stands in for
      "request": {"surface": "evs", "method": "GET", "path": "/api/v1/version",
                  "params": {"include": ["summary"]}},
      "response": {"status": 200, "headers": {}, "body": {...}}
    }

A string `body` is sent as HTML, any other body as JSON, and no body as nothing. The
fixture's headers are sent too and override the content type; headers that frame the
message (`Content-Length`, `Transfer-Encoding`, ...) are the server's and are refused.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import parse_qs, unquote, urlsplit

if TYPE_CHECKING:
    from pathlib import Path

# The upstream surfaces, each served under its own path prefix.
SURFACES = ("evs", "evs-fhir", "cadsr", "cadsr-ftp", "ssis", "ssis-sparql")
LOG_PATH = "/_log"
FRAMING_HEADERS = frozenset(
    {"content-length", "transfer-encoding", "content-encoding", "connection"}
)
_JSON = {"Content-Type": "application/json"}

type Params = dict[str, list[str]]
type Key = tuple[str, str, str, tuple[tuple[str, tuple[str, ...]], ...]]


def request_key(surface: str, method: str, path: str, params: Params) -> Key:
    """What a request is matched on. Repeated values of one parameter keep their order."""

    query = tuple(sorted((name, tuple(values)) for name, values in params.items()))
    return (surface, method.upper(), path, query)


def _merge(defaults: dict[str, str], headers: dict[str, str]) -> dict[str, str]:
    """`defaults` overridden by `headers`, matching header names in any case."""

    overridden = {name.lower() for name in headers}
    kept = {name: value for name, value in defaults.items() if name.lower() not in overridden}
    return kept | headers


@dataclass(frozen=True, slots=True)
class Fixture:
    name: str
    status: int
    headers: dict[str, str]
    body: Any

    def encode(self) -> tuple[bytes, dict[str, str]]:
        """The response body, and its headers with a content type that fits the body."""

        if self.body is None:
            return b"", self.headers
        if isinstance(self.body, str):
            html = {"Content-Type": "text/html; charset=utf-8"}
            return self.body.encode(), _merge(html, self.headers)
        return json.dumps(self.body).encode(), _merge(_JSON, self.headers)


def _problem(document: dict[str, Any]) -> str | None:
    """What makes a fixture document unusable, if anything."""

    framing = sorted(set(map(str.lower, document["response"].get("headers", {}))) & FRAMING_HEADERS)
    if framing:
        return f"the server frames the response; remove {', '.join(framing)}"
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
    request, response = document["request"], document["response"]
    key = request_key(
        request["surface"], request["method"], request["path"], request.get("params", {})
    )
    fixture = Fixture(name, response["status"], response.get("headers", {}), response.get("body"))
    return key, fixture


def load_fixtures(root: Path) -> dict[Key, Fixture]:
    """Every fixture below `root`; two fixtures for the same request are an error."""

    if not root.is_dir():
        raise ValueError(f"{root} is not a fixture directory")
    fixtures: dict[Key, Fixture] = {}
    for path in sorted(root.rglob("*.json")):
        key, fixture = _read_fixture(path, root)
        if key in fixtures:
            raise ValueError(f"{fixture.name} and {fixtures[key].name} answer the same request")
        fixtures[key] = fixture
    return fixtures


class FixtureServer:
    """The fixture upstream on a free local port, served from a background thread."""

    def __init__(self, fixtures: dict[Key, Fixture]) -> None:
        self.fixtures = fixtures
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

    def log(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._log)

    def clear_log(self) -> None:
        with self._lock:
            self._log.clear()

    def answer(
        self, method: str, target: str, headers: dict[str, str], body: str = ""
    ) -> tuple[int, bytes, dict[str, str]]:
        """Answer one upstream request from the fixtures and record it."""

        url = urlsplit(target)
        surface, _, rest = unquote(url.path).removeprefix("/").partition("/")
        path, params = f"/{rest}", parse_qs(url.query, keep_blank_values=True)
        fixture = self.fixtures.get(request_key(surface, method, path, params))
        entry = {"surface": surface, "method": method, "path": path, "params": params}
        entry |= {"headers": headers, "body": body, "fixture": fixture.name if fixture else None}
        with self._lock:
            self._log.append(entry)
        if fixture is None:
            message = {"error": "no fixture answers this request", **entry}
            return HTTPStatus.NOT_IMPLEMENTED, json.dumps(message).encode(), _JSON
        return fixture.status, *fixture.encode()


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
            self._send(*server.answer(self.command, self.path, dict(self.headers), body))

        def do_GET(self) -> None:
            if self.path == LOG_PATH:
                self._send(HTTPStatus.OK, json.dumps(server.log()).encode(), _JSON)
            else:
                self._upstream()

        def do_DELETE(self) -> None:
            if self.path == LOG_PATH:
                server.clear_log()
                self._send(HTTPStatus.NO_CONTENT, b"", {})
            else:
                self._upstream()

        # http.server finds a handler by these names.
        do_HEAD = do_POST = do_PUT = do_PATCH = do_OPTIONS = _upstream  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:
            """Silent: the request log is the record."""

    return Handler
