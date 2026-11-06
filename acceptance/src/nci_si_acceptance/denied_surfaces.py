"""An owned write-only local refusal endpoint; no upstream request is forwarded."""

from socketserver import BaseRequestHandler, TCPServer
from threading import Event, Thread

BODY = b"surface disabled by run policy"
RESPONSE = (
    b"HTTP/1.0 403 Forbidden\r\nContent-Length: "
    + str(len(BODY)).encode()
    + b"\r\nConnection: close\r\n\r\n"
    + BODY
)
# A handler only writes this tiny fixed response. It never waits for client input.
WRITE_SECONDS = 1
POLL_SECONDS = 0.1
JOIN_SECONDS = 3


class _Refusal(BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(WRITE_SECONDS)
        self.request.sendall(RESPONSE)


class _Server(TCPServer):
    failed = False

    def handle_error(self, request: object, client_address: object) -> None:
        # socketserver's default prints exception/request details; do not echo input.
        self.failed = True


class DeniedSurfaces:
    """One listener per owning worker, enclosing prepare and all dependent servers."""

    def __init__(self) -> None:
        self._server = _Server(("127.0.0.1", 0), _Refusal)
        self._server.timeout = POLL_SECONDS
        self._stop = Event()
        self.thread = Thread(target=self._serve, name="acceptance-denied-surfaces")

    def _serve(self) -> None:
        try:
            while not self._stop.is_set():
                self._server.handle_request()
        except OSError, ValueError:
            if not self._stop.is_set():
                self._server.failed = True
        finally:
            self._server.server_close()

    def __enter__(self) -> str:
        try:
            self.thread.start()
        except BaseException:
            self._server.server_close()
            raise
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __exit__(self, *_exception: object) -> None:
        self._stop.set()
        try:
            self._server.server_close()
        finally:
            self.thread.join(JOIN_SECONDS)
        if self.thread.is_alive() or self._server.failed:
            raise RuntimeError("owned refusal listener did not finish cleanly")
