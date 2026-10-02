import unittest

from nci_si_mcp.evs import (
    EVSResponseError,
    ReleaseResolutionError,
    normalize_concept,
    object_list,
    select_monthly_ncit_release,
    verify_release,
)


class EVSTest(unittest.TestCase):
    def test_select_monthly_ncit_release_ignores_weekly_latest(self):
        release = select_monthly_ncit_release(
            [
                {
                    "terminology": "ncit",
                    "version": "26.07a",
                    "date": "2026-07-06",
                    "name": "NCI Thesaurus 26.07a",
                    "latest": True,
                    "tags": {"weekly": "true"},
                },
                {
                    "terminology": "ncit",
                    "version": "26.06e",
                    "date": "2026-06-29",
                    "name": "NCI Thesaurus 26.06e",
                    "latest": True,
                    "tags": {"monthly": "true"},
                },
            ]
        )

        self.assertEqual(release.version, "26.06e")
        self.assertTrue(release.monthly)
        self.assertFalse(release.weekly)

    def test_select_monthly_ncit_release_fails_closed_when_missing(self):
        with self.assertRaises(ReleaseResolutionError):
            select_monthly_ncit_release(
                [
                    {
                        "terminology": "ncit",
                        "version": "26.07a",
                        "latest": True,
                        "tags": {"weekly": "true"},
                    }
                ]
            )

    def test_select_monthly_ncit_release_fails_closed_when_ambiguous(self):
        with self.assertRaises(ReleaseResolutionError):
            select_monthly_ncit_release(
                [
                    {
                        "terminology": "ncit",
                        "version": "26.06e",
                        "latest": True,
                        "tags": {"monthly": "true"},
                    },
                    {
                        "terminology": "ncit",
                        "version": "26.05d",
                        "latest": True,
                        "tags": {"monthly": "true"},
                    },
                ]
            )

    def test_select_monthly_ncit_release_requires_a_version(self):
        with self.assertRaises(ReleaseResolutionError):
            select_monthly_ncit_release(
                [{"terminology": "ncit", "latest": True, "tags": {"monthly": "true"}}]
            )

    def test_release_pins_requests_by_its_terminology_version(self):
        row = {
            "terminology": "ncit",
            "version": "26.06e",
            "latest": True,
            "tags": {"monthly": "true"},
        }

        named = select_monthly_ncit_release([dict(row, terminologyVersion="ncit_2606e_monthly")])
        unnamed = select_monthly_ncit_release([row])

        self.assertEqual(named.pinned_terminology, "ncit_2606e_monthly")
        self.assertEqual(unnamed.pinned_terminology, "ncit_26.06e")

    def test_monthly_release_is_found_in_a_listing_shaped_like_live_evs(self):
        def row(terminology, version, latest, **tags):
            return {"terminology": terminology, "version": version, "latest": latest, "tags": tags}

        release = select_monthly_ncit_release(
            [
                row("ncim", "202608", True, monthly="true"),
                row("hl7v30", "2.7", False),
                row("ncit", "26.07d", False, monthly="true"),
                row("ncit", "26.08e", False, monthly="true"),
                row("ncit", "26.09d", True, monthly="true", weekly="true"),
                row("ncit", "26.09c", False, weekly="true"),
            ]
        )

        self.assertEqual(release.version, "26.09d")
        self.assertTrue(release.monthly and release.weekly and release.latest)

    def test_malformed_payload_fields_are_response_errors(self):
        with self.assertRaises(EVSResponseError):
            select_monthly_ncit_release(
                [{"terminology": "ncit", "latest": True, "tags": ["monthly"]}]
            )
        for field in ("properties", "definitions", "synonyms"):
            with self.subTest(field), self.assertRaises(EVSResponseError):
                normalize_concept({"code": "C1", field: ["text"]}, None, "live_evs")
        self.assertEqual(object_list({"children": None}, "children"), [])
        self.assertEqual(object_list({}, "children"), [])

    def test_verify_release_accepts_only_the_requested_release(self):
        verify_release([], "26.06e")
        verify_release([{"version": "26.06e"}, {"version": "26.06e"}], "26.06e")
        for concepts, named in (
            ([{"version": "26.07a"}], "release 26.07a for"),
            ([{"version": "26.06e"}, {}], "release unknown for"),
            ([{"version": "26.06e"}, {"version": "26.05d"}], "release 26.05d for"),
        ):
            with self.subTest(concepts=concepts), self.assertRaises(EVSResponseError) as raised:
                verify_release(concepts, "26.06e")
            self.assertIn(named, str(raised.exception))

    def test_normalize_concept_includes_required_provenance(self):
        concept = normalize_concept(
            {
                "code": "C3262",
                "name": "Neoplasm",
                "terminology": "ncit",
                "version": "26.06e",
                "definitions": [{"definition": "A growth.", "type": "DEFINITION", "source": "NCI"}],
                "synonyms": [{"name": "Tumor", "termType": "SY", "source": "NCI"}],
                "properties": [
                    {"type": "Semantic_Type", "value": "Neoplastic Process"},
                    {"type": "Contributing_Source", "value": "GDC"},
                ],
            },
            release_date="2026-06-29",
            source="live_evs",
            retrieved_at="2026-07-09T00:00:00Z",
        )

        self.assertEqual(concept.code, "C3262")
        self.assertEqual(concept.preferred_name, "Neoplasm")
        self.assertEqual(concept.source_vocabulary, "NCI Thesaurus")
        self.assertEqual(concept.release_version, "26.06e")
        self.assertEqual(concept.release_date, "2026-06-29")
        self.assertEqual(concept.source, "live_evs")
        self.assertEqual(concept.evidence["semantic_types"], ["Neoplastic Process"])
        self.assertEqual(concept.evidence["contributing_sources"], ["GDC"])
        self.assertNotIn("raw", concept.to_dict())
        self.assertIn("raw", concept.to_dict(include_raw=True))

    def test_a_concept_of_another_terminology_is_not_labelled_nci_thesaurus(self):
        concept = normalize_concept(
            {"code": "CL1", "terminology": "ncim"}, release_date=None, source="live_evs"
        )

        self.assertEqual((concept.terminology, concept.source_vocabulary), ("ncim", "ncim"))


if __name__ == "__main__":
    unittest.main()
