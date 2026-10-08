"""Small public static-docs host with independent health and bounded request lifetimes."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from contextlib import suppress
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from os import PathLike
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit


class _StaticHandler(SimpleHTTPRequestHandler):
    timeout = 5

    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/health":
            raw = b'{"status":"ok","service":"documentation"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        super().do_GET()

    def send_head(self) -> BytesIO | BinaryIO | None:
        root = Path(self.directory).resolve()
        target = Path(self.translate_path(self.path)).resolve()
        if target.is_dir():
            target = self._index_target(target)
        if not target.is_relative_to(root):
            self.send_error(404)
            return None
        return super().send_head()

    @staticmethod
    def _index_target(directory: Path) -> Path:
        # Match the stdlib's implicit index selection before checking containment.
        for name in ("index.html", "index.htm"):
            candidate = directory / name
            if candidate.is_file():
                return candidate.resolve()
        return directory

    def list_directory(self, path: str | PathLike[str]) -> None:
        self.send_error(404)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Cache-Control", "no-cache")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self' data:; worker-src 'self' blob:; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        super().end_headers()

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        print(json.dumps({"event": "documentation_request", "status": code}), flush=True)

    def log_message(self, format: str, *args: Any) -> None:
        # The stdlib default includes request targets, including search/query text.
        return


class _StaticServer(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        del request, client_address  # Diagnostics omit request and client data.
        print(
            json.dumps(
                {
                    "event": "documentation_request_failed",
                    "errorType": type(sys.exception()).__name__,
                }
            ),
            flush=True,
        )


def create_server(site: Path, *, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    if not (site / "index.html").is_file():
        raise ValueError("Documentation site needs a built index.html")
    return _StaticServer((host, port), partial(_StaticHandler, directory=str(site.resolve())))


def _stop(_signal: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    # Container publishing controls exposure; host previews default to loopback.
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")  # noqa: S104
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    with (
        create_server(args.directory, host=args.host, port=args.port) as server,
        suppress(KeyboardInterrupt),
    ):
        server.serve_forever()


if __name__ == "__main__":
    main()
