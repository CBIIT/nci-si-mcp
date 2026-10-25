"""The weekly command reports every family, including structural drift, offline."""

import copy
import json
import subprocess
import sys
from pathlib import Path

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
