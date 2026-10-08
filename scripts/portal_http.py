"""Read-only loopback evidence companion; UAT/PROD platform integration is not enabled."""

from __future__ import annotations

import re
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import cast
from urllib.parse import parse_qs, urlsplit

from scripts.portal_help import help_page
from scripts.portal_store import EvidenceNotFoundError, EvidenceStore
from scripts.portal_views import comparison_page, history_page, page, run_page

MAX_TARGET = 2048
MAX_FILTER = 100


def _query(query: str, allowed: set[str]) -> dict[str, str]:
    values = parse_qs(query, keep_blank_values=True, max_num_fields=4)
    if not set(values) <= allowed:
        raise ValueError("Unknown query field")
    if any(len(items) != 1 or len(items[0]) > MAX_FILTER for items in values.values()):
        raise ValueError("Invalid query field")
    return {key: items[0] for key, items in values.items()}


def _route(store: EvidenceStore, target: str) -> tuple[int, str, str]:
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or len(target) > MAX_TARGET:
        raise ValueError("Invalid local request target")
    if parts.path == "/health":
        return 200, "application/json", '{"status":"ok","deployment":"local-only"}'
    if parts.path == "/":
        return 200, "text/html", history_page(store.history())
    if parts.path == "/help":
        return 200, "text/html", help_page()
    return _result_route(store, parts.path, parts.query)


def _result_route(store: EvidenceStore, path: str, query: str) -> tuple[int, str, str]:
    match = re.fullmatch(r"/runs/([a-f0-9]{32})", path)
    if match:
        return 200, "text/html", run_page(store.get(match[1]), **_query(query, {"tool", "story"}))
    if path == "/compare":
        values = _query(query, {"left", "right"})
        if set(values) != {"left", "right"}:
            raise ValueError("Two run IDs are required")
        return (
            200,
            "text/html",
            comparison_page(store.get(values["left"]), store.get(values["right"])),
        )
    raise EvidenceNotFoundError("Route not found")


def create_server(store: EvidenceStore, *, port: int = 8081) -> HTTPServer:
    """Bind only the IPv4 loopback; deployment exposure is a later platform decision."""
    return HTTPServer(("127.0.0.1", port), _handler(store))


def _handler(store: EvidenceStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def do_GET(self) -> None:
            if not self._local_host():
                self._respond(403, "text/html", page("Forbidden", "<p>Local host required.</p>"))
                return
            try:
                status, media, content = _route(store, self.path)
            except EvidenceNotFoundError:
                status, media, content = (
                    404,
                    "text/html",
                    page("Not found", "<p>No such record.</p>"),
                )
            except ValueError:
                status, media, content = (
                    400,
                    "text/html",
                    page("Invalid request", "<p>Check filters.</p>"),
                )
            except sqlite3.Error, OSError:
                status, media, content = (
                    503,
                    "text/html",
                    page("Evidence unavailable", "<p>Check the local evidence store.</p>"),
                )
            self._respond(status, media, content)

        def _local_host(self) -> bool:
            hosts = self.headers.get_all("Host", [])
            port = cast("HTTPServer", self.server).server_port
            return len(hosts) == 1 and hosts[0] in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def do_POST(self) -> None:
            self._respond(
                405, "text/html", page("Read-only", "<p>Run controls are not enabled.</p>")
            )

        def _respond(self, status: int, media: str, content: str) -> None:
            raw = content.encode()
            self.send_response(status)
            self.send_header("Content-Type", media + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                "frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            # The default logs raw request paths, which may contain private filter text.
            return

    return Handler
