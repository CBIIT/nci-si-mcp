"""Operation-specific guidance preserves the original failure and its details."""

from unittest import TestCase

from nci_si_mcp.errors import PlatformError
from nci_si_mcp.invocation import call


def fail(error):
    raise error


class InvocationMutationTest(TestCase):
    def test_search_hint_is_conditional_and_preserves_transient_failure_details(self):
        details = {"surface": "cadsr", "status": 503, "attempts": 3}
        error = PlatformError("upstream_unavailable", "Service temporarily unavailable.", **details)
        result = call("search_data_elements", lambda: fail(error))["error"]
        self.assertEqual(result.get("details"), details)
        self.assertTrue(result["message"].startswith(error.message))
        self.assertIn("if this persists", result["message"])
        self.assertNotIn("Retrying will not help", result["message"])
        self.assertIn("get_data_element", result["message"])

    def test_search_argument_failure_has_no_upstream_capability_hint(self):
        error = PlatformError("invalid_request", "Correct query.", parameter="query")
        result = call("search_data_elements", lambda: fail(error))["error"]
        self.assertEqual(result["message"], "Correct query.")
        self.assertEqual(result["details"], {"parameter": "query"})

    def test_both_matching_timeouts_name_the_matching_setting_and_preserve_details(self):
        for operation in ("match_data_elements", "match_value_meanings"):
            with self.subTest(operation=operation):
                details = {"surface": "cadsr", "seconds": 45, "attempts": 3}
                error = PlatformError("timeout", "Timed out.", **details)
                result = call(operation, lambda error=error: fail(error))["error"]
                self.assertEqual(result["code"], "timeout")
                self.assertEqual(result["details"], details)
                self.assertIn("NCI_SI_MATCH_TIMEOUT_SECONDS", result["message"])
