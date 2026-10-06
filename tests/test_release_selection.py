import asyncio
import json
import logging
import time
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import httpx2
from mcp.client import Client

from fakes import release, terminology_row
from nci_si_mcp.bounds import Budget
from nci_si_mcp.evs import EVSClient, EVSReleaseNotFoundError
from nci_si_mcp.registry import invoke
from nci_si_mcp.release_selection import SessionRelease, session_scope
from nci_si_mcp.server import create_mcp
from test_evs_client import FakeResponse
from test_server import ServerFixture


def version(result):
    return result["provenance"]["release"]["identifier"]


class ReleaseSelectionTest(ServerFixture):
    def setUp(self):
        super().setUp()
        self.evs.concepts = deepcopy(self.evs.concepts)

    def get(self, **arguments):
        return invoke(self.context, "get_concept", terminology="ncit", code="C3262", **arguments)

    def move(self):
        self.evs.release = release("26.07d")
        self.evs.concepts["C3262"]["version"] = "26.07d"

    def test_without_a_session_each_call_resolves_the_current_release(self):
        first = self.get()
        self.move()
        second = self.get(release=None)
        self.assertEqual((version(first), version(second)), ("26.06e", "26.07d"))
        self.assertEqual(sum(c[0] == "get_terminologies" for c in self.evs.calls), 2)

    def test_only_ncit_may_omit_release_and_invalid_arguments_do_not_discover(self):
        for arguments, parameter in (
            ({"terminology": "other", "code": "X"}, "release"),
            ({"terminology": "ncit", "code": "bad"}, "code"),
            ({"terminology": "ncit", "code": "C3262", "release": ""}, "release"),
            ({"terminology": "ncit", "code": "C3262", "release": "../bad"}, "release"),
        ):
            with self.subTest(arguments=arguments):
                result = invoke(self.context, "get_concept", **arguments)
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(result["error"]["details"]["parameter"], parameter)
        self.assertEqual(self.evs.calls, [])

    def test_failed_discovery_can_retry_but_a_content_failure_keeps_the_pin(self):
        state = SessionRelease()
        self.evs.rows = []
        with session_scope(state):
            self.assertEqual(self.get()["error"]["code"], "release_not_available")
            self.evs.rows = [terminology_row()]
            self.evs.concepts["C3262"]["version"] = "wrong"
            self.assertEqual(self.get()["error"]["code"], "release_mismatch")
            self.evs.rows = [terminology_row("26.07d")]
            self.evs.concepts["C3262"]["version"] = "26.06e"
            self.assertEqual(version(self.get()), "26.06e")
        self.assertEqual(sum(c[0] == "get_terminologies" for c in self.evs.calls), 2)

    def test_a_withdrawn_session_pin_fails_without_discovering_another(self):
        with session_scope(SessionRelease()):
            self.assertEqual(version(self.get()), "26.06e")
            self.move()
            with patch.object(
                self.evs, "get_concept", side_effect=EVSReleaseNotFoundError("withdrawn")
            ):
                result = self.get()
        self.assertEqual(result["error"]["code"], "release_not_available")
        self.assertEqual(result["error"]["details"]["requested"], "26.06e")
        self.assertIn("start a new session or name a release", result["error"]["message"].lower())
        self.assertEqual(sum(c[0] == "get_terminologies" for c in self.evs.calls), 1)

    def test_explicit_override_never_seeds_or_changes_the_implicit_pin(self):
        with session_scope(SessionRelease()):
            self.assertEqual(version(self.get(release="26.06e")), "26.06e")
            self.move()
            self.assertEqual(version(self.get()), "26.07d")
            self.evs.concepts["C3262"]["version"] = "26.06e"
            self.assertEqual(version(self.get(release="26.06e")), "26.06e")
            self.evs.concepts["C3262"]["version"] = "26.07d"
            self.assertEqual(version(self.get()), "26.07d")
        self.assertEqual(sum(c[0] == "get_terminologies" for c in self.evs.calls), 1)

    def test_weekly_configuration_drives_implicit_discovery(self):
        self.context.settings = replace(self.settings, release_channel="weekly")
        self.evs.release = release(channel="weekly")
        self.assertEqual(version(self.get()), "26.06e")
        self.assertIn(("get_terminologies", "ncit", (True, "weekly")), self.evs.calls)

    def test_completion_audit_names_all_three_selection_sources(self):
        logging.disable(logging.NOTSET)
        with (
            self.assertLogs("nci_si_mcp.audit", level="INFO") as captured,
            session_scope(SessionRelease()),
        ):
            self.get()
            self.get()
            self.get(release="26.06e")
        records = [r.structured["release"] for r in captured.records]
        self.assertEqual(
            [r["selection"] for r in records],
            ["freshly-resolved", "session-held", "explicit"],
        )
        self.assertEqual([r["selected"]["version"] for r in records], ["26.06e"] * 3)

    def test_real_mcp_keeps_the_pin_and_separates_sessions_on_one_server(self):
        async def interact():
            server = create_mcp(context=self.context)
            args = {"terminology": "ncit", "code": "C3262"}
            async with Client(server) as first:
                one = await first.call_tool("get_concept", args)
                self.move()
                async with Client(server) as second:
                    two = await second.call_tool("get_concept", args)
                self.evs.concepts["C3262"]["version"] = "26.06e"
                three = await first.call_tool("get_concept", args | {"release": None})
            return [version(r.structured_content) for r in (one, two, three)]

        self.assertEqual(asyncio.run(interact()), ["26.06e", "26.07d", "26.06e"])

    def test_real_mcp_caches_only_the_release_named_in_the_arguments(self):
        async def interact(client):
            args = {"terminology": "ncit", "code": "C3262"}
            implicit = await client.call_tool("get_concept", args)
            explicit = await client.call_tool("get_concept", args | {"release": "26.06e"})
            held = await client.call_tool("get_concept", args | {"release": None})
            return implicit, explicit, held

        results = self.session(interact)
        self.assertEqual([version(r.structured_content) for r in results], ["26.06e"] * 3)
        self.assertEqual(
            [(r.meta["ttlMs"], r.meta["cacheScope"]) for r in results],
            [(0, "private"), (86_400_000, "public"), (0, "private")],
        )

    def test_an_explicit_resource_does_not_seed_the_implicit_session(self):
        async def interact(client):
            addressed = await client.read_resource("ncit://concept/26.06e/C3262")
            self.move()
            implicit = await client.call_tool(
                "get_concept", {"terminology": "ncit", "code": "C3262"}
            )
            return json.loads(addressed.contents[0].text), implicit.structured_content

        addressed, implicit = self.session(interact)
        self.assertEqual((version(addressed), version(implicit)), ("26.06e", "26.07d"))

    def test_simultaneous_first_calls_discover_once_and_share_the_release(self):
        discover = self.evs.get_terminologies

        def slow_discovery(*args, **kwargs):
            # Real metadata I/O releases the GIL, letting the other first calls arrive.
            time.sleep(0.05)
            return discover(*args, **kwargs)

        async def interact(client):
            args = {"terminology": "ncit", "code": "C3262"}
            return await asyncio.gather(*(client.call_tool("get_concept", args) for _ in range(8)))

        with patch.object(self.evs, "get_terminologies", side_effect=slow_discovery):
            results = self.session(interact)
        self.assertEqual([version(r.structured_content) for r in results], ["26.06e"] * 8)
        self.assertEqual(sum(c[0] == "get_terminologies" for c in self.evs.calls), 1)

    def test_empty_batch_still_names_the_resolved_release(self):
        result = invoke(self.context, "get_concepts", terminology="ncit", codes=[])
        self.assertEqual(result["concepts"], [])
        self.assertEqual(version(result), "26.06e")
        self.assertEqual([c[0] for c in self.evs.calls], ["get_terminologies"])

    def test_nested_content_reads_reuse_the_one_call_resolution(self):
        result = invoke(self.context, "resolve_retired_code", terminology="ncit", code="C3262")
        self.assertTrue(result["active"])
        self.assertEqual(result["replacements"], [])
        self.assertEqual(version(result), "26.06e")
        self.assertEqual(sum(c[0] == "get_terminologies" for c in self.evs.calls), 1)

    def test_implicit_discovery_spends_the_same_outbound_budget_as_the_catalogue(self):
        self.context.evs = EVSClient("https://evs.invalid", max_attempts=1)
        with (
            patch("nci_si_mcp.content.Budget", return_value=Budget(requests=1)),
            patch(
                "nci_si_mcp.http_client._open",
                side_effect=[
                    FakeResponse(json.dumps([terminology_row()]).encode()),
                    FakeResponse(b"[]"),
                ],
            ) as opened,
        ):
            result = invoke(self.context, "list_relationships", terminology="ncit")
        self.assertEqual(result["error"]["code"], "bound_exceeded")
        self.assertEqual(
            result["error"]["details"], {"bound": "requests", "limit": 1, "reached": 1}
        )
        self.assertEqual(opened.call_count, 1)

    def test_conflicting_discovery_metadata_never_reaches_content(self):
        for changes in (
            {"terminology": "other"},
            {"latest": False},
            {"tags": {}},
            {"version": 7},
            {"version": "../bad"},
            {"terminologyVersion": "other_v1"},
            {"terminologyVersion": ""},
        ):
            with (
                self.subTest(changes=changes),
                patch.object(
                    self.evs, "get_terminologies", return_value=[terminology_row() | changes]
                ),
            ):
                self.assertEqual(self.get()["error"]["code"], "release_not_available")
        self.assertEqual(self.evs.calls, [])

    def test_cursor_continuation_binds_effective_release_not_omission(self):
        args = {"terminology": "ncit", "query": "Neoplasm", "limit": 1, "mode": "hybrid"}
        self.evs.concepts["C4741"]["name"] = "Neoplasm two"
        invoke(self.context, "index_codes", ["C3262", "C4741"])
        for first_pin, next_pin in ((None, "26.06e"), ("26.06e", None)):
            with self.subTest(first=first_pin), session_scope(SessionRelease()):
                first = invoke(self.context, "search_concepts", **args, release=first_pin)
                continued = invoke(
                    self.context,
                    "search_concepts",
                    **args,
                    release=next_pin,
                    cursor=first["nextCursor"],
                )
                self.assertNotIn("error", continued)
                self.assertNotEqual(first["results"], continued["results"])
                changed = invoke(
                    self.context,
                    "search_concepts",
                    **args,
                    release="26.07d",
                    cursor=first["nextCursor"],
                )
                self.assertEqual(changed["error"]["code"], "invalid_request")

    def test_real_stateless_http_resolves_each_call(self):
        self.assertEqual(asyncio.run(self.http_sequence(True)), ["26.06e", "26.07d"])

    def test_real_stateful_http_reuses_the_validated_session(self):
        self.assertEqual(asyncio.run(self.http_sequence(False)), ["26.06e", "26.07d", "26.06e"])

    async def http_sequence(self, stateless):
        app = create_mcp(context=self.context).streamable_http_app(
            stateless_http=stateless,
            json_response=True,
        )
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app), base_url="http://127.0.0.1:8000"
            ) as client,
        ):
            headers = await initialize_http(client)
            first = await http_concept(client, headers)
            versions = [version(first)]
            self.move()
            if not stateless:
                other = await http_concept(client, await initialize_http(client))
                versions.append(version(other))
                self.evs.concepts["C3262"]["version"] = "26.06e"
            second = await http_concept(client, headers)
            return [*versions, version(second)]


async def initialize_http(client):
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
    }
    response = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "release-test", "version": "1"},
            },
        },
    )
    response.raise_for_status()
    if session_id := response.headers.get("mcp-session-id"):
        headers["Mcp-Session-Id"] = session_id
    initialized = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        },
    )
    initialized.raise_for_status()
    return headers


async def http_concept(client, headers):
    response = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_concept",
                "arguments": {"terminology": "ncit", "code": "C3262"},
            },
        },
    )
    response.raise_for_status()
    return response.json()["result"]["structuredContent"]
