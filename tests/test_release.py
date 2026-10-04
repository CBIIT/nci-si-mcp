import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fakes import FakeEVS, release, terminology_row
from nci_si_mcp.errors import PlatformError, is_error_record
from nci_si_mcp.evs import EVSClient, EVSNotFoundError, EVSReleaseNotFoundError
from nci_si_mcp.release import ITEM_VERSIONING, registry_state, resolve_evs_release
from test_service import NEOPLASM, ServiceTestCase

# The weekly build is listed first, as EVS may list it, and both are latest for their channel.
WEEKLY = terminology_row("26.09c", "2026-09-21", weekly="true")
MONTHLY = terminology_row("26.09d", "2026-09-28", monthly="true")


def fake_with(*rows):
    evs = FakeEVS()
    evs.rows = list(rows)
    return evs


class FakeResponse:
    status = 200

    def __init__(self):
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return b"[]"[:limit]


class ResolveEvsReleaseTest(unittest.TestCase):
    def test_the_one_row_a_channel_names_is_the_release(self):
        resolved = resolve_evs_release(fake_with(MONTHLY), "ncit", "monthly")

        self.assertEqual(
            (resolved.terminology, resolved.channel, resolved.version, resolved.date),
            ("ncit", "monthly", "26.09d", "2026-09-28"),
        )
        self.assertEqual(resolved.pinned_terminology, "ncit_26.09d")

    def test_each_channel_takes_the_row_its_tag_names(self):
        evs = fake_with(WEEKLY, MONTHLY)

        self.assertEqual(resolve_evs_release(evs, "ncit", "monthly").version, "26.09d")
        self.assertEqual(resolve_evs_release(evs, "ncit", "weekly").version, "26.09c")

    def test_a_row_tagged_for_both_channels_is_the_release_of_each(self):
        both = terminology_row("26.09d", "2026-09-28", monthly="true", weekly="true")
        evs = fake_with(both, terminology_row("26.09c", latest=False, weekly="true"))

        for channel in ("monthly", "weekly"):
            self.assertEqual(resolve_evs_release(evs, "ncit", channel).version, "26.09d")

    def test_latest_is_scoped_to_the_channel_not_to_the_terminology(self):
        evs = fake_with(WEEKLY, dict(MONTHLY, latest=False))

        self.assertEqual(resolve_evs_release(evs, "ncit", "weekly").version, "26.09c")
        with self.assertRaises(PlatformError) as raised:
            resolve_evs_release(evs, "ncit", "monthly")
        self.assertEqual(raised.exception.code, "release_not_available")

    def test_the_terminology_version_the_row_names_is_what_requests_are_pinned_to(self):
        row = dict(MONTHLY, terminologyVersion="ncit_2609d_monthly")

        self.assertEqual(
            resolve_evs_release(fake_with(row), "ncit", "monthly").pinned_terminology,
            "ncit_2609d_monthly",
        )
        row.pop("terminologyVersion")
        self.assertEqual(
            resolve_evs_release(fake_with(row), "ncit", "monthly").pinned_terminology,
            "ncit_26.09d",
        )

    def test_zero_and_several_rows_fail_closed_naming_what_was_found_and_the_next_step(self):
        for rows, found in (
            ([WEEKLY], "none"),
            ([MONTHLY, terminology_row("26.08e", monthly="true")], "26.09d, 26.08e"),
        ):
            with self.subTest(found=found), self.assertRaises(PlatformError) as raised:
                resolve_evs_release(fake_with(*rows), "ncit", "monthly")

            error = raised.exception
            self.assertEqual(error.code, "release_not_available")
            self.assertEqual(set(error.details), {"requested", "source"})
            self.assertEqual(error.details["source"], "evs")
            self.assertIn(found, error.details["requested"])
            self.assertIn(found, error.message)
            self.assertIn("NCI_SI_RELEASE_CHANNEL", error.message)

    def test_a_row_without_a_version_is_not_a_release(self):
        with self.assertRaises(PlatformError) as raised:
            resolve_evs_release(fake_with(dict(MONTHLY, version="")), "ncit", "monthly")

        self.assertEqual(raised.exception.code, "release_not_available")

    def test_the_query_names_the_terminology_latest_and_the_channel(self):
        with (
            patch("nci_si_mcp.http_client._open", return_value=FakeResponse()) as opened,
            self.assertRaises(PlatformError) as raised,
        ):
            resolve_evs_release(EVSClient("https://example.invalid"), "ncit", "weekly")

        # An answer without a row is no release, whatever the query was.
        self.assertEqual(raised.exception.code, "release_not_available")
        self.assertEqual(
            opened.call_args.args[0].full_url,
            "https://example.invalid/api/v1/metadata/terminologies"
            "?terminology=ncit&latest=true&tag=weekly",
        )

    def test_the_unfiltered_listing_sends_no_filter(self):
        with patch("nci_si_mcp.http_client._open", return_value=FakeResponse()) as opened:
            rows = EVSClient("https://example.invalid").get_terminologies()

        self.assertEqual(rows, [])
        self.assertEqual(
            opened.call_args.args[0].full_url,
            "https://example.invalid/api/v1/metadata/terminologies",
        )


