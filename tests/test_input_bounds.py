"""Caller input bounds are visible and refused before upstream work."""

import logging
from unittest.mock import patch

from test_server import ServerFixture


class InputBoundsTest(ServerFixture):
    def test_oversized_search_is_invalid_before_any_evs_request(self):
        with patch.object(self.evs, "search_concepts", create=True, return_value=(0, [])):
            failed, result = self.call("search_concepts", query="a" * 1001, mode="lexical")
        self.assertTrue(failed)
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(self.evs.calls, [])

    def test_caller_supplied_unsafe_correlation_is_refused_under_a_safe_identifier(self):
        async def request(client):
            return await client.call_tool(
                "get_concept",
                {"terminology": "ncit", "code": "C3262"},
                meta={"correlationId": "unsafe\r\nheader"},
            )

        logging.disable(logging.NOTSET)
        with self.assertLogs("nci_si_mcp.audit", level="INFO") as captured:
            result = self.session(request)
        self.assertTrue(result.is_error)
        error = result.structured_content["error"]
        self.assertEqual(error["code"], "invalid_request")
        self.assertEqual(error["details"]["parameter"], "_meta.correlationId")
        self.assertRegex(error["correlationId"], r"^[A-Za-z0-9._:-]{1,128}$")
        self.assertNotIn("unsafe", str([row.structured for row in captured.records]))
        self.assertEqual(self.evs.calls, [])
