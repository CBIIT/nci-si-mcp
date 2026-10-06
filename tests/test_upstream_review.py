"""Upstream boundary regressions from the independent Phase 2 mutation review."""

import json
import unittest
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fakes import concept, release, terminology_row
from nci_si_mcp.errors import PlatformError
from nci_si_mcp.evs import EVSClient, EVSResponseError
from nci_si_mcp.release import served_evs_release
from test_evs_client import FakeResponse


class UpstreamReviewTest(unittest.TestCase):
    def setUp(self):
        self.client = EVSClient("https://evs.invalid", max_attempts=1)
        self.release = release()

    def reply(self, value):
        return patch(
            "nci_si_mcp.http_client._open", return_value=FakeResponse(json.dumps(value).encode())
        )

    def test_all_terminology_path_segments_are_encoded(self):
        selected = replace(self.release, pinned_terminology="other/version +?")
        cases = (
            (lambda: self.client.get_concept("C1", selected), concept("C1"), "concept", "/C1"),
            (
                lambda: self.client.get_relationship_catalogue(selected, "role"),
                [],
                "metadata",
                "/roles",
            ),
            (
                lambda: self.client.get_replacements("C1", selected),
                [],
                "history",
                "/C1/replacements",
            ),
        )
        for action, body, group, suffix in cases:
            with self.subTest(group=group), self.reply(body) as opened:
                self.assertEqual(action(), body)
                self.assertEqual(
                    urlsplit(opened.call_args.args[0].full_url).path,
                    f"/api/v1/{group}/other%2Fversion%20%2B%3F{suffix}",
                )

    def test_search_offset_past_total_is_a_valid_empty_page(self):
        with self.reply({"total": 1}):
            self.assertEqual(
                self.client.search_concepts(self.release, "q", "lexical", 2, 3), (1, [])
            )

    def test_search_rejects_empty_and_nontext_codes_at_the_client_boundary(self):
        for code in ("", None, 1, []):
            with (
                self.subTest(code=code),
                self.reply({"total": 1, "concepts": [concept("C1") | {"code": code}]}),
                self.assertRaises(EVSResponseError),
            ):
                self.client.search_concepts(self.release, "q", "lexical", 0, 10)

    def test_live_search_requests_page_size_and_highlights(self):
        with self.reply({"total": 0}) as opened:
            self.assertEqual(
                self.client.search_concepts(self.release, "q", "lexical", 0, 7), (0, [])
            )
        params = parse_qs(urlsplit(opened.call_args.args[0].full_url).query)
        self.assertEqual(params["include"], ["minimal,highlights"])
        self.assertEqual(params["pageSize"], ["7"])

    def test_full_build_requests_all_indexed_fields_and_rejects_zero_total(self):
        with self.reply({"total": 1, "concepts": [concept("C1")]}) as opened:
            total, rows = self.client.get_index_page(self.release, 0)
        self.assertEqual((total, rows[0]["code"]), (1, "C1"))
        params = parse_qs(urlsplit(opened.call_args.args[0].full_url).query)
        self.assertEqual(params["include"], ["summary,definitions,synonyms,properties"])
        with self.reply({"total": 0, "concepts": []}), self.assertRaises(EVSResponseError):
            self.client.get_index_page(self.release, 0)

    def test_paths_start_with_the_seed_and_every_code_is_nonempty(self):
        for codes in (("C2", "C1"), ("C1", "")):
            with (
                self.subTest(codes=codes),
                self.reply([[concept(code) for code in codes]]),
                self.assertRaises(EVSResponseError),
            ):
                self.client.get_paths_to_root("C1", self.release)

    def test_history_rows_require_their_source_code(self):
        with self.reply([{"replacementCode": "C2"}]), self.assertRaises(EVSResponseError):
            self.client.get_replacements("C1", self.release)

    def test_an_empty_batch_returns_without_a_network_request(self):
        with patch(
            "nci_si_mcp.http_client._open", side_effect=AssertionError("unexpected request")
        ):
            self.assertEqual(self.client.get_concepts_by_codes([], self.release), [])


class ServedReleaseReviewTest(unittest.TestCase):
    def test_another_terminology_with_the_same_version_is_not_the_requested_release(self):
        row = terminology_row() | {"terminology": "other"}
        with self.assertRaises(PlatformError) as raised:
            served_evs_release([row], "ncit", "26.06e", "monthly")
        self.assertEqual(raised.exception.code, "release_not_available")

    def test_a_served_release_retains_its_upstream_pinned_identifier(self):
        row = terminology_row() | {"terminologyVersion": "upstream-specific-pin"}
        selected = served_evs_release([row], "ncit", "26.06e", "monthly")
        self.assertEqual(selected.pinned_terminology, "upstream-specific-pin")
        self.assertEqual(selected.version, "26.06e")
