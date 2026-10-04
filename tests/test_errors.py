import json
import unittest
from typing import get_args

from nci_si_mcp.errors import (
    ErrorClass,
    PlatformError,
    is_error_record,
    serialise,
    with_next_step,
)

SIX_CLASSES = {
    "invalid_request",
    "not_found",
    "release_unavailable",
    "upstream_unavailable",
    "bound_exceeded",
    "internal",
}


class SerialiseTest(unittest.TestCase):
    def test_the_taxonomy_is_the_six_classes(self):
        self.assertEqual(set(get_args(ErrorClass)), SIX_CLASSES)

    def test_every_class_serialises_to_the_error_record(self):
        for error_class in get_args(ErrorClass):
            with self.subTest(error_class):
                record = serialise(PlatformError(error_class, "It failed. Try again."))

                self.assertEqual(
                    record, {"error": {"code": error_class, "message": "It failed. Try again."}}
                )
                self.assertTrue(is_error_record(record))

    def test_details_are_carried_when_given(self):
        error = PlatformError("bound_exceeded", "Too large.", bound="limit", limit=10)

        record = serialise(error)

        self.assertEqual(record["error"]["details"], {"bound": "limit", "limit": 10})
        self.assertEqual(json.loads(json.dumps(record)), record)

    def test_a_result_is_not_an_error_record(self):
        for result in ({}, {"hits": []}, {"error": 1, "hits": []}, {"edges": [], "nodes": []}):
            with self.subTest(result=result):
                self.assertFalse(is_error_record(result))

    def test_a_next_step_follows_the_message_after_one_full_stop(self):
        self.assertEqual(with_next_step("It failed", "Retry."), "It failed. Retry.")
        self.assertEqual(with_next_step("It failed.", "Retry."), "It failed. Retry.")
