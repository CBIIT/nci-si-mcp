"""Fixed local admin ingress for engines that cannot publish an internal-network port."""

from __future__ import annotations

import json
import select
import signal
import socket
import socketserver
import sys
import time
from contextlib import suppress
from typing import Any


def transfer(incoming: socket.socket, outgoing: socket.socket) -> None:
    streams = (incoming, outgoing)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        readable, _, _ = select.select(streams, (), (), 5)
        if not readable:
            return
        for source in readable:
            data = source.recv(65536)
            if not data:
                return
            destination = outgoing if source is incoming else incoming
            destination.sendall(data)


class Relay(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(5)
        # Never derive a destination from request bytes, headers or environment variables.
        with socket.create_connection(("administration", 8081), timeout=5) as target:
            transfer(self.request, target)


class Server(socketserver.TCPServer):
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        del request, client_address
        print(
            json.dumps(
                {"event": "admin_relay_failed", "errorType": type(sys.exception()).__name__}
            ),
            flush=True,
        )


def stop(_signal: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def main() -> None:
    signal.signal(signal.SIGTERM, stop)
    with Server(("0.0.0.0", 8081), Relay) as server, suppress(KeyboardInterrupt):  # noqa: S104
        server.serve_forever()


if __name__ == "__main__":
    main()
