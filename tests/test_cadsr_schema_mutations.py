"""Compare public caDSR schemas and audit classifications with the source contract."""

import re
from pathlib import Path
from unittest import TestCase

import yaml
from pydantic import TypeAdapter

from nci_si_mcp import results
from nci_si_mcp.registry import SPECS

ROOT = Path(__file__).resolve().parents[1]
RECORDS = yaml.safe_load((ROOT / "spec/records.yaml").read_text())
TOOLS = yaml.safe_load((ROOT / "spec/tools.yaml").read_text())
RECORD_TYPES = {
    "registry_release": results.RegistryReleaseResult,
    "data_element": results.DataElement,
    "permissible_value": results.PermissibleValue,
    "classification_scheme": results.ClassificationScheme,
    "context": results.RegistryContext,
    "data_element_match": results.DataElementMatch,
    "value_meaning_match": results.ValueMeaningMatch,
    "form": results.Form,
    "code_map": results.CodeMap,
}


def schema(record):
    return TypeAdapter(record).json_schema()


def fields_in(text, occurrence=0):
    declaration = re.findall(r"\{([^{}]*)\}", text)[occurrence]
    return {
        part.strip().removesuffix("[]").removesuffix("?"): "?" not in part
        for part in declaration.split(",")
    }


# Parameters whose values are free text or opaque (audited as a SHA-256 digest); every other
# parameter of these tools is an identifier, a closed value or a number, audited in the clear.
HASHED = {
    "get_form": {"keyword"},
    "get_permissible_value": set(),
    "get_code_map": {"targetContext", "cursor"},
    "match_data_elements": {"entities", "modelVariant", "filters"},
    "match_value_meanings": {"values"},
    "get_data_element": {"longName", "questionText"},
    "search_data_elements": {"query", "filters", "cursor"},
    "list_contexts": {"cursor"},
    "list_classification_schemes": {"context", "cursor"},
    "resolve_registry_release": set(),
}


class CaDSRSchemaMutationTest(TestCase):
    def assert_fields(self, record, fields):
        actual = schema(record)
        self.assertEqual(set(actual["properties"]), set(fields))
        self.assertEqual(
            set(actual.get("required", [])), {key for key, required in fields.items() if required}
        )

    def test_all_cadsr_record_keys_and_optionality_match_specification(self):
        for name, record in RECORD_TYPES.items():
            with self.subTest(record=name):
                fields = {
                    key: not field.get("optional", False)
                    for key, field in RECORDS[name]["fields"].items()
                }
                if name == "registry_release":
                    # The same record is a tool result and a resource; M3.2 adds
                    # top-level provenance only for its resource representation.
                    fields["provenance"] = False
                self.assert_fields(record, fields)

    def test_nested_record_fields_match_the_specification_declarations(self):
        pv = RECORDS["permissible_value"]["fields"]["valueMeaning"]["content"]
        cases = (
            (results.ValueMeaning, pv, 0),
            (results.ValueMeaningConcept, pv, 1),
            (
                results.RegistryItemIdentity,
                RECORDS["classification_scheme"]["fields"]["items"]["content"],
                0,
            ),
            (
                results.ConceptAssociation,
                RECORDS["data_element"]["fields"]["conceptAssociations"]["content"],
                0,
            ),
            (
                results.MatchedRegistryItem,
                RECORDS["value_meaning_match"]["fields"]["item"]["content"],
                0,
            ),
            (
                results.MatchCrosswalk,
                RECORDS["value_meaning_match"]["fields"]["crosswalk"]["content"],
                0,
            ),
            (results.CodeMapIdentity, RECORDS["code_map"]["fields"]["dataElement"]["content"], 0),
            (results.CodeMapValue, RECORDS["code_map"]["fields"]["values"]["content"], 0),
            (
                results.RegistryReference,
                RECORDS["provenance"]["fields"]["release"]["forms"]["registry"],
                0,
            ),
            (results.DataElementHit, TOOLS["search_data_elements"]["returns"], 0),
        )
        for record, text, occurrence in cases:
            with self.subTest(record=record.__name__):
                self.assert_fields(record, fields_in(text, occurrence))

    def test_search_envelope_matches_the_tool_contract_and_empty_page_provenance(self):
        declared = TOOLS["search_data_elements"]["returns"]
        outer = re.sub(r"\[\{[^{}]*\}\]", "[]", declared)
        self.assert_fields(results.DataElementSearch, fields_in(outer) | {"provenance": False})

    def test_nested_closed_value_sets_remain_closed(self):
        declarations = (
            (results.RegistryReference, "registry", ["cadsr"], "provenance", "release"),
            (
                results.MatchedRegistryItem,
                "itemType",
                ["Concept", "ValueMeaning"],
                "value_meaning_match",
                "item",
            ),
            (
                results.ConceptAssociation,
                "role",
                ["objectClass", "property"],
                "data_element",
                "conceptAssociations",
            ),
        )
        for record, field, values, name, specification_field in declarations:
            with self.subTest(record=record.__name__, field=field):
                text = str(RECORDS[name]["fields"][specification_field])
                self.assertTrue(all(value in text for value in values))
                declaration = schema(record)["properties"][field]
                actual = declaration.get("enum", [declaration.get("const")])
                self.assertEqual(set(actual), set(values))

    def test_every_cadsr_field_preserves_its_nullability_contract(self):
        # caDSR's own nullable text metadata passes through unchanged (A9.1).
        # vmMatch's optional metadata is instead absent on null, per its record.
        item_text = {
            "longName",
            "context",
            "workflowStatus",
            "registrationStatus",
            "dateCreated",
            "dateModified",
        }
        nullable = {
            results.DataElement: item_text,
            results.Form: item_text,
            results.RegistryItemIdentity: {"longName"},
            results.ValueMeaning: {"longName"},
            results.ValueMeaningConcept: {"longName"},
            results.ConceptAssociation: {"longName"},
            results.ClassificationScheme: {"longName", "context"},
            results.CodeMap: {"crdcName"},
        }
        nested = {
            results.MatchedRegistryItem,
            results.MatchCrosswalk,
            results.CodeMapIdentity,
            results.CodeMapValue,
            results.RegistryReference,
            results.DataElementHit,
            results.DataElementSearch,
        }
        for record in set(RECORD_TYPES.values()) | set(nullable) | nested:
            for field, declaration in schema(record)["properties"].items():
                with self.subTest(record=record.__name__, field=field):
                    permits_null = {"type": "null"} in declaration.get("anyOf", [])
                    self.assertEqual(permits_null, field in nullable.get(record, set()))

    def test_each_cadsr_parameter_has_the_specification_audit_class(self):
        tools = {spec.name: spec for spec in SPECS if spec.group == "cadsr" and spec.name in TOOLS}
        self.assertEqual(set(tools), set(HASHED))
        for name, spec in tools.items():
            for parameter in spec.parameters:
                with self.subTest(tool=name, parameter=parameter.name):
                    expected = "hash" if parameter.name in HASHED[name] else "plain"
                    self.assertEqual(spec.audit.get(parameter.name), expected)

    def test_data_element_resource_identifiers_are_plain_audit_fields(self):
        patterns = TOOLS["get_data_element"]["patterns"]
        resources = [
            spec for spec in SPECS if spec.uri and spec.uri.startswith("cadsr://data-element/")
        ]
        self.assertEqual(len(resources), 2)
        for spec in resources:
            with self.subTest(uri=spec.uri):
                self.assertTrue(all(parameter.name in patterns for parameter in spec.parameters))
                self.assertEqual(
                    spec.audit, {parameter.name: "plain" for parameter in spec.parameters}
                )
