"""Live provenance stays visible and bound through existing evidence consumers."""

import hashlib
import json
import unittest
from copy import deepcopy

from scripts.benchmark_report import Report
from scripts.evidence_benchmark_common import comparable
from scripts.evidence_envelope import EvidenceError
from scripts.portal_views import run_page

from test_evidence_acceptance import project as acceptance_projection
from test_evidence_acceptance import report as acceptance_report
from test_evidence_benchmark import benchmark, project, selected_projection

ENVIRONMENT = {
    "name": "evs-only",
    "origins": {
        "evs": "https://example.test/api",
        "evs-fhir": "https://example.test/fhir/r4",
        "cadsr": None,
        "cadsr-ftp": None,
        "cadsr-contracts": None,
        "ssis": None,
        "ssis-sparql": None,
    },
    "policy_sha256": "a" * 64,
}


class EnvironmentConsumerTest(unittest.TestCase):
    def test_benchmark_record_roundtrips_and_binds_environment_without_inventing_history(self):
        historical = benchmark() | {"mode": "live"}
        self.assertEqual(Report.from_dict(historical, "completed").to_dict(), historical)
        current = historical | {"environment": deepcopy(ENVIRONMENT)}
        parsed = None
        rejected = None
        try:
            parsed = Report.from_dict(current, "completed")
        except EvidenceError as error:
            rejected = str(error)
        self.assertIsNone(rejected)
        self.assertEqual(parsed.to_dict(), current)
        canonical = json.dumps(ENVIRONMENT, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(parsed.fingerprint().environment, hashlib.sha256(canonical).hexdigest())
        self.assertEqual(project(current).get("environment"), ENVIRONMENT)
        self.assertNotIn("environment", project(historical))

    def test_benchmark_other_fields_and_explicit_null_remain_strict(self):
        historical = benchmark()
        for change in ({"private-extra": "private-marker"}, {"environment": None}):
            with self.subTest(change=change), self.assertRaises(EvidenceError):
                Report.from_dict(historical | change, "completed")
        missing = deepcopy(historical)
        missing.pop("sourceSha256")
        with self.assertRaises(EvidenceError):
            Report.from_dict(missing, "completed")

    def test_different_known_environment_cannot_satisfy_benchmark_comparison(self):
        original = benchmark() | {"mode": "live", "environment": deepcopy(ENVIRONMENT)}
        changed = deepcopy(original)
        changed["environment"]["policy_sha256"] = "b" * 64
        left, right = selected_projection(original), selected_projection(changed)
        self.assertNotEqual(left["fingerprint"]["environment"], right["fingerprint"]["environment"])
        self.assertIn("environment", comparable(left, right)[1])

    def test_acceptance_projection_and_portal_preserve_only_recorded_live_provenance(self):
        historical = acceptance_report() | {"mode": "live"}
        self.assertNotIn("environment", acceptance_projection(historical))
        current = acceptance_projection(historical | {"environment": deepcopy(ENVIRONMENT)})
        self.assertEqual(current.get("environment"), ENVIRONMENT)
        html = run_page(
            {
                "sequence": 1,
                "checksum": "a" * 64,
                "origin": "local-import-unverified",
                "evidence": current,
            }
        )
        self.assertIn("Live environment", html)
        self.assertIn("evs-only", html)
        self.assertIn("https://example.test/api", html)
