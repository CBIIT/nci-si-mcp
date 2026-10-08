"""Loopback evidence companion; UAT/PROD platform integration is not enabled."""

from __future__ import annotations

import re
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import cast
from urllib.parse import SplitResult, parse_qs, urlsplit

from scripts.portal_actions import form_length, perform, same_origin
from scripts.portal_configuration import ConfigurationConflictError, LocalConfiguration
from scripts.portal_configuration_views import configuration_page, propose
from scripts.portal_help import help_page
from scripts.portal_job_views import job_page, jobs_page
from scripts.portal_jobs import JobConflictError, JobController, QueueFullError
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


def _target(target: str) -> SplitResult:
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or len(target) > MAX_TARGET:
        raise ValueError("Invalid local request target")
    return parts


def _route(
    store: EvidenceStore,
    target: str,
    jobs: JobController | None = None,
    configuration: LocalConfiguration | None = None,
) -> tuple[int, str, str]:
    parts = _target(target)
    if parts.path == "/health":
        return 200, "application/json", '{"status":"ok","deployment":"local-only"}'
    if parts.path == "/":
        return 200, "text/html", history_page(store.history())
    if parts.path == "/help":
        return 200, "text/html", help_page()
    if parts.path == "/configuration":
        _query(parts.query, set())
        status, content = configuration_page(configuration or LocalConfiguration(None))
        return status, "text/html", content
    if parts.path.startswith("/jobs"):
        return _job_route(store, jobs, parts.path, parts.query)
    return _result_route(store, parts.path, parts.query)


def _job_route(
    store: EvidenceStore, jobs: JobController | None, path: str, query: str
) -> tuple[int, str, str]:
    if query:
        raise ValueError("Job routes do not take query parameters")
    if jobs is None:
        return (
            503,
            "text/html",
            page("Run controls unavailable", "<p>This listener is read-only.</p>"),
        )
    if path == "/jobs":
        return 200, "text/html", jobs_page(jobs.history(), jobs.profiles)
    match = re.fullmatch(r"/jobs/([a-f0-9]{32})", path)
    if match is None:
        raise EvidenceNotFoundError("No such job route")
    try:
        row = jobs.get(match[1])
    except KeyError:
        raise EvidenceNotFoundError("No such job") from None
    return 200, "text/html", job_page(row, has_evidence=_has_evidence(store, match[1]))


def _has_evidence(store: EvidenceStore, run_id: str) -> bool:
    try:
        store.get(run_id)
    except EvidenceNotFoundError:
        return False
    return True


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


def create_server(
    store: EvidenceStore,
    *,
    port: int = 8081,
    jobs: JobController | None = None,
    configuration: LocalConfiguration | None = None,
) -> HTTPServer:
    """Bind only the IPv4 loopback; deployment exposure is a later platform decision."""
    return HTTPServer(("127.0.0.1", port), _handler(store, jobs, configuration))


def _handler(
    store: EvidenceStore, jobs: JobController | None, configuration: LocalConfiguration | None
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def do_GET(self) -> None:
            if not self._local_host():
                self._respond(403, "text/html", page("Forbidden", "<p>Local host required.</p>"))
                return
            try:
                status, media, content = _route(store, self.path, jobs, configuration)
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
            if self.path == "/configuration/proposals":
                self._propose()
                return
            if jobs is not None:
                self._mutate(jobs)
                return
            self._respond(
                405, "text/html", page("Read-only", "<p>Run controls are not enabled.</p>")
            )

        def _propose(self) -> None:
            port = cast("HTTPServer", self.server).server_port
            if not same_origin(self.headers, port):
                self._respond(
                    403, "text/html", page("Forbidden", "<p>Same-origin local form required.</p>")
                )
                return
            try:
                content = propose(configuration or LocalConfiguration(None), self._form_body())
                status = 200
            except (ValueError, OSError) as error:
                status = _action_status(error)
                content = page(
                    "Proposal not accepted",
                    "<p>Check the selected snapshot, setting and value. The target or revision "
                    'may have changed; <a href="/configuration">reload configuration</a> '
                    "before retrying. Nothing was applied.</p>",
                )
            self._respond(status, "text/html", content)

        def _form_body(self) -> bytes:
            length = form_length(self.headers)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete form")
            return raw

        def _mutate(self, controller: JobController) -> None:
            port = cast("HTTPServer", self.server).server_port
            if not same_origin(self.headers, port):
                self._respond(
                    403, "text/html", page("Forbidden", "<p>Same-origin local form required.</p>")
                )
                return
            try:
                raw = self._form_body()
                location = perform(controller, self.path, raw)
            except (ValueError, KeyError, OSError, QueueFullError) as error:
                self._respond(
                    _action_status(error),
                    "text/html",
                    page(
                        "Run request not accepted",
                        "<p>Check the profile, queue and local storage. "
                        '<a href="/jobs">Return to run controls</a>.</p>',
                    ),
                )
                return
            self._respond(
                303,
                "text/html",
                page("Run status", "<p>Opening the recorded run.</p>"),
                location=location,
            )

        def _respond(
            self, status: int, media: str, content: str, *, location: str | None = None
        ) -> None:
            raw = content.encode()
            self.send_response(status)
            self.send_header("Content-Type", media + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if location is not None:
                self.send_header("Location", location)
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


def _action_status(error: Exception) -> int:
    codes = (
        (QueueFullError, 429),
        (JobConflictError, 409),
        (ConfigurationConflictError, 409),
        (KeyError, 404),
        (OSError, 503),
    )
    return next((code for kind, code in codes if isinstance(error, kind)), 400)
