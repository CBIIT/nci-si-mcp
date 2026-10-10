"""Startup overrides preserve the same protocol and configuration protections."""

import asyncio
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from mcp.client import Client
from mcp.server.mcpserver import MCPServer

from nci_si_mcp import container_entry
from nci_si_mcp.config import Settings
from nci_si_mcp.transport import create_http_app
from test_server import ServerFixture


class AuthorityConfigurationTest(unittest.TestCase):
    def test_programmatic_empty_allowlists_are_rejected(self):
        for field, variable in (
            ("http_allowed_hosts", "NCI_SI_HTTP_ALLOWED_HOSTS"),
            ("http_allowed_origins", "NCI_SI_HTTP_ALLOWED_ORIGINS"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, variable):
                Settings(**{field: ()})


class StartupBoundaryTest(ServerFixture):
    def test_optional_index_stdio_override_serves_the_protocol_without_an_active_build(self):
        settings = replace(self.settings, transport="stdio", http_require_index=False)
        answers = []

        async def exchange(server):
            async with Client(server) as client:
                return await client.call_tool(
                    "search_concepts",
                    {
                        "terminology": "ncit",
                        "release": "26.06e",
                        "query": "neoplasm",
                        "mode": "semantic",
                    },
                )

        def serve(server):
            answers.append(asyncio.run(exchange(server)))

        with (
            patch.object(container_entry.Settings, "from_env", return_value=settings),
            patch.object(MCPServer, "run", serve),
            patch.object(container_entry, "run_http", side_effect=AssertionError("stdio selected")),
        ):
            self.assertEqual(container_entry.main(), 0)
        (answer,) = answers
        self.assertTrue(answer.is_error)
        self.assertEqual(
            json.loads(answer.content[0].text)["error"]["code"], "capability_unavailable"
        )
        self.assertIsNone(self.context.index.get_active_manifest())
        self.assertEqual(self.context.index.list_builds(), [])

    def test_http_middleware_forwards_application_startup_and_shutdown(self):
        app = create_http_app(self.settings, context=self.context)

        async def scenario():
            incoming = asyncio.Queue()
            incoming.put_nowait({"type": "lifespan.startup"})
            incoming.put_nowait({"type": "lifespan.shutdown"})
            sent = []

            async def send(message):
                sent.append(message)

            await app({"type": "lifespan", "asgi": {"version": "3.0"}}, incoming.get, send)
            return sent

        self.assertEqual(
            asyncio.run(scenario()),
            [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}],
        )