class UnknownReleaseTest(unittest.TestCase):
    def answer_404(self, message):
        body = io.BytesIO(f'{{"message": "{message}"}}'.encode())
        error = HTTPError("https://example.invalid", 404, "Not Found", {}, body)
        return patch("nci_si_mcp.http_client._open", side_effect=[error])

    def test_a_release_evs_does_not_serve_is_a_release_failure_naming_it(self):
        with (
            self.answer_404("Terminology not found = ncit_99.99z"),
            self.assertRaises(EVSReleaseNotFoundError) as raised,
        ):
            EVSClient("https://example.invalid", max_attempts=1).get_concept(
                "C4817", terminology="ncit_99.99z"
            )

        self.assertEqual(raised.exception.details, {"requested": "ncit_99.99z", "source": "evs"})

    def test_any_other_404_of_a_concept_request_is_still_a_missing_concept(self):
        with (
            self.answer_404("C4817 not found"),
            self.assertRaises(EVSNotFoundError),
        ):
            EVSClient("https://example.invalid", max_attempts=1).get_concept("C4817")


class ServiceReleaseTest(ServiceTestCase):
    def test_a_resolved_release_is_not_kept_between_calls(self):
        first = self.service.lookup("C3262", live_only=True)
        self.evs.release = release("26.07d", "2026-07-27")
        self.evs.concepts["C3262"] = dict(NEOPLASM, version="26.07d")

        second = self.service.lookup("C3262", live_only=True)

        self.assertEqual(first["provenance"]["release"]["identifier"], "26.06e")
        self.assertEqual(second["provenance"]["release"]["identifier"], "26.07d")

    def test_every_call_resolves_the_release_before_it_reads_content(self):
        self.service.lookup("C3262", live_only=True)
        self.service.traverse(["C3262"], max_depth=1)

        methods = [call[0] for call in self.evs.calls]
        self.assertEqual(methods.count("get_terminologies"), 2)
        self.assertEqual(methods[0], "get_terminologies")

    def test_the_configured_channel_decides_the_release(self):
        self.evs.release = release("26.07a", "2026-07-06", channel="weekly")
        self.evs.concepts["C3262"] = dict(NEOPLASM, version="26.07a")
        weekly = self.make_service(release_channel="weekly")

        reported = weekly.release_info()["selected_monthly_release"]
        result = weekly.lookup("C3262", live_only=True)

        self.assertEqual((reported["channel"], reported["version"]), ("weekly", "26.07a"))
        self.assertEqual(result["provenance"]["release"]["identifier"], "26.07a")
        self.assertIn(("get_terminologies", "ncit", (True, "weekly")), self.evs.calls)
        # The default service, on the monthly channel, finds no monthly row here.
        self.assertTrue(is_error_record(self.service.lookup("C3262", live_only=True)))

    def test_the_payload_version_is_a_second_guard_against_the_resolved_release(self):
        self.evs.concepts["C3262"] = dict(NEOPLASM, version="26.05d")

        result = self.service.lookup("C3262", live_only=True)

        self.assertEqual(result["error"]["code"], "release_mismatch")
        self.assertEqual(result["error"]["details"]["requested"], "26.06e")


class RegistryStateTest(unittest.TestCase):
    def test_no_registry_identifier_is_made_up_and_the_export_date_is_iso(self):
        state = registry_state("Sun, 28 Sep 2026 14:03:00 GMT")

        self.assertEqual(
            state.to_dict(),
            {
                "registryIdentifier": None,
                "exportDate": "2026-09-28T14:03:00+00:00",
                "itemVersioning": ITEM_VERSIONING,
            },
        )
        self.assertEqual(ITEM_VERSIONING, "per data element")

    def test_an_offset_date_is_given_in_utc(self):
        self.assertEqual(
            registry_state("Mon, 28 Sep 2026 16:03:00 +0200").export_date,
            "2026-09-28T14:03:00+00:00",
        )

    def test_a_missing_header_leaves_the_date_out_and_still_no_identifier(self):
        for header in (None, ""):
            with self.subTest(header=header):
                state = registry_state(header)
                self.assertEqual((state.registry_identifier, state.export_date), (None, None))

    def test_a_header_that_is_no_date_fails_instead_of_guessing_one(self):
        with self.assertRaises(PlatformError) as raised:
            registry_state("last Tuesday")

        self.assertEqual(raised.exception.code, "upstream_unavailable")
        self.assertEqual(raised.exception.details, {"surface": "cadsr"})

    def test_an_identifier_the_registry_publishes_is_carried_as_it_is(self):
        state = registry_state("Sun, 28 Sep 2026 14:03:00 GMT", " R2026.3 ")

        self.assertEqual(state.to_dict()["registryIdentifier"], "R2026.3")

    def test_a_published_identifier_that_is_blank_or_not_text_fails_closed(self):
        for identifier in ("", "  ", 7):
            with self.subTest(identifier=identifier), self.assertRaises(PlatformError) as raised:
                registry_state("Sun, 28 Sep 2026 14:03:00 GMT", identifier)

            self.assertEqual(raised.exception.code, "release_not_available")
            self.assertEqual(
                raised.exception.details, {"requested": "cadsr registry", "source": "cadsr"}
            )
