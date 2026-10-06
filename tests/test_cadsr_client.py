"""Offline caDSR fixtures crafted from the published JSON contracts.

See fixtures/cadsr/README.md for contract URLs. No fixture is a runtime data source.
"""

import base64
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from nci_si_mcp.cadsr import CaDSRClient, export_state
from nci_si_mcp.config import Settings
from nci_si_mcp.errors import InputValidationError, PlatformError
from nci_si_mcp.http_client import UpstreamRejectedError, UpstreamTimeoutError
from nci_si_mcp.release import RegistryMetadataError
from test_http_client import Reply, ServerTestCase

ROOT = Path(__file__).resolve().parents[1]
LISTING = '<pre><a href="releasedCDEsXML-OD.zip">download</a> 2026-07-01 22:19 116M\n</pre>'


def reply(body):
    return Reply(body=json.dumps(body).encode())


class ExportListingTest(unittest.TestCase):
    def test_recorded_listing_preserves_local_date_and_minute_precision(self):
        fixture = ROOT / "acceptance/fixtures/recorded/cadsr-ftp/cde-xml-listing.json"
        state = export_state(json.loads(fixture.read_text())["response"]["body"])
        self.assertEqual(
            state.to_dict(),
            {
                "published": False,
                "generatedAt": "2026-07-01T22:19",
                "sourceDistribution": "releasedCDEsXML-OD.zip",
            },
        )

    def test_reordered_and_padded_columns_keep_the_distribution_date(self):
        for listing in (
            '<pre>116M  2026-07-01   22:19  <a href="releasedCDEsXML-OD.zip">x</a>\n</pre>',
            "<table><tr>\n<td>2026-07-01 22:19</td><td>116M</td>\n"
            '<td><a href="releasedCDEsXML-OD.zip">x</a></td></tr></table>',
        ):
            with self.subTest(listing=listing):
                self.assertEqual(export_state(listing).generated_at, "2026-07-01T22:19")

    def test_missing_duplicate_or_invalid_distribution_metadata_fails_closed(self):
        for listing in (
            LISTING.replace('href="releasedCDEsXML-OD.zip"', 'href="other.zip"'),
            LISTING + LISTING,
            LISTING.replace("2026-07-01", "2026-02-30"),
            LISTING.replace("2026-07-01 22:19", "unknown"),
            LISTING.replace("22:19", "22:19:40"),
            LISTING.replace("22:19", "22:19+04:00"),
            LISTING.replace("116M", "2026-07-02 02:19"),
            LISTING.replace("download", '<a href="releasedCDEsXML-OD.zip">again</a>'),
        ):
            with self.subTest(listing=listing), self.assertRaises(RegistryMetadataError) as raised:
                export_state(listing)
            self.assertEqual(raised.exception.details, {"surface": "cadsr"})


