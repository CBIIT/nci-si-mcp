"""An absent upstream status stays absent in schema-validated MCP records."""

from unittest.mock import patch

from fakes import concept
from test_server import ServerFixture


class StatusReviewTest(ServerFixture):
    def setUp(self):
        super().setUp()
        self.evs.concepts = {
            "C1": concept("C1", "One", active=True, children=[{"code": "C2"}]),
            "C2": concept("C2", "Two", active=True),
        }

    def records(self, operation, arguments, field):
        response = self.session(
            lambda client: client.call_tool(
                operation, {"terminology": "ncit", "release": "26.06e"} | arguments
            )
        )
        self.assertFalse(response.is_error)
        payload = response.structured_content
        return payload[field] if field else [payload]

    def concept_records(self):
        return (
            self.records("get_concept", {"code": "C1"}, None)
            + self.records("get_concepts", {"codes": ["C1", "C2"]}, "concepts")
            + self.records("resolve_retired_code", {"code": "C1"}, None)
        )

    def node_records(self):
        return self.records(
            "get_concept_hierarchy", {"code": "C1", "direction": "child"}, "nodes"
        ) + self.records("get_concept_neighborhood", {"code": "C1", "kinds": ["child"]}, "nodes")

    def search_records(self):
        with patch.object(
            self.evs,
            "search_concepts",
            return_value=(1, [self.evs.concepts["C1"]]),
            create=True,
        ):
            hits = self.records("search_concepts", {"query": "One"}, "results")
        return [hit["concept"] for hit in hits]

    def set_status(self, value):
        for raw in self.evs.concepts.values():
            raw["conceptStatus"] = value

    def test_null_and_empty_status_are_omitted_in_every_output_arm(self):
        for value in (None, ""):
            with self.subTest(value=value):
                self.set_status(value)
                records = self.concept_records() + self.node_records() + self.search_records()
                self.assertEqual(len(records), 8)
                for record in records:
                    self.assertNotIn("status", record)

    def test_nonempty_status_is_unchanged_in_every_output_arm(self):
        self.set_status("  Upstream status  ")
        records = self.concept_records() + self.node_records() + self.search_records()
        self.assertEqual([record["status"] for record in records], ["  Upstream status  "] * 8)
