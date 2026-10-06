"""Content contract regressions from the Phase 2 independent mutation review."""

import base64
import json
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from fakes import concept, terminology_row
from nci_si_mcp import cursor
from nci_si_mcp.errors import InputValidationError
from nci_si_mcp.registry import invoke
from test_server import ServerFixture


def token(payload, size=None):
    text = json.dumps(payload)
    if size is not None:
        text = text.ljust(size)
    return base64.urlsafe_b64encode(text.encode()).decode()


def reposition(value, offset):
    payload = json.loads(base64.urlsafe_b64decode(value))
    return token(payload | {"offset": offset})


class CursorReviewTest(TestCase):
    def test_unknown_version_is_rejected(self):
        value = token({"v": 2, "arguments": {}, "offset": 1})
        with self.assertRaises(InputValidationError):
            cursor.decode(value, {})

    def test_maximum_length_is_inclusive_and_longer_valid_json_is_rejected(self):
        payload = {"v": 1, "arguments": {}, "offset": 1}
        boundary = token(payload, 6144)
        self.assertEqual(len(boundary), 8192)
        self.assertEqual(cursor.decode(boundary, {}).offset, 1)
        with self.assertRaises(InputValidationError):
            cursor.decode(token(payload, 6147), {})

    def test_non_alphabet_character_cannot_be_ignored(self):
        value = cursor.encode({}, 1)
        with self.assertRaises(InputValidationError):
            cursor.decode("!" + value, {})