class CaDSRClientTest(ServerTestCase):
    def cadsr(self, api, export=None, **options):
        settings = Settings(cadsr_base_url=api.url, cadsr_ftp_url=(export or api).url, **options)
        client = CaDSRClient(settings)
        client.http.sleep = lambda _: None
        client.match_http.sleep = lambda _: None
        return client

    def test_data_element_and_form_versions_reach_the_contract_paths(self):
        for operation, key, path in (
            ("get_data_element", "DataElement", "/NCIAPI/1.0/api/DataElement/123"),
            ("get_form", "form", "/NCIFormAPI.v2_0:NciFormApiRad/Form/123"),
        ):
            with self.subTest(operation=operation):
                payload = {"publicId": "123", "version": "1.2"}
                server = self.serve(reply({key: payload, "apiResponse": {"type": "I"}}))
                result = getattr(self.cadsr(server), operation)("123", "1.2")
                self.assertEqual(result, payload)
                self.assertEqual(server.seen[0][0], path + "?version=1.2")

    def test_invalid_identifiers_and_versions_make_no_request(self):
        server = self.serve()
        client = self.cadsr(server)
        for operation in (client.get_data_element, client.get_form):
            for identifier, version in (("0", None), ("1/2", None), ("123", "1&x=y")):
                with (
                    self.subTest(identifier=identifier, version=version),
                    self.assertRaises(InputValidationError),
                ):
                    operation(identifier, version)
        self.assertEqual(server.seen, [])

    def test_question_text_reaches_the_upstream_as_one_exact_parameter(self):
        text = "λ & key=value / quoted?"
        server = self.serve(reply({"DataElements": [{"publicId": "123"}]}))
        result = self.cadsr(server).get_by_question_text(text)

        self.assertEqual(result, [{"publicId": "123"}])
        self.assertEqual(
            parse_qs(urlsplit(server.seen[0][0]).query),
            {
                "documentText": [text],
                "documentType": ["Preferred Question Text"],
                "headerOnly": ["true"],
            },
        )

    def test_list_operations_keep_order_and_platform_fields(self):
        for operation, key, path, items in (
            (
                CaDSRClient.list_contexts,
                "contextNames",
                "/NCILovAPI/1.0/api/getContextNames",
                ["B", "A"],
            ),
            (
                CaDSRClient.get_crdc_list,
                "CRDCDataElements",
                "/NCIAPI/1.0/api/DataElements/getCRDCList",
                [{"CDE Public ID": "123"}],
            ),
            (
                CaDSRClient.get_models,
                "modelQueryResults",
                "/NCIModelAPI/1.0/api/Models",
                [{"modelName": "Fixture model"}],
            ),
            (
                CaDSRClient.get_crosswalk_mappings,
                "data",
                "/NCIModelAPI/1.0/api/CrossWalkMappings/Download",
                [{"sourceCode": "A"}],
            ),
        ):
            with self.subTest(operation=operation):
                server = self.serve(reply({key: items}))
                self.assertEqual(operation(self.cadsr(server)), items)
                self.assertEqual(server.seen[0][0], path)

    def test_informational_absence_is_not_a_malformed_response(self):
        server = self.serve(reply({"DataElement": None, "apiResponse": {"type": "I"}}))
        self.assertIsNone(self.cadsr(server).get_data_element("123"))

    def test_requested_search_sends_only_keyword_and_page_size(self):
        body = {"DataElements": [], "status": None, "message": None, "numRecords": None}
        server = self.serve(reply(body))
        query = "λ & context=not-a-filter"
        self.assertEqual(self.cadsr(server).search_data_elements(query, 7), body)
        url = urlsplit(server.seen[0][0])
        self.assertEqual(url.path, "/NCIAPI/1.0/api/DataElement/search")
        self.assertEqual(parse_qs(url.query), {"keyword": [query], "pageSize": ["7"]})

    def test_unserved_search_error_has_no_fallback(self):
        server = self.serve(reply({"apiResponse": {"type": "E"}}))
        with self.assertRaises(PlatformError) as raised:
            self.cadsr(server).search_data_elements("query", 10)
        self.assertEqual(raised.exception.code, "upstream_unavailable")
        self.assertEqual(len(server.seen), 1)

    def test_invalid_search_page_size_never_reaches_the_platform(self):
        server = self.serve()
        with self.assertRaises(InputValidationError):
            self.cadsr(server).search_data_elements("query", 0)
        self.assertEqual(server.seen, [])

    def test_invalid_item_envelopes_never_become_missing_content(self):
        for body in (
            [],
            {},
            {"DataElement": []},
            {"DataElement": None},
            {"DataElement": None, "apiResponse": []},
        ):
            with self.subTest(body=body):
                server = self.serve(reply(body))
                with self.assertRaises(PlatformError) as raised:
                    self.cadsr(server).get_data_element("123")
                self.assertEqual(raised.exception.code, "upstream_unavailable")

    def test_malformed_lists_are_errors_but_empty_lists_are_valid(self):
        for value in (None, {}, [1]):
            with self.subTest(value=value):
                server = self.serve(reply({"contextNames": value}))
                with self.assertRaises(PlatformError):
                    self.cadsr(server).list_contexts()
        server = self.serve(reply({"contextNames": []}))
        self.assertEqual(self.cadsr(server).list_contexts(), [])

    def test_unknown_form_error_envelope_is_the_recorded_not_found_interpretation(self):
        server = self.serve(
            reply({"form": None, "apiResponse": {"type": "E", "message": "No data"}})
        )
        with self.assertRaises(PlatformError) as raised:
            self.cadsr(server).get_form("123")
        self.assertEqual(raised.exception.code, "not_found")
        self.assertEqual(len(server.seen), 1)

    def test_match_sends_one_object_and_value_matching_sends_the_contract_array(self):
        cde = {"entity": "Fixture", "numberOfMatches": 0, "numberofPVs": 0, "sequenceNumber": 1}
        server = self.serve(reply({"matchResults": cde}), reply({"matchResults": []}))
        client = self.cadsr(server)
        entity = {"entity": "Fixture", "entityUserTip": "A & B", "pvvmData": [{"name": "X"}]}
        self.assertEqual(client.match_data_element(entity, {"matchLimit": "3"}), cde)
        self.assertEqual(
            client.match_value_meanings(
                [{"name": "X"}], {"matchType": "Restricted", "function": "match"}
            ),
            [],
        )
        self.assertEqual([json.loads(body) for body in server.bodies], [entity, [{"name": "X"}]])
        self.assertEqual(server.seen[0][1]["matchlimit"], "3")
        self.assertEqual(server.seen[1][1]["matchtype"], "Restricted")
        self.assertEqual(server.seen[1][1]["function"], "match")
        self.assertEqual(
            [path for path, _ in server.seen],
            [
                "/NCIAPI.v2_0.cdeMatch.api:cdeMatch_rad/cdeMatch",
                "/vmMatch/v1/vmMatch",
            ],
        )

    def test_missing_match_object_is_not_an_empty_success(self):
        server = self.serve(reply({"matchResults": None, "apiResponse": {"type": "I"}}))
        with self.assertRaises(PlatformError) as raised:
            self.cadsr(server).match_data_element({"entity": "Fixture"}, {})
        self.assertEqual(raised.exception.code, "upstream_unavailable")

    def test_matching_uses_its_timeout_and_retries_only_identical_requests(self):
        client = self.cadsr(self.serve(), match_timeout_seconds=45)
        observed = []

        def timeout(request, seconds):
            observed.append((request.data, seconds))
            raise TimeoutError

        with (
            patch("nci_si_mcp.http_client._open", side_effect=timeout),
            self.assertRaises(UpstreamTimeoutError) as raised,
        ):
            client.match_data_element({"entity": "Fixture"}, {})
        self.assertEqual(observed, [(b'{"entity": "Fixture"}', 45)] * 3)
        self.assertEqual(
            raised.exception.details, {"surface": "cadsr", "seconds": 45, "attempts": 3}
        )

    def test_export_origin_gets_no_authorization_even_with_a_configured_credential(self):
        credential = "fixture:" + uuid4().hex
        api = self.serve(Reply(404))
        export = self.serve(Reply(body=LISTING.encode()))
        result = self.cadsr(api, export, cadsr_credential=credential).resolve_registry_release()
        self.assertEqual(result.generated_at, "2026-07-01T22:19")
        self.assertEqual(
            api.seen[0][1]["authorization"],
            "Basic " + base64.b64encode(credential.encode()).decode(),
        )
        self.assertNotIn("authorization", export.seen[0][1])
        self.assertEqual(export.seen[0][0], "/CDE/XML/")

    def test_401_is_not_retried_or_hidden_when_no_credential_exists(self):
        server = self.serve(Reply(401))
        with self.assertRaises(UpstreamRejectedError) as raised:
            self.cadsr(server).list_contexts()
        self.assertEqual(raised.exception.details["status"], 401)
        self.assertEqual(len(server.seen), 1)
        self.assertNotIn("authorization", server.seen[0][1])

    def test_auth_echoes_are_redacted_in_every_representation(self):
        credential = "fixture:" + uuid4().hex
        token = base64.b64encode(credential.encode()).decode()
        message = " ".join((credential, credential.split(":")[1], token, "Basic " + token))
        server = self.serve(Reply(401, reason=message))
        client = self.cadsr(server, cadsr_credential=credential)
        records = []
        client.http.on_request = records.append
        with (
            self.assertLogs("nci_si_mcp", level="DEBUG") as logs,
            self.assertRaises(UpstreamRejectedError) as raised,
        ):
            client.list_contexts()
        output = repr((str(raised.exception), raised.exception.details, records, logs.output))
        for secret in (credential, credential.split(":")[1], token):
            self.assertNotIn(secret, output)
        self.assertIn("[redacted]", output)

    def test_published_registry_release_uses_its_own_date_without_reading_export(self):
        row = {"identifier": "fixture-1", "generatedAt": "2026-08-01T12:30Z", "latest": True}
        server = self.serve(reply({"registryReleases": [row]}))
        state = self.cadsr(server).resolve_registry_release()
        self.assertEqual(state.identifier, "fixture-1")
        self.assertEqual(state.generated_at, row["generatedAt"])
        self.assertEqual(len(server.seen), 1)
        self.assertEqual(
            state.source_distribution, server.url + "/NCIAPI/1.0/api/registry/releases"
        )

    def test_ambiguous_or_incomplete_published_registry_metadata_fails_closed(self):
        row = {"identifier": "fixture-1", "generatedAt": "2026-08-01", "latest": True}
        for rows in (
            [row, row],
            [row | {"latest": False}],
            [row | {"identifier": None}],
            [row | {"generatedAt": "unknown"}],
        ):
            with self.subTest(rows=rows):
                server = self.serve(reply({"registryReleases": rows}))
                with self.assertRaises(RegistryMetadataError):
                    self.cadsr(server).resolve_registry_release()
                self.assertEqual(len(server.seen), 1)

    def test_registry_access_failure_does_not_fall_back_to_export(self):
        server = self.serve(Reply(403))
        with self.assertRaises(UpstreamRejectedError):
            self.cadsr(server).resolve_registry_release()
        self.assertEqual(len(server.seen), 1)

    def test_empty_registry_listing_uses_only_the_export_row(self):
        api = self.serve(reply({"registryReleases": []}))
        export = self.serve(Reply(body=LISTING.encode()))
        state = self.cadsr(api, export).resolve_registry_release()
        self.assertFalse(state.to_dict()["published"])
        self.assertIsNone(state.identifier)
        self.assertEqual(state.generated_at, "2026-07-01T22:19")
        self.assertEqual(export.seen[0][0], "/CDE/XML/")
