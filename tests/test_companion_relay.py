"""The local ingress relay transfers bytes only to its fixed administration service."""

import socket
import threading
import unittest
from unittest.mock import patch

from scripts.companion_relay import transfer


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
