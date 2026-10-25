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


def bodies():
    return [
        json.loads((ROOT / "acceptance/fixtures" / name).read_text())["response"]["body"]
        for name in EVIDENCE
    ]


def passed():
    return [{"id": name, "status": "pass"} for name in IDS]


class SSISContractCommandTest(ServerTestCase):
    def command(self, payloads):
        server = self.serve(*[Reply(body=json.dumps(body).encode()) for body in payloads])
        result = subprocess.run(  # noqa: S603 - fixed module and local fixture server
            [sys.executable, "-m", "scripts.ssis_contracts", "--url", server.url],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, server

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

    def test_recorded_and_approved_optional_contracts_pass(self):
        result, server = self.command(bodies())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), passed())
        self.assertEqual([path for path, _ in server.seen], ["/sparql"] * 6)

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
    def test_every_required_variable_is_required_even_when_other_terms_are_valid(self):
        entries = yaml.safe_load(REGISTER.read_text())["queries"]
        for entry, original in zip(entries, bodies(), strict=True):
            for key in entry["required"]:
                with self.subTest(family=entry["id"], missing=key):
                    payload = copy.deepcopy(original)
                    del payload["results"]["bindings"][0][key]
                    with self.assertRaisesRegex(ValueError, "binding missing required variables"):
                        validate(payload, entry)

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
        payload = copy.deepcopy(original)
        payload["results"]["bindings"] = [
            {
                "version": {"type": "literal", "value": "1"},
                "concept": {"type": "uri", "value": "http://example.test/C1"},
            }
        ]
        with self.assertRaisesRegex(ValueError, "inconsistent OPTIONAL"):
            validate(payload, entry)
        payload["results"]["bindings"] = original["results"]["bindings"][:1] * 1002
        with self.assertRaisesRegex(ValueError, "exceed requested LIMIT"):
            validate(payload, entry)
