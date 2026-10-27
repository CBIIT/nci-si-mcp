"""Given item-owned metadata, MCP callers receive supplied citations and derivations."""

import json

from test_cadsr_metadata import FIXTURES, MetadataFixture, recorded_element


class CitedItemsTest(MetadataFixture):
    def test_empty_values_preserve_the_explicit_domain_type(self):
        # Given either explicit type with no values, MCP must not infer the type from emptiness.
        for domain_type in ("Enumerated", "Non-enumerated"):
            with self.subTest(domain_type=domain_type):
                body = recorded_element()
                body["DataElement"]["ValueDomain"].update(type=domain_type, PermissibleValues=[])
                result = self.answer(
                    body, publicId="2200604", include=["valueDomain", "permissibleValues"]
                )
                self.assertFalse(result.is_error, result.content)
                self.assertEqual(result.structured_content["valueDomain"]["type"], domain_type)
                self.assertEqual(result.structured_content["permissibleValues"], [])

    def test_scheme_and_item_dates_remain_separate_with_absence_and_null(self):
        # Given independently dated schemes and items, MCP preserves each owner's dates.
        body = recorded_element()
        body["DataElement"]["ClassificationSchemes"] = [
            {
                "publicId": "700",
                "version": "1",
                "longName": "Dated scheme",
                "context": "TEST",
                "dateModified": "2026-01-02",
                "endDate": None,
                "ClassificationSchemeItems": [
                    {
                        "publicId": "701",
                        "version": "2",
                        "longName": "Dated item",
                        "dateModified": "2025-03-04",
                        "beginDate": "2024-05-06",
                        "endDate": "2025-07-08",
                    }
                ],
            },
            {
                "publicId": "800",
                "version": "3",
                "longName": "Undated scheme",
                "context": "TEST",
                "ClassificationSchemeItems": [
                    {"publicId": "801", "version": "4", "longName": "Undated item"}
                ],
            },
        ]
        result = self.answer(body, publicId="2200604", include=["classificationSchemes"])
        self.assertFalse(result.is_error, result.content)
        first, second = result.structured_content["classificationSchemes"]
        self.assertEqual(first["dateModified"], "2026-01-02")
        self.assertIn("endDate", first)
        self.assertIsNone(first["endDate"])
        self.assertNotIn("beginDate", first)
        self.assertEqual(
            first["items"],
            [
                {
                    "publicId": "701",
                    "version": "2",
                    "longName": "Dated item",
                    "dateModified": "2025-03-04",
                    "beginDate": "2024-05-06",
                    "endDate": "2025-07-08",
                }
            ],
        )
        self.assertNotIn("dateModified", second)
        self.assertNotIn("endDate", second)
        self.assertNotIn("beginDate", second)
        self.assertEqual(
            second["items"], [{"publicId": "801", "version": "4", "longName": "Undated item"}]
        )
        self.assertEqual(
            first["provenance"]["upstream"],
            {"publicId": "700", "version": "1", "dateModified": "2026-01-02"},
        )
        self.assertEqual(second["provenance"]["upstream"], {"publicId": "800", "version": "3"})

    def test_recorded_values_keep_their_own_dates_and_no_version(self):
        result = self.answer(recorded_element(), publicId="2200604", include=["permissibleValues"])
        value = result.structured_content["permissibleValues"][0]
        self.assertEqual(
            {key: value[key] for key in ("dateModified", "beginDate", "endDate") if key in value},
            {"dateModified": "2025-03-25", "beginDate": "2002-02-11", "endDate": None},
        )
        self.assertNotIn("version", value)
        self.assertEqual(
            value["provenance"]["upstream"], {"publicId": "9192925", "dateModified": "2025-03-25"}
        )

    def test_component_include_contains_only_own_identity_name_and_dates(self):
        result = self.answer(recorded_element(), publicId="2200604", include=["dataElementConcept"])
        expected = {
            "publicId": "2226947",
            "version": "1",
            "longName": "Person Sex",
            "dateModified": "2026-08-25",
            "beginDate": "2005-04-27",
            "endDate": None,
            "objectClass": {
                "publicId": "2236731",
                "version": "1",
                "longName": "Person",
                "dateModified": "2007-11-07",
                "beginDate": "2005-06-20",
                "endDate": None,
            },
            "property": {
                "publicId": "2403945",
                "version": "1",
                "longName": "Sex",
                "dateModified": "2005-09-09",
                "beginDate": "2005-09-09",
                "endDate": None,
            },
        }
        self.assertEqual(result.structured_content.get("dataElementConcept"), expected)

    def test_flags_and_order_disagree_without_sorting_or_raw_flag_duplicates(self):
        body = recorded_element()
        rows = body["DataElement"]["ValueDomain"]["PermissibleValues"][0]["ValueMeaning"][
            "Concepts"
        ]
        rows[:] = [
            {"conceptCode": "C1", "primaryIndicator": "No", "displayOrder": " +0 "},
            {"conceptCode": "C2", "primaryIndicator": "Yes", "displayOrder": "-2"},
            {"conceptCode": "C1", "primaryIndicator": "No"},
        ]
        body["DataElement"]["DataElementConcept"]["ObjectClass"]["Concepts"] = rows
        result = self.answer(
            body, publicId="2200604", include=["permissibleValues", "conceptAssociations"]
        )
        expected = [
            {"conceptCode": "C1", "primary": False, "displayOrder": 0},
            {"conceptCode": "C2", "primary": True, "displayOrder": -2},
            {"conceptCode": "C1", "primary": False},
        ]
        self.assertEqual(
            result.structured_content["permissibleValues"][0]["valueMeaning"]["concepts"], expected
        )
        self.assertEqual(
            result.structured_content["conceptAssociations"][:3],
            [row | {"role": "objectClass"} for row in expected],
        )

    def test_omitted_modified_date_and_explicit_null_end_date_are_distinct(self):
        body = recorded_element()
        del body["DataElement"]["dateModified"]
        result = self.answer(body, publicId="2200604")
        self.assertNotIn("dateModified", result.structured_content)
        self.assertIsNone(result.structured_content["endDate"])

    def test_recorded_reversed_representation_keeps_source_flags_and_order(self):
        body = json.loads((FIXTURES / "data-element-2200604-version-1.json").read_text())[
            "response"
        ]["body"]
        result = self.answer(body, publicId="2200604", version="1", include=["conceptAssociations"])
        self.assertEqual(
            [
                row
                for row in result.structured_content["conceptAssociations"]
                if row["role"] == "representationTerm"
            ],
            [
                {
                    "conceptCode": "C25704",
                    "longName": "Text",
                    "terminology": "NCI_CONCEPT_CODE",
                    "primary": False,
                    "displayOrder": 1,
                    "role": "representationTerm",
                },
                {
                    "conceptCode": "C0332307",
                    "longName": "Type",
                    "terminology": "UMLS_CUI",
                    "primary": True,
                    "displayOrder": 0,
                    "role": "representationTerm",
                },
            ],
        )

    def test_malformed_derivation_flags_and_orders_fail_closed_for_every_role(self):
        for part in ("ObjectClass", "Property", "RepresentationTerm", "ValueMeaning"):
            for field, value in (
                ("primaryIndicator", None),
                ("primaryIndicator", "yes"),
                ("displayOrder", "1.5"),
                ("displayOrder", None),
                ("displayOrder", 0),
                ("displayOrder", ""),
            ):
                with self.subTest(part=part, field=field, value=value):
                    body = recorded_element()
                    element = body["DataElement"]
                    owners = {
                        "ObjectClass": element["DataElementConcept"]["ObjectClass"],
                        "Property": element["DataElementConcept"]["Property"],
                        "RepresentationTerm": element["ValueDomain"]["RepresentationTerm"],
                        "ValueMeaning": element["ValueDomain"]["PermissibleValues"][0][
                            "ValueMeaning"
                        ],
                    }
                    owners[part]["Concepts"][0][field] = value
                    result = self.answer(
                        body,
                        publicId="2200604",
                        include=["conceptAssociations", "permissibleValues"],
                    )
                    self.assertTrue(result.is_error)
                    self.assertEqual(
                        result.structured_content["error"]["code"], "upstream_unavailable"
                    )

    def test_component_identity_and_supplied_dates_fail_closed(self):
        for part in ("DataElementConcept", "ObjectClass", "Property"):
            for field, value in (
                ("publicId", None),
                ("version", "bad"),
                ("longName", None),
                ("dateModified", []),
            ):
                with self.subTest(part=part, field=field):
                    body = recorded_element()
                    dec = body["DataElement"]["DataElementConcept"]
                    owner = dec if part == "DataElementConcept" else dec[part]
                    owner[field] = value
                    result = self.answer(body, publicId="2200604", include=["dataElementConcept"])
                    self.assertTrue(result.is_error)
                    self.assertEqual(
                        result.structured_content["error"]["code"], "upstream_unavailable"
                    )
