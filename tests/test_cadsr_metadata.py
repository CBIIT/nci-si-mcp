"""CDE metadata is passed through the real MCP boundary, never filled from another call."""

import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from fakes import data_element
from test_cadsr_matching import cde_response, cde_row
from test_evs_client import FakeResponse
from test_server import ServerFixture

FIXTURES = Path(__file__).resolve().parents[1] / "acceptance/fixtures/recorded/cadsr"
SCALARS = {
    "definition": "Text designations that identify sex.  ",
    "shortName": "PRSN_SEX_TX_TP",
    "latestVersionIndicator": "Yes",
    "beginDate": "2010-05-07",
    "endDate": None,
    "origin": "NCISTD: NCI CRF Harmonization and Standards Initiative",
}


def recorded_element():
    return json.loads((FIXTURES / "data-element-2200604.json").read_text())["response"]["body"]


class DataElementMetadataTest(ServerFixture):
    def answer(self, body, tool="get_data_element", **arguments):
        with patch(
            "nci_si_mcp.http_client._open",
            side_effect=[FakeResponse(json.dumps(body).encode())],
        ) as opened:
            result = self.session(lambda client: client.call_tool(tool, arguments))
        self.paths = [urlsplit(call.args[0].full_url).path for call in opened.call_args_list]
        return result

    def test_recorded_scalars_keep_whitespace_and_null_without_fetching_detail(self):
        result = self.answer(recorded_element(), publicId="2200604")

        self.assertEqual({key: result.structured_content.get(key) for key in SCALARS}, SCALARS)
        self.assertFalse(result.is_error)
        self.assertNotIn("referenceDocuments", result.structured_content)
        self.assertEqual(self.paths, ["/NCIAPI/1.0/api/DataElement/2200604"])

    def test_reference_documents_include_keeps_every_recorded_row_and_unknown_field(self):
        body = recorded_element()
        documents = body["DataElement"]["ReferenceDocuments"]
        self.assertEqual(len(documents), 10)
        documents.append({"documentType": "Future type", "extra": {"kept": [1, None]}})

        result = self.answer(body, publicId="2200604", include=["referenceDocuments"])

        self.assertEqual(result.structured_content.get("referenceDocuments"), documents)
        self.assertFalse(result.is_error)
        self.assertNotIn("alternateNames", result.structured_content)
        self.assertNotIn("valueDomain", result.structured_content)
        self.assertEqual(self.paths, ["/NCIAPI/1.0/api/DataElement/2200604"])

    def test_absent_and_null_scalars_are_present_null_without_backfill(self):
        for extras in ({}, dict.fromkeys(SCALARS)):
            with self.subTest(extras=extras):
                result = self.answer({"DataElement": data_element(**extras)}, publicId="123")

                self.assertEqual(set(SCALARS) - result.structured_content.keys(), set())
                self.assertEqual(
                    {key: result.structured_content[key] for key in SCALARS}, dict.fromkeys(SCALARS)
                )
                self.assertFalse(result.is_error)
                self.assertEqual(self.paths, ["/NCIAPI/1.0/api/DataElement/123"])

    def test_malformed_metadata_is_an_upstream_error_not_success(self):
        for key in SCALARS:
            with self.subTest(field=key):
                result = self.answer({"DataElement": data_element(**{key: []})}, publicId="123")

                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["error"]["code"], "upstream_unavailable")

    def test_malformed_document_sections_are_not_silently_empty(self):
        for documents in (None, {}, ["not a row"]):
            with self.subTest(documents=documents):
                result = self.answer(
                    {"DataElement": data_element(ReferenceDocuments=documents)},
                    publicId="123",
                    include=["referenceDocuments"],
                )

                self.assertEqual(
                    result.structured_content.get("error", {}).get("code"), "upstream_unavailable"
                )
                self.assertTrue(result.is_error)

    def test_match_metadata_comes_only_from_its_producing_answer(self):
        result = self.answer(
            cde_response(matches=[cde_row()]), "match_data_elements", entities=[{"name": "Q"}]
        )

        item = result.structured_content["matches"][0]["dataElement"]
        self.assertEqual(set(SCALARS) - item.keys(), set())
        self.assertEqual({key: item[key] for key in SCALARS}, dict.fromkeys(SCALARS))
        self.assertEqual(len(self.paths), 1)

    def test_search_preserves_recorded_metadata_without_a_detail_fetch(self):
        body = json.loads((FIXTURES / "question-text-sex-of-a-person.json").read_text())[
            "response"
        ]["body"]
        result = self.answer(body, "search_data_elements", query="Sex of a Person")

        item = result.structured_content["results"][0]["dataElement"]
        self.assertEqual({key: item.get(key) for key in SCALARS}, SCALARS)
        self.assertEqual(self.paths, ["/NCIAPI/1.0/api/DataElements/ReferenceDocument"])
