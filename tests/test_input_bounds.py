"""Caller input bounds are visible and refused before upstream work."""

from unittest.mock import patch

from test_server import ServerFixture


class InputBoundsTest(ServerFixture):
    def test_oversized_search_is_invalid_before_any_evs_request(self):
        with patch.object(self.evs, "search_concepts", create=True, return_value=(0, [])):
            failed, result = self.call("search_concepts", query="a" * 1001, mode="lexical")
        self.assertTrue(failed)
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(self.evs.calls, [])
