"""Client boundaries that must survive malformed upstream metadata."""

import base64
import unittest
from http.client import HTTPException
from unittest.mock import patch
from uuid import uuid4

from nci_si_mcp.cadsr import CaDSRClient, export_state
from nci_si_mcp.config import Settings
from nci_si_mcp.errors import InputValidationError, PlatformError
from nci_si_mcp.evs import EVSClient
from nci_si_mcp.http_client import (
    UpstreamRejectedError,
    UpstreamTimeoutError,
    UpstreamTooLargeError,
    UpstreamUnavailableError,
)
from nci_si_mcp.release import RegistryMetadataError, resolve_evs_release
from test_cadsr_client import LISTING, reply
from test_evs_client import http_error
from test_http_client import Reply, ServerTestCase
from test_release import MONTHLY


class ClientBoundaryTest(ServerTestCase):
    def test_empty_client_errors_never_mean_an_unpublished_registry(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                server = self.serve(Reply(status, body=b""), Reply(body=LISTING.encode()))
                with self.assertRaises(UpstreamRejectedError) as raised:
                    self.cadsr(server).resolve_registry_release()
                self.assertEqual(raised.exception.details["status"], status)
                self.assertEqual(len(server.seen), 1)

    def test_unreadable_404_body_is_not_an_empty_registry_response(self):
        client = self.cadsr(self.serve())
        for failure in (OSError("unreadable"), HTTPException("incomplete")):
            error = http_error(404)
            with (
                self.subTest(failure=type(failure).__name__),
                patch.object(error, "read", side_effect=failure),
                patch("nci_si_mcp.http_client._open", side_effect=error) as opened,
                self.assertRaises(UpstreamRejectedError) as raised,
            ):
                client.resolve_registry_release()
            self.assertEqual(raised.exception.details["status"], 404)
            self.assertEqual(opened.call_count, 1)

    def cadsr(self, server, **options):
        client = CaDSRClient(
            Settings(cadsr_base_url=server.url, cadsr_ftp_url=server.url, **options)
        )
        for http in (client.http, client.match_http, client.export_http):
            http.sleep = lambda _: None
        return client

    def test_both_matching_operations_send_the_configured_authorization(self):
        credential = "fixture:" + uuid4().hex
        server = self.serve(reply({"matchResults": {}}), reply({"matchResults": []}))
        client = self.cadsr(server, cadsr_credential=credential)
        self.assertEqual(client.match_data_element({"entity": "X"}, {}), {})
        self.assertEqual(client.match_value_meanings([{"name": "X"}], {}), [])
        expected = "Basic " + base64.b64encode(credential.encode()).decode()
        self.assertEqual(
            [headers.get("authorization") for _, headers in server.seen], [expected] * 2
        )

    def test_matching_redacts_echoed_raw_credentials_and_passwords(self):
        credential = "fixture:" + uuid4().hex
        token = base64.b64encode(credential.encode()).decode()
        secrets = (credential, credential.split(":")[1], token)
        server = self.serve(Reply(401, reason=" ".join(secrets)))
        client = self.cadsr(server, cadsr_credential=credential)
        records = []
        client.match_http.on_request = records.append
        with (
            self.assertLogs("nci_si_mcp", level="DEBUG") as logs,
            self.assertRaises(UpstreamRejectedError) as raised,
        ):
            client.match_value_meanings([{"name": "X"}], {})
        output = repr((str(raised.exception), raised.exception.details, records, logs.output))
        for secret in secrets:
            self.assertNotIn(secret, output)
        self.assertIn("[redacted]", output)

    def test_value_matching_and_exports_use_their_configured_timeouts(self):
        client = self.cadsr(self.serve(), timeout_seconds=17, match_timeout_seconds=43)
        for operation, seconds in (
            (lambda: client.match_value_meanings([{"name": "X"}], {}), 43),
            (lambda: client.export_http.get_text("/CDE/XML/"), 17),
        ):
            observed = []

            def timeout(request, timeout_seconds, observed=observed):
                observed.append(timeout_seconds)
                raise TimeoutError

            with (
                patch("nci_si_mcp.http_client._open", side_effect=timeout),
                self.assertRaises(UpstreamTimeoutError) as raised,
            ):
                operation()
            self.assertEqual(observed, [seconds] * 3)
            self.assertEqual(
                raised.exception.details, {"surface": "cadsr", "seconds": seconds, "attempts": 3}
            )

    def test_all_cadsr_transports_enforce_the_ten_mib_response_bound(self):
        limit = 10 * 1024 * 1024
        for name in ("http", "match_http", "export_http"):
            server = self.serve(Reply(declared_length=limit + 1))
            transport = getattr(self.cadsr(server), name)
            with self.subTest(name=name), self.assertRaises(UpstreamTooLargeError) as raised:
                transport.get_json("/oversized")
            self.assertEqual(
                raised.exception.details,
                {"bound": "cadsr_response_bytes", "limit": limit, "reached": limit + 1},
            )
            self.assertEqual(len(server.seen), 1)

    def test_an_unpinned_response_may_name_its_registry_release(self):
        server = self.serve(reply({"DataElement": {"publicId": "123"}, "registryRelease": "R1"}))
        self.assertEqual(self.cadsr(server).get_data_element("123"), {"publicId": "123"})

    def test_null_form_near_misses_keep_common_response_classification(self):
        server = self.serve(
            reply({"form": None}), reply({"form": None, "apiResponse": {"type": "I"}})
        )
        client = self.cadsr(server)
        with self.assertRaises(PlatformError) as raised:
            client.get_form("123")
        self.assertEqual(raised.exception.code, "upstream_unavailable")
        self.assertIsNone(client.get_form("123"))

    def test_version_suffix_is_refused_before_a_request(self):
        server = self.serve()
        for operation in (self.cadsr(server).get_data_element, self.cadsr(server).get_form):
            with self.assertRaises(InputValidationError):
                operation("123", "1.0a")
        self.assertEqual(server.seen, [])

    def test_published_rows_require_boolean_latest_and_a_nonnull_identifier(self):
        row = {"identifier": "R1", "generatedAt": "2026-09-28T12:30", "latest": True}
        for change in ({"latest": "true"}, {"identifier": None}):
            server = self.serve(reply({"registryReleases": [row | change]}))
            with self.subTest(change=change), self.assertRaises(RegistryMetadataError):
                self.cadsr(server).resolve_registry_release()
            self.assertEqual(len(server.seen), 1)

    def test_malformed_search_and_crosswalk_rows_are_structured_errors(self):
        for body, name, args in (
            ({}, "search_data_elements", ("X", 10)),
            ({"DataElements": ["bad"]}, "search_data_elements", ("X", 10)),
            ({"data": ["bad"]}, "get_crosswalk_mappings", ()),
        ):
            server = self.serve(reply(body))
            with self.subTest(body=body), self.assertRaises(PlatformError) as raised:
                getattr(self.cadsr(server), name)(*args)
            self.assertEqual(raised.exception.code, "upstream_unavailable")
            self.assertEqual(raised.exception.details, {"surface": "cadsr"})

    def test_non_utf8_form_reaches_the_common_parser_and_text_names_its_surface(self):
        server = self.serve(Reply(body=b"\xff"), Reply(body=b"\xff"))
        client = self.cadsr(server)
        with self.assertRaises(PlatformError) as form:
            client.get_form("123")
        self.assertEqual(form.exception.code, "upstream_unavailable")
        with self.assertRaises(UpstreamUnavailableError) as text:
            client.export_http.get_text("/CDE/XML/")
        self.assertEqual(text.exception.details, {"surface": "cadsr", "attempts": 1})
        self.assertIn("not UTF-8", str(text.exception))

    def test_only_the_recorded_empty_404_means_no_registry_listing(self):
        server = self.serve(Reply(404, body=b""))
        self.assertEqual(self.cadsr(server).get_registry_releases(), [])
        for body in (b"<html>Not found</html>", b"{}", b" "):
            server = self.serve(Reply(404, body=body))
            with self.subTest(body=body), self.assertRaises(UpstreamRejectedError) as raised:
                self.cadsr(server).resolve_registry_release()
            self.assertEqual(
                raised.exception.details, {"surface": "cadsr", "status": 404, "attempts": 1}
            )
            self.assertEqual(len(server.seen), 1)

    def test_307_post_redirect_is_refused_without_replaying_the_body(self):
        server = self.serve(Reply(307, headers={"Location": "/redirected"}))
        with self.assertRaises(UpstreamRejectedError) as raised:
            self.cadsr(server).match_value_meanings([{"name": "X"}], {})
        self.assertEqual(raised.exception.details["status"], 307)
        self.assertIn("POST redirect refused", str(raised.exception))
        self.assertEqual(len(server.seen), 1)


class ListingBoundaryTest(unittest.TestCase):
    def test_lookalike_distribution_and_joined_dates_are_not_metadata(self):
        for listing in (
            LISTING.replace('href="released', 'href="old-released'),
            LISTING.replace("2026-07-01", "x2026-07-01"),
            LISTING.replace("2026-07-01 22:19", "2026-07-0122:19"),
        ):
            with self.subTest(listing=listing), self.assertRaises(RegistryMetadataError):
                export_state(listing)

    def test_row_boundaries_exclude_heading_dates_and_keep_unterminated_rows(self):
        anchor = '<a href="releasedCDEsXML-OD.zip">file</a> 2026-07-01 22:19'
        for listing in (
            "1999-01-01 01:01<table><tr><td>" + anchor + "</td></tr></table>",
            "<pre>" + anchor + "</pre>",
            "<table><tr>heading</tr></table>\n1999-01-01 01:01\n<pre>" + anchor + "</pre>",
        ):
            with self.subTest(listing=listing):
                self.assertEqual(export_state(listing).generated_at, "2026-07-01T22:19")


class SelectionMetadataTest(unittest.TestCase):
    def test_invalid_tags_versions_and_segments_are_release_errors(self):
        for change in (
            {"tags": None},
            {"tags": {"monthly": "false"}},
            {"version": ".26"},
            {"version": "_26"},
            {"terminologyVersion": "ncit_.26"},
            {"terminologyVersion": "ncit_-26"},
            {"terminologyVersion": 26},
        ):
            with (
                patch(
                    "nci_si_mcp.evs.EVSClient.get_terminologies", return_value=[MONTHLY | change]
                ),
                self.subTest(change=change),
                self.assertRaises(PlatformError) as raised,
            ):
                resolve_evs_release(EVSClient("https://example.invalid"), "ncit", "monthly")
            self.assertEqual(raised.exception.code, "release_not_available")
