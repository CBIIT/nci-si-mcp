"""The local ingress relay transfers bytes only to its fixed administration service."""

import json
import socket
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from scripts.companion_relay import Relay, Server, transfer


class CompanionRelayTest(unittest.TestCase):
    def sockets(self):
        streams = socket.socketpair() + socket.socketpair()
        for stream in streams:
            self.addCleanup(stream.close)
            stream.settimeout(0.1)
        return streams

    def test_idle_connection_returns_without_forwarding_or_waiting_again(self):
        client, incoming, outgoing, target = self.sockets()
        with patch(
            "scripts.companion_relay.select.select",
            side_effect=[([], [], []), AssertionError("Idle connection waited again")],
        ):
            transfer(incoming, outgoing)
        client.sendall(b"late request")
        with self.assertRaises(TimeoutError):
            target.recv(1024)

    def test_continuous_activity_cannot_extend_the_absolute_connection_deadline(self):
        client, incoming, outgoing, target = self.sockets()
        client.sendall(b"first request")
        with (
            patch("scripts.companion_relay.time.monotonic", side_effect=[0, 1, 16]),
            patch(
                "scripts.companion_relay.select.select",
                side_effect=[([incoming], [], []), AssertionError("Deadline was ignored")],
            ),
        ):
            transfer(incoming, outgoing)
        self.assertEqual(target.recv(1024), b"first request")
        client.sendall(b"after deadline")
        with self.assertRaises(TimeoutError):
            target.recv(1024)

    def test_bidirectional_bytes_are_preserved_and_eof_stops_owned_connection(self):
        client, incoming = socket.socketpair()
        outgoing, target = socket.socketpair()
        for stream in (client, incoming, outgoing, target):
            self.addCleanup(stream.close)
            stream.settimeout(2)
        thread = threading.Thread(target=transfer, args=(incoming, outgoing), daemon=True)
        thread.start()
        client.sendall(b"GET /health HTTP/1.0\r\nHost: localhost:8081\r\n\r\n")
        self.assertIn(b"Host: localhost:8081", target.recv(1024))
        target.sendall(b"HTTP/1.0 200 OK\r\n\r\nhealthy")
        self.assertIn(b"healthy", client.recv(1024))
        target.close()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_connection_failure_is_private_and_next_request_is_served(self):
        output, errors = StringIO(), StringIO()
        outgoing, target = socket.socketpair()
        self.addCleanup(outgoing.close)
        self.addCleanup(target.close)
        target.sendall(b"HTTP/1.0 200 OK\r\n\r\nhealthy")
        with Server(("127.0.0.1", 0), Relay) as server:
            server.timeout = 2

            def serve():
                server.handle_request()
                server.handle_request()

            with (
                redirect_stdout(output),
                redirect_stderr(errors),
                patch(
                    "scripts.companion_relay.socket.create_connection",
                    side_effect=[ConnectionRefusedError("PRIVATE-CANARY"), outgoing],
                ),
            ):
                thread = threading.Thread(target=serve, daemon=True)
                thread.start()
                self.addCleanup(thread.join, 3)
                for expected in (b"", b"HTTP/1.0 200 OK\r\n\r\nhealthy"):
                    with socket.socket() as client:
                        client.settimeout(3)
                        client.connect(server.server_address)
                        with client.makefile("rb") as response:
                            self.assertEqual(response.read(len(expected) or 1), expected)
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(
            json.loads(output.getvalue()),
            {"event": "admin_relay_failed", "errorType": "ConnectionRefusedError"},
        )
        self.assertEqual(errors.getvalue(), "")
