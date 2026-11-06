"""Live provenance stays visible and bound through existing evidence consumers."""

import unittest
from copy import deepcopy

from scripts.portal_views import run_page

from test_evidence_acceptance import project as acceptance_projection
from test_evidence_acceptance import report as acceptance_report

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
