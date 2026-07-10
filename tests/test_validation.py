import unittest

from nci_si_mcp.embeddings import create_embedding_provider
from nci_si_mcp.errors import InputValidationError
from nci_si_mcp.validation import validate_ncit_codes, validate_search, validate_traversal


class ValidationTest(unittest.TestCase):
    def test_codes_are_normalized_and_deduplicated(self):
        self.assertEqual(validate_ncit_codes(["c3262", " C3262 "]), ["C3262"])

    def test_search_rejects_blank_query_and_negative_limit(self):
        with self.assertRaises(InputValidationError):
            validate_search(" ", 10, "hybrid")
        with self.assertRaises(InputValidationError):
            validate_search("tumor", -1, "hybrid")

    def test_traversal_rejects_unknown_direction_and_edge_type(self):
        with self.assertRaises(InputValidationError):
            validate_traversal(["C3262"], "sideways", 1, 10, 10, None)
        with self.assertRaises(InputValidationError):
            validate_traversal(["C3262"], "out", 1, 10, 10, ["unknown"])

    def test_embedding_configuration_must_be_consistent(self):
        with self.assertRaises(ValueError):
            create_embedding_provider("sentence-transformers", "hashing")
        with self.assertRaises(ValueError):
            create_embedding_provider("unknown", "hashing")


if __name__ == "__main__":
    unittest.main()
