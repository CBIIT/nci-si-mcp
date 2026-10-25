"""The weekly command reports every family, including structural drift, offline."""

import ast
import copy
import inspect
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml
from scripts.ssis_contracts import REGISTER, request, validate

from nci_si_mcp.registry import SPECS
from nci_si_mcp.ssis import SSISClient
from test_http_client import Reply, ServerTestCase

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    "crafted/ssis-sparql/graph-identities-bounded.json",
    "recorded/ssis-sparql/data-elements-c17357.json",
    "recorded/ssis-sparql/data-elements-c17357-descendants.json",
    "recorded/ssis-sparql/values-c20197.json",
    "recorded/ssis-sparql/values-c17357-descendants.json",
    "crafted/ssis-sparql/values-of-2200604-optional.json",
)
IDS = (
    "graph-identities",
    "elements-exact",
    "elements-descendants",
    "values-exact",
    "values-descendants",
    "values-by-public-id",
)
REQUIRED = (
    ("graph", "date"),
    ("id", "version", "name"),
    ("id", "version", "name"),
    ("id", "version", "value", "concept"),
    ("id", "version", "value", "concept"),
    ("version",),
)


def bodies():
    return [
        json.loads((ROOT / "acceptance/fixtures" / name).read_text())["response"]["body"]
        for name in EVIDENCE
    ]


def passed():
    return [{"id": name, "status": "pass"} for name in IDS]


