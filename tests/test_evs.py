import unittest

from nci_si_mcp.evs import ReleaseResolutionError, normalize_concept, select_monthly_ncit_release


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
        row = {"terminology": "ncit", "version": "26.06e", "latest": True, "tags": {"monthly": "true"}}

        named = select_monthly_ncit_release([dict(row, terminologyVersion="ncit_26.06e")])
        unnamed = select_monthly_ncit_release([row])

        self.assertEqual(named.pinned_terminology, "ncit_26.06e")
        self.assertEqual(unnamed.pinned_terminology, "ncit_26.06e")

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


if __name__ == "__main__":
    unittest.main()
