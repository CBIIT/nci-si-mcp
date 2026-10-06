"""Invalid continuation inputs never trigger implicit release discovery."""

import base64
import json

from nci_si_mcp import cursor
from nci_si_mcp.registry import invoke
from test_server import ServerFixture


def token(record):
    return base64.urlsafe_b64encode(json.dumps(record).encode()).decode()


class CursorValidationTest(ServerFixture):
    def arguments(self, tool):
        if tool == "search_concepts":
            return {
                "terminology": "ncit",
                "query": "Q",
                "mode": "lexical",
                "limit": 10,
                "retired": "include",
            }
        return {
            "terminology": "ncit",
            "code": "C3262",
            "direction": "child",
            "depth": 1,
            "limit": 10,
        }

    def encoded_arguments(self, tool):
        arguments = self.arguments(tool) | {"release": "26.06e"}
        return arguments | {"tool": tool} if tool == "search_concepts" else arguments

    def test_malformed_cursor_parts_fail_before_discovery(self):
        for tool in ("search_concepts", "get_concept_hierarchy"):
            record = {"v": 1, "arguments": self.encoded_arguments(tool), "offset": 1}
            for invalid in (
                "bad",
                token(record | {"v": 2}),
                token(record | {"offset": 0}),
                token(record | {"offset": True}),
                token(record | {"build": "a" * 32}),
                token(record | {"arguments": record["arguments"] | {"limit": 9}}),
                token(record | {"arguments": record["arguments"] | {"release": None}}),
            ):
                with self.subTest(tool=tool, cursor=invalid):
                    self.evs.calls.clear()
                    result = invoke(self.context, tool, **self.arguments(tool), cursor=invalid)
                    self.assertEqual(result["error"]["code"], "invalid_request")
                    self.assertEqual(result["error"]["details"]["parameter"], "cursor")
                    self.assertEqual(self.evs.calls, [])

    def test_indexed_cursor_requires_valid_build_before_discovery(self):
        arguments = self.encoded_arguments("search_concepts") | {"mode": "semantic"}
        for build in (None, "bad"):
            with self.subTest(build=build):
                self.evs.calls.clear()
                result = invoke(
                    self.context,
                    "search_concepts",
                    **(self.arguments("search_concepts") | {"mode": "semantic"}),
                    cursor=cursor.encode(arguments, 1, build),
                )
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(self.evs.calls, [])

    def test_cursor_release_never_selects_the_effective_release(self):
        for tool in ("search_concepts", "get_concept_hierarchy"):
            with self.subTest(tool=tool):
                self.evs.calls.clear()
                arguments = self.encoded_arguments(tool) | {"release": "99.01a"}
                result = invoke(
                    self.context, tool, **self.arguments(tool), cursor=cursor.encode(arguments, 1)
                )
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(self.evs.calls, [("get_terminologies", "ncit", (True, "monthly"))])

    def test_explicit_release_mismatch_never_requests_content(self):
        for tool in ("search_concepts", "get_concept_hierarchy"):
            with self.subTest(tool=tool):
                self.evs.calls.clear()
                result = invoke(
                    self.context,
                    tool,
                    **self.arguments(tool),
                    release="99.01a",
                    cursor=cursor.encode(self.encoded_arguments(tool), 1),
                )
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(self.evs.calls, [])
