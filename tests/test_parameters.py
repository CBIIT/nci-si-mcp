import unittest
from typing import TypedDict

from nci_si_mcp.parameters import FIELD_DESCRIPTIONS, Described, describe_fields


class Row(TypedDict):
    name: str
    size: int


class DescribedTest(unittest.TestCase):
    def test_a_record_field_without_a_description_is_refused(self):
        with self.assertRaises(TypeError) as raised:
            describe_fields(Row, name="What it is called.")

        self.assertIn("Row needs one description for each of its fields", str(raised.exception))
        self.assertNotIn("Row", FIELD_DESCRIPTIONS)

    def test_a_description_for_a_field_the_record_lacks_is_refused(self):
        with self.assertRaises(TypeError):
            describe_fields(Row, name="Name.", size="Size.", colour="Colour.")

    def test_only_the_constraints_given_are_stated(self):
        self.assertEqual(Described("Text.").field_arguments(), {"description": "Text."})
        self.assertEqual(
            Described("Text.", pattern="^a$", min_items=1, max_items=2).field_arguments(),
            {"description": "Text.", "pattern": "^a$", "min_length": 1, "max_length": 2},
        )
