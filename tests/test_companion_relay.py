"""The local ingress relay transfers bytes only to its fixed administration service."""

import socket
import threading
import unittest

from scripts.companion_relay import transfer


class CompanionRelayTest(unittest.TestCase):
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
