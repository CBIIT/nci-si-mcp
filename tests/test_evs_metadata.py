"""What EVS says of a terminology and of a concept's place in its hierarchy reaches the caller."""

from fakes import concept, terminology_row
from test_server import ServerFixture

NCIT_METADATA = {"retiredStatusValue": "Retired_Concept", "uiLabel": "NCI Thesaurus"}
LICENSED_METADATA = {"retiredStatusValue": "true", "licenseText": "Use requires a licence. "}


def listing(*rows):
    return [dict(terminology_row(), **row) for row in rows]


class TerminologyMetadataTest(ServerFixture):
    def test_tags_retired_status_value_and_licence_text_pass_through_unchanged(self):
        self.evs.rows = listing(
            {"tags": {"monthly": "true", "weekly": "true"}, "metadata": NCIT_METADATA},
            {"terminology": "icd10", "tags": None, "metadata": LICENSED_METADATA},
        )

        failed, result = self.call("list_terminologies")

        self.assertFalse(failed)
        ncit, icd10 = result["terminologies"]
        self.assertEqual(ncit.get("tags"), {"monthly": "true", "weekly": "true"})
        self.assertEqual(ncit.get("retiredStatusValue"), "Retired_Concept")
        self.assertNotIn("attribution", ncit["provenance"])
        self.assertNotIn("tags", icd10)
        self.assertEqual(icd10["retiredStatusValue"], "true")
        self.assertEqual(icd10["provenance"]["attribution"], "Use requires a licence. ")

    def test_a_terminology_without_that_metadata_gets_none_of_it(self):
        self.evs.rows = listing({"metadata": {"uiLabel": "NCI Thesaurus"}})

        failed, result = self.call("list_terminologies")

        self.assertFalse(failed)
        (item,) = result["terminologies"]
        self.assertEqual(item.get("tags"), {"monthly": "true"})
        self.assertNotIn("retiredStatusValue", item)
        self.assertNotIn("attribution", item["provenance"])

    def test_malformed_terminology_metadata_fails_the_listing(self):
        # Malformed tags already fail release selection, which reads them first.
        cases = (
            {"metadata": {"retiredStatusValue": 1}},
            {"metadata": {"licenseText": ["text"]}},
            {"metadata": "none"},
        )
        for fields in cases:
            with self.subTest(fields=fields):
                self.evs.rows = listing(fields)

                failed, result = self.call("list_terminologies")

                self.assertTrue(failed)
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
                self.assertNotIn("terminologies", result)


class ConceptLeafTest(ServerFixture):
    def test_the_leaf_flag_passes_through_where_evs_gives_it(self):
        for code, fields, expected in (
            ("C1", {"leaf": True}, True),
            ("C2", {"leaf": False}, False),
            ("C3", {}, "absent"),
            ("C4", {"leaf": None}, "absent"),
        ):
            with self.subTest(fields=fields):
                self.evs.concepts[code] = concept(code, active=True, **fields)

                failed, result = self.call("get_concept", code=code)

                self.assertFalse(failed)
                self.assertEqual(result.get("leaf", "absent"), expected)

    def test_a_leaf_flag_that_is_not_a_boolean_is_refused(self):
        for value in ("true", 1):
            with self.subTest(value=value):
                self.evs.concepts["C1"] = concept("C1", active=True, leaf=value)

                failed, result = self.call("get_concept", code="C1")

                self.assertTrue(failed)
                self.assertEqual(result["error"]["code"], "upstream_unavailable")
