import json
from unittest.mock import patch

from nci_si_mcp.errors import IndexStorageError
from nci_si_mcp.evs import EVSUnavailableError
from test_server import ServerFixture


@patch("nci_si_mcp.server.configure_logging")
class CachingTest(ServerFixture):
    def test_lists_and_discovery_advertise_long_public_result_fields(self, _):
        async def listed(client):
            return [
                await client.list_tools(),
                await client.list_prompts(),
                await client.list_resources(),
                await client.list_resource_templates(),
                client.session.discover_result,
            ]

        for result in self.session(listed):
            with self.subTest(method=type(result).__name__):
                wire = result.model_dump(by_alias=True)
                self.assertEqual((wire["ttlMs"], wire["cacheScope"]), (86_400_000, "public"))
                self.assertNotIn("ttlMs", wire.get("_meta") or {})
                self.assertNotIn("cacheScope", wire.get("_meta") or {})

    def test_governed_tool_content_has_long_public_protocol_metadata(self, _):
        self.service.index_codes(["C3262"])
        calls = [
            ("ncit_lookup", {"code": "C3262"}),
            ("ncit_search", {"query": "Neoplasm"}),
            ("ncit_traverse", {"start_codes": ["C3262"], "max_depth": 0}),
        ]
        for name, arguments in calls:
            with self.subTest(tool=name):
                result = self.session(
                    lambda client, name=name, arguments=arguments: client.call_tool(name, arguments)
                )
                self.assertFalse(result.is_error)
                self.assertEqual(
                    (result.meta["ttlMs"], result.meta["cacheScope"]), (86_400_000, "public")
                )
                self.assertNotIn("ttlMs", result.model_dump(by_alias=True))
                self.assertNotIn("_meta", json.loads(result.content[0].text))
                self.assertEqual(
                    result.meta["io.modelcontextprotocol/serverInfo"]["name"], "nci-si-mcp"
                )

    def test_empty_search_is_cacheable_governed_content(self, _):
        self.service.index_codes(["C3262"])
        result = self.session(
            lambda client: client.call_tool("ncit_search", {"query": "zzzz", "mode": "bm25"})
        )
        self.assertEqual(json.loads(result.content[0].text)["hits"], [])
        self.assertEqual((result.meta["ttlMs"], result.meta["cacheScope"]), (86_400_000, "public"))

    def test_status_tools_are_public_but_not_cached(self, _):
        for name in ["ncit_release_info", "cadsr_status"]:
            with self.subTest(tool=name):
                result = self.session(lambda client, name=name: client.call_tool(name))
                self.assertFalse(result.is_error)
                self.assertEqual((result.meta["ttlMs"], result.meta["cacheScope"]), (0, "public"))

    def test_failed_release_discovery_is_still_not_cached(self, _):
        self.evs.errors = {"get_terminologies": EVSUnavailableError("down")}
        result = self.session(lambda client: client.call_tool("ncit_release_info"))
        self.assertIn("error", json.loads(result.content[0].text)["selected_monthly_release"])
        self.assertEqual((result.meta["ttlMs"], result.meta["cacheScope"]), (0, "public"))

    def test_tool_errors_are_private_and_preserve_correlation(self, _):
        result = self.session(
            lambda client: client.call_tool(
                "ncit_lookup", {"code": "C999"}, meta={"correlationId": "cache-error"}
            )
        )
        self.assertTrue(result.is_error)
        self.assertEqual((result.meta["ttlMs"], result.meta["cacheScope"]), (0, "private"))
        self.assertEqual(result.structured_content["error"]["correlationId"], "cache-error")

    def test_schema_rejections_are_private_and_not_cached(self, _):
        result = self.session(lambda client: client.call_tool("ncit_lookup", {}))
        self.assertTrue(result.is_error)
        self.assertEqual((result.meta["ttlMs"], result.meta["cacheScope"]), (0, "private"))

    def test_error_privacy_overrides_the_resolvers_public_policy(self, _):
        with patch.object(
            self.service.index, "get_active_manifest", side_effect=IndexStorageError("unreadable")
        ):
            result = self.session(lambda client: client.call_tool("ncit_release_info"))
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["error"]["code"], "internal_error")
        self.assertEqual((result.meta["ttlMs"], result.meta["cacheScope"]), (0, "private"))

    def test_resource_content_and_moving_aliases_have_distinct_result_hints(self, _):
        self.service.index_codes(["C3262"])
        cases = {
            "nci-si://concept/ncit/C3262": 86_400_000,
            "nci-si://release/ncit/26.06e": 86_400_000,
            "nci-si://index/ncit/26.06e/manifest": 86_400_000,
            "nci-si://index/ncit/active/manifest": 0,
            "nci-si://release/ncit/monthly": 0,
            "nci-si://release/ncit/latest": 0,
            "nci-si://release/ncit/monthly-latest": 0,
        }
        for uri, ttl in cases.items():
            with self.subTest(uri=uri):
                result = self.session(lambda client, uri=uri: client.read_resource(uri))
                wire = result.model_dump(by_alias=True)
                self.assertEqual((wire["ttlMs"], wire["cacheScope"]), (ttl, "public"))
                self.assertNotIn("ttlMs", wire.get("_meta") or {})
                self.assertNotIn("cacheScope", wire.get("_meta") or {})
                self.assertNotIn("ttlMs", json.loads(result.contents[0].text))

    def test_absent_index_is_a_status_result_without_a_release_to_cache(self, _):
        result = self.session(
            lambda client: client.read_resource("nci-si://index/ncit/26.06e/manifest")
        )
        self.assertEqual(json.loads(result.contents[0].text), {"active_index": None})
        self.assertEqual((result.ttl_ms, result.cache_scope), (0, "public"))
