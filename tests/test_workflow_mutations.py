"""Workflow composition preserves caller-visible release selection metadata."""

import hashlib
import json
import logging
from unittest.mock import patch

from fakes import concept
from nci_si_mcp.release_selection import SessionRelease, session_scope
from test_workflows import WorkflowFixture


class GroundReleaseTest(WorkflowFixture):
    def test_nested_reads_preserve_implicit_selection_and_its_release_date(self):
        logging.disable(logging.NOTSET)
        for arguments in ({"conceptCode": "C1"}, {"text": "query"}):
            with (
                self.subTest(arguments=arguments),
                session_scope(SessionRelease()),
                self.assertLogs("nci_si_mcp.audit", level="INFO") as captured,
                patch.object(
                    self.evs,
                    "search_concepts",
                    create=True,
                    return_value=(1, [concept("C1", active=True)]),
                ),
            ):
                first = self.call(**arguments)
                held = self.call(**arguments)
                explicit = self.call(**arguments, release="26.06e")
            self.assertNotIn("error", explicit)
            self.assertEqual(
                [result["concept"]["provenance"]["release"] for result in (first, held)],
                [{"terminology": "ncit", "identifier": "26.06e", "date": "2026-06-29"}] * 2,
            )
            records = [record.structured["release"] for record in captured.records]
            self.assertEqual(
                [record["selection"] for record in records],
                ["freshly-resolved", "session-held", "explicit"],
            )
            self.assertEqual(
                [record["selected"]["date"] for record in records[:2]], ["2026-06-29"] * 2
            )

    def test_dictionary_input_is_hashed_even_when_a_later_column_is_invalid(self):
        columns = [
            {
                "name": "sensitive-name",
                "description": "sensitive-description",
                "sampleValues": ["sensitive-value"],
            },
            {"name": None},
        ]
        logging.disable(logging.NOTSET)
        with self.assertLogs("nci_si_mcp.audit", level="INFO") as captured:
            result = self.call("harmonize_data_dictionary", columns=columns)
        self.assertEqual(result["error"]["code"], "invalid_request")
        record = captured.records[-1].structured
        encoded = json.dumps(
            columns, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        self.assertEqual(
            record["parameters"]["columns"], {"sha256": hashlib.sha256(encoded).hexdigest()}
        )
        self.assertNotIn("sensitive-", json.dumps(record))