class SSISContractCommandTest(ServerTestCase):
    def command(self, payloads):
        server = self.serve(
            *[
                Reply(body=body if isinstance(body, bytes) else json.dumps(body).encode())
                for body in payloads
            ]
        )
        result = subprocess.run(  # noqa: S603 - fixed module and local fixture server
            [sys.executable, "-m", "scripts.ssis_contracts", "--url", server.url],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, server

    def test_bad_success_body_reports_all_families_without_upstream_prose(self):
        # Given a bad first HTTP 200 and five valid responses, when the real CLI runs,
        # then it reports the failure safely and still checks every remaining family.
        for bad in (
            b"<html>private-upstream-prose</html>",
            b"private-upstream-prose: not JSON",
            b'{"apiResponse":{"type":"E","message":"private-upstream-prose"}}',
        ):
            with self.subTest(body=bad):
                payloads = bodies()
                payloads[0] = bad
                result, server = self.command(payloads)
                expected = passed()
                expected[0] = {
                    "id": "graph-identities",
                    "status": "fail",
                    "error": "upstream_unavailable",
                }
                self.assertEqual(result.stdout, json.dumps(expected, indent=2) + "\n")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(server.bodies), 6)
                self.assertEqual(result.stderr, "")

    def test_graph_dates_reject_invalid_values_and_accept_runtime_date_forms(self):
        # Given graph metadata, when the CLI checks its date, then runtime-supported
        # ISO and English dates pass while impossible or arbitrary dates fail.
        for date, valid in (
            ("not-a-date", False),
            ("2026-02-30", False),
            ("", False),
            ("2026-10-08", True),
            ("October 08, 2026", True),
        ):
            with self.subTest(date=date):
                payloads = bodies()
                payloads[0]["results"]["bindings"][0]["date"]["value"] = date
                result, server = self.command(payloads)
                expected = passed()
                if not valid:
                    expected[0] = {
                        "id": "graph-identities",
                        "status": "fail",
                        "error": "date: invalid graph date",
                    }
                self.assertEqual(json.loads(result.stdout), expected)
                self.assertEqual(result.returncode, int(not valid), result.stderr)
                self.assertEqual(len(server.bodies), 6)

    def test_missing_head_variable_fails_exact_family_and_still_reports_all_six(self):
        # Given recorded rows intact but SELECT metadata missing name, when the weekly
        # command runs, then it fails that family and still attempts the later families.
        payloads = bodies()
        payloads[1]["head"]["vars"].remove("name")
        result, server = self.command(payloads)
        expected = passed()
        expected[1] = {
            "id": "elements-exact",
            "status": "fail",
            "error": "head.vars must be exactly: id, version, name",
        }
        self.assertEqual(result.stdout, json.dumps(expected, indent=2) + "\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(len(server.bodies), 6)

    def test_blank_element_names_fail_both_families_and_still_report_all_six(self):
        # Given blank names in both element families, when the real CLI runs,
        # then each reports name drift and all six queries finish with a nonzero exit.
        for name in ("", " \t\n"):
            with self.subTest(name=name):
                payloads = bodies()
                expected = passed()
                for index in (1, 2):
                    payloads[index]["results"]["bindings"][0]["name"]["value"] = name
                    expected[index] = {
                        "id": IDS[index],
                        "status": "fail",
                        "error": "name: must not be blank",
                    }
                result, server = self.command(payloads)
                self.assertEqual(json.loads(result.stdout), expected)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(len(server.bodies), 6)

    def test_nonblank_names_and_empty_permissible_value_text_pass(self):
        # Given nonblank names with surrounding whitespace and empty permissible
        # value text, when the CLI runs, then these valid content terms still pass.
        payloads = bodies()
        for index in (1, 2):
            payloads[index]["results"]["bindings"][0]["name"]["value"] = " \tValid name\n"
        for payload in payloads[3:]:
            payload["results"]["bindings"][0]["value"]["value"] = ""
        result, server = self.command(payloads)
        self.assertEqual(json.loads(result.stdout), passed())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(server.bodies), 6)

    def test_recorded_and_approved_optional_contracts_pass(self):
        result, server = self.command(bodies())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), passed())
        self.assertEqual([path for path, _ in server.seen], ["/sparql"] * 6)

    def test_both_graph_identities_are_required_but_content_families_may_be_empty(self):
        # Given empty content families and zero, one or both graph identities, when
        # the CLI runs, then only a missing required identity fails the graph family.
        for retained in ((), (0,), (1,), (0, 1)):
            with self.subTest(retained=retained):
                payloads = bodies()
                rows = payloads[0]["results"]["bindings"]
                payloads[0]["results"]["bindings"] = [rows[index] for index in retained]
                for payload in payloads[1:]:
                    payload["results"]["bindings"] = []
                result, server = self.command(payloads)
                expected = passed()
                missing = retained != (0, 1)
                if missing:
                    expected[0] = {
                        "id": "graph-identities",
                        "status": "fail",
                        "error": "graph: required identity missing",
                    }
                self.assertEqual(json.loads(result.stdout), expected)
                self.assertEqual(result.returncode, int(missing), result.stderr)
                self.assertEqual(len(server.bodies), 6)

    def test_unselected_binding_and_third_graph_fail_the_exact_family(self):
        for index, key, term, error in (
            (
                1,
                "unselected",
                {"type": "literal", "value": "extra"},
                "binding has unselected variables",
            ),
            (
                0,
                "graph",
                {"type": "uri", "value": "https://example.test/third-graph"},
                "graph: unexpected identity",
            ),
        ):
            with self.subTest(error=error):
                payloads = bodies()
                rows = payloads[index]["results"]["bindings"]
                if index == 0:
                    rows.append(copy.deepcopy(rows[0]))
                rows[-1][key] = term
                result, server = self.command(payloads)
                expected = passed()
                expected[index] = {"id": IDS[index], "status": "fail", "error": error}
                self.assertEqual(json.loads(result.stdout), expected)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(len(server.bodies), 6)

    def test_empty_and_changed_content_counts_and_releases_pass(self):
        payloads = bodies()
        payloads[0]["results"]["bindings"][0]["version"]["value"] = "future-release"
        payloads[1]["results"]["bindings"] = []
        payloads[2]["results"]["bindings"] = payloads[2]["results"]["bindings"][:1]
        payloads[2]["results"]["bindings"][0]["name"]["value"] = "Changed upstream label"
        payloads[5]["results"]["bindings"] = [{"version": {"type": "literal", "value": "9"}}]
        payloads[5]["results"]["bindings"].append(
            {
                "version": {"type": "literal", "value": "10"},
                "value": {"type": "literal", "value": "unmapped"},
            }
        )
        result, _ = self.command(payloads)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), passed())

    def test_every_family_rejects_missing_required_bindings_and_bad_terms(self):
        for bad_term in (None, {"type": "bnode", "value": "bad"}):
            with self.subTest(term=bad_term):
                payloads = bodies()
                for payload in payloads:
                    row = payload["results"]["bindings"][0]
                    key = next(iter(row))
                    row[key] = copy.deepcopy(bad_term)
                result, _ = self.command(payloads)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual([row["status"] for row in json.loads(result.stdout)], ["fail"] * 6)

    def test_http_failure_is_reported_and_does_not_stop_later_queries(self):
        server = self.serve(Reply(403), *[Reply(body=json.dumps(b).encode()) for b in bodies()[1:]])
        result = subprocess.run(  # noqa: S603 - fixed CLI and local fixture URL
            [sys.executable, "-m", "scripts.ssis_contracts", "--url", server.url],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        expected = passed()
        expected[0] = {"id": "graph-identities", "status": "fail", "error": "UpstreamRejectedError"}
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(server.bodies), 6)