class ContentReviewTest(ServerFixture):
    def setUp(self):
        super().setUp()
        self.evs.concepts = {
            "C1": concept("C1", "One", active=True, children=[{"code": "C2"}]),
            "C2": concept("C2", "Two", active=True),
        }

    def call(self, tool, **arguments):
        return invoke(
            self.context,
            tool,
            **({"terminology": "ncit", "release": "26.06e"} | arguments),
        )

    def search(self, **arguments):
        return self.call("search_concepts", **({"query": "One", "limit": 1} | arguments))

    def live_rows(self, total, rows):
        return patch.object(self.evs, "search_concepts", return_value=(total, rows), create=True)

    def hierarchy(self, **arguments):
        return self.call(
            "get_concept_hierarchy",
            **({"code": "C1", "direction": "child", "limit": 1} | arguments),
        )

    def retirement_metadata(self):
        return terminology_row() | {
            "metadata": {
                "retiredStatusValue": "Retired_Concept",
                "conceptStatuses": ["Retired_Concept"],
            }
        }

    def test_search_cursor_binds_release_and_terminology(self):
        with self.live_rows(2, [self.evs.concepts["C1"]]):
            first = self.search()
            for changed in ({"release": "other"}, {"terminology": "other"}):
                with self.subTest(changed=changed):
                    result = self.search(cursor=first["nextCursor"], **changed)
                    self.assertEqual(result["error"]["code"], "invalid_request")
                    self.assertEqual(result["error"]["details"]["parameter"], "cursor")

    def test_hierarchy_cursor_binds_terminology(self):
        self.evs.concepts["C1"]["children"].append({"code": "C3"})
        self.evs.concepts["C3"] = concept("C3", active=True)
        first = self.hierarchy()
        for raw in self.evs.concepts.values():
            raw["terminology"] = "other"
        result = self.hierarchy(cursor=first["nextCursor"], terminology="other")
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(result["error"]["details"]["parameter"], "cursor")

    def test_search_cursor_at_or_past_total_is_invalid(self):
        with self.live_rows(2, [self.evs.concepts["C1"]]):
            first = self.search()
        with self.live_rows(2, []):
            for offset in (2, 3):
                with self.subTest(offset=offset):
                    result = self.search(cursor=reposition(first["nextCursor"], offset))
                    self.assertEqual(result["error"]["code"], "invalid_request")
                    self.assertNotIn("results", result)

    def test_hierarchy_cursor_at_total_is_invalid(self):
        self.evs.concepts["C1"]["children"].append({"code": "C3"})
        self.evs.concepts["C3"] = concept("C3", active=True)
        first = self.hierarchy()
        result = self.hierarchy(cursor=reposition(first["nextCursor"], 2))
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertNotIn("nodes", result)

    def test_live_and_hierarchy_provenance_belongs_only_to_empty_results(self):
        with self.live_rows(1, [self.evs.concepts["C1"]]):
            self.assertNotIn("provenance", self.search())
        self.assertNotIn("provenance", self.hierarchy())
        with self.live_rows(0, []):
            empty = self.search()
        self.assertEqual(empty["results"], [])
        self.assertEqual(
            empty["provenance"]["sourceUri"],
            "https://evs.test/api/v1/concept/ncit_26.06e/search",
        )

    def test_indexed_result_preserves_matching_field_without_top_level_provenance(self):
        self.context.index.upsert_concepts(
            list(self.evs.concepts.values()), None, self.context.embedding_provider
        )
        hits, total, manifest = self.context.index.search_page(
            "One", self.context.embedding_provider, 1, "vector", requested_release="26.06e"
        )
        for field in ("synonym", "definition"):
            with (
                self.subTest(field=field),
                patch.object(
                    self.context.index,
                    "search_page",
                    return_value=([replace(hits[0], matched_on=field)], total, manifest),
                ),
            ):
                result = self.search(mode="semantic")
                self.assertEqual(result["results"][0]["matchedOn"], field)
                self.assertNotIn("provenance", result)

    def test_malformed_live_match_code_fails_at_content_boundary(self):
        for code in (None, "", 123):
            with (
                self.subTest(code=code),
                self.live_rows(1, [self.evs.concepts["C1"] | {"code": code}]),
            ):
                result = self.search()
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertNotIn("results", result)

    def test_retired_only_rejects_inactive_hit_with_different_status(self):
        self.evs.rows = [self.retirement_metadata()]
        raw = concept("C1", active=False, conceptStatus="Obsolete")
        with self.live_rows(1, [raw]):
            result = self.search(retired="only")
        self.assertEqual(result["error"]["code"], "upstream_unavailable")
        self.assertNotIn("results", result)

    def test_retirement_metadata_selects_both_release_and_terminology(self):
        selected = self.retirement_metadata()
        self.evs.rows = [
            selected | {"version": "other"},
            selected | {"terminology": "other"},
            selected,
        ]
        raw = concept("C1", active=False, conceptStatus="Retired_Concept")
        with self.live_rows(1, [raw]):
            result = self.search(retired="only")
        self.assertEqual(result["results"][0]["concept"]["status"], "Retired_Concept")

    def test_absent_retirement_release_is_release_not_available(self):
        self.evs.rows = [self.retirement_metadata() | {"version": "other"}]
        result = self.search(retired="only")
        self.assertEqual(result["error"]["code"], "release_not_available")
        self.assertNotIn("results", result)

    def test_whitespace_query_is_invalid(self):
        with self.live_rows(0, []):
            result = self.search(query=" \t\n ")
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(result["error"]["details"]["parameter"], "query")

    def test_null_or_nontext_replacement_code_is_not_silently_dropped(self):
        self.evs.concepts["C1"].update(active=False)
        for code in (None, 123):
            with (
                self.subTest(code=code),
                patch.object(
                    self.evs,
                    "get_replacements",
                    return_value=[{"replacementCode": code, "replacementName": "Two"}],
                    create=True,
                ),
            ):
                result = self.call("resolve_retired_code", code="C1")
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertNotIn("replacements", result)

    def test_retired_status_keeps_inactive_and_reads_replacements_without_name(self):
        self.evs.concepts["C1"].update(active=False, conceptStatus="Retired_Concept")
        with patch.object(
            self.evs,
            "get_replacements",
            return_value=[{"replacementCode": "C2", "replacementName": "Two"}],
            create=True,
        ):
            result = self.call("resolve_retired_code", code="C1")
        self.assertFalse(result["active"])
        self.assertEqual(result["status"], "Retired_Concept")
        self.assertEqual(result["replacements"][0]["code"], "C2")
        self.assertNotIn("name", result)

    def test_explicit_null_or_empty_status_is_omitted(self):
        for status in (None, ""):
            with self.subTest(status=status):
                self.evs.concepts["C1"]["conceptStatus"] = status
                result = self.call("get_concept", code="C1")
                self.assertNotIn("status", result)