def normalized(query):
    query = re.sub(r"ncit:C[1-9][0-9]*", "ncit:CODE", query)
    query = re.sub(r'cadsr:publicId "[1-9][0-9]*"', 'cadsr:publicId "ID"', query)
    return re.sub(r"LIMIT [0-9]+", "LIMIT BOUND", " ".join(query.split()))


def query_operations():
    tree = ast.parse(inspect.getsource(SSISClient))
    return {
        node.name
        for node in tree.body[0].body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }


class SSISRegisterTest(unittest.TestCase):
    def test_content_versions_follow_item_version_contract(self):
        # Given a content binding, when its version is checked, then only the
        # item-version syntax accepted by the consuming seam passes.
        entries = yaml.safe_load(REGISTER.read_text())["queries"]
        for entry, original in zip(entries[1:], bodies()[1:], strict=True):
            for version in ("invalid", "1.2.3", " 1", "1 ", "-1", "1."):
                with self.subTest(family=entry["id"], version=version):
                    payload = copy.deepcopy(original)
                    payload["results"]["bindings"][0]["version"]["value"] = version
                    with self.assertRaises(ValueError) as raised:
                        validate(payload, entry)
                    self.assertEqual(str(raised.exception), "version: invalid item version")

    def test_concepts_require_ncit_namespace_and_code(self):
        # Given a value binding, when its concept URI cannot resolve at the seam,
        # then the checker rejects it even though it is an absolute URI.
        entries = yaml.safe_load(REGISTER.read_text())["queries"]
        for entry, original in zip(entries[3:], bodies()[3:], strict=True):
            for uri in (
                "https://example.test/C1",
                "https://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1",
                "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C0",
                "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C01",
                "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#bad",
                "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1/extra",
            ):
                with self.subTest(family=entry["id"], uri=uri):
                    payload = copy.deepcopy(original)
                    payload["results"]["bindings"][0]["concept"]["value"] = uri
                    with self.assertRaises(ValueError) as raised:
                        validate(payload, entry)
                    self.assertEqual(str(raised.exception), "concept: expected NCIt concept URI")

    def test_valid_item_versions_and_opaque_graph_versions_pass(self):
        entries = yaml.safe_load(REGISTER.read_text())["queries"]
        for entry, original in zip(entries, bodies(), strict=True):
            versions = (
                ("invalid", "1.2.3", "future-release")
                if entry["id"] == IDS[0]
                else ("0", "1", "1.0", "01.02")
            )
            for version in versions:
                with self.subTest(family=entry["id"], version=version):
                    payload = copy.deepcopy(original)
                    payload["results"]["bindings"][0]["version"] = {
                        "type": "literal",
                        "value": version,
                    }
                    self.assertIsNone(validate(payload, entry))

    def test_every_required_variable_is_required_even_when_other_terms_are_valid(self):
        entries = yaml.safe_load(REGISTER.read_text())["queries"]
        for entry, original, required in zip(entries, bodies(), REQUIRED, strict=True):
            self.assertEqual(entry["required"], list(required), entry["id"])
            for key in required:
                with self.subTest(family=entry["id"], missing=key):
                    payload = copy.deepcopy(original)
                    del payload["results"]["bindings"][0][key]
                    with self.assertRaises(ValueError) as raised:
                        validate(payload, entry)
                    self.assertEqual(str(raised.exception), "binding missing required variables")

    def test_register_maps_content_hops_to_their_spec_requirements(self):
        entries = yaml.safe_load(REGISTER.read_text())["queries"]
        expected = (
            ["X-1", "X-2", "X-3", "get_release_alignment-1"],
            ["find_data_elements_for_concept-1", "ground_value-2"],
            ["find_data_elements_for_concept-1"],
            ["find_data_elements_for_concept-2", "ground_value-2"],
            ["find_data_elements_for_concept-2"],
            ["get_concept_for_permissible_value-1"],
        )
        self.assertEqual([entry["requirements"] for entry in entries], list(expected))

    def test_register_references_real_spec_requirements_and_tools(self):
        requirements = yaml.safe_load((ROOT / "spec/requirements.yaml").read_text())
        tools = {spec.name for spec in SPECS}
        for entry in yaml.safe_load(REGISTER.read_text())["queries"]:
            with self.subTest(family=entry["id"]):
                self.assertLessEqual(set(entry["requirements"]), requirements.keys())
                self.assertLessEqual(set(entry["consumers"]), tools)

    def test_register_covers_every_query_operation_and_expansion_variant_exactly(self):
        register = yaml.safe_load(REGISTER.read_text())
        operations = query_operations()
        entries = register["queries"]
        self.assertEqual({entry["operation"] for entry in entries}, operations)
        self.assertEqual(tuple(entry["id"] for entry in entries), IDS)
        for operation in operations:
            signature = inspect.signature(getattr(SSISClient, operation))
            expected = [False, True] if "expand_descendants" in signature.parameters else [None]
            actual = [
                e["kwargs"].get("expand_descendants")
                for e in entries
                if e["operation"] == operation
            ]
            self.assertEqual(actual, expected)

    def test_real_client_requests_match_independent_evidence_only_parameters_vary(self):
        register = yaml.safe_load(REGISTER.read_text())
        for entry in register["queries"]:
            with self.subTest(family=entry["id"]):
                fixture = json.loads((ROOT / "acceptance/fixtures" / entry["evidence"]).read_text())
                captured = request(entry)
                expected = fixture["request"]
                self.assertEqual(
                    register["transport"],
                    {
                        "method": expected["method"],
                        "path": expected["path"],
                        "accept": expected["headers"]["Accept"],
                        "form": "query",
                    },
                )
                self.assertEqual(
                    (captured.path, captured.accept),
                    (expected["path"], expected["headers"]["Accept"]),
                )
                self.assertEqual(set(captured.fields), {"query"})
                self.assertEqual(
                    normalized(captured.fields["query"]), normalized(expected["form"]["query"])
                )
                self.assertEqual(
                    captured.fields["query"].split()[-2:], ["LIMIT", str(entry["limit"])]
                )
                if entry["args"]:
                    varied = copy.deepcopy(entry)
                    varied["args"] = ["123" if entry["id"] == "values-by-public-id" else "C4817"]
                    varied["kwargs"]["maximum"] = 2
                    changed = request(varied).fields["query"]
                    self.assertEqual(normalized(changed), normalized(expected["form"]["query"]))
                    self.assertEqual(changed.split()[-2:], ["LIMIT", "3"])

    def test_structural_boundaries_and_semantic_identifiers_are_checked(self):
        entry = yaml.safe_load(REGISTER.read_text())["queries"][1]
        original = bodies()[1]
        cases = [
            (None, "response must be an object"),
            ({"head": original["head"]}, "results.bindings must be a list"),
        ]
        for key, term, error in (
            ("id", {"type": "literal", "value": "007"}, "id: invalid public identifier"),
            ("version", {"type": "literal", "value": ""}, "version: must not be blank"),
            ("name", {"type": "literal", "value": 3}, "name: term value must be a string"),
        ):
            payload = copy.deepcopy(original)
            payload["results"]["bindings"][0][key] = term
            cases.append((payload, error))
        for payload, error in cases:
            with self.subTest(error=error), self.assertRaises(ValueError) as raised:
                validate(payload, entry)
            self.assertEqual(str(raised.exception), error)

    def test_optional_combinations_and_query_limit_are_not_silently_accepted(self):
        entry = yaml.safe_load(REGISTER.read_text())["queries"][5]
        original = bodies()[5]
        row = original["results"]["bindings"][0]
        for selected in (
            (),
            ("value",),
            ("concept",),
            ("role",),
            ("value", "concept"),
            ("value", "role"),
            ("concept", "role"),
            ("value", "concept", "role"),
        ):
            with self.subTest(selected=selected):
                candidate = copy.deepcopy(original)
                candidate["results"]["bindings"] = [
                    {
                        key: value
                        for key, value in row.items()
                        if key == "version" or key in selected
                    }
                ]
                if selected in ((), ("value",), ("value", "concept", "role")):
                    validate(candidate, entry)
                    self.assertEqual(
                        set(candidate["results"]["bindings"][0]), {"version", *selected}
                    )
                else:
                    with self.assertRaises(ValueError) as raised:
                        validate(candidate, entry)
                    self.assertEqual(
                        str(raised.exception), "binding has inconsistent OPTIONAL variables"
                    )
        payload = copy.deepcopy(original)
        payload["results"]["bindings"] = [
            {
                "version": {"type": "literal", "value": "1"},
                "concept": {
                    "type": "uri",
                    "value": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1",
                },
            }
        ]
        with self.assertRaisesRegex(ValueError, "inconsistent OPTIONAL"):
            validate(payload, entry)
        payload["results"]["bindings"] = original["results"]["bindings"][:1] * 1002
        with self.assertRaisesRegex(ValueError, "exceed requested LIMIT"):
            validate(payload, entry)

    def test_concept_and_role_require_absolute_uris_and_role_must_be_main(self):
        entry = yaml.safe_load(REGISTER.read_text())["queries"][5]
        original = bodies()[5]
        cases = (
            ("concept", "literal", "http://example.test/C1", "concept: expected absolute URI"),
            (
                "role",
                "literal",
                "http://cbiit.nci.nih.gov/caDSR#main_concept",
                "role: expected absolute URI",
            ),
            ("concept", "uri", "relative/C1", "concept: expected absolute URI"),
            ("role", "uri", "#main_concept", "role: expected absolute URI"),
            (
                "role",
                "uri",
                "http://cbiit.nci.nih.gov/caDSR#minor_concept",
                "role: expected main concept",
            ),
        )
        for key, kind, value, error in cases:
            with self.subTest(key=key, kind=kind, value=value):
                payload = copy.deepcopy(original)
                payload["results"]["bindings"][0][key] = {"type": kind, "value": value}
                with self.assertRaises(ValueError) as raised:
                    validate(payload, entry)
                self.assertEqual(str(raised.exception), error)
